"""多路召回编排：向量（语义）+ 全文（精确）→ RRF 融合 → 规则重排。

链路：提问 → 两路召回 → RRF 倒数排名融合 → 重排（note 权重 + MMR 去重）
     → 阈值过滤（全文精确命中 OR 向量分达标）→ 注入上下文

设计要点：
- RRF 零参数融合，规避多路分数量纲不同（向量相似度 vs 全文命中词数）的调参难题
- 全文精确命中不参与向量阈值过滤（关键词都命中即相关）
- 重排的 note 权重与 vector 检索一致，来自设置（可修改，默认 1.5/原文 1.0）
- 时效性 P0 不接入：学习资料时效概念弱，衰减价值低，留作后续扩展点
"""
from ..core.config import settings
from . import settings_store
from . import fts as fts_svc
from . import vector as vector_svc

RRF_K = 60            # RRF 平滑常数（业界常用 60）
MMR_DIVERSITY = 0.4   # MMR 多样性惩罚系数
NOTE_WEIGHT = 1.5     # 笔记权重默认值（实际值优先读设置 note_weight）


def _key(h: dict) -> tuple:
    return (h["ref_type"], h["ref_id"])


def _rrf_fuse(ranked_lists: list[list[dict]], k: int = RRF_K) -> list[dict]:
    """倒数排名融合：每路内部先按 (ref_type, ref_id) 去重（取最高分），再按名次融合。

    同一 doc 两路命中时合并来源标记，保留两路原始分（供阈值过滤用）。
    """
    scores: dict[tuple, float] = {}
    docs: dict[tuple, dict] = {}
    for lst in ranked_lists:
        seen: set[tuple] = set()
        deduped: list[dict] = []
        for h in lst:
            key = _key(h)
            if key not in seen:
                seen.add(key)
                deduped.append(h)
        for rank, h in enumerate(deduped):
            key = _key(h)
            scores[key] = scores.get(key, 0) + 1.0 / (k + rank + 1)
            if key not in docs:
                docs[key] = h
            else:
                prev = docs[key]
                prev["from_vector"] = prev.get("from_vector", False) or h.get("from_vector", False)
                prev["from_fts"] = prev.get("from_fts", False) or h.get("from_fts", False)
                prev["from_entity"] = prev.get("from_entity", False) or h.get("from_entity", False)
                if h.get("vec_score") is not None:
                    prev["vec_score"] = h["vec_score"]
                if h.get("fts_score") is not None:
                    prev["fts_score"] = h["fts_score"]
    merged = sorted(docs.values(), key=lambda h: -scores[_key(h)])
    for h in merged:
        h["rrf_score"] = round(scores[_key(h)], 5)
        h["score"] = h["rrf_score"]  # 统一对外分 = 融合分（前端来源卡片不展示 score）
    return merged


def _rerank(hits: list[dict], note_weight: float = NOTE_WEIGHT) -> list[dict]:
    """规则重排：note 权重 → 归一化相关性 → MMR 多样性去重（避免连续召回同材料片段）。"""
    if not hits:
        return hits
    max_rrf = max(h["rrf_score"] for h in hits) or 1.0
    for h in hits:
        w = note_weight if h["ref_type"] == "note" else 1.0
        h["_rel"] = (h["rrf_score"] / max_rrf) * w

    selected: list[dict] = []
    remaining: list[dict] = list(hits)
    while remaining:
        def _mmr(h: dict) -> float:
            pen = 0.0
            for s in selected:
                if s["ref_type"] == h["ref_type"] and s["ref_id"] == h["ref_id"]:
                    pen = max(pen, 1.0)
                elif (s.get("material_id") is not None
                      and s.get("material_id") == h.get("material_id")):
                    pen = max(pen, 0.6)
            return h.get("_rel", 0.0) - MMR_DIVERSITY * pen
        best = max(remaining, key=_mmr)
        selected.append(best)
        remaining.remove(best)
    return selected


def _where_material_ids(where: dict | None) -> set[int] | None:
    """从检索 where 解析 material_id 白名单；无约束返回 None（不过滤）。"""
    if not where:
        return None
    v = where.get("material_id")
    if v is None:
        return None
    if isinstance(v, dict):
        ids = v.get("$in") or []
    elif isinstance(v, (list, tuple, set)):
        ids = list(v)
    else:
        ids = [v]
    try:
        return {int(x) for x in ids}
    except (TypeError, ValueError):
        return None


def _entity_hits(db, query: str, kinds=("chunk", "note"), limit: int = 8,
                 where: dict | None = None) -> list[dict]:
    """知识图谱召回：问题命中实体 → 扩展召回该实体关联的块/笔记。

    这是「轻量实体索引」的唯一检索用途——不做关系多跳、不做图谱可视化，
    只走「实体 → 关联块」这条最短路径，把跨材料的关联内容串回来。

    命中逻辑：问题里的关键词能子串匹配到某实体的 name，则该实体命中；
    取命中的实体 → 找 entity_refs 里它关联的 chunk / note → 还原为和 vector/fts
    同构的 hit。score 用命中实体数（RRF 只看名次）。
    """
    from ..models import Entity, EntityRef, MaterialChunk, Note

    kws = fts_svc._keywords(query)
    if not kws:
        return []

    ref_types = [t for t in kinds if t in ("chunk", "note")]
    if not ref_types:
        return []

    # 2026-10-27 P1 根治：实体召回路与 vector/fts 同口径吃 where——
    # 此前 where 被丢弃，应用语境下库外笔记经实体路直接进入回答（炼丹炉 P0 实证翻车，
    # 当时只能在 apps.py 事后过滤兜底）。过滤点放在「引用落点」EntityRef.material_id，
    # 实体名匹配仍全表扫（同名实体可跨材料存在，名字匹配不该受材料限制）。
    allow_ids = _where_material_ids(where)

    # 命中的实体名（任一关键词是实体名的子串，或实体名是关键词的子串）
    entity_rows = db.query(Entity).all()
    matched_names: set[str] = set()
    for e in entity_rows:
        en = e.name or ""
        if not en:
            continue
        for k in kws:
            if k and (k in en or en in k):
                matched_names.add(en)
                break
    if not matched_names:
        return []

    q = (db.query(EntityRef).filter(
        EntityRef.ref_type.in_(ref_types),
        EntityRef.entity.in_(matched_names)))
    if allow_ids is not None:
        q = q.filter(EntityRef.material_id.in_(allow_ids))
    refs = q.all()

    hits = []
    seen: set[tuple[str, int]] = set()
    for ref in refs:
        key = (ref.ref_type, ref.ref_id)
        if key in seen:
            continue
        seen.add(key)
        text = ""
        material_id = ref.material_id
        metadata = {}
        if ref.ref_type == "chunk":
            c = db.get(MaterialChunk, ref.ref_id)
            if not c:
                continue
            text = c.content or ""
            material_id = c.material_id
            title = c.material.title if c.material else ""
            metadata = {"ref_type": "chunk", "ref_id": c.id, "material_id": c.material_id,
                        "material_title": title, "page_no": c.page_no,
                        "section_path": c.section_path or ""}
        else:
            n = db.get(Note, ref.ref_id)
            if not n:
                continue
            text = f"{n.title}\n{n.content or ''}".strip()
            material_id = n.material_id
            title = n.material.title if n.material else ""
            metadata = {"ref_type": "note", "ref_id": n.id, "material_id": n.material_id,
                        "note_title": n.title, "material_title": title,
                        "page_no": (n.anchor or {}).get("page_no", 0)}
        if not text:
            continue
        # 解析后再校验一次：ref.material_id 是写入时的快照，真实对象的归属才是权威
        if allow_ids is not None and material_id not in allow_ids:
            continue
        hits.append({
            "ref_type": ref.ref_type,
            "ref_id": ref.ref_id,
            "material_id": material_id,
            "text": text,
            "metadata": metadata,
            "score": float(len(matched_names)),
            "from_fts": False,
            "from_vector": False,
            "from_entity": True,
        })
    # 命中实体多在前（名次越靠前 RRF 分越高；此处按实体命中数降序，稳定排序）
    hits.sort(key=lambda h: (-h["score"], h["ref_id"]))
    return hits[:limit]


def search(query: str, db, kinds=("chunk", "note"), where: dict | None = None,
           top_k: int | None = None) -> list[dict]:
    """多路召回统一入口，返回重排后的 hits（结构与 vector.search 兼容）。

    每个 hit 额外携带：
    - from_vector / from_fts：来源标记（阈值过滤用）
    - vec_score：原始向量分（仅向量命中时有）
    - rrf_score：融合分
    """
    top_k = top_k or settings.kb_top_k

    # 两路召回各自隔离：一路失败降级为另一路，不拖垮整条检索（如 chroma 索引运行中损坏）
    vec: list[dict] = []
    try:
        vec = vector_svc.search(query, top_k=top_k, kinds=kinds, where=where)
    except Exception:
        vec = []
    for h in vec:
        h["from_vector"] = True
        h["from_fts"] = False
        h["vec_score"] = h["score"]

    fts: list[dict] = []
    try:
        fts = fts_svc.search(db, query, kinds=kinds, where=where, limit=top_k)
    except Exception:
        fts = []
    for h in fts:
        h["from_vector"] = False
        h["from_fts"] = True
        h["fts_score"] = h["score"]

    # 第三路：知识图谱实体召回（命中实体 → 扩展关联块）。失败/无实体时为空，不影响主链路。
    # 2026-10-27 P1：where 同口径传入（根治应用语境的库外泄漏）
    ent: list[dict] = []
    try:
        ent = _entity_hits(db, query, kinds=kinds, limit=top_k, where=where)
    except Exception:
        ent = []

    merged = _rrf_fuse([vec, fts, ent])
    # 每次检索现读笔记权重（默认 1.5），改动即时生效，无需重建索引
    note_w = float(settings_store.load().get("note_weight") or NOTE_WEIGHT)
    merged = _rerank(merged, note_w)
    for h in merged:
        h.pop("_rel", None)  # 清理重排中间量，仅内部使用
    return merged[:top_k]



def is_fragment(text: str) -> bool:
    """零信息量碎片块（2026-10-30 从 routers/chat.py 上收，判定只留这一份）。

    标签行/标题行这类 <50 字的小块，关键词密度高反而免阈值直进 top-3，把有内容的块挤出去；
    表格行（含 |）与列表项豁免 —— 短但有结构信息。
    ⚠️ 为什么上收：炼丹炉试聊要与正式对话**同口径**过滤，两处各写一份必然漂移。
    """
    t = (text or "").strip()
    if len(t) >= 50:
        return False
    if "|" in t:
        return False
    if t.startswith(("- ", "* ", "·", "1.")):
        return False
    return True
