# -*- coding: utf-8 -*-
"""N1 探针（2026-10-26）：新建复习卡当日优先（fresh boost）

单隔离实例完成全部验证（四个 ASC_* 重定向 + BGE 硬链接 + ASC_REVIEW_DAILY_LIMIT=5）：
  种子 = 8 张旧到期卡（A×5/B×3）+ 3 张新建卡（A×2/C×1）
  + 2 张北京日界边界卡（北京今日 00:30 → fresh；北京昨日 23:59 → 非 fresh）+ 哨兵卡。

Phase 1（API 级判据）：
  - fresh_count==4、total_due==13、cards==9（4 fresh + 5 rest）
  - len(cards) > limit —— 分辨力守卫：旧逻辑 rows=interleaved[:limit] 恒 ≤ limit，
    该断言只有在 fresh 免截断生效后才可能 PASS
  - fresh slice 恰好是 4 张新卡 id；rest 交错生效（前两张材料不同）
  - 材料筛选联动：material=A → fresh_count==2；material=B → fresh_count==0（无新卡路径不变）

Phase 2（浏览器级，同一实例 serve dist，验前端 fresh_count 接线）：
  - /review 出现「今日新增 4 张，已排在最前」提示
  - 顺延行数学不变：「今日上限 5 张，剩余 4 张顺延」
  - 队列第一张卡是新卡（问题含「新卡」/「边界-北京今日」，旧卡不含）
  - 零 JS 错误
"""
import json
import os
import pathlib
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta

REPO = pathlib.Path(__file__).resolve().parents[2]        # ai-study-companion/
WS = REPO.parent                                          # 工作区根
BACKEND = REPO / "backend"
TMP = WS / "_tmp_probe_fresh_20261026"
CHROME = BACKEND / "data" / "browsers" / "chromium-1200" / "chrome-win64" / "chrome.exe"
RESULTS = []
LIMIT = 5
MARK = "_probe_n1_"


def report(rid, ok, detail=""):
    RESULTS.append((rid, "PASS" if ok else "FAIL", detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {rid}  {detail}")


def iso(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


TMP.mkdir(parents=True, exist_ok=True)
(TMP / "files").mkdir(exist_ok=True)
(TMP / "chroma").mkdir(exist_ok=True)
_db = TMP / "app.db"
if _db.exists():
    _db.unlink()

REAL_BGE = BACKEND / "data" / "models" / "bge-small-zh-v1.5"
ISO_BGE = TMP / "models" / "bge-small-zh-v1.5"
linked = 0
for src in REAL_BGE.rglob("*"):
    if src.is_file():
        dst = ISO_BGE / src.relative_to(REAL_BGE)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists():
            os.link(src, dst)
            linked += 1
print(f"[setup] BGE 硬链接 {linked} 个文件")

s = socket.socket(); s.bind(("127.0.0.1", 0)); PORT = s.getsockname()[1]; s.close()
env = dict(os.environ)
env["ASC_DATA_DIR"] = str(TMP)
env["ASC_FILES_DIR"] = str(TMP / "files")
env["ASC_DB_URL"] = f"sqlite:///{TMP / 'app.db'}"
env["ASC_CHROMA_DIR"] = str(TMP / "chroma")
env["ASC_REVIEW_DAILY_LIMIT"] = str(LIMIT)
env["PYTHONIOENCODING"] = "utf-8"

proc = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
    cwd=str(BACKEND), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
BASE = f"http://127.0.0.1:{PORT}/api"


def http_json(method, path, body=None, timeout=20):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with opener.open(req, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def wait_ready():
    for _ in range(60):
        try:
            with opener.open(BASE + "/version", timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def insert_card(conn, mid, mtitle, question, created, due_at):
    cur = conn.execute(
        "INSERT INTO review_cards (note_id, material_id, material_title, question, answer, "
        "type, source, level, ease, next_review_at, created_at) VALUES "
        "(NULL, ?, ?, ?, '探针答案', 'qa', 'note', 0, 2.5, ?, ?)",
        (mid, mtitle, question, iso(due_at), iso(created)))
    return cur.lastrowid


try:
    if not wait_ready():
        report("READY", False, "隔离实例 60 次轮询未就绪")
        sys.exit(1)
    report("READY", True, f"port={PORT} limit={LIMIT}")

    now = datetime.utcnow()
    CN = timedelta(hours=8)
    today_start_utc = datetime.combine((now + CN).date(), datetime.min.time()) - CN

    conn = sqlite3.connect(str(TMP / "app.db"))
    # 哨兵：把 max(id) 顶高，防「删掉最大行复用 id」掩盖旧卡残留。
    # ⚠️ 必须「非到期 + 非当日新建」：否则它会进今日队列并被算作 fresh（10-26 首跑翻车点）
    conn.execute(
        "INSERT INTO review_cards (note_id, material_id, material_title, question, answer, "
        "type, source, level, ease, next_review_at, created_at) VALUES "
        "(NULL, 999999, '_probe_sentinel', '哨兵题', '哨兵答案', 'qa', 'note', 0, 2.5, ?, ?)",
        (iso(now + timedelta(days=10)), iso(now - timedelta(days=3))))
    mats = {}
    for name in ["_probe_n1_资料A", "_probe_n1_资料B", "_probe_n1_资料C"]:
        cur = conn.execute(
            "INSERT INTO materials (title, format, parsed_status, created_at) VALUES (?, 'txt', 'success', ?)",
            (name, iso(now)))
        mats[name] = cur.lastrowid
    MA, MB, MC = mats["_probe_n1_资料A"], mats["_probe_n1_资料B"], mats["_probe_n1_资料C"]

    old_created = now - timedelta(days=3)
    # A×5 / B×3，next_review_at 递升交错
    seq = [(MA, "A"), (MB, "B"), (MA, "A"), (MB, "B"), (MA, "A"), (MB, "B"), (MA, "A"), (MA, "A")]
    for i, (mid, tag) in enumerate(seq):
        insert_card(conn, mid, f"_probe_n1_资料{tag}", f"{MARK}旧卡{tag}{i}",
                    old_created, now - timedelta(days=1, minutes=i))
    fresh_ids = []
    for mid, tag in [(MA, "A"), (MC, "C"), (MA, "A")]:
        fresh_ids.append(insert_card(
            conn, mid, f"_probe_n1_资料{tag}", f"{MARK}新卡{tag}{len(fresh_ids)}",
            now, now))
    # 边界卡：北京今日 00:30（必须 fresh）/ 北京昨日 23:59（必须非 fresh）
    b1 = insert_card(conn, MC, "_probe_n1_资料C", MARK + "边界-北京今日0030",
                     today_start_utc + timedelta(minutes=30), now)
    b2 = insert_card(conn, MB, "_probe_n1_资料B", MARK + "边界-北京昨日2359",
                     today_start_utc - timedelta(minutes=1), now)
    conn.commit()
    fresh_set = set(fresh_ids) | {b1}

    # ---------- Phase 1：API ----------
    code, d = http_json("GET", "/review/today")
    cards = d["cards"]
    ids = [c["id"] for c in cards]
    report("R1 total_due/limit", d["total_due"] == 13 and d["limit"] == LIMIT,
           f"total_due={d['total_due']} limit={d['limit']}")
    report("R2 fresh_count=4（含北京今日边界卡）", d.get("fresh_count") == 4,
           f"fresh_count={d.get('fresh_count')}")
    report("R3 队列=4 fresh+5 rest=9", len(cards) == 9, f"len={len(cards)}")
    report("R4 分辨力守卫：len(cards) > limit（旧逻辑恒 ≤limit 不可能 PASS）",
           len(cards) > LIMIT, f"{len(cards)} > {LIMIT}")
    report("R5 fresh slice 恰好是 4 张新卡", set(ids[:4]) == fresh_set,
           f"ids[:4]={ids[:4]}")
    report("R6 北京昨日 23:59 卡不在 fresh slice", b2 not in ids[:4],
           f"b2={b2} ids[:4]={ids[:4]}")
    rest_slice = cards[4:]
    report("R7 rest 截断到上限 5 张", len(rest_slice) == LIMIT, f"rest={len(rest_slice)}")
    report("R8 rest 交错生效（前两张材料不同）",
           len(rest_slice) >= 2 and rest_slice[0]["material_id"] != rest_slice[1]["material_id"],
           f"mat={[c['material_id'] for c in rest_slice[:3]]}")

    code, da = http_json("GET", f"/review/today?material_id={MA}")
    report("R9 筛选 A：fresh_count==2 且全部归属 A（2 新+5 旧=7）",
           da.get("fresh_count") == 2 and all(c["material_id"] == MA for c in da["cards"])
           and len(da["cards"]) == 7,
           f"fresh_count={da.get('fresh_count')} len={len(da['cards'])}")
    code, db_ = http_json("GET", f"/review/today?material_id={MB}")
    report("R10 筛选 B：无新卡路径不变（fresh_count==0，4 张全旧）",
           db_.get("fresh_count") == 0 and len(db_["cards"]) == 4,
           f"fresh_count={db_.get('fresh_count')} len={len(db_['cards'])}")

    n_sent = conn.execute(
        "SELECT COUNT(*) FROM review_cards WHERE material_title='_probe_sentinel'").fetchone()[0]
    report("R11 哨兵卡仍在（max(id) 未被复用掩盖）", n_sent == 1, f"sentinel={n_sent}")
    conn.close()

    # ---------- Phase 2：浏览器（同一实例 serve dist） ----------
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=str(CHROME))
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{PORT}/review", wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        hint = page.locator(".queue-progress", has_text="今日新增")
        cnt = hint.count()
        txt = hint.first.inner_text() if cnt else ""
        report("UI1 「今日新增 4 张，已排在最前」提示可见", cnt > 0 and "今日新增 4 张" in txt
               and "已排在最前" in txt, txt or "(未找到提示行)")
        carry = page.locator(".queue-progress", has_text="顺延")
        txt2 = carry.first.inner_text() if carry.count() else ""
        report("UI2 顺延行数学不变（上限 5 / 剩余 4 张顺延）",
               "今日上限 5 张" in txt2 and "剩余 4 张顺延" in txt2, txt2 or "(无顺延行)")
        body = page.locator("body").inner_text()
        # 当前卡（队列第一张）必须是新卡：fresh slice 排前 → 第一张含「新卡」或「边界-北京今日」
        first_is_fresh = (f"{MARK}新卡A0" in body) or (f"{MARK}边界-北京今日" in body)
        report("UI3 队列首张是新建卡（排前生效）", first_is_fresh,
               "命中新卡标题" if first_is_fresh else "首张不是新卡")
        report("UI4 页面零 JS 错误", not errors, errors[:2])
        browser.close()
finally:
    proc.terminate()
    try:
        proc.wait(timeout=8)
    except Exception:
        proc.kill()

npass = sum(1 for r in RESULTS if r[1] == "PASS")
nfail = sum(1 for r in RESULTS if r[1] == "FAIL")
print(f"\n===== 总计 {npass} PASS / {nfail} FAIL =====")
sys.exit(1 if nfail else 0)
