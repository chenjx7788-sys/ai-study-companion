# -*- coding: utf-8 -*-
"""检索增强层探针（2026-10-28）：检索后评估器（改写重检）+ 两段式计划检索

验证项：
  U 级（in-process，monkeypatch llm.chat，确定性）：
  [U0] llm.DEFAULT_PROMPTS 含 prompt_query_rewrite / prompt_retrieval_plan（缺键 = get_prompt 直接 KeyError）
  [U1] plan_queries：合法 JSON ≥2 点 → 返回子查询列表
  [U2] plan_queries 回落族：LLM 抛错 / 非法 JSON / 单元素 → None（want==0 族，配 U1 作分辨力证明）
  [U3] rewrite_query：合法 → 新查询；与原句相同 / 空 / LLM 抛错 → None（同上配分辨力）
  [U4] needs_plan：多点问法 True；「什么是碳基搅拌器？」False（want==0 的反例守卫）
  [U5] merge_hits：去重保最优 rrf、按 top_k 截断；空进空出（配非空用例）
  [U6] retrieve() 编排（fakes）：计划逐点检索 / can_plan=False 不调计划器 / miss+改写重检 /
       can_rewrite=False 不改写 / 计划触发后 miss 不再改写（互斥）/ lock 与 augment 生效
  [U7] 防跑偏校验：改写/子查询与原问题零内容词重叠 → 丢弃（各配反向用例作分辨力）
  [U8] settings_store.FIELDS 已登记 2 开关 + 2 提示词（漏登记 = 保存静默丢弃）
  E 级（隔离实例，无 LLM key → 两条新链路必须静默降级、口径不变）：
  [E1] 基线：auto + 关键词问题 → kb 命中（证明检索在本环境能工作；后续判据的前提）
  [E2] kb + 无关问题 → 仍 kb_miss 且 kb_hit=False，sources 事件带 retrieval 字段且 rewrite=None
       （改写器降级不能把 miss 口径弄丢、不能 500）
  [E3] auto + 多点问题 → 仍 kb 命中，retrieval.plan=None（计划器降级 = 回落单查询，结果不变）；
       挂在 E1 基线上，基线不过记 BLOCKED
  [E4] E2 会话落库 assistant.answer_kind == 'kb_miss'（降级路径的落库口径不变）
  [E5] 开关持久化：PUT 关 → GET 为 false；PUT 开 → GET 为 true 且 prompt 键已暴露
       （验证 FIELDS 白名单登记端到端生效）

隔离实例：四个 ASC_* 一起重定向到 _tmp_probe_ragent_20261028/（不碰用户真实数据库）；
BGE 模型硬链接进隔离目录（缺了会联网下载卡死）；只杀自己 Popen 起来的 uvicorn。
⚠️ 环境变量必须在任何 app 导入之前设置（settings 在导入时定稿）。
"""
import json
import os
import pathlib
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request

WS = pathlib.Path(__file__).resolve().parents[3]  # tests/probes/x.py → 工作区根
BACKEND = WS / "ai-study-companion" / "backend"
TMP = WS / "_tmp_probe_ragent_20261028"
RESULTS: list[tuple[str, str, str]] = []


def report(rid: str, ok, detail: str = ""):
    status = "PASS" if ok is True else ("BLOCKED" if ok is None else "FAIL")
    RESULTS.append((rid, status, detail))
    print(f"[{status:7}] {rid}  {detail}")


# ---------------- 隔离环境（必须先于 app 导入） ----------------
TMP.mkdir(parents=True, exist_ok=True)
(TMP / "files").mkdir(exist_ok=True)
(TMP / "chroma").mkdir(exist_ok=True)
_db = TMP / "app.db"
if _db.exists():
    _db.unlink()
os.environ["ASC_DATA_DIR"] = str(TMP)
os.environ["ASC_FILES_DIR"] = str(TMP / "files")
os.environ["ASC_DB_URL"] = f"sqlite:///{TMP / 'app.db'}"
os.environ["ASC_CHROMA_DIR"] = str(TMP / "chroma")

sys.path.insert(0, str(BACKEND))
from app.services import llm as llm_svc  # noqa: E402
from app.services import retrieval_agent as ra  # noqa: E402

# ---------------- U0：提示词键登记 ----------------
# ⚠️ report 的 ok 必须是严格 bool（`is True` 判定）：and 链末端是 str 会得 truthy 字符串 → 假 FAIL。
report("U0", bool("prompt_query_rewrite" in llm_svc.DEFAULT_PROMPTS
       and "prompt_retrieval_plan" in llm_svc.DEFAULT_PROMPTS
       and llm_svc.DEFAULT_PROMPTS["prompt_query_rewrite"].strip()
       and llm_svc.DEFAULT_PROMPTS["prompt_retrieval_plan"].strip()),
       "两个新提示词键已登记且非空")

# ---------------- U1-U3：LLM 面函数（monkeypatch llm.chat） ----------------
_real_chat = llm_svc.chat


def _fake_chat_ok(msgs, **kw):
    return '["碳基搅拌器 构造原理", "液压花洒 工作方式"]'


llm_svc.chat = _fake_chat_ok
r = ra.plan_queries("碳基搅拌器和液压花洒有什么区别？")
report("U1", r == ["碳基搅拌器 构造原理", "液压花洒 工作方式"], f"plan={r}")


def _fake_chat_raise(msgs, **kw):
    raise RuntimeError("no key")


llm_svc.chat = _fake_chat_raise
report("U2a", ra.plan_queries("A和B的区别") is None, "LLM 抛错 → None")

llm_svc.chat = lambda msgs, **kw: "这不是 JSON"
report("U2b", ra.plan_queries("A和B的区别") is None, "非法输出 → None")

llm_svc.chat = lambda msgs, **kw: '["碳基搅拌器"]'
report("U2c", ra.plan_queries("什么是碳基搅拌器") is None, "单元素 → None（不拆）")

# 超上限截断 + 去重 + 非字符串剔除：6 项含 1 重复 1 数字 → 剩 4 且不超上限
# （子查询须与原问题共享内容词才过防跑偏校验，故词元以「多点问题」为根）
llm_svc.chat = lambda msgs, **kw: '["多点问题a","多点问题b","多点问题a",3,"多点问题c","多点问题d","多点问题e"]'
r = ra.plan_queries("多点问题")
report("U2d", r == ["多点问题a", "多点问题b", "多点问题c", "多点问题d"], f"clamp/dedupe → {r}")

llm_svc.chat = lambda msgs, **kw: "碳基搅拌器 工作原理"
r = ra.rewrite_query("碳基搅拌器是咋回事啊？")
report("U3a", r == "碳基搅拌器 工作原理", f"rewrite={r!r}")

llm_svc.chat = lambda msgs, **kw: "碳基搅拌器是咋回事啊？"
report("U3b", ra.rewrite_query("碳基搅拌器是咋回事啊？") is None, "与原句相同 → None（不重检）")

llm_svc.chat = _fake_chat_raise
report("U3c", ra.rewrite_query("碳基搅拌器是咋回事啊？") is None, "LLM 抛错 → None")

llm_svc.chat = lambda msgs, **kw: "某个问题的第一行\n第二行解释"
report("U3d", ra.rewrite_query("某个问题") == "某个问题的第一行", "多行输出只取首行（含共享词过校验）")

llm_svc.chat = _real_chat

# ---------------- U4：启发式门（want==0 配反例） ----------------
report("U4a", ra.needs_plan("碳基搅拌器和液压花洒有什么区别？") is True, "多点信号词 → True")
report("U4b", ra.needs_plan("什么是碳基搅拌器？") is False, "简单问法 → False（反例守卫）")
_long = "请帮我梳理一下索引构建和检索融合这两个环节各自的实现方式"
report("U4c", ra.needs_plan(_long) is True, f"长句+并列词 → True（len={len(_long)}）")
report("U4d", ra.needs_plan("猫和狗") is False, "短句含并列词 → False（长度门生效）")

# ---------------- U5：merge_hits ----------------
def _h(rt, rid, score):
    return {"ref_type": rt, "ref_id": rid, "rrf_score": score}


merged = ra.merge_hits([
    [_h("chunk", 1, 0.5), _h("chunk", 2, 0.3)],
    [_h("chunk", 1, 0.9), _h("note", 7, 0.4)],
], top_k=10)
report("U5a", [(h["ref_type"], h["ref_id"]) for h in merged] == [("chunk", 1), ("note", 7), ("chunk", 2)]
       and merged[0]["rrf_score"] == 0.9,
       f"去重保最优+降序 → {[(h['ref_type'], h['ref_id'], h['rrf_score']) for h in merged]}")
report("U5b", len(ra.merge_hits([[_h("chunk", i, 0.1 * i) for i in range(10)]], top_k=3)) == 3,
       "top_k 截断")
report("U5c", ra.merge_hits([], top_k=5) == [], "空进空出（U5a 作分辨力证明）")

# ---------------- U6：retrieve() 编排（fakes，确定性） ----------------
_real_plan_fn, _real_rewrite_fn = ra.plan_queries, ra.rewrite_query


def _h2(rid, score=0.1, ok=True):
    return {"ref_type": "chunk", "ref_id": rid, "rrf_score": score, "ok": ok}


def _boom_plan(q):
    raise AssertionError("plan_queries 不该被调用")


def _boom_rw(q):
    raise AssertionError("rewrite_query 不该被调用")


_MULTI_Q = "碳基搅拌器和液压花洒有什么区别？"   # 含「区别」，needs_plan 为真

# U6a 计划触发：逐点检索 + meta.plan + inject_limit 放宽
calls_a: list[str] = []
ra.plan_queries = lambda q: ["q甲", "q乙"]
r = ra.retrieve(_MULTI_Q, "碳基搅拌器 液压花洒 区别",
                do_search=lambda q: calls_a.append(q) or [_h2(q)], can_plan=True)
report("U6a", r["meta"]["plan"] == ["q甲", "q乙"] and r["inject_limit"] == ra.PLAN_INJECT_LIMIT
       and calls_a == ["q甲", "q乙"], f"calls={calls_a}, meta={r['meta']}")

# U6b can_plan=False：计划器绝不调用（被调即抛错 → 假绿守卫）
ra.plan_queries = _boom_plan
calls_b: list[str] = []
r = ra.retrieve(_MULTI_Q, "原查询",
                do_search=lambda q: calls_b.append(q) or [_h2(1)], can_plan=False)
report("U6b", calls_b == ["原查询"] and r["meta"]["plan"] is None, f"calls={calls_b}")

# U6c miss + can_rewrite：改写重检一次且命中（U6d 作反向分辨力）
ra.rewrite_query = lambda q: "改写后的查询"
seen_c: list[str] = []
r = ra.retrieve("问", "原查询",
                do_search=lambda q: seen_c.append(q) or [_h2(1, ok=(q == "改写后的查询"))],
                can_rewrite=True,
                quality_filter=lambda hs: [h for h in hs if h["ok"]])
report("U6c", seen_c == ["原查询", "改写后的查询"] and r["meta"]["rewrite"] == "改写后的查询"
       and len(r["filtered"]) == 1, f"seen={seen_c}, meta={r['meta']}")

# U6d miss + can_rewrite=False：改写器绝不调用，回落 miss
ra.rewrite_query = _boom_rw
r = ra.retrieve("问", "原查询", do_search=lambda q: [_h2(1, ok=False)], can_rewrite=False,
                quality_filter=lambda hs: [])
report("U6d", r["filtered"] == [] and r["meta"]["rewrite"] is None, "改写器未调用，回落 miss")

# U6e 计划触发后 miss 不再改写（互斥；改写器被调即抛错）
ra.plan_queries = lambda q: ["q甲", "q乙"]
r = ra.retrieve(_MULTI_Q, "x", do_search=lambda q: [_h2(1, ok=False)],
                can_plan=True, can_rewrite=True, quality_filter=lambda hs: [])
report("U6e", r["meta"]["plan"] == ["q甲", "q乙"] and r["meta"]["rewrite"] is None
       and r["filtered"] == [], f"meta={r['meta']}")

# U6f/U6g：lock 与 augment 注入生效
ra.plan_queries = _boom_plan
r = ra.retrieve("问", "原查询", do_search=lambda q: [_h2(1), _h2(2)], lock=lambda hs: hs[:1])
report("U6f", [h["ref_id"] for h in r["filtered"]] == [1], "lock 截断生效")
r = ra.retrieve("问", "原查询", do_search=lambda q: [_h2(1)], augment=lambda hs: [_h2(99)] + hs)
report("U6g", [h["ref_id"] for h in r["filtered"]] == [99, 1], "augment 保送队首")

ra.plan_queries, ra.rewrite_query = _real_plan_fn, _real_rewrite_fn

# ---------------- U7：防跑偏校验（真实校验 + 假 LLM） ----------------
llm_svc.chat = lambda msgs, **kw: "实验装置的一般原理"   # 与原问题零内容词重叠
report("U7a", ra.rewrite_query("碳基搅拌器怎么工作") is None, "跑偏改写 → None（不重检）")
llm_svc.chat = lambda msgs, **kw: "碳基搅拌器 工作原理"
report("U7b", ra.rewrite_query("碳基搅拌器怎么工作") == "碳基搅拌器 工作原理",
       "相关改写 → 保留（U7a 的分辨力证明）")
llm_svc.chat = lambda msgs, **kw: '["碳基搅拌器 构造", "美股熔断机制"]'
report("U7c", ra.plan_queries(_MULTI_Q) is None, "一个子查询跑偏被丢 → 剩 1 点 → None 回落")
llm_svc.chat = lambda msgs, **kw: '["碳基搅拌器 构造", "液压花洒 原理"]'
report("U7d", ra.plan_queries(_MULTI_Q) == ["碳基搅拌器 构造", "液压花洒 原理"],
       "两点都相关 → 保留（U7c 的分辨力证明）")
llm_svc.chat = _real_chat

# ---------------- U8：FIELDS 白名单登记 ----------------
from app.services import settings_store as ss  # noqa: E402

_missing = [k for k in ("query_rewrite_enabled", "retrieval_plan_enabled",
                        "prompt_query_rewrite", "prompt_retrieval_plan") if k not in ss.FIELDS]
report("U8", not _missing and len(ss.FIELDS) > 30, f"missing={_missing}（len 守卫={len(ss.FIELDS)}）")

# ---------------- E 级：隔离实例（无 LLM key，验证降级） ----------------
REAL_BGE = BACKEND / "data" / "models" / "bge-small-zh-v1.5"
ISO_BGE = TMP / "models" / "bge-small-zh-v1.5"
linked = 0
for src in REAL_BGE.rglob("*"):
    if not src.is_file():
        continue
    dst = ISO_BGE / src.relative_to(REAL_BGE)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        continue
    os.link(src, dst)
    linked += 1
print(f"[setup] BGE 硬链接 {linked} 个文件")

s = socket.socket(); s.bind(("127.0.0.1", 0)); PORT = s.getsockname()[1]; s.close()
env = dict(os.environ)
env["PYTHONIOENCODING"] = "utf-8"
# 隔离实例不带 LLM 配置：两条新链路必须静默降级（正是本探针 E 级要验的）
for k in ("OPENAI_API_KEY", "OPENAI_BASE_URL"):
    env.pop(k, None)

proc = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
    cwd=str(BACKEND), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
BASE = f"http://127.0.0.1:{PORT}/api"


def http_json(method: str, path: str, body=None, timeout=15):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with opener.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def ask_sources(question: str, mode: str):
    """发一次流式问答读到流结束（无 LLM key → 快速走 error 分支落库），返回 sources 事件载荷。"""
    body = {"question": question, "mode": mode}
    req = urllib.request.Request(BASE + "/chat/ask/stream",
                                 data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    resp = opener.open(req, timeout=30)
    buf = b""
    sources_payload, last_ev = None, ""
    try:
        while True:
            chunk = resp.read1(4096) if hasattr(resp, "read1") else resp.read(4096)
            if not chunk:
                break
            buf += chunk
            while b"\n\n" in buf:
                block, buf = buf.split(b"\n\n", 1)
                text = block.decode("utf-8", "replace")
                if text.startswith("event: sources"):
                    sources_payload = json.loads(text.split("data:", 1)[1])
                elif text.startswith("event:"):
                    last_ev = text.split("\n", 1)[0][6:].strip()
            if sources_payload and last_ev in ("done", "error"):
                break
    finally:
        resp.close()
    return sources_payload


try:
    ok = False
    for _ in range(90):
        try:
            http_json("GET", "/chat/sessions", timeout=2)
            ok = True
            break
        except Exception:
            time.sleep(1)
    if not ok:
        raise RuntimeError("隔离实例 90s 内未就绪")

    # 造数据：两条笔记（两个独立知识点，供多点问题）
    now = "2026-10-28 12:00:00"
    con = sqlite3.connect(str(TMP / "app.db"))
    con.execute("INSERT INTO materials (id, title, parsed_status, created_at) VALUES (1, '探针材料', 'success', ?)", (now,))
    con.execute(
        "INSERT INTO notes (title, content, material_id, created_at) VALUES (?, ?, 1, ?)",
        ("搅拌器笔记",
         "碳基搅拌器是一种假想的实验装置，用于演示检索链路是否正常工作，本身没有任何实际意义。", now))
    con.execute(
        "INSERT INTO notes (title, content, material_id, created_at) VALUES (?, ?, 1, ?)",
        ("花洒笔记",
         "液压花洒是一种假想的洗浴装置，依靠水压驱动喷淋，用于演示多点检索链路的合并行为。", now))
    con.commit(); con.close()

    HIT_Q = "什么是碳基搅拌器？"
    MISS_Q = "怎么给猫洗澡"
    MULTI_Q = "碳基搅拌器和液压花洒有什么区别？"

    # E1 基线：auto 命中
    p1 = ask_sources(HIT_Q, "auto")
    base_ok = bool(p1) and p1.get("answer_kind") == "kb" and p1.get("kb_hit")
    report("E1", base_ok,
           f"auto+关键词 → kind={p1 and p1.get('answer_kind')}, sources={len((p1 or {}).get('sources') or [])}")

    # E2 miss 口径不变 + retrieval 观测字段（rewrite 降级为 None）
    p2 = ask_sources(MISS_Q, "kb")
    ret2 = (p2 or {}).get("retrieval") or {}
    report("E2", bool(p2) and p2.get("answer_kind") == "kb_miss" and p2.get("kb_hit") is False
           and ret2.get("rewrite") is None and "rewrite" in ret2,
           f"kb+无关 → kind={p2 and p2.get('answer_kind')}, retrieval={ret2}")

    # E3 多点问题：计划器降级 → 回落单查询仍命中（挂 E1 基线）
    p3 = ask_sources(MULTI_Q, "auto")
    if not base_ok:
        report("E3", None, "基线 E1 未过，本项无法分辨（记 BLOCKED 而非 PASS）")
    else:
        ret3 = (p3 or {}).get("retrieval") or {}
        report("E3", bool(p3) and p3.get("answer_kind") == "kb" and p3.get("kb_hit") is True
               and ret3.get("plan") is None and "plan" in ret3,
               f"auto+多点 → kind={p3 and p3.get('answer_kind')}, retrieval={ret3}")

    # E4 落库口径（E2 会话）
    sid = (p2 or {}).get("session_id")
    if sid:
        msgs = http_json("GET", f"/chat/sessions/{sid}/messages")
        asst = [m for m in msgs if m.get("role") == "assistant"]
        got = asst[-1].get("answer_kind") if asst else None
        report("E4", got == "kb_miss", f"会话{sid} assistant.answer_kind={got}")
    else:
        report("E4", False, "E2 未拿到 session_id")

    # E5 开关持久化（FIELDS 白名单登记的端到端验证）
    http_json("PUT", "/settings", {"query_rewrite_enabled": False, "retrieval_plan_enabled": False})
    g = http_json("GET", "/settings")
    report("E5a", g.get("query_rewrite_enabled") is False and g.get("retrieval_plan_enabled") is False,
           f"关 → {g.get('query_rewrite_enabled')}, {g.get('retrieval_plan_enabled')}")
    http_json("PUT", "/settings", {"query_rewrite_enabled": True, "retrieval_plan_enabled": True})
    g2 = http_json("GET", "/settings")
    report("E5b", g2.get("query_rewrite_enabled") is True and g2.get("retrieval_plan_enabled") is True
           and "prompt_query_rewrite" in g2 and "prompt_retrieval_plan" in g2,
           "开 → True 且两个 prompt 覆盖键已在 GET 暴露")

finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()

fails = [r for r in RESULTS if r[1] == "FAIL"]
blocked = [r for r in RESULTS if r[1] == "BLOCKED"]
print(f"\n== 汇总：{len(RESULTS)} 项，PASS {sum(1 for r in RESULTS if r[1]=='PASS')}，"
      f"FAIL {len(fails)}，BLOCKED {len(blocked)} ==")
print(f"隔离实例临时目录（可手动删）：{TMP}")
sys.exit(1 if fails else 0)
