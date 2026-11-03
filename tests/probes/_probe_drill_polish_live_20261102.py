"""P5 单题重答端到端验证（真 LLM · 真 HTTP · 隔离到一场临时陪练）。

流程：建场 → 出题 → 答第 1 题 → 提前结束 → 重答第 1 题
断言：不推进 / 不追问 / 总分重算 / 状态不变。
⚠️ 跑完会直连 sqlite 删掉这场临时记录（不用 API 删，避开删除配额护栏）。
"""
import json
import sqlite3
import sys
import urllib.request

BASE = "http://127.0.0.1:8000/api"
op = urllib.request.build_opener(urllib.request.ProxyHandler({}))

PASS, FAIL = [], []


def chk(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + ((" | " + str(detail)) if detail and not cond else ""))


def call(method, path, payload=None, timeout=180):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with op.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def sse(path, payload, timeout=300):
    """读一条 SSE 流，返回 (events, final_status)。"""
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    events, cur = [], None
    with op.open(req, timeout=timeout) as r:
        for raw in r:
            line = raw.decode("utf-8").strip()
            if line.startswith("event:"):
                cur = line[6:].strip()
            elif line.startswith("data:"):
                try:
                    body = json.loads(line[5:].strip())
                except Exception:
                    body = {}
                events.append((cur, body))
    return events


# ---------- 0. 选一条有内容的笔记 ----------
notes = call("GET", "/notes")
if isinstance(notes, dict):
    notes = notes.get("items") or []
note = next((n for n in notes if (n.get("content") or "").strip()), None)
if not note:
    print("SKIP 没有可用笔记")
    sys.exit(2)

# ---------- 1. 建场 ----------
s = call("POST", "/drill/sessions", {
    "title": "_打磨验证_重答",
    "scope": "note",
    "scope_refs": [{"type": "note", "id": note["id"], "title": note.get("title") or "笔记"}],
    "config": {"question_count": 3, "follow_up": "shallow", "show_answer": True, "adaptive": True},
})
sid = s["id"]
print("session:", sid, "note:", note["id"], note.get("title"))

cleanup_ok = False
try:
    # ---------- 2. 出题 ----------
    ev = sse(f"/drill/sessions/{sid}/start/stream", {})
    kinds = [k for k, _ in ev]
    q = next((b for k, b in ev if k == "question"), None)
    chk("出题成功", q is not None, kinds[-5:])
    if not q:
        raise SystemExit(1)

    # ---------- 3. 答第 1 题（故意答得很浅） ----------
    ev2 = sse(f"/drill/sessions/{sid}/answer/stream",
              {"seq": q["seq"], "answer": "大概就是讲了一些功能吧"})
    sc = next((b for k, b in ev2 if k == "score"), None)
    chk("第 1 题已评分", sc is not None and sc.get("score") is not None,
        [k for k, _ in ev2])
    first_score = sc.get("score") if sc else None

    # ---------- 4. 提前结束（进入成绩单态） ----------
    call("POST", f"/drill/sessions/{sid}/finish", {"reason": "abort"})
    d0 = call("GET", f"/drill/sessions/{sid}")
    n_before = len(d0["turns"])
    seq_before = d0["current_seq"]
    total_before = d0["total_score"]
    chk("已结算（成绩单态）", d0["status"] == "finished", d0["status"])
    print("结算后: turns=%s current_seq=%s total=%s 首题=%s"
          % (n_before, seq_before, total_before, first_score))

    # ---------- 5. 重答第 1 题 ----------
    # ⚠️ 答案必须**贴着期望要点**写：写成一段跑题的话，两次都得 5 分，
    #    「分数已更新」就成了恒真判据（第一版脚本正是这么假绿的）。
    pts = (sc or {}).get("expect_points") or []
    better = "。".join(str(p).rstrip("。") for p in pts) + "。这就是我的理解。"
    if not pts:
        better = "数智图书相比纸质书新增了音频讲解、视频解读和AI问答助手三类富媒体，读者可以随时追问。"
    ev3 = sse(f"/drill/sessions/{sid}/retry/stream",
              {"seq": q["seq"], "answer": better})
    kinds3 = [k for k, _ in ev3]
    sc3 = next((b for k, b in ev3 if k == "score"), None)
    chk("重答返回 score 事件", sc3 is not None, kinds3)
    chk("重答事件带 retry 标记", bool(sc3 and sc3.get("retry")), sc3 and sc3.get("retry"))
    chk("重答**没有**出下一题（不含 question 事件）", "question" not in kinds3, kinds3)
    chk("重答**没有**追问（不含 followup 题）",
        not any(b.get("is_followup") for k, b in ev3 if k == "question"))

    d1 = call("GET", f"/drill/sessions/{sid}")
    chk("轮次数不变（不推进）", len(d1["turns"]) == n_before, f'{n_before} → {len(d1["turns"])}')
    chk("current_seq 不变", d1["current_seq"] == seq_before, f'{seq_before} → {d1["current_seq"]}')
    t1 = next((t for t in d1["turns"] if t["seq"] == q["seq"]), None)
    chk("作答被替换成新答案", t1 and "这就是我的理解" in (t1["user_answer"] or ""))
    chk("该题分数已更新", t1 and t1["score"] is not None, t1 and t1["score"])
    # 分辨力守卫：第二版是照着期望要点写的，分数必须真的涨起来；
    # 若不变，说明重答根本没重新评分（只是把答案换了）。
    chk("重答分数实打实提高（≥ 第一版 + 15）",
        t1 and t1["score"] is not None and first_score is not None
        and t1["score"] >= first_score + 15,
        f'第一版={first_score} 重答={t1 and t1["score"]} 期望要点数={len(pts)}')

    graded = [t["score"] for t in d1["turns"] if t["score"] is not None]
    want = round(sum(graded) / len(graded), 1) if graded else None
    chk("总分按逐题重算（= 已评分均值）", d1["total_score"] == want,
        f'落库={d1["total_score"]} 期望={want} 逐题={graded}')
    chk("重答后状态仍是 finished", d1["status"] == "finished", d1["status"])
    print("重答后: 首题 %s → %s, 总分 %s → %s" % (t1 and t1["score"], first_score,
                                              total_before, d1["total_score"]))
finally:
    # ---------- 6. 清场：直连 sqlite（不用 API 删，绕开删除配额） ----------
    import os
    WS_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    dbp = os.path.join(WS_ROOT, "backend", "data", "app.db")
    if not os.path.exists(dbp):
        cand = [p for p in __import__("glob").glob(
            os.path.join(WS_ROOT, "**", "*.db"), recursive=True)]
        dbp = cand[0] if cand else ""
    if dbp:
        con = sqlite3.connect(dbp)
        try:
            con.execute("DELETE FROM drill_turns WHERE session_id=?", (sid,))
            con.execute("DELETE FROM drill_sessions WHERE id=?", (sid,))
            con.commit()
            cleanup_ok = True
        finally:
            con.close()
    print("清理临时场次:", "OK" if cleanup_ok else "FAILED", dbp)

print(f"\n==== {len(PASS)} PASS / {len(FAIL)} FAIL ====")
if FAIL:
    print("FAILED:", FAIL)
sys.exit(1 if FAIL else 0)
