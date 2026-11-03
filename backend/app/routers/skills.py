"""技能路由：列表 / 详情 / 建议匹配 / 增删改 / 导入导出 / 启停。

⚠️ 列表**不返回 instructions**：列表只吃 name+description；`MAX_RESIDENT=13`
实际是单次语义路由的候选上限。instructions 只在详情与注入时取。

⚠️ 路由顺序：所有具体路径（`/match` `/import` `/validate`）必须写在 `/{skill_id}`
之前。FastAPI 按声明顺序匹配，`GET /{skill_id}` 一旦先声明，`GET /skills/import`
就会变成「找一个叫 import 的技能」（404 而不是 405，很难查）。
"""
import os
import urllib.parse

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from ..services import skills as skills_svc

router = APIRouter(prefix="/skills", tags=["skills"])

# 批量导入是本地目录递归扫描：没有规模护栏时，误选盘符/仓库根会长时间占住请求线程。
IMPORT_FOLDER_MAX_FILES = 500
IMPORT_FOLDER_MAX_BYTES = 512 * 1024
IMPORT_FOLDER_SCAN_LIMIT = 20000
IMPORT_FOLDER_MAX_DEPTH = 8
IMPORT_FILES_MAX_TOTAL_BYTES = 16 * 1024 * 1024  # 浏览器选择模式的 JSON 总上限


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
    # 使用痕迹（P0-1 资产掌控）：一次查库合并进列表，前端不必为每张卡再发一次请求。
    # ⚠️ 从没用过的技能也必须带 `use_count=0` 出现在 items 里 ——「从没用过」
    #    本身是最有价值的信息，藏起来等于让用户永远发现不了自己的技能没生效过。
    usage, usage_err = skills_svc.usage_map()
    for it in items:
        u = usage.get(it["id"]) or {}
        it["use_count"] = int(u.get("count") or 0)
        it["last_used_at"] = u.get("last_at") or ""
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
        # 非空 = 使用痕迹查不出来（典型：改了代码但没重启，表还没建）。
        # 前端据此提示，避免把「读不到」静默显示成「全部还没用过」。
        "usage_error": usage_err,
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


@router.get("/usage")
def list_usage():
    """技能使用痕迹汇总（P0-1 资产掌控）。

    ⚠️ 必须声明在 `/{skill_id}` **之前**，并且 `usage` 已进 `RESERVED_IDS` —— 两道防线。
       只靠其中一道，哪天有人调整了声明顺序就会变成「找一个叫 usage 的技能」，
       症状是 404 而不是报错，很难查（见文件头注释里的同类事故）。
    """
    m, err = skills_svc.usage_map()
    items = [{"skill_id": k, "count": v["count"], "last_at": v["last_at"]}
             for k, v in sorted(m.items(), key=lambda kv: (-kv[1]["count"], kv[0]))]
    return {"items": items, "total": sum(i["count"] for i in items), "error": err}


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


class ImportFolderReq(BaseModel):
    path: str
    dry_run: bool = True        # True=只扫描预览不写盘；False=逐份落盘


class ImportFileItem(BaseModel):
    file: str
    text: str


class ImportFilesReq(BaseModel):
    files: list[ImportFileItem]
    dry_run: bool = True


def _batch_payload(results: list[dict]) -> dict:
    return {"total": len(results),
            "ok_count": sum(1 for r in results if r["status"] == "ok"),
            "ready_count": sum(1 for r in results if r["status"] == "ready"),
            "dup_count": sum(1 for r in results if r["status"] == "dup"),
            "results": results}


def _import_text_candidates(entries: list[dict], dry_run: bool, results: list[dict] | None = None) -> dict:
    """批量导入的唯一处理链：路径扫描与浏览器文件夹选择都必须走这里。"""
    results = [] if results is None else results
    seen: dict[str, str] = {}      # id → 先出现的那份文件（.agents/.claude 双目录同一份技能的排重）
    for entry in entries:
        rel = entry["file"]
        item = {"file": rel, "id": "", "name": "", "status": "", "reason": "", "warnings": []}
        try:
            text = skills_svc.external_to_skill_text(
                entry["text"], fallback_name=entry.get("fallback_name") or "skill")
            s = skills_svc.validate_text(text)      # 不落盘的权威校验
        except Exception as e:
            item["status"] = "failed"
            item["reason"] = str(getattr(e, "detail", None) or e)[:160]
            results.append(item)
            continue
        item.update({"id": s.id, "name": s.name, "warnings": list(s.warnings)})
        if s.id in seen:
            # 同一技能在包里有多份（.agents/.claude 厂商目录约定）→ 只导先出现的那份
            item["status"] = "dup"
            item["reason"] = f"与 {seen[s.id]} 是同一份技能（id 相同），跳过"
            results.append(item)
            continue
        seen[s.id] = rel
        if not s.triggers:
            item["warnings"].append("没有触发词：导入后只能手动挂载（可在编辑器里补）")
        if dry_run:
            item["status"] = "ready"
            results.append(item)
            continue
        try:
            skills_svc.import_skill(text, overwrite=False)
            item["status"] = "ok"
        except skills_svc.SkillConflict:
            item["status"] = "conflict"
            item["reason"] = "同名 id 已存在（先到技能页删掉旧的，或在编辑器里改 id 再导）"
        except Exception as e:
            item["status"] = "failed"
            item["reason"] = str(getattr(e, "detail", None) or e)[:160]
        results.append(item)
    return _batch_payload(results)


@router.post("/import-folder")
def import_folder(req: ImportFolderReq):
    """从本地文件夹批量导入技能包（P6）。路径模式是浏览器选择器之外的兜底。"""
    from pathlib import Path
    root = Path((req.path or "").strip().strip('"').strip("'"))
    if not root.is_dir():
        raise HTTPException(400, f"路径不存在或不是文件夹：{req.path}")

    candidates = []
    oversized = []
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(root):  # followlinks 默认 False，避免目录环
        rel_dir = Path(dirpath).relative_to(root)
        depth = 0 if str(rel_dir) == "." else len(rel_dir.parts)
        if depth >= IMPORT_FOLDER_MAX_DEPTH:
            dirnames[:] = []
        scanned += len(dirnames) + len(filenames)
        if scanned > IMPORT_FOLDER_SCAN_LIMIT:
            raise HTTPException(400,
                                f"文件夹条目过多（扫描上限 {IMPORT_FOLDER_SCAN_LIMIT}），请先收窄到技能包目录")
        for name in filenames:
            if not (name.lower() == "skill.md" or name.lower().endswith(".skill.md")):
                continue
            p = Path(dirpath) / name
            try:
                size = p.stat().st_size
            except OSError:
                candidates.append(p)          # 读取错误交给逐条结果透出，不能静默丢
                continue
            if size > IMPORT_FOLDER_MAX_BYTES:
                oversized.append((p, size))
            else:
                candidates.append(p)
        if len(candidates) > IMPORT_FOLDER_MAX_FILES:
            raise HTTPException(400,
                                f"候选技能文件超过 {IMPORT_FOLDER_MAX_FILES} 份，请先拆小文件夹再导入")

    results = []
    for p, size in oversized:
        rel = str(p.relative_to(root))
        results.append({
            "file": rel, "id": "", "name": "", "status": "failed",
            "reason": f"文件 {size} 字节，超过单文件上限 {IMPORT_FOLDER_MAX_BYTES // 1024}KB，已跳过",
            "warnings": [],
        })

    entries = []
    for p in sorted(candidates):
        rel = str(p.relative_to(root))
        try:
            raw = p.read_text(encoding="utf-8")
        except Exception as e:
            results.append({"file": rel, "id": "", "name": "", "status": "failed",
                            "reason": str(e)[:160], "warnings": []})
            continue
        entries.append({"file": rel, "text": raw, "fallback_name": p.parent.name})
    if not entries and not results:
        raise HTTPException(400, "这个文件夹里没找到技能文件（SKILL.md 或 .skill.md）")

    payload = _import_text_candidates(entries, req.dry_run, results)
    return {"root": str(root), **payload}


@router.post("/import-files")
def import_files(req: ImportFilesReq):
    """浏览器文件夹选择模式：接收相对路径 + 文件内容，不需要也无法拿到本机绝对路径。"""
    if not req.files:
        raise HTTPException(400, "没有收到要导入的技能文件")
    if len(req.files) > IMPORT_FOLDER_MAX_FILES:
        raise HTTPException(400, f"候选技能文件超过 {IMPORT_FOLDER_MAX_FILES} 份，请先拆小文件夹再导入")
    total_bytes = sum(len((f.text or "").encode("utf-8")) for f in req.files)
    if total_bytes > IMPORT_FILES_MAX_TOTAL_BYTES:
        raise HTTPException(400,
                            f"选中文件总大小超过 {IMPORT_FILES_MAX_TOTAL_BYTES // 1024 // 1024}MB，请改用本地路径扫描")

    results = []
    entries = []
    for i, f in enumerate(req.files):
        raw_rel = (f.file or f"skill_{i}.skill.md").replace("\\", "/")
        parts = [p for p in raw_rel.split("/") if p not in ("", ".", "..")]
        rel = "/".join(parts) or f"skill_{i}.skill.md"
        name = rel.rsplit("/", 1)[-1]
        if not (name.lower() == "skill.md" or name.lower().endswith(".skill.md")):
            results.append({"file": rel, "id": "", "name": "", "status": "failed",
                            "reason": "不是技能文件（只认 SKILL.md 或 .skill.md）", "warnings": []})
            continue
        size = len((f.text or "").encode("utf-8"))
        if size > IMPORT_FOLDER_MAX_BYTES:
            results.append({"file": rel, "id": "", "name": "", "status": "failed",
                            "reason": f"文件 {size} 字节，超过单文件上限 {IMPORT_FOLDER_MAX_BYTES // 1024}KB，已跳过",
                            "warnings": []})
            continue
        parent = rel.rsplit("/", 1)[0].rsplit("/", 1)[-1] if "/" in rel else "skill"
        entries.append({"file": rel, "text": f.text or "", "fallback_name": parent})
    if not entries and not results:
        raise HTTPException(400, "这个文件夹里没找到技能文件（SKILL.md 或 .skill.md）")

    payload = _import_text_candidates(entries, req.dry_run, results)
    return {"source": "picker", **payload}


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
