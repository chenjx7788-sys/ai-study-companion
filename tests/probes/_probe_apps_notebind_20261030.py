# -*- coding: utf-8 -*-
"""炼丹炉·笔记绑定探针（2026-10-30）：应用语料宇宙 = 绑定资料 ∪ 显式绑定笔记。

单隔离实例（四个 ASC_* 重定向 + BGE 硬链接）。种子：材料 A「淬火权杖」/ 材料 B「星辉辅食」、
挂在 A 下的笔记 NA（独特词「淬心口诀」）、材料无关笔记 NF（独特词「星尘锚点」）。

关键判据（每条都带分辨力证明）：
- R1 纯笔记应用问 NF 的独特词 → kb_hit 必须 True（fts 精确命中免阈值）。
  若 note_ref_id 锁没生效（note 集合退化全库），NF 当然也会中 —— 所以必须配 R2 负向：
- R2 纯笔记应用问 A 的正文 → kb_hit 必须 False（chunk 集合没锁死则必中）。
- R3 只绑 A 的应用问 NF 独特词 → False（未显式绑定的材料无关笔记绝不进语料）。
- R4 只绑 A 的应用问 NA 独特词 → True（挂在已绑资料下的笔记随资料自动纳入）。
- D 级联：删 NF → 纯笔记应用降 draft、对话入口 400；重新绑资料复活。
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

REPO = pathlib.Path(__file__).resolve().parents[2]
WS = REPO.parent
BACKEND = REPO / "backend"
TMP = WS / "_tmp_probe_notebind_20261030"
RESULTS = []

# 环境预检：探针自己会起隔离 uvicorn，解释器不对时子进程秒退，
# 外表只是一句「READY 未就绪」—— 会被误读成回归。这里提前拦住。
try:
    import uvicorn  # noqa: F401
except ModuleNotFoundError:
    print("[PRECHECK] 当前解释器没有 uvicorn：" + sys.executable, flush=True)
    print(r"[PRECHECK] 请用 C:\Users\陈锦祥\.workbuddy\binaries\python\envs\default\Scripts\python.exe 运行本探针",
          flush=True)
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


def sse_meta(path, body, timeout=120):
    """只读到 meta 事件就关流（kb_hit 判定不需要等 LLM 吐字）。"""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with opener.open(req, timeout=timeout) as r:
            for raw in r:
                line = raw.decode("utf-8").strip()
                if line.startswith("data: "):
                    payload = json.loads(line[6:])
                    if "kb_hit" in payload:
                        return payload
    except urllib.error.HTTPError as e:
        return {"_http": e.code}
    return {}


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

CONTENT_A = ("# 淬火权杖研究\n\n淬火权杖是本书虚构的政治学概念，指权力主体在危机时刻通过仪式性淬火"
             "（象征性让步）重新锻造合法性的过程。全书共九章，分别讨论淬火权杖的起源、形态与边界。")
CONTENT_B = ("# 星辉辅食手册\n\n星辉辅食是本书虚构的婴幼儿喂养方案，主张以谷物星光粉为主料，"
             "按月龄分四阶段添加。星辉辅食强调夜间不喂食原则。")
NOTE_FREE = ("星尘锚点是本笔记虚构的航海概念，指船长在无星夜用来校准航向的心理参照物。"
             "三条使用守则：先定锚再张帆、锚点不可外借、每旬校正一次。")


def seed_material(title, content):
    st, obj = http_json("POST", "/materials/document", {"title": title, "content": content}, timeout=30)
    assert st == 200, f"建材料失败 {st}: {obj}"
    mid = obj["id"]
    for _ in range(90):
        st, m = http_json("GET", f"/materials/{mid}")
        if st == 200 and m.get("parsed_status") == "success":
            return mid
        time.sleep(1)
    raise RuntimeError(f"材料 {mid} 解析超时")


try:
    for _ in range(60):
        try:
            with opener.open(BASE + "/version", timeout=2) as r:
                if r.status == 200:
                    break
        except Exception:
            time.sleep(0.5)
    else:
        _tail = ""
        try:
            _boot_fh.flush()
            _tail = BOOT_LOG.read_bytes().decode("utf-8", "replace")[-1500:]
        except Exception:
            pass
        report("READY", False, "隔离实例未就绪（子进程末段日志见下）")
        print("---- iso uvicorn log tail ----", flush=True)
        print(_tail, flush=True)
        print("---- end ----", flush=True)
        sys.exit(1)
    report("READY", True, f"port={PORT}")

    # M1 迁移：app_notes 表必须被 create_all 建出来
    conn = sqlite3.connect(str(TMP / "app.db"))
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    report("M1 app_notes 建表", "app_notes" in tables, f"has={'app_notes' in tables}")

    # ---- 种子 ----
    t0 = time.time()
    MA = seed_material("探针·淬火权杖研究", CONTENT_A)
    MB = seed_material("探针·星辉辅食手册", CONTENT_B)
    st, na = http_json("POST", "/notes", {"material_id": MA, "title": "淬心口诀笔记",
                                          "content": "淬心口诀是挂在资料甲下的笔记独有词：淬火前先淬心，三息而定。"})
    NA = (na.get("note") or na).get("id") if isinstance(na, dict) else None
    st2, nf = http_json("POST", "/notes/from-chat", {"title": "星尘锚点杂记", "content": NOTE_FREE})
    NF = ((nf.get("note") or {}).get("id")) if isinstance(nf, dict) else None
    report("SEED", bool(st == 200 and NA and st2 == 200 and NF),
           f"A={MA} B={MB} NA={NA} NF={NF} 解析+索引 {time.time()-t0:.1f}s")
    if not (NA and NF):
        print("[FATAL] 种子笔记创建失败，后续用例无意义", flush=True)
        sys.exit(1)

    # ---- CRUD ----
    st, _ = http_json("POST", "/apps", {"name": "x", "persona": "p", "material_ids": [], "note_ids": []})
    report("C1 资料笔记全空=400", st == 400, f"st={st}")
    st, _ = http_json("POST", "/apps", {"name": "x", "persona": "p", "note_ids": [999999]})
    report("C2 不存在笔记=400", st == 400, f"st={st}")

    # 纯笔记应用（本次能力的核心形态：材料无关笔记首次能进语料）
    st, appn = http_json("POST", "/apps", {
        "name": "星尘锚点答疑炉", "persona": "你是星尘锚点杂记的答疑者，只答笔记内容。",
        "miss_text": "锚点之外，概不回答。", "material_ids": [], "note_ids": [NF]})
    AN = appn.get("id")
    report("C3 纯笔记创建", st == 200 and AN and appn.get("note_count") == 1
           and appn.get("material_count") == 0, f"st={st} id={AN} nc={appn.get('note_count')}")
    report("C3b 详情返回笔记快照", bool(appn.get("notes")) and appn["notes"][0].get("id") == NF
           and appn.get("note_ids") == [NF], f"notes={appn.get('notes')}")

    # 混合应用：资料 A + 显式笔记 NF
    st, apph = http_json("POST", "/apps", {
        "name": "淬火混合炉", "persona": "p 人格", "material_ids": [MA], "note_ids": [NF]})
    AH = apph.get("id")
    report("C4 混合创建", st == 200 and AH and apph.get("material_count") == 1
           and apph.get("note_count") == 1, f"st={st} id={AH}")

    # ---- PATCH 绑定语义 ----
    st, obj = http_json("PATCH", f"/apps/{AH}", {"material_ids": []})   # 笔记还在 → 合法
    report("P1 只清空资料（笔记兜底）=200", st == 200 and obj.get("material_count") == 0
           and obj.get("note_count") == 1, f"st={st} mc={obj.get('material_count')}")
    st, _ = http_json("PATCH", f"/apps/{AH}", {"material_ids": [], "note_ids": []})
    report("P2 双空=400", st == 400, f"st={st}")
    st, obj = http_json("PATCH", f"/apps/{AH}", {"material_ids": [MA]})
    report("P3 恢复资料（笔记字段未传不动）", st == 200 and obj.get("material_count") == 1
           and obj.get("note_count") == 1, f"st={st} nc={obj.get('note_count')}")
    st, obj = http_json("PATCH", f"/apps/{AH}", {"note_ids": []})
    report("P4 只清空笔记（资料兜底）=200", st == 200 and obj.get("note_count") == 0
           and obj.get("material_count") == 1, f"st={st}")
    st, obj = http_json("PATCH", f"/apps/{AH}", {"note_ids": [NF]})
    report("P5 重新绑回笔记", st == 200 and obj.get("note_count") == 1, f"st={st}")

    # 增量接口的合计护栏：remove 唯一资料但还有笔记 → 200
    st, obj = http_json("PATCH", f"/apps/{AH}/materials", {"remove": [MA]})
    report("P6 增量删唯一资料（笔记兜底）=200", st == 200 and obj.get("material_count") == 0,
           f"st={st}")
    st, _ = http_json("PATCH", f"/apps/{AN}/materials", {"add": [999999]})
    report("P7 增量加不存在资料=400", st == 400, f"st={st}")

    # ---- 检索锁分辨力（preview 只读 meta，不等 LLM 吐字） ----
    cfgN = {"name": "星尘锚点答疑炉", "persona": "只答笔记内容", "miss_text": "锚点之外不答。",
            "material_ids": [], "note_ids": [NF]}
    meta = sse_meta("/apps/preview/stream", {**cfgN, "question": "星尘锚点的使用守则是什么？"})
    report("R1 纯笔记应用问绑定笔记=命中", meta.get("kb_hit") is True,
           f"kb_hit={meta.get('kb_hit')}（False=笔记没进语料）")
    meta = sse_meta("/apps/preview/stream", {**cfgN, "question": "淬火权杖的起源是什么？"})
    report("R2 纯笔记应用问资料A=未命中", meta.get("kb_hit") is False,
           f"kb_hit={meta.get('kb_hit')}（True=chunk 集合没锁死，FAIL）")
    meta = sse_meta("/apps/preview/stream", {**cfgN, "question": "星辉辅食怎么添加？"})
    report("R2b 纯笔记应用问资料B=未命中", meta.get("kb_hit") is False,
           f"kb_hit={meta.get('kb_hit')}")

    cfgA = {"name": "淬火领读", "persona": "只答书内", "miss_text": "不在炉中。",
            "material_ids": [MA], "note_ids": []}
    meta = sse_meta("/apps/preview/stream", {**cfgA, "question": "星尘锚点的使用守则是什么？"})
    report("R3 未绑笔记绝不进语料（负向）", meta.get("kb_hit") is False,
           f"kb_hit={meta.get('kb_hit')}（True=材料无关笔记泄漏）")
    meta = sse_meta("/apps/preview/stream", {**cfgA, "question": "淬心口诀是什么？"})
    report("R4 挂已绑资料的笔记随资料纳入", meta.get("kb_hit") is True,
           f"kb_hit={meta.get('kb_hit')}（False=随资料纳入被破坏）")

    # ---- 正式对话入口（纯笔记应用走 /chat/ask/stream） ----
    ev_meta = sse_meta("/chat/ask/stream", {"question": "星尘锚点的使用守则是什么？", "app_id": AN})
    # 正式流首个事件是 sources（含 kb_hit）
    report("R5 正式对话纯笔记命中", ev_meta.get("kb_hit") is True, f"kb_hit={ev_meta.get('kb_hit')}")

    # ---- 删除级联 ----
    st, _ = http_json("DELETE", f"/notes/{NF}")
    report("D1 删除被绑笔记=200", st == 200, f"st={st}")
    st, obj = http_json("GET", f"/apps/{AN}")
    report("D2 纯笔记应用降 draft", st == 200 and obj.get("status") == "draft"
           and obj.get("note_count") == 0, f"st={st} status={obj.get('status')}")
    st, obj = http_json("GET", f"/apps/{AH}")
    # AH 在 P6 后只剩 NF 一条绑定；NF 被删 → 绑定全空 → 同降 draft
    report("D3 混合应用绑定删光同降 draft", st == 200 and obj.get("status") == "draft"
           and obj.get("note_count") == 0 and obj.get("material_count") == 0,
           f"st={st} status={obj.get('status')} nc={obj.get('note_count')} mc={obj.get('material_count')}")
    # 草稿态对话入口 400
    data = json.dumps({"question": "hi", "app_id": AN}).encode("utf-8")
    req = urllib.request.Request(BASE + "/chat/ask/stream", data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with opener.open(req, timeout=15) as r:
            st = r.status
    except urllib.error.HTTPError as e:
        st = e.code
    report("D4 草稿态对话=400", st == 400, f"st={st}")
    # 复活：重新绑资料
    st, obj = http_json("PATCH", f"/apps/{AN}", {"material_ids": [MB]})
    report("D5 重新绑资料复活 active", st == 200 and obj.get("status") == "active", f"st={st}")

    # 删应用级联清 AppNote（不留幽灵行）
    st, appn2 = http_json("POST", "/apps", {"name": "临时炉", "persona": "p", "note_ids": [NA]})
    AN2 = appn2.get("id")
    st, _ = http_json("DELETE", f"/apps/{AN2}")
    conn = sqlite3.connect(str(TMP / "app.db"))
    ghost = conn.execute("SELECT COUNT(*) FROM app_notes WHERE app_id=?", (AN2,)).fetchone()[0]
    conn.close()
    report("D6 删应用清 app_notes 幽灵行", st == 200 and ghost == 0, f"ghost={ghost}")

finally:
    proc.terminate()
    try:
        proc.wait(timeout=8)
    except Exception:
        proc.kill()

passed = sum(1 for _, ok in RESULTS if ok is True)
failed = [r for r, ok in RESULTS if ok is False]
skips = sum(1 for _, ok in RESULTS if ok is None)
print(f"\n合计 {len(RESULTS)} 项，通过 {passed}，失败 {len(failed)}，跳过 {skips}")
if failed:
    print("失败项：" + "、".join(failed))
print(f"[cleanup] 临时目录保留待用户清理：{TMP}")
sys.exit(1 if failed else 0)
