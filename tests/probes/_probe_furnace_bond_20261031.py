# -*- coding: utf-8 -*-
"""炼丹炉第七轮探针（2026-10-31）：B1 默契度成长 + C3 miss 清单 + A3 列表活跃排序

隔离实例（四个 ASC_* 重定向 + BGE 硬链接），种子数据 sqlite3 直插：
- 不烧 LLM、不碰检索（纯聚合口径验证，Chroma 空库不触发）；
- 应用行也直插（POST /apps 会走 check_materials_ready 拦不存在的资料，绕开它反而偏离被测面）。

种子设计：
  app1「权杖领读人」：3 会话 / 12 问 / 2 活跃天 → 预期 stage=1「熟络」
    S1(昨天 15:00)：Q1→kb命中 / Q2→kb_miss / Q3→kb_miss / Q4→报错 / Q5→旧数据 kb_hit=false
    S2(今天 09:00)：6 问全命中
    S3(昨天 10:00)：1 问命中
    miss 清单预期：Q5/Q3/Q2（倒序）；不含 Q1（命中）、不含 Q4（报错不算 miss）
  app2「冷启动」：0 会话，updated_at 设 5 天前 → stage=0、last_active=None、排序垫底
  app3「差一天」：3 会话 / 12 问 / 1 活跃天（全在今天）→ 仍 stage=0（分辨力：差一维不升级）

判据分辨力：
- B1-N1 若「差一维也升级」，app3 会变 stage=1 → N1 红；
- C3-N1 若 miss 清单没过滤（把命中/报错也列出），Q1/Q4 会混进来 → N1 红；
- A3-N1 若排序退回 updated_at（没读 last_active），app3(昨天聊过)会排在 app1(今天聊过)前 → N1 红。
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
import urllib.error
import urllib.request
from datetime import datetime, timedelta

REPO = pathlib.Path(__file__).resolve().parents[2]
WS = REPO.parent
BACKEND = REPO / "backend"
TMP = WS / "_tmp_probe_furnace_bond_20261031"
RESULTS = []

# 环境预检：解释器不对时隔离子进程秒退，外表只是「READY 未就绪」——会被误读成回归。
try:
    import uvicorn  # noqa: F401
except ModuleNotFoundError:
    print("[PRECHECK] 当前解释器没有 uvicorn：" + sys.executable, flush=True)
    print(r"[PRECHECK] 请用 C:\Users\陈锦祥\.workbuddy\binaries\python\envs\default\Scripts\python.exe 运行本探针", flush=True)
    print("[PRECHECK] 这不是回归，是环境不对 —— 不计入 PASS/FAIL", flush=True)
    sys.exit(2)


def report(rid, ok, detail=""):
    RESULTS.append((rid, ok))
    tag = "SKIP" if ok is None else ("PASS" if ok else "FAIL")
    print(f"[{tag}] {rid}  {detail}", flush=True)


def http_json(method, path, body=None, timeout=30):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}


# ---------- 隔离实例 ----------
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
print(f"[setup] BGE 硬链接 {linked} 个文件", flush=True)

s = socket.socket(); s.bind(("127.0.0.1", 0)); PORT = s.getsockname()[1]; s.close()
env = dict(os.environ)
env["ASC_DATA_DIR"] = str(TMP)
env["ASC_FILES_DIR"] = str(TMP / "files")
env["ASC_DB_URL"] = f"sqlite:///{TMP / 'app.db'}"
env["ASC_CHROMA_DIR"] = str(TMP / "chroma")
env["PYTHONIOENCODING"] = "utf-8"

BOOT_LOG = TMP / "iso_uvicorn.log"
_boot_fh = open(BOOT_LOG, "wb")
proc = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
    cwd=str(BACKEND), env=env, stdout=_boot_fh, stderr=subprocess.STDOUT)
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
BASE = f"http://127.0.0.1:{PORT}/api"


def seed_db():
    """sqlite3 直插种子。⚠️ 清场纪律允许直连 DB；这里造的是隔离库，与真实库无关。"""
    now = datetime.utcnow()
    today9 = now.replace(hour=9, minute=0, second=0)
    yest10 = (now - timedelta(days=1)).replace(hour=10, minute=0, second=0)
    yest15 = (now - timedelta(days=1)).replace(hour=15, minute=0, second=0)
    five_ago = now - timedelta(days=5)
    fmt = "%Y-%m-%d %H:%M:%S"
    con = sqlite3.connect(str(_db))
    cur = con.cursor()
    apps = [
        (1, "权杖领读人", "book", "人格", five_ago.strftime(fmt), five_ago.strftime(fmt)),
        (2, "冷启动", "flame", "人格", five_ago.strftime(fmt), five_ago.strftime(fmt)),
        (3, "差一天", "halo", "人格", five_ago.strftime(fmt), five_ago.strftime(fmt)),
    ]
    cur.executemany(
        "INSERT INTO apps (id, name, spirit, persona, boundaries, miss_text, welcome,"
        " questions, model_id, suggest_on, status, created_at, updated_at)"
        " VALUES (?,?,?,?,'','','','[]','',1,'active',?,?)", apps)
    # app1：S1 昨天15点 / S2 今天9点 / S3 昨天10点（app3 的 S4-S6 在下面循环里插）
    # ⚠️ 列序：VALUES 里 pinned 是字面量 0，5 个占位符依次 = id/title/created_at/updated_at/app_id
    sess = [
        (1, "S1", yest15.strftime(fmt), yest15.strftime(fmt), 1),
        (2, "S2", today9.strftime(fmt), today9.strftime(fmt), 1),
        (3, "S3", yest10.strftime(fmt), yest10.strftime(fmt), 1),
    ]
    cur.executemany(
        "INSERT INTO chat_sessions (id, title, pinned, created_at, updated_at, app_id)"
        " VALUES (?,?,0,?,?,?)", sess)

    mid = [0]

    def msg(sid, role, content, at, kb=0, kind="", err="", app_id=1):
        mid[0] += 1
        cur.execute(
            "INSERT INTO chat_messages (id, session_id, role, content, reasoning, kb_hit,"
            " answer_kind, error, sources, suggestions, tool_steps, resources, app_id, created_at)"
            " VALUES (?,?,?,?,'',?,?,?,'[]','[]','[]','[]',?,?)",
            (mid[0], sid, role, content, kb, kind, err, app_id, at.strftime(fmt)))

    # S1（昨天，app1）：Q1 命中 / Q2 miss / Q3 miss / Q4 报错 / Q5 旧数据回落 miss
    qa_s1 = [
        ("Q1-权杖是什么", "kb", 1, ""),
        ("Q2-权杖什么铸成", "kb_miss", 0, ""),
        ("Q3-星辉辅食怎么加", "kb_miss", 0, ""),
        ("Q4-报错的提问", "", 0, "生成失败"),
        ("Q5-旧口径miss", "", 0, ""),   # answer_kind 空串 + kb_hit=false → 旧数据回落算 miss
    ]
    for q, kind, kb, err in qa_s1:
        msg(1, "user", q, yest15)
        msg(1, "assistant", "答" if not err else "", yest15, kb=kb, kind=kind, err=err)
    # S2（今天，app1）：6 问全命中 + 1 问重复 miss（Q2 再问一次——验去重计数 ×2）
    for i in range(6):
        msg(2, "user", f"S2-Q{i}", today9)
        msg(2, "assistant", "答", today9, kb=1, kind="kb")
    msg(2, "user", "Q2-权杖什么铸成", today9)
    msg(2, "assistant", "这个问题不在资料范围内。", today9, kb=0, kind="kb_miss")
    # S3（昨天，app1）：1 问命中
    msg(3, "user", "S3Q", yest10)
    msg(3, "assistant", "答", yest10, kb=1, kind="kb")
    # S4（今天，app3）：3 会话 12 问但全在 1 天
    for sid_off in range(3):
        sid = 4 + sid_off
        cur.execute(
            "INSERT INTO chat_sessions (id, title, pinned, created_at, updated_at, app_id)"
            " VALUES (?,?,0,?,?,3)", (sid, f"S4-{sid_off}", today9.strftime(fmt), today9.strftime(fmt)))
        for i in range(4):
            msg(sid, "user", f"S4{sid_off}-Q{i}", today9, app_id=3)
            msg(sid, "assistant", "答", today9, kb=1, kind="kb", app_id=3)
    con.commit()
    con.close()


try:
    ready = False
    for _ in range(90):
        try:
            with opener.open(BASE + "/version", timeout=2) as r:
                if r.status == 200:
                    ready = True
                    break
        except Exception:
            pass
        time.sleep(1)
    else:
        ready = False
    if not ready:
        print("[FAIL] READY 隔离实例未就绪（iso_uvicorn.log 末段）：", flush=True)
        try:
            print("\n".join(BOOT_LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-15:]))
        except Exception:
            pass
        sys.exit(2)
    print(f"[setup] 隔离实例就绪 port={PORT}", flush=True)

    # 等建表完成后直插种子（首次请求后表结构已建）
    http_json("GET", "/apps")
    seed_db()
    print("[setup] 种子数据已直插", flush=True)

    # ---------- B1：默契度口径 ----------
    st, d1 = http_json("GET", "/apps/1")
    b = (d1 or {}).get("bond") or {}
    report("B1-T1 app1 bond=熟络（3场/13问/2天）",
           st == 200 and b.get("stage") == 1 and b.get("label") == "熟络"
           and b.get("sessions") == 3 and b.get("questions") == 13 and b.get("days") == 2,
           f"st={st} bond={b}")
    nxt = b.get("next") or {}
    expect_prog = round((3 / 8 + 13 / 40 + 2 / 4) / 3, 3)
    report("B1-T2 下一级缺口与进度",
           nxt.get("label") == "默契" and nxt.get("sessions") == 8
           and nxt.get("questions") == 40 and nxt.get("days") == 4
           and abs((b.get("progress") or 0) - expect_prog) < 0.002,
           f"next={nxt} progress={b.get('progress')} 预期≈{expect_prog}")

    st, d2 = http_json("GET", "/apps/2")
    b2 = (d2 or {}).get("bond") or {}
    report("B1-T3 app2 冷启动=初识 stage0",
           b2.get("stage") == 0 and b2.get("label") == "初识" and b2.get("sessions") == 0,
           f"bond={b2}")

    st, d3 = http_json("GET", "/apps/3")
    b3 = (d3 or {}).get("bond") or {}
    report("B1-N1 分辨力：3场12问但只1天 → 不升级（仍初识）",
           b3.get("stage") == 0 and b3.get("sessions") == 3 and b3.get("days") == 1,
           f"bond.stage={b3.get('stage')} days={b3.get('days')}（若为 1 说明「差一维也升」是假绿）")

    # ---------- C3：miss 清单（去重计数：Q2 在 S1/S2 各 miss 一次 → 一行 ×2）----------
    st, stats = http_json("GET", "/apps/1/stats")
    mq = (stats or {}).get("miss_questions") or []
    qs = [m.get("q") for m in mq]
    counts = [m.get("count") for m in mq]
    report("C3-T1 miss 清单去重（Q2 聚成一行置首，计数 2）",
           qs == ["Q2-权杖什么铸成", "Q5-旧口径miss", "Q3-星辉辅食怎么加"] and counts == [2, 1, 1],
           f"清单={list(zip(qs, counts))}")
    report("C3-N1 分辨力：命中与报错不在清单",
           "Q1-权杖是什么" not in qs and "Q4-报错的提问" not in qs,
           f"Q1混入={'Q1-权杖是什么' in qs} Q4混入={'Q4-报错的提问' in qs}")
    # miss_count 是消息数（4），清单是去重后的问题数（3）——两个口径各自成立
    report("C3-T2 清单与 miss_count 口径（消息 4 条 / 去重 3 题）",
           stats.get("miss_count") == 4 and len(mq) == 3,
           f"miss_count={stats.get('miss_count')} 清单条数={len(mq)}")

    # ---------- A3：列表活跃排序 + 字段 ----------
    st, apps = http_json("GET", "/apps")
    order = [a["id"] for a in (apps or [])]
    report("A3-T1 活跃倒序（app1 今天 > app3 昨天 > app2 没聊过）",
           order == [1, 3, 2],
           f"顺序={order}（若为 [1,2,3] 或 [2,..] 说明没按 last_active 排）")
    row1 = next((a for a in apps if a["id"] == 1), {})
    row2 = next((a for a in apps if a["id"] == 2), {})
    report("A3-T2 last_active 字段（app1 有值 / app2 为 None）",
           bool(row1.get("last_active")) and row2.get("last_active") is None,
           f"app1.last_active={row1.get('last_active')} app2={row2.get('last_active')}")
    report("A3-T3 slim 列表带 bond 徽章字段",
           (row1.get("bond") or {}).get("label") == "熟络"
           and (row2.get("bond") or {}).get("label") == "初识",
           f"app1.bond={ (row1.get('bond') or {}).get('label') } app2.bond={ (row2.get('bond') or {}).get('label') }")
    report("A3-N1 分辨力：app3(昨天聊过) 必须排在 app2(5天前配置、没聊过) 前",
           order.index(3) < order.index(2),
           f"顺序={order}")

finally:
    proc.terminate()
    try:
        proc.wait(timeout=8)
    except Exception:
        proc.kill()
    _boot_fh.close()

n_pass = sum(1 for _, ok in RESULTS if ok is True)
n_fail = sum(1 for _, ok in RESULTS if ok is False)
n_skip = sum(1 for _, ok in RESULTS if ok is None)
print("\n" + "=" * 60)
print(f"总计 {len(RESULTS)} 项：PASS {n_pass} / FAIL {n_fail} / SKIP {n_skip}")
sys.exit(1 if n_fail else 0)
