"""知识库路由（PRD 模块 D）：总览 / 重建索引 / 检索测试 / 阈值校准"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func

from ..database import get_db
from ..models import Material, Note, KbEntry, MaterialChunk, Entity, Relation, EntityRef
from ..services import kb_index, vector as vector_svc, search as search_svc
router = APIRouter(prefix="/kb", tags=["kb"])


class RebuildReq(BaseModel):
    material_id: int


@router.get("/overview")
def kb_overview(search: str | None = None, db: Session = Depends(get_db)):
    """D2 总览：按材料 / 按笔记两个维度"""
    # 按材料：入库块数 + 笔记数 + 实体数（知识图谱生成状态）
    materials = db.query(Material).order_by(Material.created_at.desc()).all()
    by_material = []
    for m in materials:
        chunk_count = (db.query(KbEntry)
                       .filter(KbEntry.material_id == m.id, KbEntry.ref_type == "chunk").count())
        note_count = db.query(Note).filter(Note.material_id == m.id).count()
        entity_count = db.query(Entity).filter(Entity.material_id == m.id).count()
        if search and search not in m.title:
            continue
        by_material.append({
            "material_id": m.id, "title": m.title, "format": m.format,
            "parsed_status": m.parsed_status,
            "chunk_count": chunk_count, "note_count": note_count,
            "entity_count": entity_count,
            "indexed": chunk_count > 0,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        })

    # 按笔记：全部笔记 + 是否已索引
    q = db.query(Note).order_by(Note.updated_at.desc())
    notes = []
    for n in q.all():
        if search and search not in n.title and search not in (n.content or ""):
            continue
        indexed = (db.query(KbEntry)
                   .filter(KbEntry.ref_type == "note", KbEntry.ref_id == n.id).count()) > 0
        if n.source_type == "chat":
            src_label = "AI 问答"          # 问答转存的材料无关笔记
        elif n.source_type == "weekly_report":
            src_label = "AI 学习周报"       # 数据统计页的 AI 分析报告
        elif n.source_type == "podcast_script":
            src_label = "AI 播客脚本"       # 播客页的对话脚本（演绎层）
        elif n.source_type == "podcast_brief":
            src_label = "AI 播客简报"       # 播客页的知识简报（提炼层）
        elif n.material_id:
            material = db.get(Material, n.material_id)
            src_label = material.title if material else "（材料已删除）"
        else:
            src_label = "（无来源材料）"
        notes.append({
            "id": n.id, "title": n.title, "content": (n.content or "")[:120],
            "source_type": n.source_type, "material_id": n.material_id,
            "material_title": src_label,
            "indexed": indexed,
            "updated_at": n.updated_at.isoformat() if n.updated_at else None,
        })
    return {"materials": by_material, "notes": notes}


@router.post("/rebuild")
def rebuild_index(req: RebuildReq, db: Session = Depends(get_db)):
    """D4 重建指定材料的原文索引（笔记索引不动）"""
    m = db.get(Material, req.material_id)
    if not m:
        raise HTTPException(404, "材料不存在")
    if m.parsed_status != "success":
        raise HTTPException(400, f"材料状态为 {m.parsed_status}，无法建索引")
    count = kb_index.index_material(db, m.id)
    return {"ok": True, "chunk_count": count}


@router.post("/rebuild-all")
def rebuild_all(db: Session = Depends(get_db)):
    """切换向量模型后全量重建：清空两个集合 + KbEntry，重灌全部材料与笔记

    必须全清的原因：不同 embedding 模型向量维度/语义空间不同，混排会导致检索错乱。
    """
    import chromadb
    from ..core.config import settings as cfg
    client = chromadb.PersistentClient(path=str(cfg.chroma_dir))
    for name in ("kb_chunks", "kb_notes"):
        try:
            client.delete_collection(name)
        except Exception:
            pass
    db.query(KbEntry).delete()
    db.commit()

    material_count, chunk_total, note_total = 0, 0, 0
    materials = db.query(Material).filter(Material.parsed_status == "success").all()
    for m in materials:
        chunk_total += kb_index.index_material(db, m.id)
        material_count += 1
    for n in db.query(Note).all():
        kb_index.index_note(db, n)
        note_total += 1
    return {"ok": True, "materials": material_count, "chunks": chunk_total, "notes": note_total}


@router.post("/search")
def kb_search(payload: dict, db: Session = Depends(get_db)):
    """检索调试接口：多路召回（向量 + 全文 → RRF 融合 + 重排）"""
    query = (payload or {}).get("query", "").strip()
    if not query:
        raise HTTPException(400, "query 不能为空")
    return search_svc.search(query, db)


@router.get("/entities")
def kb_entities(db: Session = Depends(get_db)):
    """知识图谱数据：实体 + 关系 + 引用，供前端可视化。

    返回结构：
    - entities: [{name, type, material_ids, ref_count}]
      ref_count = 该实体关联的内容块/笔记数（去重后），作为节点重要度
    - relations: [{src, rel, dst, material_ids}]（仅保留两端实体都存在的）
      material_ids 记录该关系出现在哪些材料（跨材料同名实体会使同一条 src-rel-dst 在
      多个材料各自出现），供前端「按材料隔离」精确过滤——避免只看实体名导致的看 A 显 B。
    - materials: {id: title} —— 实体来自哪几篇材料

    只读接口；实体由检索端 P1 后台异步抽取产出，可能随时间增长。
    """
    ents = db.query(Entity).all()
    rels = db.query(Relation).all()
    refs = db.query(EntityRef).all()

    # 实体归并：同名同类型跨材料合并，记录素材 id 集合
    ent_map: dict[tuple[str, str], dict] = {}
    for e in ents:
        key = (e.name, e.type)
        if key not in ent_map:
            ent_map[key] = {"name": e.name, "type": e.type, "material_ids": [], "ref_count": 0}
        if e.material_id not in ent_map[key]["material_ids"]:
            ent_map[key]["material_ids"].append(e.material_id)

    # 引用计数：按 (entity, ref_type, ref_id) 去重统计（同一内容多处提到同一实体只算一次）。
    # ⚠️ 用「实体名 → 计数」单次遍历，避免 O(引用数 × 实体数) 双重循环（数据增长会变慢）。
    name_to_item: dict[str, dict] = {item["name"]: item for item in ent_map.values()}
    seen_refs: set[tuple[str, str, int]] = set()
    for r in refs:
        key = (r.entity, r.ref_type, r.ref_id)
        if key in seen_refs:
            continue
        seen_refs.add(key)
        item = name_to_item.get(r.entity)
        if item is not None:
            item["ref_count"] += 1

    entities = list(ent_map.values())

    # 关系：只保留两端实体都存在的（悬空关系不展示），按 (src, rel, dst) 全局去重，
    # 同时累计该关系出现的材料集合（跨材料同名实体会让同一条关系在多个材料各自出现）。
    names = {e["name"] for e in entities}
    rel_map: dict[tuple[str, str, str], dict] = {}
    for r in rels:
        if r.src not in names or r.dst not in names or r.src == r.dst:
            continue
        key = (r.src, r.rel, r.dst)
        if key not in rel_map:
            rel_map[key] = {"src": r.src, "rel": r.rel, "dst": r.dst, "material_ids": []}
        if r.material_id not in rel_map[key]["material_ids"]:
            rel_map[key]["material_ids"].append(r.material_id)
    relations = list(rel_map.values())

    # 材料标题映射
    mids = set()
    for e in entities:
        mids.update(e["material_ids"])
    materials = {}
    if mids:
        for m in db.query(Material).filter(Material.id.in_(mids)).all():
            materials[m.id] = m.title

    return {"entities": entities, "relations": relations, "materials": materials}


@router.post("/calibrate")
def calibrate_threshold(db: Session = Depends(get_db)):
    """阈值校准：用知识库内随机 chunk 自查询得相关分、跨材料得无关分，给建议阈值。

    换 embedding 模型后分数分布会变，可用此校准参考设定 kb_hit_threshold。
    """
    chunks = db.query(MaterialChunk).filter(MaterialChunk.content != "").order_by(func.random()).limit(3).all()
    if not chunks:
        raise HTTPException(400, "知识库为空，请先导入材料")

    samples = []
    for c in chunks:
        hits = vector_svc.search(c.content[:200], top_k=12)
        self_score = hits[0]["score"] if hits else 0
        cross = [h["score"] for h in hits if h.get("material_id") != c.material_id]
        cross_score = max(cross) if cross else None
        samples.append({"self_score": self_score, "cross_score": cross_score})

    selfs = [s["self_score"] for s in samples]
    crosses = [s["cross_score"] for s in samples if s["cross_score"] is not None]
    # 建议阈值取「相关下限的 30%」这一保守值（自匹配分远高于真实查询相关分，不能直接取中值；
    # 跨材料分在此语料中也偏高，因为材料主题相近）。仅供换模型后作起始参考。
    suggested = round(max(0.15, min(selfs) * 0.3), 3) if selfs else 0.15
    return {"samples": samples, "suggested_threshold": suggested}

