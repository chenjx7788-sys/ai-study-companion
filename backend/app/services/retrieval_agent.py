"""检索增强的轻量 Agentic 层（2026-10-28）：检索后评估改写 + 两段式计划检索。

两条链路都只在「值得」时触发，且全部带静默回落（LLM 不可用/解析失败 → 原路径，
口径与旧链路逐字一致）：

1. 检索后评估器（query rewriting loop）
   触发：阈值过滤后 filtered 为空（本来要 miss）。
   动作：让模型把问题改写成更适合检索的查询，重检一次（最多一次）。
   取舍：只救 miss ——「命中但质量低」没有可靠判据，给命中路径加一次 LLM 调用是纯成本。

2. 两段式计划检索（plan-then-retrieve）
   触发：主问答 + content 意图 + 启发式门（needs_plan）通过。
   动作：先让模型输出「这个问题需要查哪几个点」（JSON 数组，1-4 个），
        ≥2 个才逐点检索、合并去重；只返回 1 个 = 模型判断无需拆分 → 回落单查询。
   取舍：启发式门只用于省 LLM 调用，不是正确性判据 —— 误放行的代价是一次
        返回 [原问题] 的计划调用，行为与旧链路一致。

编排入口 retrieve()（2026-10-28 二轮）：计划/检索/锁/补强/过滤/改写的装配集中在此，
chat.py 只注入判据闭包（怎么搜/怎么锁/怎么滤/怎么补强）——策略可脱离 HTTP 层单测，
将来加第三件（自我评估环）只动本文件。

防跑偏校验（同日二轮）：改写结果与每个子查询都必须与原问题共享至少一个内容词
（≥2 字，复用全文检索的关键词口径），否则丢弃——挡住「碳基搅拌器→实验装置」这类
泛化跑偏的主模式。词面对得上但意图偏的情况挡不住，靠 sources 事件的 retrieval
观测字段事后发现。
"""
import json
import re

from . import fts as fts_svc
from . import llm

PLAN_MAX_QUERIES = 4        # 子查询上限（防模型拆分失控）
PLAN_PER_QUERY_TOP_K = 4    # 每个子查询的召回条数
PLAN_INJECT_LIMIT = 5       # 计划模式下注入上下文/来源卡片条数（普通路径恒 3）

# 多点问题的信号词。门只挡成本：误放行 → 计划器返回 [原问题] → 回落，行为不变。
_PLAN_HINTS = ("对比", "比较", "区别", "差异", "异同", "分别", "各自", "优缺点",
               "优点和缺点", "；", ";")
_PLAN_CONJ = ("以及", "和", "与")


def needs_plan(question: str) -> bool:
    """启发式门：这个问题像不像「要查多个点」。仅在主问答 content 意图下被调用。"""
    q = question.strip()
    if any(h in q for h in _PLAN_HINTS):
        return True
    return len(q) >= 24 and any(c in q for c in _PLAN_CONJ)


def _content_terms(text: str) -> list[str]:
    """内容词元（≥2 字）：复用全文检索的关键词切分口径，防跑偏校验与检索判据不漂移。"""
    return [k for k in fts_svc._keywords(text or "") if len(k) >= 2]


def _shares_content_word(query: str, question: str) -> bool:
    """query 是否与原问题共享内容词：两侧词元两两做「短词是长词的子串」匹配。

    为什么不是「问题词元 in query」：无分词器时整句是单个长词元
    （「碳基搅拌器和液压花洒有什么区别」不切分），改写/拆点后的短查询永远匹配不上；
    双向子串匹配让「碳基搅拌器」这类子词元能命中原问题的长词元。
    软校验天性是误放行（如「工作」命中「工作方式」），只挡泛化跑偏的主模式，
    挡不住的靠 sources 事件的 retrieval 观测字段事后发现。原问题提不出词元时放行。
    """
    q_terms = _content_terms(question)
    if not q_terms:
        return True
    for a in q_terms:
        for b in _content_terms(query):
            shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
            if shorter in longer:
                return True
    return False


def _parse_query_list(raw: str) -> list[str]:
    """把模型输出解析成子查询列表；任何不规范都收紧成 []（调用方回落单查询）。"""
    m = re.search(r"\[[\s\S]*\]", raw or "")
    if not m:
        return []
    try:
        items = json.loads(m.group(0))
    except (ValueError, TypeError):
        return []
    if not isinstance(items, list):
        return []
    out: list[str] = []
    for it in items:
        if not isinstance(it, str):
            continue
        s = it.strip().strip('"“”')[:60]
        if s and s not in out:
            out.append(s)
    return out[:PLAN_MAX_QUERIES]


def plan_queries(question: str) -> list[str] | None:
    """两段式第一段：输出「这个问题需要查哪些点」。

    返回 None = 计划失败（LLM 不可用/解析失败/只拆出 1 点/拆点全部跑偏）
    → 调用方回落单查询；只有返回 ≥2 个不同且与原问题相关的子查询时才逐点检索。
    """
    try:
        raw = llm.chat([
            {"role": "system", "content": llm.get_prompt("prompt_retrieval_plan")},
            {"role": "user", "content": question},
        ], kind="retrieval_plan")
    except Exception:
        return None
    qs = _parse_query_list(raw)
    # 防跑偏校验：与原问题零内容词重叠的子查询直接丢弃；丢完不足 2 点 = 回落
    qs = [s for s in qs if _shares_content_word(s, question)]
    if len(qs) < 2:
        return None
    return qs


def rewrite_query(question: str) -> str | None:
    """检索后评估器的改写步：返回改写后的查询；失败/为空/与原句相同/跑偏 → None（不重检）。

    ⚠️ 最多重检一次的纪律在编排层（retrieve）；这里只管产出改写结果。
    """
    try:
        raw = llm.chat([
            {"role": "system", "content": llm.get_prompt("prompt_query_rewrite")},
            {"role": "user", "content": question},
        ], kind="query_rewrite")
    except Exception:
        return None
    q = (raw or "").strip().strip('"“”')
    q = q.splitlines()[0].strip() if q else ""
    if not q or len(q) > 80 or q == question.strip():
        return None
    if not _shares_content_word(q, question):
        return None
    return q


def merge_hits(lists: list[list[dict]], top_k: int) -> list[dict]:
    """多点检索结果合并：按 (ref_type, ref_id) 去重保最优分，按 rrf_score 降序截断。

    ⚠️ 各路 rrf_score 来自不同查询的名次，只作合并排序的近似依据；
       下游阈值过滤看的是 from_fts / from_entity / vec_score，与本步无关。
    """
    best: dict[tuple, dict] = {}
    for lst in lists:
        for h in lst:
            key = (h["ref_type"], h["ref_id"])
            cur = best.get(key)
            if cur is None or (h.get("rrf_score") or 0.0) > (cur.get("rrf_score") or 0.0):
                best[key] = h
    merged = sorted(best.values(), key=lambda h: -(h.get("rrf_score") or 0.0))
    return merged[:top_k]


def retrieve(question: str, search_q: str, *, do_search, lock=None, quality_filter=None,
             augment=None, can_plan: bool = False, can_rewrite: bool = False,
             top_k: int = 8) -> dict:
    """检索编排唯一入口：计划 → 检索 → 范围锁 → 补强 → 质量过滤 →（miss 时）改写重检。

    参数（全部关键字；判据由调用方注入，本层只做策略装配）：
    - do_search(q) -> hits：原始召回（chat 侧含 folder 范围展开）
    - lock(hits) -> hits：范围锁（炼丹炉材料白名单；None = 不锁）
    - quality_filter(hits) -> filtered：质量门（阈值+碎片过滤；specified 强意图传恒等）
    - augment(hits) -> hits：候选补强（枚举意图标题保送；None = 无补强）
    - can_plan / can_rewrite：策略开关（调用方已算好意图门与设置开关）

    返回 {"filtered", "meta": {"rewrite", "plan"}, "inject_limit"}。
    互斥纪律：计划触发后即使 miss 也不再改写（问题已被拆点，改写整句收益低、延迟翻倍）。
    """
    def _id(hs):
        return hs
    lock = lock if lock is not None else _id
    quality_filter = quality_filter if quality_filter is not None else _id

    meta = {"rewrite": None, "plan": None}
    inject_limit = 3

    sub = plan_queries(question) if (can_plan and needs_plan(question)) else None
    if sub:
        hits = merge_hits([do_search(sq) for sq in sub], top_k)
        meta["plan"] = sub
        inject_limit = PLAN_INJECT_LIMIT
    else:
        hits = do_search(search_q)
    hits = lock(hits)
    if augment is not None:
        hits = augment(hits)

    filtered = quality_filter(hits)
    if not filtered and meta["plan"] is None and can_rewrite:
        rq = rewrite_query(question)
        if rq:
            f2 = quality_filter(lock(do_search(rq)))
            if f2:
                filtered = f2
                meta["rewrite"] = rq
    return {"filtered": filtered, "meta": meta, "inject_limit": inject_limit}
