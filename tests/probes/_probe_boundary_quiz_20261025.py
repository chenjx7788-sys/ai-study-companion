# -*- coding: utf-8 -*-
"""A5 · P2 边界测试题验收探针（2026-10-25）

三段：
  U 系 单元（mock summary_chat）：总数不变 / 边界数=max(1,N//4) / 交错不堆尾 / 降级 / 500 纪律 / 计数边界
  C 系 契约：白名单 +1、SettingsView +1、prompt_doc_kind 仍不在、KIND_ORDER 未动
  E 系 端到端（隔离实例 + 真模型）：POST /review/quiz 出 8 题，边界题正确项含「未涉及」
"""
import json
import os
import pathlib
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request

WS = pathlib.Path(__file__).resolve().parents[3]  # tests/probes/x.py → 工作区根
BACKEND = WS / "ai-study-companion" / "backend"
TMP = WS / "_tmp_probe_boundary_20261025"
RESULTS = []


def report(rid, ok, detail=""):
    RESULTS.append((rid, "PASS" if ok else "FAIL", detail))
    print(("[PASS] " if ok else "[FAIL] ") + rid + "  " + str(detail)[:170])


# ================= U 系：单元（mock summary_chat） =================
print("===== U 系：混题逻辑（mock LLM） =====")
sys.path.insert(0, str(BACKEND))
from fastapi import HTTPException  # noqa: E402
from app.services import llm  # noqa: E402

calls = []


def make_fake(boundary_fail=False, main_fail=False):
    def fake(messages, kind=None, **kw):
        sys_prompt = messages[0]["content"]
        m = re.search(r"生成 (\d+) 道", sys_prompt)
        n = int(m.group(1)) if m else 0
        calls.append(("boundary" if "边界识别" in sys_prompt else "main", n))
        if "边界识别" in sys_prompt and boundary_fail:
            raise RuntimeError("mock 边界题失败")
        if "边界识别" not in sys_prompt and main_fail:
            raise HTTPException(500, "mock 主体失败")   # 与真实 summary_chat 的失败形态一致
        if "边界识别" in sys_prompt:
            return json.dumps([{"question": f"材料是否讲解了量子计算{i}？",
                                "options": ["讲了", "讲了一部分", "材料未涉及该内容", "不确定"],
                                "correct_index": 2,
                                "explanation": "本题考察边界识别：材料未涉及该知识点"}
                               for i in range(n)], ensure_ascii=False)
        return json.dumps([{"question": f"主体题{i}", "options": ["A", "B", "C", "D"],
                            "correct_index": 0, "explanation": "x"} for i in range(n)],
                          ensure_ascii=False)
    return fake


orig = llm.summary_chat
try:
    llm.summary_chat = make_fake()
    calls.clear()
    qas = llm.generate_quiz("成本管理", "固定成本与可变成本……盈亏平衡点……", 8)
    boundary = [q for q in qas if "边界识别" in q["explanation"]]
    report("U1 总数不变（8 = 6 主体 + 2 边界）", len(qas) == 8, f"total={len(qas)}")
    report("U2 边界题数 == max(1,8//4)=2", len(boundary) == 2, f"boundary={len(boundary)}")
    report("U3 两次调用的题数拆分正确（main=6/boundary=2）",
           ("main", 6) in calls and ("boundary", 2) in calls, calls)
    pos = [i for i, q in enumerate(qas) if "边界识别" in q["explanation"]]
    report("U4 交错不堆尾（边界题不全在最后两位）", pos != [6, 7], f"pos={pos}")
    report("U5 边界题正确项指向「未涉及」",
           all("未涉及" in q["options"][q["correct_index"]] for q in boundary))

    # 降级：边界失败 → 主体照返
    llm.summary_chat = make_fake(boundary_fail=True)
    qas2 = llm.generate_quiz("成本管理", "内容", 8)
    report("U6 边界失败静默降级（返 6 主体、不抛错）", len(qas2) == 6, f"total={len(qas2)}")

    # 主体失败仍 500
    llm.summary_chat = make_fake(main_fail=True)
    try:
        llm.generate_quiz("成本管理", "内容", 8)
        report("U7 主体失败仍抛 500", False, "未抛异常")
    except HTTPException as e:
        report("U7 主体失败仍抛 500", e.status_code == 500, f"status={e.status_code}")

    # 计数边界
    llm.summary_chat = make_fake()
    calls.clear()
    qas4 = llm.generate_quiz("t", "c", 4)
    report("U8 count=4 → 4 道（3+1）", len(qas4) == 4 and ("main", 3) in calls
           and ("boundary", 1) in calls, f"total={len(qas4)} calls={calls}")
    calls.clear()
    qas1 = llm.generate_quiz("t", "c", 1)
    report("U9 count=1 → 恰好 1 道（不少也不多）", len(qas1) == 1,
           f"total={len(qas1)} calls={calls}")
    calls.clear()
    qas0 = llm.generate_quiz("t", "c", 0)
    report("U10 count=0 → 空且不调用 LLM", qas0 == [] and calls == [], f"calls={calls}")
finally:
    llm.summary_chat = orig

# ================= C 系：契约 =================
print("\n===== C 系：契约 =====")
ss = (BACKEND / "app/services/settings_store.py").read_text(encoding="utf-8")
report("C1 白名单含 prompt_quiz_boundary", '"prompt_quiz_boundary"' in ss)
report("C2 prompt_doc_kind 仍不在白名单（决议 #4 不动摇）", '"prompt_doc_kind"' not in ss)
sv = (BACKEND.parent / "frontend/src/views/SettingsView.vue").read_text(encoding="utf-8")
report("C3 SettingsView promptFields + reactive 都含新键",
       sv.count("prompt_quiz_boundary") >= 2, f"出现 {sv.count('prompt_quiz_boundary')} 次")
report("C4 KIND_ORDER 未动（不新增 kind）", "quiz_boundary" not in sv.split("KIND_ORDER")[1][:200]
       if "KIND_ORDER" in sv else True, "KIND_ORDER 段无 boundary")

# ================= E 系：隔离实例 + 真模型 =================
print("\n===== E 系：真模型出题（隔离实例） =====")
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

    NOW = time.strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(str(TMP / "app.db"))
    cur = conn.execute(
        "INSERT INTO materials (title, format, parsed_status, created_at) "
        "VALUES ('_probe_边界题材料', 'txt', 'success', ?)", (NOW,))
    MID = cur.lastrowid
    conn.execute(
        "INSERT INTO material_chunks (material_id, page_no, content) VALUES (?, 1, ?)",
        (MID, "成本管理基础：固定成本是不随产量变化的成本（如厂房租金）；"
              "可变成本随产量线性变化（如原材料）；盈亏平衡点是总收入等于总成本的产量点，"
              "计算公式为固定成本除以单位边际贡献。"))
    conn.commit()
    conn.close()

    data = json.dumps({"material_id": MID, "count": 8}).encode("utf-8")
    req = urllib.request.Request(BASE + "/review/quiz", data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with opener.open(req, timeout=180) as r:
        res = json.loads(r.read().decode("utf-8"))
    cards = res.get("cards", [])
    print(f"  [e2e] 出卡 {len(cards)} 张，耗时 {time.time()-t0:.0f}s")
    b_cards = [c for c in cards
               if "未涉及" in (c.get("options") or [""])[c.get("correct_index", 0)]]
    report("E1 真模型出题：总卡数 8 张", len(cards) == 8, f"total={len(cards)}")
    report("E2 边界题识别（正确项含「未涉及」）≥1 且 ≤2", 1 <= len(b_cards) <= 2,
           f"boundary={len(b_cards)}")
    report("E3 边界题解析点明「边界识别」",
           all("边界识别" in (c.get("answer") or "") for c in b_cards),
           b_cards and b_cards[0].get("answer", "")[:60])
    report("E4 全部卡 source=quiz（未新增枚举）",
           all(c.get("source") == "quiz" for c in cards))
    for i, c in enumerate(cards):
        mark = "B" if c in b_cards else "."
        print(f"    [{mark}] {c['question'][:44]}  正确项: {c['options'][c['correct_index']][:22]}")
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
