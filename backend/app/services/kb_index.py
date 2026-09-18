"""知识库索引服务：把原文块和笔记写入向量库 + KbEntry 记账

被三个地方调用：
- materials._parse_material（解析成功 → 原文归档，D1）
- notes 路由（笔记增/改/删三联动）
- kb 路由（重建索引，D4）
"""
from sqlalchemy.orm import Session

from ..models import Material, MaterialChunk, Note, KbEntry
from . import settings_store
from . import vector as vector_svc

NOTE_WEIGHT = 1.5     # 笔记权重默认值（实际值读设置 note_weight，检索打分另行读取）
CHUNK_WEIGHT = 1.0


def index_material(db: Session, material_id: int) -> int:
    """把一份材料的全部原文块切分向量化入库。返回入库块数。

    不依赖 parsed_status（所有调用方均已保证材料解析成功），改为检查 chunks 是否存在，
    以支持「先建索引、后标记 success」的时序，避免 status=success 先于索引导致的删除竞态。

    ⚠️ 写入前会先清掉本材料在 `kb_chunks` 里的**全部旧向量** —— 材料「重试解析」会删旧 chunk
    建新 chunk，新的 id 与旧的必然不同，按 id 的「先删后增」删不到旧向量（详见函数体内说明）。
    """
    m = db.get(Material, material_id)
    if not m:
        return 0

    # ⚠️ 先清本材料的全部旧块向量，再重建（顺序不能颠倒）：
    #   `_parse_material` 重试时会「删旧 chunk → 建新 chunk」，新 chunk 的 id 与旧的**必然不同**
    #   （SQLite 的 rowid 取 max+1，真实库里总有别的材料的块压在表尾，不会复用），
    #   而 embedding_id 由 `chunk:{id}:{seq}` 派生 → `index_texts` 的「按 id 先删后增」
    #   一条旧向量都删不到 → 旧向量留在 Chroma 被检索召回，AI 问答会引用**不存在的 chunk**、
    #   知识库块数虚高。实测：重试解析一次后 向量 6 / 块 3。
    #   ⚠️ 不能用 `vector.delete_by_material`：它遍历两个集合，会把该材料的**笔记向量**一起删
    #      （笔记不随材料删除，只解绑）。
    #   ⚠️ 必须放在「无块则返回」**之前**：材料被重解析成空（scanned）时同样要清。
    try:
        col = vector_svc.get_collection(vector_svc.CHUNK_COL)
        stale = col.get(where={"material_id": material_id})
        if stale and stale.get("ids"):
            col.delete(ids=stale["ids"])
    except BaseException as e:
        # ⚠️ 用 BaseException 且绝不外抛：清旧向量是「卫生」步骤，失败也不能阻断建索引
        #    （hnsw 索引残缺时 col.get 会抛 InternalError；宁可留孤儿，也不能让解析整体失败）
        print(f"[kb_index] 清理材料 {material_id} 的旧向量失败（继续建索引）: {e}")

    chunks = (db.query(MaterialChunk)
              .filter(MaterialChunk.material_id == material_id)
              .order_by(MaterialChunk.page_no, MaterialChunk.id).all())
    if not chunks:
        return 0

    items, entries = [], []
    for c in chunks:
        for seq, part in enumerate(vector_svc.split_text(c.content)):
            eid = vector_svc.make_embedding_id("chunk", c.id, seq)
            items.append({
                "embedding_id": eid,
                "text": part,
                "metadata": {
                    "ref_type": "chunk", "ref_id": c.id,
                    "material_id": material_id, "material_title": m.title,
                    "page_no": c.page_no or 0, "section_path": c.section_path or "",
                },
            })
            entries.append(KbEntry(material_id=material_id, ref_type="chunk",
                                   ref_id=c.id, embedding_id=eid, weight=CHUNK_WEIGHT))

    vector_svc.index_texts(items, kind="chunk")
    db.query(KbEntry).filter(KbEntry.material_id == material_id, KbEntry.ref_type == "chunk").delete()
    db.add_all(entries)
    db.commit()
    return len(items)


def reindex_chunk(db: Session, chunk: MaterialChunk):
    """转写校对后：重建单个原文块的向量索引（删旧向量 + 重切分入库）"""
    vector_svc.delete_by_ref("chunk", chunk.id)
    db.query(KbEntry).filter(KbEntry.ref_type == "chunk", KbEntry.ref_id == chunk.id).delete()
    db.flush()

    m = db.get(Material, chunk.material_id)
    items, entries = [], []
    for seq, part in enumerate(vector_svc.split_text(chunk.content)):
        eid = vector_svc.make_embedding_id("chunk", chunk.id, seq)
        items.append({
            "embedding_id": eid,
            "text": part,
            "metadata": {
                "ref_type": "chunk", "ref_id": chunk.id,
                "material_id": chunk.material_id, "material_title": m.title if m else "",
                "page_no": chunk.page_no or 0, "section_path": chunk.section_path or "",
            },
        })
        entries.append(KbEntry(material_id=chunk.material_id, ref_type="chunk",
                               ref_id=chunk.id, embedding_id=eid, weight=CHUNK_WEIGHT))
    vector_svc.index_texts(items, kind="chunk")
    db.add_all(entries)
    db.commit()


def deindex_chunk(db: Session, chunk: MaterialChunk):
    """删除单个原文块：清向量 + 清 KbEntry（编辑模式删除文本块/整页时调用）"""
    vector_svc.delete_by_ref("chunk", chunk.id)
    db.query(KbEntry).filter(KbEntry.ref_type == "chunk", KbEntry.ref_id == chunk.id).delete()
    db.flush()


def index_note(db: Session, note: Note):
    """笔记向量化（创建/更新时调用）。内容为空则只清索引。"""
    deindex_note(db, note.id)
    text = f"{note.title}\n{note.content}".strip()
    if not text:
        return
    eid = vector_svc.make_embedding_id("note", note.id)
    vector_svc.index_texts([{
        "embedding_id": eid,
        "text": text,
        "metadata": {
            "ref_type": "note", "ref_id": note.id,
            # 孤儿笔记（材料已删除）material_id 为悬空 NULL，chromadb 不接受 None，兜底为 0
            "material_id": note.material_id or 0,
            "note_title": note.title or "",
            "page_no": (note.anchor or {}).get("page_no") or 0,
        },
    }], kind="note")
    db.add(KbEntry(material_id=note.material_id, ref_type="note",
                   ref_id=note.id, embedding_id=eid,
                   weight=float(settings_store.load().get("note_weight") or NOTE_WEIGHT)))
    db.commit()


def deindex_note(db: Session, note_id: int):
    vector_svc.delete_by_ref("note", note_id)
    db.query(KbEntry).filter(KbEntry.ref_type == "note", KbEntry.ref_id == note_id).delete()
    db.commit()


def deindex_material(db: Session, material_id: int):
    """删除材料时清理其原文索引；笔记索引保留（笔记不随材料删除）"""
    col = vector_svc.get_collection(vector_svc.CHUNK_COL)
    results = col.get(where={"material_id": material_id})
    if results and results.get("ids"):
        col.delete(ids=results["ids"])
    db.query(KbEntry).filter(
        KbEntry.material_id == material_id, KbEntry.ref_type == "chunk").delete()
    db.commit()
