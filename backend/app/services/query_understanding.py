"""查询理解前置层（2026-10-23，AI 问答 P0 族修复）：检索前的意图分类与指代解析。

背景：检索前没有查询理解，导致一族实测 bug——
- 概览/统计问法（"资料库有多少份文件"）被当内容问题走向量检索，LLM 拿 3 块碎片如实答"3 份"；
- 指代问法（"材料51和材料50"）正文无此词，检索永远匹配不到界面编号；
- 意图词（"讲了什么"）被 FTS 当内容词，命中同名笔记排第一。

设计原则：
- 规则实现，不调 LLM——零成本零延迟；
- 按优先级分流，全部落空走原检索路径（兜底不更差）；
- 意图词剥离保守：剥离后剩余内容词 < 4 字则放弃剥离。
"""
import re
from dataclasses import dataclass, field

_KB_WORDS = ("知识库", "资料库")
_KB_OBJECTS = ("资料", "材料", "笔记", "文件")
_STATS_WORDS = ("多少", "几份", "几个", "几条", "几", "数量", "总共", "共计", "多吗")
_ENUM_WORDS = ("哪些", "哪几", "有什么", "有哪些", "都有什么")
_OVERVIEW_HINTS = ("有什么", "有哪些", "包含", "目录", "内容", "学了什么", "都有什么", "收录", "讲了什么")

# 意图词（剥离用）：长的在前，先替长的再替短的
_STRIP_WORDS = (
    "讲了什么", "讲的是什么", "是什么", "有什么", "有哪些", "都有什么", "哪些", "哪几",
    "多少", "几份", "几个", "几条", "数量", "总共", "共计",
    "知识库", "资料库", "分别", "介绍一下", "介绍", "说说", "告诉我", "讲讲", "讲了", "讲",
    "文件", "资料", "材料", "笔记",
    "里面", "里", "中", "的", "了", "吗", "呢", "，", "？", "?", ",", "、", " ",
)


@dataclass
class QueryPlan:
    intent: str = "content"               # reference / stats / enumerate / overview / content
    material_ids: list[int] = field(default_factory=list)  # reference 命中的材料 id
    meta_text: str = ""                   # stats / enumerate 注入文本
    search_query: str = ""                # 剥离意图词后的检索词（空 = 用原句）
    enum_material_ids: list[int] = field(default_factory=list)  # enumerate 标题命中的材料（保送用）


def _strip_intent_words(q: str) -> str:
    s = q
    for w in _STRIP_WORDS:
        s = s.replace(w, " ")
    return re.sub(r"\s+", " ", s).strip()


def _resolve_material_refs(q: str, db) -> list[int]:
    """识别「材料N」（排除「页/条/份/个」等量词后缀）与《书名号》标题，返回存在的材料 id（去重保序）"""
    from ..models import Material
    ids: list[int] = []
    # (?!\d) 防回溯：「材料50页」若只允许 \d+ 匹配成 5 就糟了——先禁跟数字，再禁跟量词
    for m in re.finditer(r"材料\s*(\d{1,4})(?!\d)(?!\s*[页条份个张本])", q):
        ids.append(int(m.group(1)))
    for m in re.finditer(r"《([^《》]{2,40})》", q):
        t = m.group(1)
        exact = db.query(Material).filter(Material.title == t).all()
        if exact:
            ids.extend(x.id for x in exact)
        else:
            # 模糊命中：标题包含书名号文本，上限 3 份（2026-10-23 用户拍板：多份全注入）
            fuzzy = (db.query(Material).filter(Material.title.contains(t))
                     .order_by(Material.id.desc()).limit(3).all())
            ids.extend(x.id for x in fuzzy)
    return [i for i in dict.fromkeys(ids) if db.get(Material, i) is not None]


def _build_stats_text(db) -> str:
    from ..models import Material, Note
    m_ok = db.query(Material).filter(Material.parsed_status == "success").count()
    m_all = db.query(Material).count()
    n_notes = db.query(Note).count()
    lines = [f"知识库当前共收录资料 {m_all} 份（其中解析完成可检索 {m_ok} 份）、笔记 {n_notes} 条。"]
    return "\n".join(lines)


def _build_enum_text(db, keyword: str) -> tuple[str, list[int]]:
    from ..models import Material
    if not keyword:
        return "", []
    # 按词元 OR 匹配标题（剥离后可能是「藏书馆vip方案 藏书馆」多个词元）
    tokens = [t for t in keyword.split() if len(t) >= 2]
    if not tokens:
        return "", []
    from sqlalchemy import or_
    cond = or_(*[Material.title.contains(t) for t in tokens])
    matches = db.query(Material).filter(cond).order_by(Material.id).limit(20).all()
    if not matches:
        return "", []
    lines = [f"标题与「{keyword}」相关的材料共 {len(matches)} 份："]
    lines += [f"- 材料{m.id}《{m.title}》" for m in matches]
    return "\n".join(lines), [m.id for m in matches]


def analyze_query(question: str, db) -> QueryPlan:
    """意图分类主入口。调用方仅在 scope=all 且未显式指定条目时使用本结果。"""
    q = question.strip()
    plan = QueryPlan(search_query=q)

    # 1. 指代（最强意图）：「材料N」/《标题》→ 直接定位材料
    ref_ids = _resolve_material_refs(q, db)
    if ref_ids:
        plan.intent = "reference"
        plan.material_ids = ref_ids
        return plan

    has_kb = any(w in q for w in _KB_WORDS) or any(w in q for w in _KB_OBJECTS)
    stripped = _strip_intent_words(q)

    # 2. 统计：知识库词 + 数量词（限长问句，防误伤）
    if has_kb and any(w in q for w in _STATS_WORDS) and len(q) <= 30:
        plan.intent = "stats"
        plan.meta_text = _build_stats_text(db)
        return plan

    # 3. 枚举：列举词 + 对象词（"知识库有哪些文件讲了藏书馆"）
    if any(w in q for w in _ENUM_WORDS) and any(o in q for o in _KB_OBJECTS):
        plan.intent = "enumerate"
        plan.meta_text, plan.enum_material_ids = _build_enum_text(db, stripped)
        plan.search_query = stripped if len(stripped) >= 2 else q
        return plan

    # 4. 概览：原词表逻辑（保守，前置到检索之前）
    if len(q) <= 20 and has_kb and any(k in q for k in _OVERVIEW_HINTS):
        plan.intent = "overview"
        return plan

    # 5. 内容问答：剥离意图词（保守：剩余 < 4 字放弃剥离）
    plan.intent = "content"
    plan.search_query = stripped if len(stripped) >= 4 else q
    return plan
