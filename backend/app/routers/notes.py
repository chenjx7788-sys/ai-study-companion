"""笔记路由（PRD 模块 C 最小闭环）：列表 / 创建（手动+转笔记）/ 更新 / 删除

知识库向量化入库在 M3 补齐（此处预留钩子）。
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Note, Material

router = APIRouter(prefix="/notes", tags=["notes"])


class NoteCreate(BaseModel):
    material_id: int
    title: str = "未命名笔记"
    content: str = ""
    source_type: str = "manual"        # manual / ai_asset
    source_asset_id: int | None = None
    anchor: dict | None = None

class NoteUpdate(BaseModel):
    title: str | None = None
    content: str | None = None

class ChatNoteCreate(BaseModel):
    """AI 问答转笔记：材料无关（material_id=NULL, source_type=chat）
    标题=用户问题，内容=AI 回答；同「标题+内容」幂等，重复转存返回既有笔记。"""
    title: str = ""
    content: str = ""
    sources: list[dict] | None = None    # 引用来源快照（仅展示，不挂靠材料）


def note_to_dict(n: Note) -> dict:
    return {
        "id": n.id, "material_id": n.material_id, "title": n.title,
        "content": n.content, "source_type": n.source_type,
        "source_asset_id": n.source_asset_id, "anchor": n.anchor,
        "created_at": n.created_at.isoformat() if n.created_at else None,
        "updated_at": n.updated_at.isoformat() if n.updated_at else None,
    }


@router.get("")
def list_notes(material_id: int, db: Session = Depends(get_db)):
    """C3 按材料隔离：只返回该材料的笔记"""
    rows = (db.query(Note).filter(Note.material_id == material_id)
            .order_by(Note.updated_at.desc()).all())
    return [note_to_dict(n) for n in rows]


@router.get("/{note_id}")
def get_note(note_id: int, db: Session = Depends(get_db)):
    """单条笔记全文（孤儿笔记预览用，列表接口按材料查询拿不到）"""
    n = db.get(Note, note_id)
    if not n:
        raise HTTPException(404, "笔记不存在")
    return note_to_dict(n)


@router.post("/from-chat")
def create_chat_note(req: ChatNoteCreate, db: Session = Depends(get_db)):
    """AI 问答转笔记（材料无关）：沉淀到知识库「按笔记」，可检索、可出复习卡。

    幂等口径：同「标题+内容」视为同一条问答——重复转存不新建，返回既有笔记；
    若用户编辑过旧笔记、或回答内容已变（重新生成），则内容不同会放行新建。
    """
    title = (req.title or "").strip()[:255]
    content = (req.content or "").strip()
    if not title:
        title = content[:24] or "AI 问答"
    if len(content) < 2:
        raise HTTPException(400, "回答内容为空或过短，无法转成笔记")

    dup = (db.query(Note).filter(Note.source_type == "chat",
                                  Note.title == title,
                                  Note.content == content).first())
    if dup:
        return {"note": note_to_dict(dup), "created": False}

    # 引用来源快照（标题/类型/页码/片段，截短防 anchor 过大；纯展示用）
    anchor = None
    if req.sources:
        snap = []
        for s in req.sources:
            item = {}
            for k in ("title", "ref_type", "page_no", "material_id"):
                if s.get(k) is not None:
                    item[k] = s[k]
            if s.get("snippet"):
                item["snippet"] = s["snippet"][:160]
            if item:
                snap.append(item)
        if snap:
            anchor = {"chat": True, "sources": snap}

    n = Note(material_id=None, title=title, content=content,
             source_type="chat", anchor=anchor)
    db.add(n)
    db.commit()
    db.refresh(n)
    # 向量化入知识库（失败不阻断创建，但回传警告给前端提示）
    reindex_warning = None
    try:
        from ..services import kb_index
        kb_index.index_note(db, n)
    except Exception as e:
        import logging
        reindex_warning = "已转存，但向量索引未建立，后续问答可能检索不到（可到知识库重建索引）"
        logging.getLogger("uvicorn.error").warning(f"问答转笔记 {n.id} 索引失败: {e}")
    d = note_to_dict(n)
    if reindex_warning:
        d["reindex_warning"] = reindex_warning
    return {"note": d, "created": True}


@router.post("")
def create_note(req: NoteCreate, db: Session = Depends(get_db)):
    if not db.get(Material, req.material_id):
        raise HTTPException(404, "材料不存在")
    # 内容质量校验：拒绝划线误选产生的残缺片段（污染知识库检索与复习出题）
    content = (req.content or "").strip()
    if len(content) < 2:
        raise HTTPException(400, "选中的内容不完整（少于 2 字），请重新选择更完整的段落")
    n = Note(**req.model_dump())
    n.content = content
    db.add(n)
    db.commit()
    db.refresh(n)
    # 笔记向量化入知识库（失败不阻断）
    try:
        from ..services import kb_index
        kb_index.index_note(db, n)
    except Exception:
        pass
    return note_to_dict(n)


@router.put("/{note_id}")
def update_note(note_id: int, req: NoteUpdate, db: Session = Depends(get_db)):
    n = db.get(Note, note_id)
    if not n:
        raise HTTPException(404, "笔记不存在")
    if req.title is not None:
        n.title = req.title
    if req.content is not None:
        content = req.content.strip()
        if len(content) < 2:
            raise HTTPException(400, "内容过短（少于 2 字），请选择更完整的段落")
        n.content = content
    db.commit()
    db.refresh(n)
    # 同步更新向量索引（失败不阻断保存，但回传警告给前端提示）
    reindex_warning = None
    try:
        from ..services import kb_index
        kb_index.index_note(db, n)
    except Exception as e:
        import logging
        reindex_warning = "已保存，但索引更新失败，后续问答可能检索不到（可到知识库重建索引）"
        logging.getLogger("uvicorn.error").warning(f"笔记 {note_id} 重索引失败: {e}")
    d = note_to_dict(n)
    if reindex_warning:
        d["reindex_warning"] = reindex_warning
    return d


@router.delete("/{note_id}")
def delete_note(note_id: int, db: Session = Depends(get_db)):
    n = db.get(Note, note_id)
    if not n:
        raise HTTPException(404, "笔记不存在")
    # 级联删除该笔记的复习卡（避免孤儿卡残留）
    from ..models import ReviewCard
    db.query(ReviewCard).filter(ReviewCard.note_id == note_id).delete()
    db.delete(n)
    db.commit()
    # 同步删除向量条目
    try:
        from ..services import kb_index
        kb_index.deindex_note(db, note_id)
    except Exception:
        pass
    return {"ok": True}
