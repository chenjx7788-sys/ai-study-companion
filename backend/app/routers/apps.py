"""炼丹炉路由（2026-10-27）：应用 CRUD / 绑定增删 / AI 辅助（草稿+优化）/ 试聊预览。

硬规则：
- 创建与解绑都必须保住「至少 1 份资料」（空范围 = 拆掉知识边界，R3）
- preview/stream 为纯 LLM 代理 → SSE 生成器**刻意同步**（改 async = 卡死全服）；
  且不吃请求级 Depends(get_db)：检索与装配做完即关临时会话，流式期间不持有 DB
"""
import json
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db, SessionLocal
from ..models import App, AppMaterial, ChatSession
from ..services import apps as apps_svc
from ..services import llm

router = APIRouter(prefix="/apps", tags=["apps"])


# ---------- CRUD ----------

class AppUpsertReq(BaseModel):
    name: str = ""
    spirit: str = "book"
    persona: str = ""
    boundaries: str = ""
    miss_text: str = ""
    welcome: str = ""
    questions: list[str] = []
    model_id: str = ""
    suggest_on: bool = True
    material_ids: list[int] = []


@router.get("")
def list_apps(db: Session = Depends(get_db)):
    # O1（2026-10-30）：列表走瘦身投影（一次 group by，人格截断），详情仍走 app_to_dict。
    return apps_svc.list_apps_slim(db)


@router.post("")
def create_app(req: AppUpsertReq, db: Session = Depends(get_db)):
    ids = sorted(set(req.material_ids))
    if not ids:
        raise HTTPException(400, "至少选择 1 份资料才能开炉")          # R3
    apps_svc.validate_fields(req.name, req.persona, req.spirit, req.questions,
                             boundaries=req.boundaries.strip(),
                             miss_text=req.miss_text.strip(),
                             welcome=req.welcome.strip())
    apps_svc.check_materials_ready(db, ids)
    app = App(name=req.name.strip(), spirit=apps_svc.normalize_spirit(req.spirit, req.name.strip()),
              persona=req.persona.strip(), boundaries=req.boundaries.strip(),
              miss_text=req.miss_text.strip(), welcome=req.welcome.strip(),
              questions=[str(q).strip() for q in req.questions if str(q).strip()][:3],
              model_id=(req.model_id or "").strip(), suggest_on=bool(req.suggest_on),
              status="active")
    db.add(app)
    db.flush()                                   # 先拿 id 再写绑定
    db.add_all([AppMaterial(app_id=app.id, material_id=mid) for mid in ids])
    db.commit()
    db.refresh(app)
    return apps_svc.app_to_dict(app, db)


@router.get("/{app_id}")
def get_app(app_id: int, db: Session = Depends(get_db)):
    return apps_svc.app_to_dict(apps_svc.get_app_or_404(db, app_id), db)


@router.patch("/{app_id}")
def patch_app(app_id: int, req: AppUpsertReq, db: Session = Depends(get_db)):
    """部分更新：用 model_fields_set 区分「未传」与「显式空值」（同 folders 语义）。
    ⚠️ 人格修改不回溯历史回答（R6）：只改 apps 行，不动任何消息。
    P2：追踪字段有实际变化时，应用新值之前自动快照旧值（人格版本化）。"""
    app = apps_svc.get_app_or_404(db, app_id)
    fields = req.model_fields_set
    new_name = req.name.strip() if "name" in fields else app.name
    new_persona = req.persona.strip() if "persona" in fields else app.persona
    # ⚠️ 此处**不切片**（2026-10-29 修）：先切片会让第 4 条被静默丢弃（PATCH 200 但少一条），
    #    与创建路径（>3 直接 400）口径不一致。校验通过后必然 ≤3。
    new_questions = ([str(q).strip() for q in req.questions if str(q).strip()]
                     if "questions" in fields else (app.questions or []))
    # spirit 先归一化（裸装饰名→含名字哈希的令牌），再参与版本比对
    new_spirit = apps_svc.normalize_spirit(req.spirit, new_name) if "spirit" in fields else app.spirit
    apps_svc.validate_fields(
        new_name, new_persona, new_spirit, new_questions,
        boundaries=(req.boundaries.strip() if "boundaries" in fields else None),
        miss_text=(req.miss_text.strip() if "miss_text" in fields else None),
        welcome=(req.welcome.strip() if "welcome" in fields else None))
    # ---- 版本快照（P2）：只在追踪字段有实际变化时留档，同义改写不产生垃圾版本 ----
    incoming = {}
    if "name" in fields: incoming["name"] = new_name
    if "persona" in fields: incoming["persona"] = new_persona
    if "spirit" in fields: incoming["spirit"] = new_spirit
    if "questions" in fields: incoming["questions"] = new_questions
    for k in ("boundaries", "miss_text", "welcome", "model_id"):
        if k in fields: incoming[k] = getattr(req, k).strip()
    if "suggest_on" in fields: incoming["suggest_on"] = bool(req.suggest_on)
    apps_svc.maybe_snapshot(db, app, incoming)
    for k in ("boundaries", "miss_text", "welcome", "model_id"):
        if k in fields:
            setattr(app, k, getattr(req, k).strip())
    if "name" in fields:
        app.name = new_name
    if "persona" in fields:
        app.persona = new_persona
    if "spirit" in fields:
        app.spirit = new_spirit           # 上面已归一化，不再二次处理
    if "questions" in fields:
        app.questions = new_questions
    if "suggest_on" in fields:
        app.suggest_on = bool(req.suggest_on)
    if "material_ids" in fields:                 # 整体替换绑定（编辑页保存语义）
        ids = sorted(set(req.material_ids))
        if not ids:
            raise HTTPException(400, "应用至少绑定 1 份资料")      # R3
        apps_svc.check_materials_ready(db, ids)
        db.query(AppMaterial).filter(AppMaterial.app_id == app.id).delete(synchronize_session=False)
        db.add_all([AppMaterial(app_id=app.id, material_id=mid) for mid in ids])
        if app.status == "draft":                # 重新绑定 = 复活（资料被删会降级草稿态）
            app.status = "active"
    db.commit()
    db.refresh(app)
    return apps_svc.app_to_dict(app, db)


class AppMaterialsReq(BaseModel):
    add: list[int] = []
    remove: list[int] = []


@router.patch("/{app_id}/materials")
def patch_app_materials(app_id: int, req: AppMaterialsReq, db: Session = Depends(get_db)):
    """增量增删绑定。删到 0 → 400 回滚（最后一份不可移除，R3）。"""
    app = apps_svc.get_app_or_404(db, app_id)
    cur = set(apps_svc.material_ids(db, app_id))
    apps_svc.check_materials_ready(db, sorted(set(req.add)))
    new = (cur | set(req.add)) - set(req.remove)
    if not new:
        raise HTTPException(400, "应用至少绑定 1 份资料，最后一份不可移除")
    db.query(AppMaterial).filter(AppMaterial.app_id == app_id).delete(synchronize_session=False)
    db.add_all([AppMaterial(app_id=app_id, material_id=mid) for mid in sorted(new)])
    if app.status == "draft":                    # 同上：增量重绑也复活
        app.status = "active"
    db.commit()
    db.refresh(app)
    return apps_svc.app_to_dict(app, db)


@router.delete("/{app_id}")
def delete_app(app_id: int, db: Session = Depends(get_db)):
    """删除应用：级联清会话与消息（前端二级确认），资料本体不动。"""
    app = apps_svc.get_app_or_404(db, app_id)
    return apps_svc.delete_app_cascade(db, app)


# ---------- 形象（P1 程序化独版丹灵） ----------

@router.post("/{app_id}/spirit/reroll")
def spirit_reroll(app_id: int, db: Session = Depends(get_db)):
    """换一身：保留装饰、随机换配色（前后端调色板等长，令牌 g:<deco>:<palette>）。"""
    app = apps_svc.get_app_or_404(db, app_id)
    app.spirit = apps_svc.reroll_spirit(app)
    db.commit()
    return {"spirit": app.spirit}


# ---------- 人格版本（P2） ----------

@router.get("/{app_id}/versions")
def list_versions(app_id: int, db: Session = Depends(get_db)):
    apps_svc.get_app_or_404(db, app_id)
    return apps_svc.list_versions(db, app_id)


@router.post("/{app_id}/versions/{version_id}/restore")
def restore_version(app_id: int, version_id: int, db: Session = Depends(get_db)):
    """回滚到指定版本（服务端先自动快照当前态，回滚可撤销）。"""
    app = apps_svc.get_app_or_404(db, app_id)
    app = apps_svc.restore_version(db, app, version_id)
    return apps_svc.app_to_dict(app, db)


# ---------- 应用空间·用量（P2） ----------

@router.get("/{app_id}/stats")
def app_stats(app_id: int, db: Session = Depends(get_db)):
    apps_svc.get_app_or_404(db, app_id)
    return apps_svc.app_stats(db, app_id)


# ---------- 闲置主动搭话（P1，不落库） ----------

class NudgeReq(BaseModel):
    session_id: int | None = None


# 搭话节流（O5，2026-10-30）：接口原来没有任何冷却，可被刷 token（每次一次 LLM）。
# 进程内字典够用（单实例 dev / 桌面端）；重启清零不影响正确性。
_NUDGE_LAST: dict[tuple[int, int | None], float] = {}
_NUDGE_COOLDOWN = 60.0


@router.post("/{app_id}/nudge")
def nudge(app_id: int, req: NudgeReq, db: Session = Depends(get_db)):
    """生成一句人格化搭话。失败向上抛 502，前端静默降级为不显示。"""
    app = apps_svc.get_app_or_404(db, app_id)
    # 2026-10-29 修：草稿态（0 绑定）应用不得主动搭话 —— 与对话入口 400 同口径，
    # 否则「待补料」的应用仍会白烧一次 LLM，且搭话内容没有资料可依。
    if not apps_svc.material_ids(db, app.id) or app.status == "draft":
        raise HTTPException(400, "应用的资料已失效，补料前不能主动搭话")
    if req.session_id:
        s = db.get(ChatSession, req.session_id)
        if not s or s.app_id != app.id:
            raise HTTPException(400, "会话不属于此应用")
    # O5：60s 内同一 (应用, 会话) 只生成一次。超频返回空文案（200），
    # 前端原本就按「没有 text 就不显示」处理 → 静默降级，不弹错误气泡。
    key = (app.id, req.session_id)
    now = time.monotonic()
    if now - _NUDGE_LAST.get(key, 0.0) < _NUDGE_COOLDOWN:
        return {"text": "", "questions": [], "throttled": True}
    _NUDGE_LAST[key] = now
    return apps_svc.gen_nudge(db, app, req.session_id)


# ---------- AI 辅助（草稿 / 优化）：产出建议，绝不入库（R7） ----------

class AssistReq(BaseModel):
    kind: str                      # draft / polish
    material_ids: list[int] = []   # draft 用
    field: str = ""                # polish 用：persona / bounds
    text: str = ""                 # polish 用：当前文本
    name: str = ""                 # polish 用：应用名


@router.post("/assist")
def assist(req: AssistReq, db: Session = Depends(get_db)):
    if req.kind == "draft":
        return apps_svc.assist_draft(db, sorted(set(req.material_ids)))
    if req.kind == "polish":
        return apps_svc.assist_polish(req.name, req.field, req.text)
    raise HTTPException(400, "kind 只支持 draft / polish")


# ---------- 试聊预览（不落库、不建会话） ----------

class PreviewReq(BaseModel):
    name: str = ""
    persona: str = ""
    boundaries: str = ""
    miss_text: str = ""
    material_ids: list[int] = []
    question: str
    model_id: str = ""             # ''=跟随全局问答模型
    # 多轮上下文（2026-10-29）：[{role:'user'|'assistant', content:str}]，只带最近 3 轮。
    # ⚠️ 类型故意用 Any：脏载荷交给 normalize_preview_history 丢弃。
    #    写成 list 会让「传了字符串」在入口就 422 —— 对用户是「请求失败」四个字，
    #    而试聊是调试面板，宁可忽略脏字段也要让它能继续跑。
    history: Any = None


@router.post("/preview/stream")
def preview_stream(req: PreviewReq):
    q = (req.question or "").strip()
    if not q:
        raise HTTPException(400, "问题不能为空")
    # 前置校验与装配在临时会话里做完即关：流式期间不持有 DB（SSE 纪律）
    db = SessionLocal()
    try:
        messages, kb_hit = apps_svc.build_preview_messages(
            db, req.model_dump(), q, history=req.history)
    finally:
        db.close()

    def gen():                     # 纯 LLM 代理 → 刻意同步生成器
        # ⚠️ 必须 event+data 双行：前端 streamSSE 缺 event 行会静默丢弃（先有契约后实现）
        yield f"event: meta\ndata: {json.dumps({'kb_hit': kb_hit}, ensure_ascii=False)}\n\n"
        # ⚠️⚠️ 异常必须自己收敛（2026-10-29 修）：chat_stream 对「未配置/模型不可用」抛的是
        #    HTTPException([NEED_SETUP])，而它在**响应头已经发出（200）之后**才被调用 ——
        #    不接住就是「只发 meta 就断流」：前端 pvBusy 永不复位（试聊按钮永久转圈），
        #    sse.js 专门做的「前往配置」引导也永远弹不出来。GeneratorExit 属 BaseException，
        #    这里只接 Exception，故意不吞客户端断开。
        try:
            for kind, text in llm.chat_stream(messages, model_id=req.model_id or None, kind="app_preview"):
                if kind == "content" and text:
                    yield f"event: token\ndata: {json.dumps({'t': text}, ensure_ascii=False)}\n\n"
        except Exception as e:
            msg = getattr(e, "detail", None) or str(e) or "生成失败"
            yield f"event: error\ndata: {json.dumps({'message': str(msg)}, ensure_ascii=False)}\n\n"
            return
        yield f"event: done\ndata: {json.dumps({'ok': True})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
