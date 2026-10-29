# -*- coding: utf-8 -*-
"""炼丹炉边界探针（2026-10-28）：主探针没覆盖的边界与负向用例。

覆盖：
- 幽灵绑定：资料被删除后，应用的绑定行 / 列表计数 / 对话 / 试聊行为
- 非法入参：app_id 负数、material_ids 非整数、questions 超 3、persona 超长、非法形象令牌
- 跨应用版本回滚、nudge 死会话、mode=general+app_id 强制 kb、重复绑定去重、空 PATCH
判据原则：一律不许 500；语义必须落在文档承诺上。
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
TMP = WS / "_tmp_probe_apps_edge_20261028"
RESULTS = []


def report(rid, ok, detail=""):
    RESULTS.append((rid, ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {rid}  {detail}", flush=True)


def http_json(method, path, body=None, timeout=30, raw=False):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with opener.open(req, timeout=timeout) as r:
            payload = r.read().decode("utf-8")
            return r.status, (payload if raw else json.loads(payload))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}


def sse_post(path, body, timeout=120):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    events = []
    cur_ev = ""
    with opener.open(req, timeout=timeout) as r:
        for raw_ in r:
            line = raw_.decode("utf-8").strip()
            if line.startswith("event: "):
                cur_ev = line[7:]
            elif line.startswith("data: "):
                payload = json.loads(line[6:])
                payload["_ev"] = cur_ev
                events.append(payload)
    return events


# ---------- 隔离实例 ----------
TMP.mkdir(parents=True, exist_ok=True)
(TMP / "files").mkdir(exist_ok=True)
(TMP / "chroma").mkdir(exist_ok=True)
_db = TMP / "app.db"
if _db.exists():
    _db.unlink()

REAL_BGE = BACKEND / "data" / "models" / "bge-small-zh-v1.5"
for src in REAL_BGE.rglob("*"):
    if src.is_file():
        dst = TMP / "models" / "bge-small-zh-v1.5" / src.relative_to(REAL_BGE)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists():
            os.link(src, dst)

REAL_LLM = BACKEND / "data" / "llm_settings.json"
if REAL_LLM.exists():
    shutil.copy(REAL_LLM, TMP / "llm_settings.json")

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
        report("READY", False, "隔离实例未就绪")
        sys.exit(1)
    report("READY", True, f"port={PORT}")

    MA = seed_material("边界·权杖", "# 权杖\n\n权杖是虚构概念，讨论其起源与形态。")
    MB = seed_material("边界·辅食", "# 辅食\n\n辅食是虚构方案，按月龄分阶段。")

    # ============ 1. 非法入参：一律不许 500 ============
    st, _ = http_json("POST", "/chat/ask/stream", {"question": "hi", "app_id": -1})
    report("X1 app_id=-1 → 4xx 不 500", st in (400, 404, 422), f"st={st}")

    st, _ = http_json("POST", "/apps", {"name": "x", "persona": "p", "material_ids": ["abc"]})
    report("X2 非整数资料 id → 4xx 不 500", st in (400, 422), f"st={st}")

    st, _ = http_json("POST", "/apps", {"name": "x", "persona": "p",
                                        "questions": ["a", "b", "c", "d"], "material_ids": [MA]})
    report("X3 建议问题 4 条 → 400", st == 400, f"st={st}")

    st, _ = http_json("POST", "/apps", {"name": "x", "persona": "长" * 4001, "material_ids": [MA]})
    report("X4 人格超长 → 400", st == 400, f"st={st}")

    st, _ = http_json("POST", "/apps", {"name": "x", "persona": "p", "spirit": "g:hack:9",
                                        "material_ids": [MA]})
    report("X5 伪造形象令牌 → 400", st == 400, f"st={st}")

    st, app = http_json("POST", "/apps", {"name": "边界炉", "persona": "只答权杖",
                                          "material_ids": [MA, MA, MA]})
    report("X6 重复绑定去重", st == 200 and app.get("material_count") == 1,
           f"count={app.get('material_count')}")
    AID = app.get("id")

    st, obj = http_json("PATCH", f"/apps/{AID}", {})
    report("X7 空 PATCH = 200 无副作用", st == 200 and obj.get("persona") == "只答权杖", f"st={st}")

    st, _ = http_json("POST", f"/apps/{AID}/versions/999999/restore")
    report("X8 回滚不存在版本 → 404 不 500", st == 404, f"st={st}")

    st, _ = http_json("POST", f"/apps/{AID}/nudge", {"session_id": 999999})
    report("X9 nudge 死会话 → 4xx 不 500", st in (400, 404), f"st={st}")

    # ============ 2. mode=general + app_id：必须被强制成 kb 语义 ============
    ev = sse_post("/chat/ask/stream",
                  {"question": "推荐几只科技股吧", "app_id": AID, "mode": "general"}, timeout=150)
    src_ev = next((e for e in ev if e.get("_ev") == "sources"), {})
    report("X10 应用+general 档仍强制 kb（书外=miss）",
           src_ev.get("answer_kind") == "kb_miss", f"kind={src_ev.get('answer_kind')}")

    # ============ 3. 幽灵绑定：删除被绑资料 ============
    MC = seed_material("边界·将被删", "# 将被删\n\n这份资料马上会被删除，用于测幽灵绑定。")
    st, app2 = http_json("POST", "/apps", {"name": "幽灵炉", "persona": "只答将被删",
                                           "material_ids": [MC]})
    GID = app2.get("id")
    st, _ = http_json("DELETE", f"/materials/{MC}")
    report("Y0 删除被绑资料本身成功", st == 200, f"st={st}")

    st, obj = http_json("GET", f"/apps/{GID}")
    mats = obj.get("materials") or []
    cnt = obj.get("material_count")
    report("Y1 幽灵绑定：列表计数与材料清单一致",
           st == 200 and cnt == len(mats),
           f"count={cnt} 实际材料数={len(mats)}（不一致=幽灵计数）")
    conn = sqlite3.connect(str(TMP / "app.db"))
    ghost = conn.execute("SELECT COUNT(*) FROM app_materials WHERE app_id=?", (GID,)).fetchone()[0]
    g_status = conn.execute("SELECT status FROM apps WHERE id=?", (GID,)).fetchone()[0]
    conn.close()
    report("Y2 幽灵绑定：绑定行已级联清理", ghost == 0, f"残留绑定行={ghost}")
    report("Y2b 0 绑定应用降级草稿态", g_status == "draft", f"status={g_status}")

    st, obj = http_json("POST", "/chat/ask/stream", {"question": "权杖起源", "app_id": GID})
    report("Y3 空绑定应用对话 → 400 明确引导（不是永远空命中）",
           st == 400 and "重新绑定" in str(obj.get("detail", "")), f"st={st} detail={str(obj.get('detail'))[:30]}")
    st, _ = http_json("POST", "/apps/preview/stream",
                      {"question": "q", "material_ids": [MC]})
    report("Y4 试聊引用已删资料 → 400", st == 400, f"st={st}")
    st, obj = http_json("GET", f"/apps/{GID}/stats")
    report("Y5 幽灵应用 stats 不 500", st == 200, f"st={st}")

    # Y6 复活：重新绑资料 → status 回 active、对话恢复
    st, _ = http_json("PATCH", f"/apps/{GID}/materials", {"add": [MA]})
    conn = sqlite3.connect(str(TMP / "app.db"))
    g_status2 = conn.execute("SELECT status FROM apps WHERE id=?", (GID,)).fetchone()[0]
    conn.close()
    ev = sse_post("/chat/ask/stream", {"question": "权杖起源", "app_id": GID}, timeout=120)
    src_ev = next((e for e in ev if e.get("_ev") == "sources"), {})
    report("Y6 重新绑定复活（active+对话恢复）",
           g_status2 == "active" and src_ev.get("session_id") is not None,
           f"status={g_status2} sid={src_ev.get('session_id')}")

    # ============ 4. 跨应用版本回滚 ============
    http_json("PATCH", f"/apps/{AID}", {"persona": "第二版人格"})
    st, vs = http_json("GET", f"/apps/{AID}/versions")
    st, vs_g = http_json("GET", f"/apps/{GID}/versions")
    if vs:
        st, _ = http_json("POST", f"/apps/{GID}/versions/{vs[0]['id']}/restore")
        report("X11 用 A 的版本回滚 B → 404", st == 404, f"st={st}")
    else:
        report("X11 用 A 的版本回滚 B → 404", False, "前置：A 无版本")

finally:
    proc.terminate()
    try:
        proc.wait(timeout=8)
    except Exception:
        proc.kill()

passed = sum(1 for _, ok in RESULTS if ok is True)
failed = [r for r, ok in RESULTS if ok is False]
print(f"\n合计 {len(RESULTS)} 项，通过 {passed}，失败 {len(failed)}")
if failed:
    print("失败项：" + "、".join(failed))
print(f"[cleanup] 临时目录保留待用户清理：{TMP}")
sys.exit(1 if failed else 0)
