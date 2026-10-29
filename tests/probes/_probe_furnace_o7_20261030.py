# -*- coding: utf-8 -*-
"""O7 验收探针（2026-10-30）：试聊预览与正式对话的检索口径是否真的一致

背景：预览原走 search_locked + filter_hits（无改写重检 / 无碎片过滤 / 无注入上限），
正式走 retrieval_agent.retrieve。同一个问题会出现「试聊拒答、正式能答」，
用户会误判成人格或边界配错。本探针三段：

  H0 静态契约：apps.py 不再直接 filter_hits(search_locked ...)；预览确实调 retrieve
  H1 分辨力证明：**库内**同题对比「旧路径 vs 新路径」的 kb_hit，必须能找出不同项，
                 否则说明这轮改动对结果毫无影响（那 H2 全绿也是白绿）
  H2 两侧一致：同一批问题，预览 kb_hit == 正式 kb_hit（正式用 mode=kb，避免通用兜底噪声）
  H3 内容断言：命中时答案必须含资料里的具体事实（只验 kb_hit 会放过「命中却答不出」）

⚠️ 前置：本机强杀 uvicorn 会触发 chroma 全量重建（约 2 分钟），重建期间向量检索静默降级
   为纯全文 —— 那段时间跑 H1/H2 得到的是假结果。故先等 from_vector 恢复。
"""
import json
import pathlib
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8000"
WS = pathlib.Path(__file__).resolve().parents[3]   # tests/probes/x.py → 工作区根
BACKEND = WS / "ai-study-companion" / "backend"
APP_ID = 1
# ⚠️ 比对必须两侧同一批资料：正式对话走「应用自己绑定的资料」，预览若固定传别处的 id
#    就成了苹果比橘子（第一版这么比，结论全错）。ids 运行时从 /api/apps/{id} 读。
IDS = []
FACT = "ReAct"
RESULTS = []

# (问题, 期望命中, 命中时必须出现的事实锚点)
QUESTIONS = [
    ("AI Agent 的五种常见设计模式有哪些？", True, "ReAct"),
    ("RAG 检索里多路结果怎么融合排序？", True, "RRF"),
    ("今天上证指数收盘多少点？", False, None),
]

_op = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def report(rid, ok, detail=""):
    RESULTS.append((rid, "PASS" if ok else "FAIL", str(detail)))
    print(("[PASS] " if ok else "[FAIL] ") + rid + "  " + str(detail)[:200], flush=True)


def skip(rid, why):
    RESULTS.append((rid, "SKIP", why))
    print("[SKIP] " + rid + "  " + why, flush=True)


def http_json(method, path, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with _op.open(req, timeout=timeout) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def sse(path, body, timeout=300):
    data = json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    events, cur = [], ""
    with _op.open(req, timeout=timeout) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line.startswith("event: "):
                cur = line[7:]
            elif line.startswith("data: "):
                try:
                    events.append((cur, json.loads(line[6:])))
                except Exception:
                    pass
    return events


def preview(q, ids=None, history=None):
    body = {"question": q, "material_ids": list(ids or IDS)}
    if history:
        body["history"] = history
    evs = sse("/api/apps/preview/stream", body)
    hit = next((p.get("kb_hit") for e, p in evs if e == "meta"), None)
    ans = "".join(p.get("t", "") for e, p in evs if e == "token")
    err = next((p for e, p in evs if e == "error"), None)
    return {"kb_hit": hit, "answer": ans, "error": err, "events": [e for e, _ in evs]}


def formal(q, session_id=None):
    body = {"question": q, "app_id": APP_ID, "mode": "kb"}
    if session_id:
        body["session_id"] = session_id
    evs = sse("/api/chat/ask/stream", body)
    src = next((p for e, p in evs if e == "sources"), {}) or {}
    ans = "".join(p.get("t", "") for e, p in evs if e == "token")
    return {"kb_hit": src.get("kb_hit"), "kind": src.get("answer_kind") or "",
            "answer": ans, "session_id": src.get("session_id"), "error": src.get("error")}


# ---------------- H0 静态契约 ----------------
def check_static():
    rtr = (BACKEND / "app/services/apps.py").read_text(encoding="utf-8")
    chat = (BACKEND / "app/routers/chat.py").read_text(encoding="utf-8")
    srch = (BACKEND / "app/services/search.py").read_text(encoding="utf-8")
    bad = []
    if "filter_hits(search_locked" in rtr:
        bad.append("apps.py 仍在用旧链路 filter_hits(search_locked)")
    if "retrieval_agent_svc.retrieve(" not in rtr:
        bad.append("apps.py 未调用 retrieval_agent.retrieve")
    if rtr.count("_preview_retrieve(") != 3:
        bad.append(f"_preview_retrieve 调用点={rtr.count('_preview_retrieve(')}（预期 3：定义1+调用2）")
    if "return search_svc.is_fragment(text)" not in chat:
        bad.append("chat.py 的 _is_fragment 未转发到 search.is_fragment")
    if "def is_fragment" not in srch:
        bad.append("search.py 缺 is_fragment")
    report("H0 静态契约（预览复用 retrieve + 碎片判定单一口径）", not bad, "；".join(bad) or "全部满足")


# ---------------- H1 新旧路径分辨力（离线单元级，绝不触碰 chroma）----------------
def check_delta():
    """证明「新路径能做到旧路径做不到的事」，而不是靠真库跑一遍。

    ⚠️⚠️ 第一版用「进程内 import + 真检索」对比，结果自己把服务端索引搞挂了：
       另一个进程打开 Chroma 会判集合损坏并**删除重建**，而 uvicorn 正握着同一个目录
       → 服务端索引被判 corruption、重建中断，随后 H2/H3 全红（是我造的，不是代码问题）。
       结论：跨进程只能走 HTTP。这里全程把 search_svc.search 换成假函数，改写函数换成
       罐头值，既不碰 chroma 也不烧 LLM。
    """
    sys.path.insert(0, str(BACKEND))
    try:
        from app.services import apps as apps_svc
        from app.services import retrieval_agent as ra_svc
        from app.services import search as search_svc
    except Exception as e:
        skip("H1 新旧路径差异（证明改动可见）", f"导入后端失败：{type(e).__name__}: {e}")
        return

    Q = "这些东西怎么选"
    SOLID = {"ref_type": "chunk", "ref_id": 1, "material_id": IDS[0],
             "text": "《政治学十五讲》由燕继荣著，是政治学入门教材，全书共十五讲。" * 2,
             "from_fts": True, "score": 1.0, "metadata": {"material_title": "政治学十五讲"}}

    calls = {"n": 0}

    def fake_search(q, db=None, **kw):
        calls["n"] += 1
        # 只有「改写后」的查询才召回：原句故意召回空
        return [dict(SOLID)] if Q not in q else []

    orig_search, orig_rewrite, orig_plan = search_svc.search, ra_svc.rewrite_query, ra_svc.plan_queries
    search_svc.search = fake_search
    ra_svc.rewrite_query = lambda q: "燕继荣 政治学十五讲 作者"
    ra_svc.plan_queries = lambda q: None          # 关掉计划模式，只留改写重检这条差异
    try:
        old_hits = apps_svc.filter_hits(apps_svc.search_locked(Q, None, IDS, top_k=5))
        new_hits = apps_svc._preview_retrieve(None, IDS, Q)
    finally:
        search_svc.search = orig_search
        ra_svc.rewrite_query = orig_rewrite
        ra_svc.plan_queries = orig_plan

    old_hit, new_hit = bool(old_hits), bool(new_hits)
    print(f"      · 离线构造：原句召回为空、改写句有货 → 旧={old_hit} 新={new_hit}（search 被调 {calls['n']} 次）")
    report("H1 新旧路径差异（改写重检确实接上了）",
           (old_hit is False) and (new_hit is True),
           f"旧路径 kb_hit={old_hit}（无改写）新路径 kb_hit={new_hit}（改写后命中）")


# ---------------- 主流程 ----------------
def wait_vector_ready(limit=240):
    t0 = time.time()
    while time.time() - t0 < limit:
        try:
            hits = http_json("POST", "/api/kb/search", {"query": "政治学十五讲 作者"}, timeout=60)
            if any(h.get("from_vector") for h in hits):
                return True
        except Exception:
            pass
        time.sleep(5)
    return False


def main():
    global IDS
    print("等向量索引可用（强杀重启后会全量重建）…", flush=True)
    if not wait_vector_ready():
        print("向量检索在 240s 内没恢复 —— 现在跑出来的口径对比不可信，先停")
        sys.exit(2)
    app = http_json("GET", f"/api/apps/{APP_ID}")
    IDS = app.get("material_ids") or []
    if not IDS:
        print(f"应用 {APP_ID} 没有绑定资料，无法比对")
        sys.exit(2)
    print(f"向量索引可用；应用「{app.get('name')}」绑定资料 {IDS}\n", flush=True)

    check_static()
    check_delta()

    sid = None
    pairs = []
    for q, expect_hit, fact in QUESTIONS:
        pv = preview(q)
        fm = formal(q, sid)
        sid = fm.get("session_id") or sid
        pairs.append((q, expect_hit, pv, fm, fact))
        print(f"      · {q}\n          预览 kb_hit={pv['kb_hit']}  正式 kb_hit={fm['kb_hit']} kind={fm['kind']!r}",
              flush=True)

    same = [q for q, _, pv, fm, _f in pairs if pv["kb_hit"] == fm["kb_hit"]]
    bad = [q for q, _, pv, fm, _f in pairs if pv["kb_hit"] != fm["kb_hit"]]
    report("H2 预览与正式 kb_hit 一致", len(same) == len(pairs),
           f"{len(same)}/{len(pairs)} 一致" + ("" if not bad else f"，不一致：{bad}"))

    # 命中题逐题验事实（只验 kb_hit 会放过「命中却答不出」——上一轮就是栽在这）
    fact_rows = []
    for q, expect, pv, fm, fact in pairs:
        if not expect or not fact:
            continue
        ok_pv, ok_fm = fact in (pv["answer"] or ""), fact in (fm["answer"] or "")
        fact_rows.append((fact, ok_pv, ok_fm))
    report("H3 命中题两侧答案都含资料事实",
           bool(fact_rows) and all(a and b for _f, a, b in fact_rows),
           "；".join(f"{f}: 预览={a} 正式={b}" for f, a, b in fact_rows) or "无用例题")

    q3, _, pv3, fm3, _ = pairs[2]
    refused = pv3["kb_hit"] is False and fm3["kb_hit"] is False
    report("H4 书外问题两侧都不命中（边界不被改写试穿）", refused,
           f"预览 kb_hit={pv3['kb_hit']} 正式 kb_hit={fm3['kb_hit']} kind={fm3['kind']!r}")

    errs = [q for q, _, pv, fm, _f in pairs if pv.get("error") or fm.get("error")]
    report("H5 两侧流均无 error 事件", not errs, f"出错题：{errs}" if errs else "无")

    n_pass = sum(1 for _, s, _ in RESULTS if s == "PASS")
    n_fail = sum(1 for _, s, _ in RESULTS if s == "FAIL")
    n_skip = sum(1 for _, s, _ in RESULTS if s == "SKIP")
    print("\n" + "=" * 60)
    print(f"总计 {len(RESULTS)} 项：PASS {n_pass} / FAIL {n_fail} / SKIP {n_skip}")
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
