"""知识库索引服务：把原文块和笔记写入向量库 + KbEntry 记账

被三个地方调用：
- materials._parse_material（解析成功 → 原文归档，D1）
- notes 路由（笔记增/改/删三联动）
- kb 路由（重建索引，D4）
"""
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..models import Material, MaterialChunk, Note, KbEntry
from . import settings_store
from . import vector as vector_svc

NOTE_WEIGHT = 1.5     # 笔记权重默认值（实际值读设置 note_weight，检索打分另行读取）
CHUNK_WEIGHT = 1.0

MERGE_MIN = 50   # 短于此长度的非表格碎块，索引时与相邻块合并为一组向量
                 # （2026-10-23 第三期：实测 42% 的 chunk 是 <50 字碎片——标题行/标签行，
                 #    白吃 embedding 算力、稀释检索。只改索引层，DB 原文不动，可回滚）


def _group_for_embedding(chunks):
    """把连续碎块合并为 embedding 组：累计 <MERGE_MIN 字的邻块并成一组，含表格行或
    累计达标即封口。组内用**首块 id** 作代表 ref_id（来源回显/跳转均按首块）。

    取舍说明：
    - 组的向量文本 = 组内各块拼接，但检索回显的 snippet 取向量文档（拼接文本），
      实体/FTS 通道仍按单块回显——两通道文案粒度不同，可接受；
    - reindex_chunk（转写校对）按单块重建：若该块曾是组首，组内其余碎块暂失索引，
      待下次整材料重建恢复；若非组首，会短暂双份。校对对象是音视频转写块（都是大块），
      与碎块组几乎不重叠，风险可忽略。
    """
    groups, cur, cur_len = [], [], 0
    for c in chunks:
        cur.append(c)
        cur_len += len(c.content or "")
        if "|" in (c.content or "") or cur_len >= MERGE_MIN:
            groups.append(cur)
            cur, cur_len = [], 0
    if cur:
        groups.append(cur)
    return groups


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
    #   一条旧向量都删不到 → 旧向量留在 Chroma 被检索召回，AI 对话会引用**不存在的 chunk**、
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

    # ⚠️ 跳过 kind='meta'（剪藏署名 / 来源 URL）：它们是文档元信息，不该进向量。
    #    必须在 `_group_for_embedding` **之前**剔除：溯源块合计不到 50 字，
    #    留在列表里会把「标题」和「来源 URL」并成首组，白占一条向量且污染标题语义。
    chunks = (db.query(MaterialChunk)
              .filter(MaterialChunk.material_id == material_id,
                      or_(MaterialChunk.kind.is_(None), MaterialChunk.kind != "meta"))
              .order_by(MaterialChunk.page_no, MaterialChunk.id).all())
    if not chunks:
        return 0

    items, entries = [], []
    for group in _group_for_embedding(chunks):
        head = group[0]   # 组代表：来源回显/跳转按首块
        text = "\n".join((c.content or "").strip() for c in group if (c.content or "").strip())
        if not text:
            continue
        for seq, part in enumerate(vector_svc.split_text(text)):
            eid = vector_svc.make_embedding_id("chunk", head.id, seq)
            items.append({
                "embedding_id": eid,
                "text": part,
                "metadata": {
                    "ref_type": "chunk", "ref_id": head.id,
                    "material_id": material_id, "material_title": m.title,
                    "page_no": head.page_no or 0, "section_path": head.section_path or "",
                },
            })
            entries.append(KbEntry(material_id=material_id, ref_type="chunk",
                                   ref_id=head.id, embedding_id=eid, weight=CHUNK_WEIGHT))

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


def backfill_clip_meta_chunks(db: Session) -> int:
    """把**存量**剪藏材料的溯源块标为 `kind='meta'`，返回新标记块数。幂等。

    新剪藏已在 `materials._parse_material` 里标过（`cleaner.mark_provenance`），
    这里只补历史数据：实测 17 篇剪藏各留 2 块（第 2 块署名 / 公众号名、第 3 块
    `> 来源：URL`），共 34 块进了向量与全文检索；探针重抽时「微信公众平台」被抽成
    跨 2 份材料的实体（存量库里材料 43 已落下 `运气` / `教职号` 两个号名实体）。
    ⚠️ 只改 kind、**不动 content / page_no**：页码由块顺序决定，删块会让笔记的
       `anchor.page_no`、划线位置全部错位 —— 所以只能标记不能删。
    ⚠️ 判据复用 `cleaner.mark_provenance`（结构锚点），与解析路径同一份逻辑。
    ⚠️ 向量与 KbEntry 的清理不在这里（要 chroma 就绪）→ 见 `purge_meta_indexes`。
    """
    import json

    from . import cleaner
    from ..models import Material

    clip_ids = []
    for mid, raw_meta in db.query(Material.id, Material.origin_meta).all():
        meta = raw_meta
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}
        if isinstance(meta, dict) and meta.get("saved_from") == "clip":
            clip_ids.append(mid)
    if not clip_ids:
        return 0

    total = 0
    for mid in clip_ids:
        rows = (db.query(MaterialChunk)
                .filter(MaterialChunk.material_id == mid)
                .order_by(MaterialChunk.page_no, MaterialChunk.id)
                .limit(cleaner._PROV_SCAN).all())
        if not rows:
            continue
        payload = [{"content": r.content or ""} for r in rows]
        if not cleaner.mark_provenance(payload):
            continue
        for d, r in zip(payload, rows):
            if d.get("kind") == "meta" and r.kind != "meta":
                r.kind = "meta"
                total += 1
    if total:
        db.commit()
    return total


def purge_meta_indexes(db: Session) -> int:
    """清掉 `kind='meta'`（剪藏溯源块）残留的向量与 KbEntry 记账。幂等。

    `main._backfill_clip_meta_chunks` 只改 kind，索引层已不再收录 meta 块，
    但**旧向量与记账还在** —— 不清掉的话，启动自检比对「向量数 vs 记账数」
    会判成索引残缺 → 触发一次全量重建（白跑两分钟）。
    ⚠️ 需要 chroma 已就绪，故不在 import 期调用，由启动自检线程调。
    """
    meta_ids = [r[0] for r in
                db.query(MaterialChunk.id).filter(MaterialChunk.kind == "meta").all()]
    if not meta_ids:
        return 0
    n = 0
    for cid in meta_ids:
        entry = (db.query(KbEntry)
                 .filter(KbEntry.ref_type == "chunk", KbEntry.ref_id == cid).first())
        if entry is None:
            continue
        try:
            vector_svc.delete_by_ref("chunk", cid)
        except BaseException as e:
            # 删向量失败就**保留记账**：宁可让自检判残缺去重建，也不能两边都删一半
            print(f"[kb_index] 清理溯源块 {cid} 的向量失败（保留记账）: {e}")
            continue
        db.query(KbEntry).filter(KbEntry.ref_type == "chunk",
                                 KbEntry.ref_id == cid).delete()
        n += 1
    db.commit()
    return n


def deindex_material(db: Session, material_id: int):
    """删除材料时清理其原文索引；笔记索引保留（笔记不随材料删除）"""
    vector_svc.with_heal(vector_svc.CHUNK_COL,
                         lambda col: vector_svc._delete_where(col, {"material_id": material_id}))
    db.query(KbEntry).filter(
        KbEntry.material_id == material_id, KbEntry.ref_type == "chunk").delete()
    db.commit()
