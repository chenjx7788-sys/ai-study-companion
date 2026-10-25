"""技能路由：列表 / 详情 / 建议匹配 / 增删改 / 导入导出 / 启停。

⚠️ 列表**不返回 instructions**：常驻清单每轮都进上下文，只吃 name+description
（`MAX_RESIDENT=12` 的预算就是按这个算的）。instructions 只在详情与注入时取。

⚠️ 路由顺序：所有具体路径（`/match` `/import` `/validate`）必须写在 `/{skill_id}`
之前。FastAPI 按声明顺序匹配，`GET /{skill_id}` 一旦先声明，`GET /skills/import`
就会变成「找一个叫 import 的技能」（404 而不是 405，很难查）。
"""
import urllib.parse

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from ..services import skills as skills_svc

router = APIRouter(prefix="/skills", tags=["skills"])


def _guard(fn, *args, **kwargs):
    """把 service 层的领域异常翻译成 HTTP 状态（400/403/404/409 由异常自带）。

    统一走这里而不是注册全局 handler：全局 handler 会把别的模块的同名异常一起吃掉。
    """
    try:
        return fn(*args, **kwargs)
    except skills_svc.SkillInvalid as e:
        raise HTTPException(getattr(e, "status", 400), e.reason)


@router.get("")
def list_skills():
    """列表 + 被拒绝的文件清单。

    `rejected` 必须返回：技能文件被静默忽略是最难查的故障（用户以为自己写对了）。
    `items` **含已停用技能**（`enabled=false`）：否则一关掉就再也看不到、无法恢复。
    """
    items, truncated = skills_svc.list_public()
    return {
        "items": items,
        "truncated": truncated,
        "resident_count": sum(1 for i in items if i.get("resident")),
        "enabled_count": sum(1 for i in items if i.get("enabled")),
        "max_resident": skills_svc.MAX_RESIDENT,
        # 用户不知道把 .skill.md 放哪就等于没有扩展能力 → 返回真实路径供页面显示
        "user_dir": str(skills_svc.user_dir()),
        "rejected": skills_svc.load_all()["rejected"],
        # 语义预判开关（P3）：页面顶部的开关要能显示真实状态
        "semantic_enabled": skills_svc.semantic_enabled(),
        # 可用能力清单（P4）：编辑器要按这张表渲染多选，硬编码在前端会两处漂移
        "capabilities": [
            {"name": k, "label": v["label"], "builtin_only": v["builtin_only"]}
            for k, v in skills_svc.CAPABILITIES.items()
        ],
    }


class MatchReq(BaseModel):
    question: str
    scope: str = "all"


class EnabledReq(BaseModel):
    """布尔开关。语义预判与技能启停共用（两个端点形状完全一样，没必要各写一份）。

    ⚠️ 必须定义在**任何引用它的端点之前**：这个文件没有 `from __future__ import
    annotations`，注解在函数定义时立即求值 → 放后面会 NameError（而且是在 import 期
    就炸，整个 /api/skills 全 404）。
    """
    enabled: bool


@router.post("/match")
def match_skill(req: MatchReq):
    """给出建议技能。规则命中 <5ms；规则未命中且语义开关开时，多调一次 LLM 分类。

    ⚠️ 语义是**可选增强**：模型没配置/超时/答非所问时一律返回「无建议」，
       绝不从候选里兜底挑一个（方案 §十一 P3 判据 ①）。
    """
    return skills_svc.match(req.question, req.scope)


@router.get("/semantic")
def get_semantic():
    """语义预判开关状态（含说明文案要用的默认值）。"""
    return {"enabled": skills_svc.semantic_enabled(),
            "default": skills_svc.DEFAULT_SEMANTIC}


@router.put("/semantic")
def set_semantic(req: EnabledReq):
    """开/关语义预判。

    ⚠️ 本条必须声明在 `/{skill_id}` 之前（见文件头注释）：否则 `/skills/semantic`
       会被当成「找一个叫 semantic 的技能」。`RESERVED_IDS` 是第二道防线。
    """
    return {"enabled": skills_svc.set_semantic(req.enabled)}


# ---------------------------------------------------------------- 写操作（P2）

class SkillFields(BaseModel):
    """编辑器字段。校验一律交给 service（唯一真相），这里只做搬运。

    ⚠️ 字段全部给默认值：PUT 允许只传改动项，缺的从旧文件继承。
    """
    id: str | None = None
    name: str = ""
    icon: str = "skill"
    kind: str = "chat"
    description: str = ""
    triggers: list[str] = Field(default_factory=list)
    priority: int = 10
    citation: str = "optional"
    instructions: str = ""
    # 工具能力（P4）。白名单与 builtin_only 校验都在 service：路由只搬运。
    # 编辑器里勾了未知能力或超额权限时，service 会剔除并回 warning（不拒保存）。
    capabilities: list[str] = Field(default_factory=list)
    # 首轮必调工具（P3-①）。同 capabilities：校验在 service，路由只搬运。
    tool_first: bool = False


class ImportReq(BaseModel):
    text: str
    overwrite: bool = False


class ValidateReq(BaseModel):
    text: str


def _warnings(skill) -> list[str]:
    """非阻塞提示：不拦保存，但提前告诉用户「这个技能很可能匹配不上」。"""
    out = list(getattr(skill, "warnings", []) or [])
    if len(skill.description) < 10:
        out.append("描述过短：它是判断「要不要推荐这个技能」的唯一依据，建议写清适用场景")
    if len(skill.instructions) < 80:
        out.append("方法说明偏薄（不足 80 字）：模型很难照着一句话改变回答方式")
    if not skill.triggers:
        out.append("没有触发词：它只能手动挂载，永远不会被自动建议")
    return out


@router.post("/validate")
def validate_skill_text(req: ValidateReq):
    """dry-run 校验（不落盘）。编辑器「校验」按钮用。

    ⚠️ 校验失败也返回 200 + `ok=false`：这是**表单校验**，不是服务器错误；
       用 4xx 会让前端只能拿到一句 detail，没法逐字段标红。
    """
    try:
        skill = skills_svc.validate_text(req.text)
    except skills_svc.SkillInvalid as e:
        return {"ok": False, "error": e.reason, "warnings": []}
    return {"ok": True, "error": "", "warnings": _warnings(skill),
            "skill": skill.to_public(with_instructions=True)}


@router.post("/import")
def import_skill(req: ImportReq):
    """导入一份 `.skill.md` 文本。字段级校验失败 → 400，冲突 → 409，都不落盘。"""
    s = _guard(skills_svc.import_skill, req.text, req.overwrite)
    return {"ok": True, "skill": s.to_public(), "id": s.id}


@router.post("")
def create_skill(fields: SkillFields):
    """新建用户技能。id 冲突 → 409（内置同 id 也 409，且提示改名）。"""
    s = _guard(skills_svc.create_skill, fields.model_dump())
    return s.to_public()


@router.get("/{skill_id}")
def get_skill_detail(skill_id: str):
    s = skills_svc.get_skill(skill_id)
    if not s:
        raise HTTPException(404, f"技能不存在：{skill_id}")
    return s.to_public(with_instructions=True)


@router.get("/{skill_id}/export")
def export_skill(skill_id: str):
    """导出 `.skill.md` 原文（附件下载）。

    ⚠️ 读磁盘原文而不是重建：重建会悄悄改掉用户的缩进与未知键顺序。
    ⚠️ 文件名只用 ASCII 的 id：Content-Disposition 里放中文会抛 latin-1 编码错误。
    """
    filename, text = _guard(skills_svc.export_text, skill_id)
    quoted = urllib.parse.quote(filename)
    return PlainTextResponse(
        text, media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition":
                 f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quoted}"},
    )


@router.put("/{skill_id}")
def update_skill(skill_id: str, fields: SkillFields):
    """编辑用户技能（内置 → 403；id 不可改）。

    ⚠️ 必须 `exclude_unset=True`：只传一个字段时，Pydantic 的默认值**也在 model_dump 里**，
       用 `model_dump()` 会把没传的 name/description 一起清成空串 —— 一个「改触发词」
       的请求会顺手抹掉技能名。
    """
    s = _guard(skills_svc.update_skill, skill_id,
               fields.model_dump(exclude_none=True, exclude_unset=True))
    return s.to_public()


@router.delete("/{skill_id}")
def delete_skill(skill_id: str):
    """删除用户技能（内置 → 403）。直接删文件，不烧删除配额。"""
    return _guard(skills_svc.delete_skill, skill_id)


@router.put("/{skill_id}/enabled")
def set_skill_enabled(skill_id: str, req: EnabledReq):
    """启停开关。内置技能也能停用（状态存 `<data_dir>/skills/_skills_state.json`，
    不动随包发布的文件）。"""
    if skills_svc.get_skill(skill_id) is None:
        raise HTTPException(404, f"技能不存在：{skill_id}")
    skills_svc.set_enabled(skill_id, req.enabled)
    return {"id": skill_id, "enabled": bool(req.enabled)}
