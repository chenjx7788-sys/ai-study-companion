"""AI 理解引擎路由（PRD 模块 B）：摘要 / 知识点 / 划线解读 / 追问链

- 摘要与知识点支持再生成（version 递增，assets 接口只返回最新版）
- 解读/追问以 AIAsset 树存储：explain 为根，qa 为子（parent_id 串联）
"""
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Material, MaterialChunk, AIAsset
from ..services import llm

router = APIRouter(prefix="/ai", tags=["ai"])


# ---------- 请求模型 ----------

class SummaryReq(BaseModel):
    material_id: int
    instruction: str | None = None   # 再生成补充指令，如"更精简"

class KeywordsReq(BaseModel):
    material_id: int
    instruction: str | None = None   # B6 再生成补充指令

class SectionSummaryReq(BaseModel):
    material_id: int
    section_path: str                # B7 总结本章

class ExplainReq(BaseModel):
    material_id: int
    selected_text: str
    anchor: dict = {}                # {page_no, start?, end?}
    question: str | None = None      # None=首次解读；有值=追问
    parent_id: int | None = None     # 追问时带上一条 asset id

class AskReq(BaseModel):
    material_id: int
    question: str

class NoteTransformReq(BaseModel):
    content: str
    mode: str                    # rewrite | expand | summarize | continue
    title: str | None = None


# ---------- 工具 ----------

def _get_material_or_404(db: Session, material_id: int) -> Material:
    m = db.get(Material, material_id)
    if not m:
        raise HTTPException(404, "材料不存在")
    if m.parsed_status != "success":
        raise HTTPException(400, f"材料状态为 {m.parsed_status}，AI 功能不可用")
    return m


def _load_chunks(db: Session, material_id: int) -> list[dict]:
    rows = (db.query(MaterialChunk)
            .filter(MaterialChunk.material_id == material_id)
            .order_by(MaterialChunk.page_no, MaterialChunk.id).all())
    return [{"content": r.content, "page_no": r.page_no, "section_path": r.section_path} for r in rows]


def _latest_asset(db: Session, material_id: int, type_: str) -> AIAsset | None:
    return (db.query(AIAsset)
            .filter(AIAsset.material_id == material_id, AIAsset.type == type_)
            .order_by(AIAsset.version.desc()).first())


def asset_to_dict(a: AIAsset) -> dict:
    return {
        "id": a.id, "material_id": a.material_id, "type": a.type,
        "content": a.content, "anchor": a.anchor, "parent_id": a.parent_id,
        "version": a.version,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


# ---------- 摘要（B2/B6） ----------

@router.post("/summary")
def create_summary(req: SummaryReq, db: Session = Depends(get_db)):
    _get_material_or_404(db, req.material_id)
    chunks = _load_chunks(db, req.material_id)
    content = llm.generate_summary(chunks, req.instruction)

    prev = _latest_asset(db, req.material_id, "summary")
    a = AIAsset(material_id=req.material_id, type="summary", content=content,
                version=(prev.version + 1) if prev else 1)
    db.add(a)
    db.commit()
    db.refresh(a)
    return asset_to_dict(a)


# ---------- 知识点（B3/B6） ----------

@router.post("/keywords")
def create_keywords(req: KeywordsReq, db: Session = Depends(get_db)):
    _get_material_or_404(db, req.material_id)
    chunks = _load_chunks(db, req.material_id)
    import json
    items = llm.generate_keywords(chunks, req.instruction)

    prev = _latest_asset(db, req.material_id, "keywords")
    a = AIAsset(material_id=req.material_id, type="keywords",
                content=json.dumps(items, ensure_ascii=False),
                version=(prev.version + 1) if prev else 1)
    db.add(a)
    db.commit()
    db.refresh(a)
    return {**asset_to_dict(a), "items": items}


# ---------- 分章总结（B7） ----------

@router.post("/section-summary")
def create_section_summary(req: SectionSummaryReq, db: Session = Depends(get_db)):
    """总结本章：按 section_path 精确过滤原文块后生成局部摘要"""
    _get_material_or_404(db, req.material_id)
    rows = (db.query(MaterialChunk)
            .filter(MaterialChunk.material_id == req.material_id,
                    MaterialChunk.section_path == req.section_path)
            .order_by(MaterialChunk.page_no, MaterialChunk.id).all())
    if not rows:
        raise HTTPException(404, "该章节没有可总结的文本内容")
    chunks = [{"content": r.content, "page_no": r.page_no, "section_path": r.section_path} for r in rows]
    content = llm.generate_summary(chunks, f"只总结「{req.section_path}」这一章，不要扩展到其他章节",
                                   kind="section_summary")

    a = AIAsset(material_id=req.material_id, type="section_summary", content=content,
                anchor={"section_path": req.section_path})
    db.add(a)
    db.commit()
    db.refresh(a)
    return asset_to_dict(a)


# ---------- 划线解读 / 持续追问（B4/B5） ----------

@router.post("/explain")
def create_explain(req: ExplainReq, db: Session = Depends(get_db)):
    _get_material_or_404(db, req.material_id)
    if len(req.selected_text) > 2000:
        req.selected_text = req.selected_text[:2000]

    # 上下文：所选页 ±1 页的原文块
    page = (req.anchor or {}).get("page_no", 1)
    ctx_rows = (db.query(MaterialChunk)
                .filter(MaterialChunk.material_id == req.material_id,
                        MaterialChunk.page_no.between(max(1, page - 1), page + 1))
                .order_by(MaterialChunk.page_no).all())
    context = "\n".join(r.content for r in ctx_rows)[:4000]

    # 追问历史：沿 parent_id 链回溯
    history, root_id = [], req.parent_id
    if req.parent_id:
        node = db.get(AIAsset, req.parent_id)
        chain = []
        while node:
            chain.append(node)
            node = db.get(AIAsset, node.parent_id) if node.parent_id else None
        chain.reverse()
        for n in chain:
            if n.type == "qa":
                history.append({"role": "user", "content": (n.anchor or {}).get("question", "")})
                history.append({"role": "assistant", "content": n.content})
            elif n.type == "explain":
                history.append({"role": "assistant", "content": n.content})
        root = db.get(AIAsset, chain[0].id) if chain else None
        root_id = chain[0].id if chain else req.parent_id
        # 兼容：追问链根的选中文字以根节点为准
        if root and (root.anchor or {}).get("selected_text"):
            req.selected_text = root.anchor["selected_text"]

    content = llm.explain(req.selected_text, context, history, req.question)

    anchor = dict(req.anchor or {})
    anchor["selected_text"] = req.selected_text[:500]
    if req.question:
        anchor["question"] = req.question
    a = AIAsset(
        material_id=req.material_id,
        type="qa" if req.question else "explain",
        content=content, anchor=anchor,
        parent_id=req.parent_id,
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return {**asset_to_dict(a), "root_id": root_id or a.id}


# ---------- 文本加工（改写/扩写/续写/总结；笔记与材料文本共用） ----------

NOTE_TRANSFORM_MODES = {"rewrite", "expand", "summarize", "continue"}


@router.post("/note/transform/stream")
def note_transform_stream(req: NoteTransformReq):
    """文本加工（改写/扩写/续写/总结）流式：纯文本变换，不入库、不依赖材料（笔记、材料文本、文档编辑通用）"""
    content = (req.content or "").strip()
    if not content:
        raise HTTPException(400, "待加工内容为空")
    if req.mode not in NOTE_TRANSFORM_MODES:
        raise HTTPException(400, "不支持的加工模式")
    if len(content) > 8000:
        content = content[:8000]
    messages = llm.note_transform_messages(req.mode, content, req.title)

    def gen():
        full = ""
        try:
            for kind, text in llm.chat_stream(messages, kind="transform"):
                if kind == "content":
                    full += text
                    yield _sse_event("token", {"t": text})
        except Exception as e:
            yield _sse_event("error", {"message": str(e)[:200]})
            return
        yield _sse_event("done", {"content": full})

    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


# ---------- 直接对材料提问（B11，不依赖划线） ----------

@router.post("/ask")
def ask_material(req: AskReq, db: Session = Depends(get_db)):
    """基于当前材料全文回答用户提问。结果存为 type=ask 的产物，可转笔记、可出现在追问记录。"""
    m = _get_material_or_404(db, req.material_id)
    q = (req.question or "").strip()
    if not q:
        raise HTTPException(400, "问题为空")
    if len(q) > 1000:
        q = q[:1000]
    chunks = _load_chunks(db, req.material_id)
    context = "\n".join(c["content"] for c in chunks)[:8000]
    answer = llm.ask_material(q, context, m.title)

    a = AIAsset(material_id=req.material_id, type="ask", content=answer,
                anchor={"question": q})
    db.add(a)
    db.commit()
    db.refresh(a)
    return asset_to_dict(a)


# ---------- SSE 流式（缩短等待感，文字逐字出现） ----------

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _sse_event(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _err_text(e: Exception) -> str:
    """错误文案归一：HTTPException 取 detail，避免前端看到「502: xxx」这种带状态码前缀的串"""
    detail = getattr(e, "detail", None)
    return str(detail) if detail else str(e)


@router.post("/explain/stream")
def explain_stream(req: ExplainReq, db: Session = Depends(get_db)):
    """SSE 流式解读/追问：逐 token 输出，完成后落库"""
    _get_material_or_404(db, req.material_id)
    if len(req.selected_text) > 2000:
        req.selected_text = req.selected_text[:2000]

    page = (req.anchor or {}).get("page_no", 1)
    ctx_rows = (db.query(MaterialChunk)
                .filter(MaterialChunk.material_id == req.material_id,
                        MaterialChunk.page_no.between(max(1, page - 1), page + 1))
                .order_by(MaterialChunk.page_no).all())
    context = "\n".join(r.content for r in ctx_rows)[:4000]

    history, root_id = [], req.parent_id
    if req.parent_id:
        node = db.get(AIAsset, req.parent_id)
        chain = []
        while node:
            chain.append(node)
            node = db.get(AIAsset, node.parent_id) if node.parent_id else None
        chain.reverse()
        for n in chain:
            if n.type == "qa":
                history.append({"role": "user", "content": (n.anchor or {}).get("question", "")})
                history.append({"role": "assistant", "content": n.content})
            elif n.type == "explain":
                history.append({"role": "assistant", "content": n.content})
        root = db.get(AIAsset, chain[0].id) if chain else None
        root_id = chain[0].id if chain else req.parent_id
        if root and (root.anchor or {}).get("selected_text"):
            req.selected_text = root.anchor["selected_text"]

    messages = llm.explain_messages(req.selected_text, context, history, req.question)
    anchor = dict(req.anchor or {})
    anchor["selected_text"] = req.selected_text[:500]
    if req.question:
        anchor["question"] = req.question

    def gen():
        full = ""
        try:
            for kind, text in llm.chat_stream(messages, kind="explain"):
                if kind == "content":
                    full += text
                    yield _sse_event("token", {"t": text})
        except Exception as e:
            yield _sse_event("error", {"message": str(e)[:200]})
            return
        a = AIAsset(material_id=req.material_id,
                    type="qa" if req.question else "explain",
                    content=full, anchor=anchor, parent_id=req.parent_id)
        db.add(a)
        db.commit()
        db.refresh(a)
        yield _sse_event("done", {"asset": asset_to_dict(a), "root_id": root_id or a.id})

    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/ask/stream")
def ask_stream(req: AskReq, db: Session = Depends(get_db)):
    """SSE 流式直接对材料提问"""
    m = _get_material_or_404(db, req.material_id)
    q = (req.question or "").strip()
    if not q:
        raise HTTPException(400, "问题为空")
    if len(q) > 1000:
        q = q[:1000]
    chunks = _load_chunks(db, req.material_id)
    context = "\n".join(c["content"] for c in chunks)[:8000]
    messages = llm.ask_messages(q, context, m.title)

    def gen():
        full = ""
        try:
            for kind, text in llm.chat_stream(messages, kind="ask"):
                if kind == "content":
                    full += text
                    yield _sse_event("token", {"t": text})
        except Exception as e:
            yield _sse_event("error", {"message": str(e)[:200]})
            return
        a = AIAsset(material_id=req.material_id, type="ask", content=full, anchor={"question": q})
        db.add(a)
        db.commit()
        db.refresh(a)
        yield _sse_event("done", {"asset": asset_to_dict(a)})

    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/summary/stream")
def summary_stream(req: SummaryReq, db: Session = Depends(get_db)):
    """SSE 流式摘要（短文档逐 token；长文档两段式退回一次性返回）"""
    _get_material_or_404(db, req.material_id)
    chunks = _load_chunks(db, req.material_id)
    total = sum(len(c["content"]) for c in chunks)
    extra = f"\n补充要求：{req.instruction}" if req.instruction else ""
    sys_prompt = llm.get_prompt("prompt_summary")

    def _save(content):
        prev = _latest_asset(db, req.material_id, "summary")
        a = AIAsset(material_id=req.material_id, type="summary", content=content,
                    version=(prev.version + 1) if prev else 1)
        db.add(a)
        db.commit()
        db.refresh(a)
        return a

    if total > llm.MAX_SINGLE_CHARS:
        groups = llm.split_groups(chunks)

        def gen_long():
            # 逐组回传进度：长文档要串行跑 N 组 + 1 次汇总，全程无输出会让用户以为卡死
            # （实测 24.7 万字的书 = 21 组，改前前端 149 秒收不到任何字节，只能反复点重试）
            # 分组阶段并发（SUMMARY_CONCURRENCY 路）：耗时由生成主导，串行改并发才能
            # 成比例压缩；进度按「已完成数」回传，完成顺序与分组序号无关，
            # 但 partials 按序号落位，汇总时的「第 N 部分」顺序仍然正确。
            partials: list = [None] * len(groups)
            pool = ThreadPoolExecutor(max_workers=llm.SUMMARY_CONCURRENCY)
            try:
                yield _sse_event("progress", {"done": 0, "total": len(groups)})
                futures = {pool.submit(llm.map_group_safe, g, i, len(groups)): i
                           for i, g in enumerate(groups)}
                done = 0
                for fut in as_completed(futures):
                    partials[futures[fut]] = fut.result()
                    done += 1
                    yield _sse_event("progress", {"done": done, "total": len(groups)})
                content = llm.reduce_summary([p for p in partials if p], req.instruction)
            except Exception as e:
                yield _sse_event("error", {"message": _err_text(e)[:200]})
                return
            finally:
                # 出错时不等剩余分组跑完（cancel_futures 取消排队中的），尽快把错误回给前端
                pool.shutdown(wait=False, cancel_futures=True)
            a = _save(content)
            yield _sse_event("token", {"t": content})
            yield _sse_event("done", {"asset": asset_to_dict(a)})

        return StreamingResponse(gen_long(), media_type="text/event-stream", headers=SSE_HEADERS)

    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": f"以下是文档全文（含页码标记）：\n\n{llm.format_chunks(chunks)}{extra}"},
    ]

    def gen():
        full = ""
        try:
            for text in llm.summary_chat_stream(messages, kind="summary"):
                full += text
                yield _sse_event("token", {"t": text})
        except Exception as e:
            yield _sse_event("error", {"message": _err_text(e)[:200]})
            return
        a = _save(full)
        yield _sse_event("done", {"asset": asset_to_dict(a)})

    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


# ---------- 产物读取 ----------

@router.get("/assets")
def get_assets(material_id: int, db: Session = Depends(get_db)):
    """学习页打开时恢复 AI 面板：最新摘要 + 最新知识点"""
    out = {}
    for t in ("summary", "keywords"):
        a = _latest_asset(db, material_id, t)
        if a:
            d = asset_to_dict(a)
            if t == "keywords":
                import json
                try:
                    d["items"] = json.loads(a.content)
                except Exception:
                    d["items"] = []
            out[t] = d
    return out


@router.get("/chains")
def get_chains(material_id: int, db: Session = Depends(get_db)):
    """追问记录：以 explain 为根组装每条追问链；ask（直接提问）作为单条记录（按时间正序）"""
    rows = (db.query(AIAsset)
            .filter(AIAsset.material_id == material_id, AIAsset.type.in_(["explain", "qa", "ask"]))
            .order_by(AIAsset.id).all())
    by_parent = {}
    roots = []
    for r in rows:
        if r.type in ("explain", "ask"):
            roots.append(r)
        else:
            by_parent.setdefault(r.parent_id, []).append(r)

    chains = []
    for root in roots:
        items = [asset_to_dict(root)]
        cur = root.id
        while cur in by_parent:
            for child in by_parent[cur]:
                items.append(asset_to_dict(child))
                cur = child.id  # 线性链：每条只跟最后一个子节点
        chains.append({"root_id": root.id, "items": items})
    chains.reverse()  # 最新在前
    return chains
