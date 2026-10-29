# -*- coding: utf-8 -*-
"""O9 实测探针（2026-10-30）：进入既有会话后会不会触发「闲置搭话」

背景：2026-10-29 的静态审计说「onMounted 没调 armIdle → 搭话只在发过消息后才可能出现」。
但代码里 openSession() 结尾是调了 armIdle 的 —— 静态审计可能误判，必须实测。

判据口径：搭话消息**不落库**（_idle 标记、刷新即消失），所以
  「点击既有会话后消息数 +1」且「刷新后消息数回到 DB 条数」两个条件同时成立才算真搭话；
  只看「多了条消息」会把「后端真回答」也算进去。
"""
import json
import sys
import urllib.request

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5173"
API = "http://127.0.0.1:8000"
CHROME = r"C:\Users\陈锦祥\AppData\Local\ms-playwright\chromium-1223\chrome-win64\chrome.exe"
APP_ID = 1
IDLE_MS = 1500

_op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
RESULTS = []


def report(rid, ok, detail=""):
    RESULTS.append((rid, "PASS" if ok else "FAIL", str(detail)))
    print(("[PASS] " if ok else "[FAIL] ") + rid + "  " + str(detail)[:190], flush=True)


def api(path):
    with _op.open(API + path, timeout=20) as r:
        return json.loads(r.read().decode())


def main():
    sessions = api(f"/api/chat/sessions?app_id={APP_ID}")
    if not sessions:
        print("应用没有会话，无法验证（先跑一遍 O7 探针造会话）")
        sys.exit(2)
    sid = sessions[0]["id"]
    db_msgs = api(f"/api/chat/sessions/{sid}/messages")
    print(f"用会话 {sid}（DB 内 {len(db_msgs)} 条消息）", flush=True)

    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, headless=True)
        pg = b.new_page(viewport={"width": 1440, "height": 900})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))

        # I1 进入页面但未选会话：空会话不搭话（欢迎块承担开场）
        pg.goto(f"{BASE}/furnace/app/{APP_ID}?idle_ms={IDLE_MS}", wait_until="domcontentloaded")
        pg.wait_for_selector(".fc-msgarea", timeout=25000)
        pg.wait_for_timeout(IDLE_MS + 2200)
        n_enter = len(pg.query_selector_all(".msg"))
        report("I1 未选会话时不搭话（空会话留给欢迎块）", n_enter <= 1, f".msg 数={n_enter}")

        # I2 点开既有会话 → 闲置后应出现搭话
        pg.wait_for_selector(".sess-item", timeout=10000)
        pg.click(".sess-item")
        pg.wait_for_timeout(1200)
        n_loaded = len(pg.query_selector_all(".msg"))
        pg.wait_for_timeout(IDLE_MS + 2500)
        n_after = len(pg.query_selector_all(".msg"))
        grew = n_after > n_loaded
        report("I2 既有会话闲置后出现搭话", grew, f"载入 {n_loaded} → 闲置后 {n_after}（DB {len(db_msgs)} 条）")

        # I3 搭话不落库：刷新后消失
        pg.reload(wait_until="domcontentloaded")
        pg.wait_for_selector(".sess-item", timeout=20000)
        pg.click(".sess-item")
        pg.wait_for_timeout(1500)
        n_reload = len(pg.query_selector_all(".msg"))
        report("I3 搭话不落库（刷新后消失）", n_reload == len(db_msgs),
               f"刷新后 {n_reload} 条 / DB {len(db_msgs)} 条")

        report("I4 页面无 JS 运行时异常", not errs, f"{errs[:2]}" if errs else "无")
        b.close()

    n_pass = sum(1 for _, s, _ in RESULTS if s == "PASS")
    n_fail = sum(1 for _, s, _ in RESULTS if s == "FAIL")
    print(f"\n总计 {len(RESULTS)} 项：PASS {n_pass} / FAIL {n_fail}")
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
