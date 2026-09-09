"""全文检索：SQLite LIKE 子串匹配，覆盖精确词 / 术语 / 人名 / 型号。

与向量检索（语义相似度）互补：
- 向量：语义 / 概念关联
- 全文：精确命中（2 字中文词、英文缩写、产品型号、数字）

选型理由（实测，2026-09-07）：FTS5 trigram 对 2 字中文词失效（`MATCH '费曼'` 空），
而中文人名/术语大量是 2 字；LIKE 零依赖、零体积，且数据量小（知识库实测 195 块，
5000 块模拟 LIKE < 4ms），无性能压力。
"""
import re

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..models import MaterialChunk, Note

# 查询词切分：空白 + 中文标点 + 破折号（连字符 - 保留，保证型号 ABC-123 整体精确匹配）
_KEY_SPLIT_RE = re.compile(r"[\s，。！？；、,;:：/\\—_·()（）\[\]【】\"'“”‘’]+")

# 子串级停用词：仅疑问词（几乎不会作为实词组成部分，可安全从 query 中剔除）
# 长词优先，避免"什么是"被"什么"先删而残留"是"
_SUBSTR_STOPWORDS = sorted([
    "是什么", "为什么", "什么是", "什么关系", "怎么做", "怎么", "如何", "哪些", "哪个",
], key=len, reverse=True)

# 整词级停用词：单字虚词 + 语气词 + 祈使动词
# 仅在整个 token 相等时删除（不做子串删除，避免误伤"解释器/产品介绍/区别对待"里的实词）
_WORD_STOPWORDS = {
    "的", "了", "在", "是", "有", "和", "与", "及", "或", "请",
    "吗", "呢", "啊", "吧", "什么", "哪",
    "讲讲", "说说", "解释", "介绍", "关于", "区别", "请问", "帮我", "给我", "告诉我", "介绍一下",
}


def _keywords(query: str) -> list[str]:
    """自然语言问题 → 关键词：先删子串级停用词，再按标点切分，最后滤整词虚词（去重保序）。

    中文不引入 jieba（保零依赖），对自然语言长句切分不保证完美，
    切不准的交给向量检索兜底——全文搜索的核心价值在「明确关键词」场景
    （专有名词 / 型号 / 人名 / 术语）。
    """
    q = query.strip()
    for sw in _SUBSTR_STOPWORDS:
        if sw in q:
            q = q.replace(sw, " ")
    seen: set[str] = set()
    out: list[str] = []
    for raw in _KEY_SPLIT_RE.split(q):
        k = raw.strip()
        if not k or k in _WORD_STOPWORDS or k in seen:
            continue
        seen.add(k)
        out.append(k)
    return out


def _like(pattern: str) -> str:
    """转义 LIKE 通配符 %、_ 与转义符本身，生成子串匹配模式"""
    return "%" + pattern.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _hit(ref_type: str, ref_id: int, material_id: int | None, text: str,
         metadata: dict, cnt: int) -> dict:
    return {
        "ref_type": ref_type,
        "ref_id": ref_id,
        "material_id": material_id,
        "text": text,
        "metadata": metadata,
        "score": float(cnt),   # 命中词数，RRF 融合时只看名次
        "from_fts": True,
        "from_vector": False,
    }


def search(db: Session, query: str, kinds=("chunk", "note"),
           where: dict | None = None, limit: int = 8) -> list[dict]:
    """全文检索，返回与 vector.search 同构的 hits（按命中词数降序）。

    where 兼容 chroma 格式：{"material_id": {"$in": [...]}} / {"ref_id": {"$in": [...]}}
    """
    kws = _keywords(query)
    if not kws:
        return []

    material_ids: set[int] | None = None
    note_ids: set[int] | None = None
    if where:
        if "material_id" in where and "$in" in where["material_id"]:
            material_ids = set(where["material_id"]["$in"])
        if "ref_id" in where and "$in" in where["ref_id"]:
            note_ids = set(where["ref_id"]["$in"])

    hits: list[dict] = []

    if "chunk" in kinds:
        conds = [MaterialChunk.content.like(_like(k), escape="\\") for k in kws]
        q = (db.query(MaterialChunk)
             .filter(MaterialChunk.content != "", or_(*conds)))
        if material_ids is not None:
            q = q.filter(MaterialChunk.material_id.in_(material_ids))
        for c in q.all():
            cnt = sum(1 for k in kws if k in (c.content or ""))
            title = c.material.title if c.material else ""
            hits.append(_hit("chunk", c.id, c.material_id, c.content, {
                "ref_type": "chunk", "ref_id": c.id, "material_id": c.material_id,
                "material_title": title, "page_no": c.page_no,
                "section_path": c.section_path or "",
            }, cnt))

    if "note" in kinds:
        conds = []
        for k in kws:
            conds.append(Note.title.like(_like(k), escape="\\"))
            conds.append(Note.content.like(_like(k), escape="\\"))
        q = db.query(Note).filter(or_(*conds))
        if note_ids is not None:
            q = q.filter(Note.id.in_(note_ids))
        for n in q.all():
            text = f"{n.title}\n{n.content or ''}".strip()
            cnt = sum(1 for k in kws if k in text)
            title = n.material.title if n.material else ""
            hits.append(_hit("note", n.id, n.material_id, text, {
                "ref_type": "note", "ref_id": n.id, "material_id": n.material_id,
                "note_title": n.title, "material_title": title,
                "page_no": (n.anchor or {}).get("page_no", 0),
            }, cnt))

    hits.sort(key=lambda h: (-h["score"], h["ref_id"]))
    return hits[:limit]
