# -*- coding: utf-8 -*-
"""学习闭环 UI 探针（2026-10-25）：真 8000 + 真库种子 + playwright

流程：
  V-UI: /video/<种子id> → 点「转复习卡」（真 LLM 出题 1 次）→ 按钮三态变化 → /review 见到卡
  D-UI: /drill?sid=<种子id> → 成绩单点「薄弱点生成复习卡」（零 LLM）→ /review 筛「AI 陪练」见卡
  NEG: 点击前 /review 无标记卡（分辨力）

种子全部带 _probe_ui_ 标记，验完直连 DB 删除（清场纪律；删除走 DB 不走 API）。
"""
import json
import pathlib
import sqlite3
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

APP = "http://127.0.0.1:8000"
DB_PATH = r"C:\Users\陈锦祥\WorkBuddy\AI 伴学\ai-study-companion\backend\data\app.db"
CHROME = r"C:\Users\陈锦祥\WorkBuddy\AI 伴学\ai-study-companion\backend\data\browsers\chromium-1200\chrome-win64\chrome.exe"
MARK = "_probe_ui_闭环"
WEAK_MARK = "盈亏平衡探针标记点"

fails, oks = [], []


def ok(name, cond, detail=""):
    (oks if cond else fails).append(name)
    print(("PASS " if cond else "FAIL ") + name + (("  | " + str(detail)[:150]) if detail else ""))


def db():
    c = sqlite3.connect(DB_PATH)
    c.execute("PRAGMA busy_timeout=5000")
    return c


NOW = time.strftime("%Y-%m-%d %H:%M:%S")
# ---------------- 种子 ----------------
conn = db()
cur = conn.execute(
    "INSERT INTO videos (title, status, skin, script, shots, points, duration_ms, created_at, updated_at) "
    "VALUES (?, 'finished', 'board', ?, '[]', '[]', 60000, ?, ?)",
    (MARK + "视频", json.dumps([{"speaker": "host", "text": "这页讲" + MARK + "：固定成本不随产量变，"
     "可变成本随产量线性变，盈亏平衡点是二者与收入的交叉点。"}], ensure_ascii=False), NOW, NOW))
VID = cur.lastrowid
cur = conn.execute(
    "INSERT INTO drill_sessions (title, scope, status, outline, total_score, created_at, updated_at) "
    "VALUES (?, 'all', 'finished', '[]', 55.0, ?, ?)", (MARK + "陪练", NOW, NOW))
SID = cur.lastrowid
cur = conn.execute(
    "INSERT INTO drill_turns (session_id, seq, outline_index, is_followup, question, "
    "expected_points, source_material_id, source_title, user_answer, score, verdict, "
    "missing_points, created_at) VALUES (?, 1, 0, 0, '什么是盈亏平衡？', "
    "'[\"定义盈亏平衡点\",\"会算平衡产量\"]', NULL, ?, '没答全', 55, 'partial', ?, ?)",
    (SID, MARK, json.dumps([WEAK_MARK], ensure_ascii=False), NOW))
TID = cur.lastrowid
conn.commit()
conn.close()
print(f"[seed] video={VID} drill_session={SID} turn={TID}")

created_card_ids = []
note_ids = []

try:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        js_errors = []
        page.on("pageerror", lambda e: js_errors.append(str(e)))

        # ---------- NEG：点击前 /review 无标记卡 ----------
        page.goto(APP + "/review", wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        body = page.locator("body").inner_text()
        ok("NEG 点击前复习页无标记卡", MARK not in body and WEAK_MARK not in body)

        # ---------- V-UI：视频转复习卡 ----------
        page.goto(APP + "/video/%d" % VID, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        btn = page.locator("button", has_text="转复习卡")
        ok("V-UI1 详情页「转复习卡」按钮在", btn.count() >= 1 and btn.first.is_enabled())

        btn.first.click()
        # 真 LLM 出题，给足等待；成功提示 ElMessage
        try:
            page.wait_for_selector(".el-message", timeout=120000)
            msg = page.locator(".el-message").last.inner_text()
        except Exception:
            msg = "(无消息弹出)"
        ok("V-UI2 点击后弹出「已生成 N 张」", "已生成" in msg and "复习卡" in msg, msg[:80])
        page.wait_for_timeout(1200)
        btn_txt = page.locator("button", has_text="复习卡").last.inner_text()
        ok("V-UI3 按钮转三态（已生成 N 张）", "已生成" in btn_txt, btn_txt)

        # ---------- D-UI：薄弱点生成复习卡 ----------
        page.goto(APP + "/drill?sid=%d" % SID, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        dbtn = page.locator("button", has_text="薄弱点生成复习卡")
        ok("D-UI1 成绩单「薄弱点生成复习卡」可点", dbtn.count() >= 1 and dbtn.first.is_enabled(),
           "count=%d" % dbtn.count())
        dbtn.first.click()
        try:
            page.wait_for_selector(".el-message", timeout=15000)
            dmsg = page.locator(".el-message").last.inner_text()
        except Exception:
            dmsg = "(无消息弹出)"
        ok("D-UI2 点击后弹出已生成提示", "已生成" in dmsg, dmsg[:80])

        # ---------- 复习页总装验证：统计下钻弹窗（不受今日队列上限/交错/queue 占用影响） ----------
        page.goto(APP + "/review", wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        page.locator(".stat-chip[title='点击查看全部卡片']").first.click()
        page.wait_for_selector(".status-list", timeout=8000)
        page.wait_for_timeout(1200)
        body = page.locator("body").inner_text()
        ok("V-UI4 全部卡片弹窗出现视频卡（来源标题带标记）", ("视频 · " + MARK + "视频") in body,
           "命中" if MARK in body else "未命中")
        ok("D-UI3 全部卡片弹窗出现【陪练薄弱点】标记卡", ("【陪练薄弱点】" + WEAK_MARK) in body,
           "命中" if WEAK_MARK in body else "未命中")
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
        page.locator(".el-select").first.click()
        page.wait_for_timeout(600)
        opts = page.locator(".el-select-dropdown__item").all_inner_texts()
        ok("V-UI5 资料筛选含「AI 视频」", any("AI 视频" in o for o in opts), opts)
        ok("D-UI4 资料筛选含「AI 陪练」", any("AI 陪练" in o for o in opts), opts)
        page.keyboard.press("Escape")

        ok("E0 全程零 JS 错误", not js_errors, js_errors[:2])
        browser.close()
finally:
    # ---------------- 清场（直连 DB） ----------------
    conn = db()
    created_card_ids = [r[0] for r in conn.execute(
        "SELECT id FROM review_cards WHERE material_title LIKE ? OR question LIKE ?",
        ("%" + MARK + "%", "%" + WEAK_MARK + "%")).fetchall()]
    n1 = conn.execute(
        "DELETE FROM review_cards WHERE material_title LIKE ? OR question LIKE ?",
        ("%" + MARK + "%", "%" + WEAK_MARK + "%")).rowcount
    note_ids = [r[0] for r in conn.execute(
        "SELECT id FROM notes WHERE title LIKE ?", ("%" + MARK + "%",)).fetchall()]
    n2 = conn.execute("DELETE FROM notes WHERE title LIKE ?", ("%" + MARK + "%",)).rowcount
    n3 = conn.execute("DELETE FROM drill_turns WHERE session_id=?", (SID,)).rowcount
    n4 = conn.execute("DELETE FROM drill_sessions WHERE id=?", (SID,)).rowcount
    n5 = conn.execute("DELETE FROM videos WHERE id=?", (VID,)).rowcount
    conn.commit()
    conn.close()
    print(f"[cleanup] cards={n1} notes={n2} drill_turns={n3} drill_sessions={n4} videos={n5}")

print("\n===== 总计: %d PASS / %d FAIL =====" % (len(oks), len(fails)))
for f in fails:
    print("FAILED:", f)
sys.exit(1 if fails else 0)
