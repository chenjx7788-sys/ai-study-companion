# -*- coding: utf-8 -*-
"""学习闭环后端探针（2026-10-25）：B7 视频→复习卡 + B8 薄弱点→定向复述卡

隔离实例：四个 ASC_* 重定向到 _tmp_probe_loop_20261025/；BGE 硬链接；llm_settings.json
拷入（B7 需要 1 次真实 LLM 出题调用；B8 零 LLM）。只杀自己 Popen 的 uvicorn；
临时目录留在原地由用户手动清（删除配额纪律）。

判据要点（对应 MEMORY 纪律）：
  - 哨兵卡先把 max(id) 顶高，再验「force 重建后旧卡 id 消失」（防 SQLite 复用 id 假绿）
  - created==0 的断言全部配对「库内卡数 > 0」守卫（want==0 防恒真）
  - 聚合/幂等判据锚在模板前缀与逐字段值上，不锚计数之外的行号
"""
import json
import os
import pathlib
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request

WS = pathlib.Path(__file__).resolve().parents[3]  # tests/probes/x.py → 工作区根
BACKEND = WS / "ai-study-companion" / "backend"
TMP = WS / "_tmp_probe_loop_20261025"
RESULTS = []


def report(rid, ok, detail=""):
    status = "PASS" if ok else "FAIL"
    RESULTS.append((rid, status, detail))
    print(f"[{status}] {rid}  {detail}")


# ---------------- 隔离环境 ----------------
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
    if not src.is_file():
        continue
    dst = ISO_BGE / src.relative_to(REAL_BGE)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists():
        os.link(src, dst)
        linked += 1
print(f"[setup] BGE 硬链接 {linked} 个文件")

# B7 需要一次真实 LLM 出题 → 拷入用户 LLM 配置（只读拷贝，不改原件）
real_cfg = BACKEND / "data" / "llm_settings.json"
if real_cfg.exists():
    shutil.copy2(real_cfg, TMP / "llm_settings.json")
    print("[setup] llm_settings.json 已拷入隔离实例")
else:
    print("[setup] ⚠️ 真实环境无 llm_settings.json，B7 出题会失败")

s = socket.socket(); s.bind(("127.0.0.1", 0)); PORT = s.getsockname()[1]; s.close()
env = dict(os.environ)
env["ASC_DATA_DIR"] = str(TMP)
env["ASC_FILES_DIR"] = str(TMP / "files")
env["ASC_DB_URL"] = f"sqlite:///{TMP / 'app.db'}"
env["ASC_CHROMA_DIR"] = str(TMP / "chroma")
env["PYTHONIOENCODING"] = "utf-8"

proc = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
    cwd=str(BACKEND), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
BASE = f"http://127.0.0.1:{PORT}/api"


def http_json(method, path, body=None, timeout=20, expect_error=False):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if expect_error:
            return e.code, json.loads(e.read().decode("utf-8") or "{}")
        raise


def wait_ready():
    for _ in range(60):
        try:
            with opener.open(BASE + "/version", timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def db():
    return sqlite3.connect(str(TMP / "app.db"))


NOW = time.strftime("%Y-%m-%d %H:%M:%S")

try:
    assert wait_ready(), "隔离实例未就绪"

    # ---------------- 种子（直连隔离库） ----------------
    conn = db()
    # 视频种子（有讲稿）
    cur = conn.execute(
        "INSERT INTO videos (title, status, skin, script, shots, points, duration_ms, created_at, updated_at) "
        "VALUES ('_probe_闭环视频', 'finished', 'board', ?, '[]', '[]', 60000, ?, ?)",
        (json.dumps([{"speaker": "host", "text": "今天我们讲成本管理。成本分固定成本与可变成本，"
                     "盈亏平衡点是总收入等于总成本的产量点。"}], ensure_ascii=False), NOW, NOW))
    VID = cur.lastrowid
    # 空讲稿视频（V8）
    cur = conn.execute(
        "INSERT INTO videos (title, status, skin, script, shots, points, duration_ms, created_at, updated_at) "
        "VALUES ('_probe_空讲稿', 'finished', 'board', '[]', '[]', '[]', 0, ?, ?)", (NOW, NOW))
    VID_EMPTY = cur.lastrowid
    # 陪练种子 S1（finished + 2 轮带漏点）
    cur = conn.execute(
        "INSERT INTO drill_sessions (title, scope, status, outline, total_score, created_at, updated_at) "
        "VALUES ('_probe_陪练S1', 'all', 'finished', "
        "'[{\"point\":\"成本概念\",\"difficulty\":\"medium\"}]', 47.5, ?, ?)", (NOW, NOW))
    S1 = cur.lastrowid
    conn.execute(
        "INSERT INTO drill_turns (session_id, seq, outline_index, is_followup, question, "
        "expected_points, source_material_id, source_title, user_answer, score, verdict, "
        "missing_points, created_at) VALUES (?, 1, 0, 0, '什么是成本分析？', "
        "'[\"识别固定成本\",\"识别可变成本\",\"计算盈亏平衡点\"]', 777, '成本管理教材', '答了一半', "
        "55, 'partial', '[\"计算盈亏平衡点\",\"识别可变成本\"]', ?)", (S1, NOW))
    conn.execute(
        "INSERT INTO drill_turns (session_id, seq, outline_index, is_followup, question, "
        "expected_points, source_material_id, source_title, user_answer, score, verdict, "
        "missing_points, created_at) VALUES (?, 2, 1, 0, '如何进行风险评估？', "
        "'[\"风险识别\",\"风险量化\"]', NULL, '', '不会', 40, 'wrong', '[\"风险量化\"]', ?)",
        (S1, NOW))
    conn.commit()
    conn.close()
    print(f"[setup] 种子: video={VID} video_empty={VID_EMPTY} drillS1={S1} + 哨兵卡")

    # ---------------- B7：视频 → 复习卡 ----------------
    print("\n===== B7 视频→复习卡 =====")
    code, r = http_json("POST", f"/videos/99999/to-cards", {}, expect_error=True)
    report("V0 不存在作品 → 404", code == 404, f"code={code}")

    code, r = http_json("POST", f"/videos/{VID_EMPTY}/to-cards", {}, expect_error=True)
    report("V8 空讲稿 → 400", code == 400, f"code={code} body={str(r)[:80]}")

    t0 = time.time()
    code, r = http_json("POST", f"/videos/{VID}/to-cards", {}, timeout=180)
    report("V1 to-cards 成功", code == 200 and r.get("created", 0) >= 1,
           f"created={r.get('created')} note_id={r.get('note_id')} 耗时{time.time()-t0:.0f}s")
    note_id = r.get("note_id")

    conn = db()
    cards = conn.execute(
        "SELECT id, material_id, material_title, source, type, options, correct_index "
        "FROM review_cards WHERE note_id=?", (note_id,)).fetchall()
    n_cards = len(cards)
    report("V2 卡字段齐全（source=note / mid=-2 / 标题前缀 / 选择题四选项）",
           n_cards >= 1 and all(
               c[1] == -2 and c[2].startswith("视频 ·") and c[3] == "note"
               and c[4] == "choice" and len(json.loads(c[5] or "[]")) == 4
               and c[6] in (0, 1, 2, 3) for c in cards),
           f"cards={[(c[1], c[2][:12], c[3], c[4]) for c in cards]}")
    ids_v1 = {c[0] for c in cards}
    conn.close()
    report("V2b 守卫: 库内总卡数 > 0（哨兵+新卡）",
           True if n_cards >= 1 else False, f"note卡={n_cards}")

    code, r = http_json("GET", f"/videos/{VID}/cards-state")
    report("V3 cards-state 报卡数", code == 200 and r.get("cards") == n_cards,
           f"state={r}")

    code, r = http_json("POST", f"/videos/{VID}/to-cards", {})
    report("V4 重复调用幂等（created=0 且报 existing）",
           code == 200 and r.get("created") == 0 and r.get("existing") == n_cards, f"{r}")

    # 哨兵卡必须在「待删行之后」创建才能把 max(id) 顶高 —— 放在 V4 之后、force 之前
    conn = db()
    conn.execute(
        "INSERT INTO review_cards (note_id, material_id, material_title, question, answer, "
        "type, source, level, ease, next_review_at, created_at) VALUES "
        "(NULL, 999999, '_probe_sentinel', '哨兵题', '哨兵答案', 'qa', 'note', 0, 2.5, ?, ?)",
        (NOW, NOW))
    conn.commit()
    conn.close()

    code, r = http_json("POST", f"/videos/{VID}/to-cards", {"force": True}, timeout=180)
    conn = db()
    ids_v2 = {row[0] for row in conn.execute(
        "SELECT id FROM review_cards WHERE note_id=?", (note_id,)).fetchall()}
    sentinel_alive = conn.execute(
        "SELECT COUNT(*) FROM review_cards WHERE material_id=999999").fetchone()[0] == 1
    conn.close()
    report("V5 force 重建：新卡 id 与旧卡不相交（旧卡真被删）",
           code == 200 and r.get("created", 0) >= 1 and ids_v1.isdisjoint(ids_v2),
           f"旧ids={sorted(ids_v1)} 新ids={sorted(ids_v2)} recreated={r.get('recreated')}")
    report("V5b 哨兵卡仍在（删除没有误伤别的卡）", sentinel_alive)

    code, r = http_json("GET", "/review/filters")
    report("V6 筛选项含「AI 视频」(-2)",
           code == 200 and any(m.get("id") == -2 and m.get("title") == "AI 视频"
                               for m in r.get("materials", [])),
           f"materials={r.get('materials')}")

    conn = db()
    n_row = conn.execute(
        "SELECT anchor FROM notes WHERE id=?", (note_id,)).fetchone()
    conn.close()
    anchor = json.loads(n_row[0]) if n_row and n_row[0] else {}
    report("V7 讲稿笔记 anchor 正确（复用 to_note 幂等）",
           anchor.get("kind") == "video_script" and anchor.get("video_id") == VID,
           f"anchor={anchor}")

    # ---------------- B8：薄弱点 → 定向复述卡 ----------------
    print("\n===== B8 薄弱点→定向复述卡 =====")
    code, r = http_json("POST", "/drill/cards/from-weak", {}, expect_error=True)
    report("D0 跨场模式场次不足 → 400（当前仅 1 场）", code == 400, f"code={code} {str(r)[:60]}")

    code, r = http_json("POST", "/drill/cards/from-weak", {"session_id": 99999}, expect_error=True)
    report("D0b 不存在场次 → 404", code == 404, f"code={code}")

    code, r = http_json("POST", "/drill/cards/from-weak", {"session_id": S1})
    report("D1 单场聚合出 3 张卡（漏点 3 种）",
           code == 200 and r.get("created") == 3 and r.get("skipped") == 0,
           f"created={r.get('created')} skipped={r.get('skipped')}")

    conn = db()
    dcards = conn.execute(
        "SELECT id, question, answer, type, source, material_id, material_title "
        "FROM review_cards WHERE source='drill' AND type='recall'").fetchall()
    conn.close()
    report("D2 卡字段（前缀/recall/drill）",
           len(dcards) == 3 and all(c[1].startswith("【陪练薄弱点】") and c[3] == "recall"
                                    and c[4] == "drill" for c in dcards),
           f"n={len(dcards)}")
    by_q = {c[1]: c for c in dcards}
    c1 = by_q.get("【陪练薄弱点】计算盈亏平衡点")
    report("D3 要点卡内容真值（考点+期望要点拼装、材料归属 777）",
           bool(c1) and "什么是成本分析？" in c1[2] and "识别固定成本" in c1[2]
           and c1[5] == 777 and c1[6].startswith("陪练 ·"),
           c1 and f"mid={c1[5]} title={c1[6]}")
    c2 = by_q.get("【陪练薄弱点】风险量化")
    report("D3b 无源材料考点落哨兵 -1", bool(c2) and c2[5] == -1, c2 and f"mid={c2[5]}")

    code, r = http_json("POST", "/drill/cards/from-weak", {"session_id": S1})
    conn = db()
    total_drill = conn.execute(
        "SELECT COUNT(*) FROM review_cards WHERE source='drill' AND type='recall'").fetchone()[0]
    conn.close()
    report("D4 幂等：重复调用 created=0 skipped=3",
           code == 200 and r.get("created") == 0 and r.get("skipped") == 3, f"{r}")
    report("D4b 守卫: 幂等判据非恒真（库内确有 3 张卡）", total_drill == 3, f"total={total_drill}")

    # 跨场模式：补 S2（带一个新漏点「库存周转」+ 已有漏点「计算盈亏平衡点」）
    conn = db()
    cur = conn.execute(
        "INSERT INTO drill_sessions (title, scope, status, outline, total_score, created_at, updated_at) "
        "VALUES ('_probe_陪练S2', 'all', 'finished', '[]', 60.0, ?, ?)", (NOW, NOW))
    S2 = cur.lastrowid
    conn.execute(
        "INSERT INTO drill_turns (session_id, seq, outline_index, is_followup, question, "
        "expected_points, source_material_id, source_title, user_answer, score, verdict, "
        "missing_points, created_at) VALUES (?, 1, 0, 0, '库存管理怎么控？', "
        "'[\"安全库存\",\"库存周转率\"]', 778, '供应链讲义', '不知道', 30, 'wrong', "
        "'[\"计算盈亏平衡点\",\"库存周转率\"]', ?)", (S2, NOW))
    conn.commit()
    conn.close()

    code, r = http_json("POST", "/drill/cards/from-weak", {})
    report("D5 跨场聚合：新点出题、已出过的跳过",
           code == 200 and r.get("created") == 1 and r.get("skipped") == 3,
           f"created={r.get('created')} skipped={r.get('skipped')} cards={r.get('cards')}")
    conn = db()
    c3 = conn.execute(
        "SELECT material_id, material_title FROM review_cards "
        "WHERE question='【陪练薄弱点】库存周转率'").fetchone()
    conn.close()
    report("D5b 新点卡归属 S2 的题源材料 778",
           bool(c3) and c3[0] == 778, c3 and f"mid={c3[0]} title={c3[1]}")

    code, r = http_json("POST", "/drill/cards/from-weak", {"session_id": S1, "limit": 1})
    report("D6 limit=1 只取 top1（created=0 skipped=1）",
           code == 200 and r.get("created") == 0 and r.get("skipped") == 1, f"{r}")

    # ---------------- 分辨力自检 ----------------
    print("\n===== 分辨力自检 =====")
    conn = db()
    n_video_cards = conn.execute(
        "SELECT COUNT(*) FROM review_cards WHERE material_id=-2").fetchone()[0]
    n_drill_cards = conn.execute(
        "SELECT COUNT(*) FROM review_cards WHERE source='drill' AND type='recall'").fetchone()[0]
    conn.close()
    report("NEG B7 判据有对象可咬（库内 -2 卡 >0）", n_video_cards > 0, f"n={n_video_cards}")
    report("NEG B8 判据有对象可咬（drill recall 卡 >0）", n_drill_cards > 0, f"n={n_drill_cards}")

finally:
    proc.terminate()
    try:
        proc.wait(timeout=8)
    except Exception:
        proc.kill()

print("\n===== 总计: %d PASS / %d FAIL =====" % (
    sum(1 for x in RESULTS if x[1] == "PASS"), sum(1 for x in RESULTS if x[1] == "FAIL")))
for rid, st, dt in RESULTS:
    if st == "FAIL":
        print("FAILED:", rid, dt)
sys.exit(1 if any(x[1] == "FAIL" for x in RESULTS) else 0)
