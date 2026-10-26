"""三档问答真机验证（真模型 · 8000 已在跑新代码 · 2026-10-25）

判据：
  kb 档未命中  → answer_kind == 'kb_miss'，kb_hit == False，正文非空且含「知识库」口径措辞
  general 档   → answer_kind == 'general'，sources 为空，正文非空
  auto 档命中  → answer_kind == 'kb'，sources 非空
  分辨力       → 三条 answer_kind 不恒同
测完直连 DB 删除测试会话（清场纪律：不走 API 删）。
"""
import json
import sqlite3
import sys
import urllib.request

BASE = "http://127.0.0.1:8000"
DB = r"C:\Users\陈锦祥\WorkBuddy\AI 伴学\ai-study-companion\backend\data\app.db"
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

fails, oks = [], []


def ok(name, cond, detail=""):
    (oks if cond else fails).append(name)
    print(("PASS " if cond else "FAIL ") + name + (("  | " + str(detail)[:160]) if detail else ""))


def get(path):
    with opener.open(BASE + path, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def ask(question, mode):
    body = {"question": question, "mode": mode}
    req = urllib.request.Request(
        BASE + "/api/chat/ask/stream",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    sources, text, done, err = None, "", False, None
    with opener.open(req, timeout=180) as r:
        ev = None
        for raw in r:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line.startswith("event: "):
                ev = line[7:].strip()
                continue
            if line.startswith("data: ") and ev:
                try:
                    obj = json.loads(line[6:])
                except Exception:
                    obj = None
                if ev == "sources":
                    sources = obj
                elif ev == "token" and obj:
                    text += obj.get("t", "")
                elif ev == "done":
                    done = True
                elif ev == "error":
                    err = obj
                ev = None
    return sources, text, done, err


# ---- 找一条真实材料构造 auto 档命中问题 ----
mats = get("/api/materials")
items = mats.get("items") if isinstance(mats, dict) else mats
if not items:
    print("FATAL: 材料库为空，无法构造命中问题")
    sys.exit(1)
mat_title = (items[0].get("title") or "").strip()
print("材料数:", len(items), "| 首条标题:", mat_title[:60])

seed_sessions = []

# ---- 1. kb 档未命中 ----
q_miss = "国际空间站对接环的公差配合标准是什么？"   # 与本库内容无关的生僻工业话题
s, t, d, e = ask(q_miss, "kb")
if s and s.get("session_id"):
    seed_sessions.append(s["session_id"])
ok("kb档: answer_kind == kb_miss", s and s.get("answer_kind") == "kb_miss", s and s.get("answer_kind"))
ok("kb档: kb_hit == False", s and s.get("kb_hit") is False, s and s.get("kb_hit"))
ok("kb档: 完成且正文非空", d and len(t) > 20, "done=%s len=%d err=%s" % (d, len(t), e))
ok("kb档: 正文含知识库口径措辞", ("知识库" in t) or ("未找到" in t) or ("没有" in t), t[:120])
ok("kb档: 引导不冗长（<=1500 字）", len(t) <= 1500, len(t))

# ---- 2. general 档 ----
s2, t2, d2, e2 = ask("用一句话解释什么是机会成本。", "general")
if s2 and s2.get("session_id"):
    seed_sessions.append(s2["session_id"])
ok("general档: answer_kind == general", s2 and s2.get("answer_kind") == "general", s2 and s2.get("answer_kind"))
ok("general档: sources 为空", s2 is not None and s2.get("sources") == [], s2 and str(s2.get("sources"))[:60])
ok("general档: 完成且正文非空", d2 and len(t2) > 10, "done=%s len=%d err=%s" % (d2, len(t2), e2))

# ---- 3. auto 档命中（锚真实材料）----
q_hit = "《%s》这份材料主要讲了什么？" % mat_title if mat_title else "我的知识库里有哪些材料？"
s3, t3, d3, e3 = ask(q_hit, "auto")
if s3 and s3.get("session_id"):
    seed_sessions.append(s3["session_id"])
ok("auto档: answer_kind == kb", s3 and s3.get("answer_kind") == "kb", s3 and s3.get("answer_kind"))
ok("auto档: sources 非空", s3 is not None and len(s3.get("sources") or []) > 0, s3 and len(s3.get("sources") or []))
ok("auto档: 完成且正文非空", d3 and len(t3) > 20, "done=%s len=%d err=%s" % (d3, len(t3), e3))

# ---- 分辨力 ----
kinds = {x.get("answer_kind") for x in (s, s2, s3) if x}
ok("分辨力: 三档 answer_kind 不恒同", len(kinds) >= 2, kinds)

print("\n--- kb_miss 正文节选 ---")
print(t[:400])
print("--- general 正文节选 ---")
print(t2[:200])

# ---- 清场：直连 DB 删测试会话 ----
if seed_sessions:
    conn = sqlite3.connect(DB)
    q = ",".join(str(i) for i in seed_sessions)
    n1 = conn.execute("DELETE FROM chat_messages WHERE session_id IN (%s)" % q).rowcount
    n2 = conn.execute("DELETE FROM chat_sessions WHERE id IN (%s)" % q).rowcount
    conn.commit()
    conn.close()
    print("清场: messages=%d sessions=%d" % (n1, n2))

print("\n===== 总计: %d PASS / %d FAIL =====" % (len(oks), len(fails)))
sys.exit(1 if fails else 0)
