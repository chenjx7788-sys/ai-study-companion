# -*- coding: utf-8 -*-
"""A6-B4 · AI 陪练合并冒烟探针（2026-10-25）

背景：10-08 的七支陪练探针已在工作区清理中归档/删除。本探针是当前盘上**唯一**的陪练
合并冒烟（隔离实例，不调真实 LLM——start/answer 流式链路不在本探针范围）。

覆盖：scope 预览 / 会话 CRUD / finish 幂等（written_back_at）/ 趋势空态 /
from-weak 空场与幂等 / tts 400 / asr [NEED_ASR] / 删除会话。
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

WS = pathlib.Path(__file__).resolve().parents[3]  # tests/probes/x.py → 工作区根
BACKEND = WS / "ai-study-companion" / "backend"
TMP = WS / "_tmp_probe_drill_b4_20261025"
RESULTS = []


def report(rid, ok, detail=""):
    RESULTS.append((rid, "PASS" if ok else "FAIL", detail))
    print(("[PASS] " if ok else "[FAIL] ") + rid + "  " + str(detail)[:160])


TMP.mkdir(parents=True, exist_ok=True)
(TMP / "files").mkdir(exist_ok=True)
(TMP / "chroma").mkdir(exist_ok=True)
_db = TMP / "app.db"
if _db.exists():
    _db.unlink()
REAL_BGE = BACKEND / "data" / "models" / "bge-small-zh-v1.5"
ISO_BGE = TMP / "models" / "bge-small-zh-v1.5"
for src in REAL_BGE.rglob("*"):
    if src.is_file():
        dst = ISO_BGE / src.relative_to(REAL_BGE)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists():
            os.link(src, dst)

# 建会话有 llm_ready() 前置校验 → 拷入真实 LLM 配置（只读拷贝；本探针不真调 LLM）
import shutil
real_cfg = BACKEND / "data" / "llm_settings.json"
if real_cfg.exists():
    shutil.copy2(real_cfg, TMP / "llm_settings.json")

s = socket.socket(); s.bind(("127.0.0.1", 0)); PORT = s.getsockname()[1]; s.close()
env = dict(os.environ)
env["ASC_DATA_DIR"] = str(TMP)
env["ASC_FILES_DIR"] = str(TMP / "files")
env["ASC_DB_URL"] = f"sqlite:///{TMP / 'app.db'}"
env["ASC_CHROMA_DIR"] = str(TMP / "chroma")
env["PYTHONIOENCODING"] = "utf-8"
proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app",
                         "--host", "127.0.0.1", "--port", str(PORT)],
                        cwd=str(BACKEND), env=env,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
BASE = f"http://127.0.0.1:{PORT}/api"


def http(method, path, body=None, timeout=20):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8") or "{}")
        except Exception:
            return e.code, {}


try:
    ready = False
    for _ in range(60):
        try:
            with opener.open(BASE + "/version", timeout=2) as r:
                if r.status == 200:
                    ready = True
                    break
        except Exception:
            time.sleep(0.5)
    assert ready, "隔离实例未就绪"

    # 种子材料：scope=all 的建会话要求范围内有可出题内容（空库会 400）
    NOW = time.strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(str(TMP / "app.db"))
    cur = conn.execute(
        "INSERT INTO materials (title, format, parsed_status, created_at) "
        "VALUES ('_probe_b4材料', 'txt', 'success', ?)", (NOW,))
    _mid = cur.lastrowid
    conn.execute(
        "INSERT INTO material_chunks (material_id, page_no, content) VALUES (?, 1, ?)",
        (_mid, "成本管理基础：固定成本不随产量变化；可变成本随产量线性变化；"
               "盈亏平衡点是总收入等于总成本的产量点。"))
    conn.commit()
    conn.close()

    # D1 范围预览（纯 DB 统计）
    code, r = http("POST", "/drill/scope/preview", {"scope": "all"})
    report("D1 scope/preview 200 且返回估算字段",
           code == 200 and isinstance(r, dict) and len(r) > 0, f"code={code} keys={list(r)[:6]}")

    # D2 建会话
    code, r = http("POST", "/drill/sessions",
                   {"title": "_probe_b4", "scope": "all",
                    "config": {"question_count": 3, "follow_up": "shallow", "show_answer": True}})
    sid = r.get("id")
    report("D2 创建会话", code == 200 and bool(sid), f"code={code} id={sid}")

    # D3 详情
    code, r = http("GET", f"/drill/sessions/{sid}")
    report("D3 会话详情可读", code == 200 and r.get("id") == sid and "turns" in r,
           f"status={r.get('status')}")

    # D4 finish 幂等（结算 + 错题回写只跑一次）
    code1, r1 = http("POST", f"/drill/sessions/{sid}/finish", {"reason": "abort"})
    code2, r2 = http("POST", f"/drill/sessions/{sid}/finish", {"reason": "abort"})
    conn = sqlite3.connect(str(TMP / "app.db"))
    wb = conn.execute("SELECT written_back_at, status FROM drill_sessions WHERE id=?",
                      (sid,)).fetchone()
    conn.close()
    report("D4 finish 两次都 200 且 written_back_at 已落",
           code1 == 200 and code2 == 200 and wb and wb[0] is not None and wb[1] == "finished",
           f"wb={wb}")

    # D5 趋势：只有 1 场 → n_sessions=1、weak 为空（不编造）
    code, r = http("GET", "/drill/trend")
    report("D5 趋势空态如实（n=1, weak=[]）",
           code == 200 and r.get("n_sessions") == 1 and r.get("weak") == [], r)

    # D6 from-weak：本场无漏点 → created=0
    code, r = http("POST", "/drill/cards/from-weak", {"session_id": sid})
    report("D6 from-weak 空场 created=0", code == 200 and r.get("created") == 0, r)

    # D6b 守卫：跨场模式场次不足 → 400（证明 D6 的 0 不是接口坏了）
    code, r = http("POST", "/drill/cards/from-weak", {})
    report("D6b 跨场模式场次不足 → 400", code == 400, f"code={code} {str(r)[:60]}")

    # D7 tts 空文本 400 / asr 未装模型 [NEED_ASR]
    code, r = http("POST", "/drill/tts", {"text": ""})
    report("D7 tts 空文本 → 400", code == 400, f"code={code}")
    code, r = http("POST", "/drill/asr", body=None)  # 无文件 → 422
    report("D8 asr 无文件 → 422/400", code in (400, 422), f"code={code}")

    # D9 删除会话
    code, r = http("DELETE", f"/drill/sessions/{sid}")
    code2, r2 = http("GET", f"/drill/sessions/{sid}")
    report("D9 删除会话后详情 404", code == 200 and code2 == 404, f"del={code} get={code2}")

    # D10 未授权路径不穿透：给超上限 count 也能建（config 由服务兜底）——跳过，归 P4。
finally:
    proc.terminate()
    try:
        proc.wait(timeout=8)
    except Exception:
        proc.kill()

print("\n===== 总计: %d PASS / %d FAIL =====" % (
    sum(1 for x in RESULTS if x[1] == "PASS"), sum(1 for x in RESULTS if x[1] == "FAIL")))
sys.exit(1 if any(x[1] == "FAIL" for x in RESULTS) else 0)
