# -*- coding: utf-8 -*-
"""三档问答模式探针（2026-10-25）

验证项：
  [U1] without_kb_tools：剥离 3 个检索类工具、保留 save_note（纯函数，确定性）
  [U2] KB_TOOLS 名单恰好 = {list_materials, search_kb, read_material}（防误伤 save_note）
  [E0] 隔离实例迁移：chat_messages 表有 answer_kind 列
  [E1] 基线：auto + 关键词问题 → kb（证明本环境检索**能**命中；后续判据的前提）
  [E2] auto + 无关问题 → general_fallback 且 kb_hit=False
  [E3] general + 同一个关键词问题 → general、kb_hit=False、sources 为空
       （区分性判据：检索本来能中[E1]，通用档必须跳过；E1 不过则本项记 BLOCKED）
  [E4] kb + 关键词问题 → kb
  [E5] kb + 无关问题 → kb_miss 且 kb_hit=False
  [E6] mode='bogus' → HTTP 400
  [E7] 落库：E3 会话的 assistant 消息 answer_kind == 'general'
  [E8] 区分度：四档口径至少出现 3 种（answer_kind 字段不是恒值 → 不是假绿）

隔离实例：四个 ASC_* 一起重定向到 _tmp_probe_modes_20261025/（不碰用户真实数据库）；
只杀自己 Popen 起来的 uvicorn；临时目录留在原地（删除配额纪律），由用户手动清。
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
TMP = WS / "_tmp_probe_modes_20261025"
RESULTS: list[tuple[str, str, str]] = []   # (id, PASS/FAIL/BLOCKED, detail)


def report(rid: str, ok, detail: str = ""):
    status = "PASS" if ok is True else ("BLOCKED" if ok is None else "FAIL")
    RESULTS.append((rid, status, detail))
    print(f"[{status:7}] {rid}  {detail}")


# ---------------- U1/U2：纯函数单测 ----------------
sys.path.insert(0, str(BACKEND))
from app.services import skill_tools as st  # noqa: E402

fake_tools = [
    {"type": "function", "function": {"name": "list_materials"}},
    {"type": "function", "function": {"name": "search_kb"}},
    {"type": "function", "function": {"name": "read_material"}},
    {"type": "function", "function": {"name": "save_note"}},
]
kept = [t["function"]["name"] for t in st.without_kb_tools(fake_tools)]
report("U1", kept == ["save_note"], f"kept={kept}")
report("U2", st.KB_TOOLS == frozenset({"list_materials", "search_kb", "read_material"}),
       f"KB_TOOLS={sorted(st.KB_TOOLS)}")
# 负向自检：空列表进 → 空列表出（want==0 必须有分辨力证明：fake 4 进 1 出已在 U1 覆盖）
report("U1b", st.without_kb_tools([]) == [], "empty→empty")

# ---------------- 隔离实例 ----------------
# 不整目录 rmtree（删除配额纪律）：硬链接跨轮复用，只删 app.db 一个文件拿干净库。
TMP.mkdir(parents=True, exist_ok=True)
(TMP / "files").mkdir(exist_ok=True)
(TMP / "chroma").mkdir(exist_ok=True)
_db = TMP / "app.db"
if _db.exists():
    _db.unlink()

# 向量检索的 BGE 模型按 data_dir/models 寻址（vector.py L60），隔离环境没有 →
# 启动时会尝试联网下载并卡死。硬链接真实模型进来（零拷贝；whisper/zipvoice 与本探针无关）。
REAL_BGE = BACKEND / "data" / "models" / "bge-small-zh-v1.5"
ISO_BGE = TMP / "models" / "bge-small-zh-v1.5"
linked = 0
for src in REAL_BGE.rglob("*"):
    if not src.is_file():
        continue
    dst = ISO_BGE / src.relative_to(REAL_BGE)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        continue
    os.link(src, dst)
    linked += 1
print(f"[setup] BGE 硬链接 {linked} 个文件")

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


def http_json(method: str, path: str, body=None, timeout=15):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with opener.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def ask_sources(question: str, mode: str, session_id=None):
    """发一次流式问答，读到流结束（LLM 无 key 会快速走 error 分支落库），
    返回 (sources_payload, last_event)。"""
    body = {"question": question, "mode": mode}
    if session_id:
        body["session_id"] = session_id
    req = urllib.request.Request(BASE + "/chat/ask/stream",
                                 data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    resp = opener.open(req, timeout=30)
    buf = b""
    sources_payload, last_ev = None, ""
    try:
        while True:
            chunk = resp.read1(4096) if hasattr(resp, "read1") else resp.read(4096)
            if not chunk:
                break
            buf += chunk
            while b"\n\n" in buf:
                block, buf = buf.split(b"\n\n", 1)
                text = block.decode("utf-8", "replace")
                if text.startswith("event: sources"):
                    sources_payload = json.loads(text.split("data:", 1)[1])
                elif text.startswith("event:"):
                    last_ev = text.split("\n", 1)[0][6:].strip()
            if sources_payload and last_ev in ("done", "error"):
                break
    finally:
        resp.close()
    return sources_payload, last_ev


try:
    # 等启动（首启要建库建表 + 迁移）
    ok = False
    for _ in range(90):
        try:
            http_json("GET", "/chat/sessions", timeout=2)
            ok = True
            break
        except Exception:
            time.sleep(1)
    if not ok:
        raise RuntimeError("隔离实例 90s 内未就绪")

    # E0 迁移：answer_kind 列存在
    con = sqlite3.connect(str(TMP / "app.db"))
    cols = {r[1] for r in con.execute("PRAGMA table_info(chat_messages)")}
    report("E0", "answer_kind" in cols, f"cols has answer_kind: {'answer_kind' in cols}")

    # 造数据：1 材料 + 1 笔记（内容含独造词「碳基搅拌器」，>50 字防碎片过滤）
    now = "2026-10-25 12:00:00"
    con.execute("INSERT INTO materials (id, title, parsed_status, created_at) VALUES (1, '探针材料', 'success', ?)", (now,))
    con.execute(
        "INSERT INTO notes (title, content, material_id, created_at) VALUES (?, ?, 1, ?)",
        ("搅拌器笔记",
         "碳基搅拌器是一种假想的实验装置，用于演示检索链路是否正常工作，本身没有任何实际意义。", now))
    con.commit(); con.close()

    HIT_Q = "什么是碳基搅拌器？"
    MISS_Q = "怎么给猫洗澡"

    # E1 基线：auto 命中
    p, _ = ask_sources(HIT_Q, "auto")
    base_ok = bool(p) and p.get("answer_kind") == "kb" and p.get("kb_hit") and len(p.get("sources") or []) >= 1
    report("E1", base_ok, f"auto+关键词 → kind={p and p.get('answer_kind')}, sources={len((p or {}).get('sources') or [])}")

    # E2 auto 未命中
    p2, _ = ask_sources(MISS_Q, "auto")
    report("E2", bool(p2) and p2.get("answer_kind") == "general_fallback" and p2.get("kb_hit") is False,
           f"auto+无关 → kind={p2 and p2.get('answer_kind')}, kb_hit={p2 and p2.get('kb_hit')}")

    # E3 general 跳过检索（区分性判据，挂在 E1 基线上）
    p3, _ = ask_sources(HIT_Q, "general")
    if not base_ok:
        report("E3", None, "基线 E1 未过，本项无法分辨（记 BLOCKED 而非 PASS）")
    else:
        report("E3", bool(p3) and p3.get("answer_kind") == "general" and p3.get("kb_hit") is False
               and not (p3.get("sources") or []),
               f"general+关键词 → kind={p3 and p3.get('answer_kind')}, sources={len((p3 or {}).get('sources') or [])}")

    # E4 kb 命中
    p4, _ = ask_sources(HIT_Q, "kb")
    report("E4", bool(p4) and p4.get("answer_kind") == "kb" and p4.get("kb_hit") is True,
           f"kb+关键词 → kind={p4 and p4.get('answer_kind')}")

    # E5 kb 未命中
    p5, _ = ask_sources(MISS_Q, "kb")
    report("E5", bool(p5) and p5.get("answer_kind") == "kb_miss" and p5.get("kb_hit") is False,
           f"kb+无关 → kind={p5 and p5.get('answer_kind')}")

    # E6 非法 mode → 400
    try:
        ask_sources(HIT_Q, "bogus")
        report("E6", False, "mode=bogus 未报 400")
    except urllib.error.HTTPError as e:
        report("E6", e.code == 400, f"mode=bogus → HTTP {e.code}")

    # E7 落库 answer_kind（E3 的会话）
    sid = (p3 or {}).get("session_id")
    if sid:
        msgs = http_json("GET", f"/chat/sessions/{sid}/messages")
        asst = [m for m in msgs if m.get("role") == "assistant"]
        got = asst[-1].get("answer_kind") if asst else None
        report("E7", got == "general", f"会话{sid} assistant.answer_kind={got}")
    else:
        report("E7", False, "E3 未拿到 session_id")

    # E8 区分度：answer_kind 不是恒值
    kinds = {x.get("answer_kind") for x in (p, p2, p3, p4, p5) if x}
    report("E8", len(kinds) >= 3, f"observed kinds={sorted(kinds)}")

finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()

fails = [r for r in RESULTS if r[1] == "FAIL"]
blocked = [r for r in RESULTS if r[1] == "BLOCKED"]
print(f"\n== 汇总：{len(RESULTS)} 项，PASS {sum(1 for r in RESULTS if r[1]=='PASS')}，"
      f"FAIL {len(fails)}，BLOCKED {len(blocked)} ==")
print(f"隔离实例临时目录（可手动删）：{TMP}")
sys.exit(1 if fails else 0)
