"""P4 / P6 前端行为验证（真机 · 造一场临时陪练，跑完清场）。

P4：输入「我」点提交 → 出软拦提示，且**不得**发出任何评分请求
P6：会话中点返回 → 出「进度已保存」确认框；选继续答题 → 留在原页
"""
import json
import os
import sqlite3
import sys
import urllib.request

from playwright.sync_api import sync_playwright

CHROME = r"C:\Users\陈锦祥\AppData\Local\ms-playwright\chromium-1223\chrome-win64\chrome.exe"
BASE = "http://127.0.0.1:8000/api"
FE = "http://127.0.0.1:5173"
op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
PASS, FAIL = [], []


def chk(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + ((" | " + str(detail)) if detail and not cond else ""))


def call(method, path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with op.open(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


notes = call("GET", "/notes")
if isinstance(notes, dict):
    notes = notes.get("items") or []
note = next((n for n in notes if (n.get("content") or "").strip()), None)
if not note:
    print("SKIP 无可用笔记")
    sys.exit(2)

s = call("POST", "/drill/sessions", {
    "title": "_打磨验证_P4P6", "scope": "note",
    "scope_refs": [{"type": "note", "id": note["id"], "title": note.get("title") or "笔记"}],
    "config": {"question_count": 3, "follow_up": "shallow", "show_answer": True, "adaptive": True},
})
sid = s["id"]
print("temp session:", sid)

answer_reqs = []
try:
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, headless=True)
        pg = b.new_page(viewport={"width": 1440, "height": 1000})
        pg.on("request", lambda r: answer_reqs.append(r.url) if "/answer/stream" in r.url else None)
        pg.goto(f"{FE}/drill?sid={sid}", wait_until="domcontentloaded")

        # 新建的场次是 draft 态 —— 按设计不自动开跑，需要点「开始陪练」
        start_btn = pg.wait_for_selector("text=开始陪练", timeout=15000)
        chk("draft 场次出现「开始陪练」入口", start_btn is not None)
        start_btn.click()

        # 等出题完成（作答框出现）—— 只等目标元素，不等空态
        pg.wait_for_selector(".answer-box", timeout=180000)
        chk("深链进入后已出题", True)

        # ---------- P4 ----------
        pg.fill(".answer-box textarea", "我")
        pg.click(".answer-actions .el-button--primary")
        pg.wait_for_selector(".el-message", timeout=8000)
        msg = pg.inner_text(".el-message")
        chk("P4 短作答给出软拦提示", "不像一次作答" in msg, msg)
        chk("P4 软拦时**没有**发出评分请求", len(answer_reqs) == 0, answer_reqs)
        # 文字仍是「我」，没有被清空（用户还能继续补写）
        chk("P4 软拦不清空输入", pg.input_value(".answer-box textarea").strip() == "我")

        # ---------- P6 ----------
        pg.click(".flow-back")
        pg.wait_for_selector(".el-message-box", timeout=8000)
        box = pg.inner_text(".el-message-box")
        chk("P6 离开会话有确认框", "进度已保存" in box, box[:60])
        # 选「继续答题」→ 应留在会话态
        pg.click(".el-message-box .el-button:not(.el-button--primary)")
        pg.wait_for_selector(".el-message-box", state="detached", timeout=8000)
        chk("P6 取消后仍留在会话态", pg.query_selector(".answer-box") is not None)
        b.close()
finally:
    WS_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    con = sqlite3.connect(os.path.join(WS_ROOT, "backend", "data", "app.db"))
    try:
        con.execute("DELETE FROM drill_turns WHERE session_id=?", (sid,))
        con.execute("DELETE FROM drill_sessions WHERE id=?", (sid,))
        con.commit()
    finally:
        con.close()
    print("清理临时场次 OK")

print(f"\n==== {len(PASS)} PASS / {len(FAIL)} FAIL ====")
if FAIL:
    print("FAILED:", FAIL)
sys.exit(1 if FAIL else 0)
