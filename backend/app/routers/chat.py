"""全局问答路由（PRD 模块 E）：知识库优先 + 通用补充，SSE 流式输出

问答流（PRD 4.4）：
提问 → 向量检索 Top-K → 阈值过滤
  ├→ 命中：检索内容注入上下文，流式回答 + 来源标注（材料/页码/笔记）
  └→ 未命中：走通用回答，kb_hit=false，前端展示灰色提示条
"""
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..core.config import settings
from ..database import get_db, SessionLocal
from ..models import ChatSession, ChatMessage
from ..services import llm, search as search_svc, settings_store

router = APIRouter(prefix="/chat", tags=["chat"])

HISTORY_LIMIT = 10   # 注入上下文的最近消息数（PRD：单会话上限 20 轮）


class AskReq(BaseModel):
    question: str
    session_id: int | None = None
    scope: str = "all"                       # all 整个知识库 / material 资料 / note 笔记 / folder 文件夹
    material_ids: list[int] | None = None    # scope=material 时指定材料（空=全部）
    note_ids: list[int] | None = None        # scope=note 时指定笔记（空=全部）
    folder_ids: list[int] | None = None      # scope=folder 时指定文件夹（含子文件夹）
    model_id: str | None = None              # 指定模型（问答页切换），空=默认问答模型
    reasoning_effort: str | None = None      # 推理强度 low/high/max，空=服务商默认

SCOPE_KINDS = {"all": ("chunk", "note"), "material": ("chunk",), "note": ("note",), "folder": ("chunk",)}


def _folder_to_material_ids(db: Session, folder_ids: list[int]) -> list[int]:
    """把文件夹（含子文件夹）展开为其下所有材料 id，用于 folder 范围检索。

    动态实时查询（不存快照），文件夹内文件增删移后检索范围自然更新。
    """
    from ..models import Folder, Material
    all_fids: set[int] = set()
    queue = [f for f in folder_ids if f is not None]
    while queue:
        cur = queue.pop(0)
        if cur in all_fids:
            continue
        all_fids.add(cur)
        for child in db.query(Folder).filter(Folder.parent_id == cur).all():
            if child.id not in all_fids:
                queue.append(child.id)
    if not all_fids:
        return []
    return [m.id for m in db.query(Material).filter(Material.folder_id.in_(all_fids)).all()]


def session_to_dict(s: ChatSession, db: Session) -> dict:
    # 只统计问题数（role=user），不含回答数
    count = (db.query(ChatMessage)
             .filter(ChatMessage.session_id == s.id, ChatMessage.role == "user")
             .count())
    last = (db.query(ChatMessage).filter(ChatMessage.session_id == s.id)
            .order_by(ChatMessage.id.desc()).first())
    return {"id": s.id, "title": s.title, "message_count": count,
            "pinned": bool(s.pinned),
            "last_message": (last.content[:60] if last else None),
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "updated_at": s.updated_at.isoformat() if s.updated_at else None}


def message_to_dict(m: ChatMessage) -> dict:
    return {"id": m.id, "role": m.role, "content": m.content,
            "reasoning": m.reasoning or "",
            "kb_hit": m.kb_hit,
            "sources": m.sources or [], "suggestions": m.suggestions or [],
            "created_at": m.created_at.isoformat() if m.created_at else None}


@router.get("/sessions")
def list_sessions(db: Session = Depends(get_db)):
    rows = (db.query(ChatSession)
            .order_by(ChatSession.pinned.desc(), ChatSession.updated_at.desc()).all())
    return [session_to_dict(s, db) for s in rows]


@router.post("/sessions")
def create_session(db: Session = Depends(get_db)):
    s = ChatSession(title="新会话")
    db.add(s)
    db.commit()
    db.refresh(s)
    return session_to_dict(s, db)


class RenameReq(BaseModel):
    title: str


@router.patch("/sessions/{session_id}")
def rename_session(session_id: int, req: RenameReq, db: Session = Depends(get_db)):
    s = db.get(ChatSession, session_id)
    if not s:
        raise HTTPException(404, "会话不存在")
    s.title = req.title.strip()[:50] or s.title
    db.commit()
    return session_to_dict(s, db)


class PinReq(BaseModel):
    pinned: bool


@router.patch("/sessions/{session_id}/pin")
def pin_session(session_id: int, req: PinReq, db: Session = Depends(get_db)):
    s = db.get(ChatSession, session_id)
    if not s:
        raise HTTPException(404, "会话不存在")
    s.pinned = req.pinned
    db.commit()
    return session_to_dict(s, db)


@router.delete("/sessions/{session_id}")
def delete_session(session_id: int, db: Session = Depends(get_db)):
    s = db.get(ChatSession, session_id)
    if not s:
        raise HTTPException(404, "会话不存在")
    db.delete(s)
    db.commit()
    return {"ok": True}


@router.get("/sessions/{session_id}/messages")
def list_messages(session_id: int, db: Session = Depends(get_db)):
    rows = (db.query(ChatMessage).filter(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.id).all())
    return [message_to_dict(m) for m in rows]


@router.delete("/sessions/{session_id}/messages/from/{message_id}")
def truncate_messages(session_id: int, message_id: int, db: Session = Depends(get_db)):
    """截断会话：删除该消息及其之后的所有消息（用于「编辑问题重新生成」）"""
    s = db.get(ChatSession, session_id)
    if not s:
        raise HTTPException(404, "会话不存在")
    db.query(ChatMessage).filter(
        ChatMessage.session_id == session_id,
        ChatMessage.id >= message_id,
    ).delete(synchronize_session=False)
    db.commit()
    return {"ok": True}


def _hits_to_sources(hits: list[dict]) -> list[dict]:
    """检索结果 → 来源卡片（最多 3 条）"""
    sources = []
    for h in hits[:3]:
        meta = h["metadata"]
        sources.append({
            "ref_type": h["ref_type"],
            "material_id": h["material_id"],
            "title": meta.get("material_title") or meta.get("note_title") or "未知来源",
            "page_no": meta.get("page_no"),
            "snippet": h["text"][:80],
            "score": h["score"],
        })
    return sources


_OVERVIEW_HINTS = ("有什么", "有哪些", "包含", "目录", "内容", "学了什么", "都有什么", "收录", "讲了什么")


def _is_overview_question(q: str) -> bool:
    """是否为「知识库整体内容」类元问题：问题短 + 含概览词（保守，避免误伤具体检索）"""
    q = q.strip()
    if len(q) > 20:
        return False
    if not any(k in q for k in ("知识库", "资料", "笔记", "材料")):
        return False
    return any(k in q for k in _OVERVIEW_HINTS)


def _build_overview_text(db: Session) -> str:
    """构造知识库目录文本（资料标题 + 笔记标题 + 统计），用于回答概览类元问题"""
    from ..models import Material, Note
    materials = (db.query(Material).filter(Material.parsed_status == "success")
                 .order_by(Material.id).all())
    notes = db.query(Note).order_by(Note.id).all()
    if not materials and not notes:
        return ""
    lines = [f"知识库共收录 {len(materials)} 份资料、{len(notes)} 条笔记。"]
    if materials:
        lines.append("\n【资料】")
        for m in materials:
            lines.append(f"- 《{m.title}》")
    if notes:
        lines.append("\n【笔记】")
        for n in notes:
            mt = db.get(Material, n.material_id)
            src = f"（出自《{mt.title}》）" if mt else ""
            lines.append(f"- {n.title}{src}")
    return "\n".join(lines)


@router.post("/ask/stream")
def ask_stream(req: AskReq, db: Session = Depends(get_db)):
    """SSE 流式问答：先发 sources 事件，再逐 token，最后 done 事件"""
    question = req.question.strip()
    if not question:
        raise HTTPException(400, "问题不能为空")

    # 会话：无则新建，首问作为标题
    if req.session_id:
        session = db.get(ChatSession, req.session_id)
        if not session:
            raise HTTPException(404, "会话不存在")
    else:
        session = ChatSession(title=question[:20])
        db.add(session)
        db.commit()
        db.refresh(session)
    if session.title == "新会话":
        session.title = question[:20]
    session.updated_at = datetime.utcnow()   # 最近活动时间（侧边栏排序/相对时间）

    user_msg = ChatMessage(session_id=session.id, role="user", content=question)
    db.add(user_msg)
    db.commit()
    db.refresh(user_msg)

    # 检索 + 阈值过滤（scope 限定集合；指定条目时加 metadata 过滤）
    kinds = SCOPE_KINDS.get(req.scope, ("chunk", "note"))
    specified = bool(req.material_ids or req.note_ids or req.folder_ids)
    where = None
    if req.scope == "material" and req.material_ids:
        where = {"material_id": {"$in": req.material_ids}}
    elif req.scope == "note" and req.note_ids:
        where = {"ref_id": {"$in": req.note_ids}}

    if req.scope == "folder":
        # folder 范围：实时展开所选文件夹（含子文件夹）→ 动态反映内容变化。
        # 未指定文件夹 或 文件夹下无材料 → 不检索（避免 where=None 误检索整个知识库）
        folder_mids = _folder_to_material_ids(db, req.folder_ids) if req.folder_ids else []
        hits = [] if not folder_mids else search_svc.search(
            question, db, kinds=kinds, where={"material_id": {"$in": folder_mids}})
    else:
        hits = search_svc.search(question, db, kinds=kinds, where=where)

    # 指定单份资料时：若该资料已有脉络摘要产物，作为最高优先级上下文注入——
    # 「这篇讲了什么」类泛化提问用摘要比向量碎片更准
    summary_hit = None
    if req.scope == "material" and req.material_ids and len(req.material_ids) == 1:
        from ..models import AIAsset, Material
        mid = req.material_ids[0]
        summ = (db.query(AIAsset)
                .filter(AIAsset.material_id == mid, AIAsset.type == "summary")
                .order_by(AIAsset.version.desc()).first())
        m = db.get(Material, mid)
        if summ and m:
            summary_hit = {
                "ref_type": "summary", "ref_id": summ.id, "material_id": mid,
                "text": f"【该资料的脉络摘要】\n{summ.content}",
                "metadata": {"material_title": m.title, "page_no": None},
                "score": 9.9,
            }
    # 用户显式指定了资料/笔记 = 强意图：不做任何分数过滤（向量分可为负，阈值滤会误杀），
    # 指定内容必被引用；未指定时才用阈值过滤，避免无关内容注入
    _threshold = float(settings_store.load().get("kb_hit_threshold", settings.kb_hit_threshold))
    # 阈值过滤：全文精确命中（关键词命中即相关）或向量分达标者保留；
    # 指定条目仍不过滤（强意图必引用）
    if specified:
        filtered = hits
    else:
        filtered = [h for h in hits if h.get("from_fts") or (h.get("vec_score") or 0.0) >= _threshold]
    if summary_hit:
        filtered = [summary_hit] + filtered
    kb_hit = bool(filtered)
    sources = _hits_to_sources(filtered)

    # 历史上下文（最近 HISTORY_LIMIT 条，不含刚存入的当前问题）
    history_rows = (db.query(ChatMessage)
                    .filter(ChatMessage.session_id == session.id)
                    .order_by(ChatMessage.id.desc()).limit(HISTORY_LIMIT + 1).all())
    # assistant 消息需回传 reasoning_content（DeepSeek/Kimi 思考模式要求原样保留，否则 400）
    history = []
    for m in reversed(history_rows):
        if m.role == "assistant" and m.reasoning:
            history.append({"role": "assistant", "content": m.content,
                            "reasoning_content": m.reasoning})
        else:
            history.append({"role": m.role, "content": m.content})
    history = history[:-1]  # 去掉当前问题，后面统一拼

    if kb_hit:
        mapped = [{"title": h["metadata"].get("material_title") or h["metadata"].get("note_title"),
                   "page_no": h["metadata"].get("page_no"), "text": h["text"]} for h in filtered[:3]]
        messages = llm.build_kb_qa_prompt(question, mapped)
        # 历史插在 system 之后
        messages = messages[:1] + history + messages[1:]
    else:
        # 元问题兜底：向量未命中 + 问「知识库有什么内容」→ 注入目录，避免误答「没找到」
        overview_text = _build_overview_text(db) if _is_overview_question(question) else ""
        if overview_text:
            kb_hit = True   # 视为命中，前端不显示「未找到」灰条；目录非具体引文故不带 sources
            sources = []
            messages = [
                {"role": "system", "content": llm.get_prompt("prompt_kb_overview")},
                *history,
                {"role": "user", "content": f"【知识库目录】\n{overview_text}\n\n【用户问题】{question}"},
            ]
        else:
            messages = [
                {"role": "system", "content": llm.get_prompt("prompt_general")},
                *history,
                {"role": "user", "content": question},
            ]

    session_id = session.id

    def gen():
        full = ""
        reasoning = ""
        yield f"event: sources\ndata: {json.dumps({'session_id': session_id, 'user_message_id': user_msg.id, 'kb_hit': kb_hit, 'sources': sources}, ensure_ascii=False)}\n\n"
        try:
            for kind, text in llm.chat_stream(messages, model_id=req.model_id,
                                              reasoning_effort=req.reasoning_effort):
                if kind == "reasoning":
                    reasoning += text
                    yield f"event: reasoning\ndata: {json.dumps({'t': text}, ensure_ascii=False)}\n\n"
                else:
                    full += text
                    yield f"event: token\ndata: {json.dumps({'t': text}, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'message': str(e)[:200]}, ensure_ascii=False)}\n\n"
            return
        # 回答完成 → 生成 3 个相关问题（问答内容驱动，失败不阻塞）
        suggestions = llm.generate_suggestions(question, full)
        if suggestions:
            yield f"event: suggestions\ndata: {json.dumps({'questions': suggestions}, ensure_ascii=False)}\n\n"
        # 落库 assistant 消息（含思维链）
        db2 = SessionLocal()
        try:
            m = ChatMessage(session_id=session_id, role="assistant", content=full,
                            reasoning=reasoning,
                            kb_hit=kb_hit, sources=sources, suggestions=suggestions)
            db2.add(m)
            db2.commit()
            db2.refresh(m)
            yield f"event: done\ndata: {json.dumps({'message_id': m.id}, ensure_ascii=False)}\n\n"
        finally:
            db2.close()

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
