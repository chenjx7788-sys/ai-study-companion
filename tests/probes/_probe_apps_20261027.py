# -*- coding: utf-8 -*-
"""炼丹炉后端探针（2026-10-27）：apps 数据层 + CRUD + 绑定 + AI 辅助 + 试聊

单隔离实例（四个 ASC_* 重定向 + BGE 硬链接 + 复制真实 llm_settings.json 走真 LLM 路径）。
种子：材料 A「淬火权杖」/ 材料 B「星辉辅食」—— 两份内容互不重叠。

关键判据：
- V5 检索锁分辨力：应用只绑 A 却问 B 的内容 → kb_hit 必须为 False。
  若 where=None 静默检索全库，B 的块必命中 → True → FAIL（该红时真红）。
- C3/V2/C1/C9 等负向断言：空范围在创建、解绑、试聊三个入口都必须 400。
- 真 LLM 调用 4 次（draft/polish/preview×2），隔离实例不计费污染：log_usage 进临时库。
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
import urllib.error
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[2]
WS = REPO.parent
BACKEND = REPO / "backend"
TMP = WS / "_tmp_probe_apps_20261027"
RESULTS = []


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


def sse_post(path, body, timeout=120):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    events = []
    cur_ev = ""
    with opener.open(req, timeout=timeout) as r:
        for raw in r:
            line = raw.decode("utf-8").strip()
            if line.startswith("event: "):
                cur_ev = line[7:]
            elif line.startswith("data: "):
                payload = json.loads(line[6:])
                payload["_ev"] = cur_ev          # chat 主流是 event+data 双行；preview 仅 data
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

REAL_LLM = BACKEND / "data" / "llm_settings.json"
if REAL_LLM.exists():
    shutil.copy(REAL_LLM, TMP / "llm_settings.json")
    print("[setup] 已复制真实 llm_settings.json（草稿/优化/试聊走真模型）", flush=True)
else:
    print("[setup] ⚠️ 未找到 llm_settings.json，真模型用例将 400", flush=True)

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

CONTENT_A = ("# 淬火权杖研究\n\n淬火权杖是本书虚构的政治学概念，指权力主体在危机时刻通过仪式性淬火"
             "（象征性让步）重新锻造合法性的过程。全书共九章，分别讨论淬火权杖的起源、形态与边界。"
             "淬火权杖不同于一般强制力：它以仪式而非暴力为后盾。第三章给出三个历史案例。")
CONTENT_B = ("# 星辉辅食手册\n\n星辉辅食是本书虚构的婴幼儿喂养方案，主张以谷物星光粉为主料，"
             "按月龄分四阶段添加。第六章讨论过敏体质的调整。星辉辅食强调夜间不喂食原则。")


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

    # M1 迁移：新表 + 两个 app_id 列
    conn = sqlite3.connect(str(TMP / "app.db"))
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    sess_cols = {r[1] for r in conn.execute("PRAGMA table_info(chat_sessions)")}
    msg_cols = {r[1] for r in conn.execute("PRAGMA table_info(chat_messages)")}
    tables_ok = {"apps", "app_materials"} <= tables
    s_ok, m_ok = "app_id" in sess_cols, "app_id" in msg_cols
    report("M1 迁移", tables_ok and s_ok and m_ok,
           f"tables={tables_ok} sess.app_id={s_ok} msg.app_id={m_ok}")
    conn.close()

    # 种子材料（真实解析管线 → chroma 真索引）
    t0 = time.time()
    MA = seed_material("探针·淬火权杖研究", CONTENT_A)
    MB = seed_material("探针·星辉辅食手册", CONTENT_B)
    report("SEED", True, f"A={MA} B={MB} 解析+索引 {time.time()-t0:.1f}s")

    # ---- CRUD 负向与正向 ----
    st, _ = http_json("POST", "/apps", {"name": "x", "persona": "p", "material_ids": []})
    report("C1 空资料创建=400", st == 400, f"st={st}")
    st, _ = http_json("POST", "/apps", {"name": "x", "persona": "p", "material_ids": [999999]})
    report("C2 不存在资料=400", st == 400, f"st={st}")

    conn = sqlite3.connect(str(TMP / "app.db"))
    conn.execute("UPDATE materials SET parsed_status='parsing' WHERE id=?", (MB,))
    conn.commit(); conn.close()
    st, _ = http_json("POST", "/apps", {"name": "x", "persona": "p", "material_ids": [MB]})
    report("C3 解析中资料=400", st == 400, f"st={st}")
    conn = sqlite3.connect(str(TMP / "app.db"))
    conn.execute("UPDATE materials SET parsed_status='success' WHERE id=?", (MB,))
    conn.commit(); conn.close()

    st, _ = http_json("POST", "/apps", {"name": "x", "persona": "", "material_ids": [MA]})
    report("C4 空人格=400", st == 400, f"st={st}")
    st, _ = http_json("POST", "/apps", {"name": "x", "persona": "p", "spirit": "ghost", "material_ids": [MA]})
    report("C5 非法形象=400", st == 400, f"st={st}")

    st, app = http_json("POST", "/apps", {
        "name": "《淬火权杖》领读人", "spirit": "book",
        "persona": "你是《淬火权杖研究》的领读人，只讲书内内容。",
        "boundaries": "只答本书范围", "miss_text": "星尘法则：此炉只答淬火权杖相关内容，此题不在炉中。",
        "welcome": "你好，我是本书领读人。", "questions": ["淬火权杖是什么？"],
        "material_ids": [MA]})
    AID = app.get("id")
    report("C6 创建成功", st == 200 and AID and app.get("material_count") == 1
           and app.get("session_count") == 0, f"st={st} id={AID}")
    # P1-3：spirit 归一化为独版令牌 g:<deco>:<palette>（老值 book 升級为 g:book:N）
    TOK_RE = r"^g:(book|flame|halo|sprout|star|horn):[0-7]$"
    report("C6b spirit 独版令牌", bool(re.match(TOK_RE, app.get("spirit", "")))
           and app["spirit"].startswith("g:book:"), f"spirit={app.get('spirit')}")

    # P1-3 确定性：同名同装饰 → 令牌一致（前后端同算法是出炉预览真身一致的前提）
    st, dup1 = http_json("POST", "/apps", {"name": "星辉重名炉", "persona": "p", "spirit": "flame", "material_ids": [MB]})
    st, dup2 = http_json("POST", "/apps", {"name": "星辉重名炉", "persona": "p", "spirit": "flame", "material_ids": [MB]})
    report("P1-3c 令牌确定性（同名同装饰同令牌）",
           dup1.get("spirit") == dup2.get("spirit") and bool(re.match(TOK_RE, dup2.get("spirit", ""))),
           f"{dup1.get('spirit')} vs {dup2.get('spirit')}")
    # P1-3 重掷：保留装饰、必换配色
    st, rr = http_json("POST", f"/apps/{dup1.get('id')}/spirit/reroll")
    tok0, tok1 = dup1.get("spirit", ""), rr.get("spirit", "")
    report("P1-3d 换一身（装饰不变配色必变）",
           st == 200 and re.match(TOK_RE, tok1) and tok1[:7] == tok0[:7] and tok1 != tok0,
           f"st={st} {tok0} → {tok1} resp={str(rr)[:80]}")
    http_json("DELETE", f"/apps/{dup1.get('id')}")
    http_json("DELETE", f"/apps/{dup2.get('id')}")

    st, lst = http_json("GET", "/apps")
    report("C7 列表含新应用", st == 200 and any(a.get("id") == AID for a in lst), f"count={len(lst) if isinstance(lst, list) else '?'}")

    st, obj = http_json("PATCH", f"/apps/{AID}", {"name": "《淬火权杖》领读人·改"})
    report("C8 改名", st == 200 and obj.get("name").endswith("·改"), f"st={st}")
    st, _ = http_json("PATCH", f"/apps/{AID}", {"material_ids": []})
    report("C9 整体解绑到 0=400", st == 400, f"st={st}")

    st, obj = http_json("PATCH", f"/apps/{AID}/materials", {"add": [MB]})
    report("B1 增量加绑=2", st == 200 and obj.get("material_count") == 2, f"st={st}")
    st, obj = http_json("PATCH", f"/apps/{AID}/materials", {"remove": [MB]})
    report("B2 增量解绑=1", st == 200 and obj.get("material_count") == 1, f"st={st}")
    st, _ = http_json("PATCH", f"/apps/{AID}/materials", {"remove": [MA]})
    report("B3 删最后一份=400", st == 400, f"st={st}")

    # ---- P2 人格版本化（独立新应用，版本号不受前序测试干扰） ----
    st, vapp = http_json("POST", "/apps", {"name": "版本测试炉", "persona": "第一版人格", "material_ids": [MB]})
    VID = vapp.get("id")
    st, vs = http_json("GET", f"/apps/{VID}/versions")
    report("G0 创建不留档", st == 200 and vs == [], f"count={len(vs) if isinstance(vs, list) else '?'}")
    http_json("PATCH", f"/apps/{VID}", {"persona": "第一版人格"})   # 同值改写
    st, vs = http_json("GET", f"/apps/{VID}/versions")
    report("G1 同值改写不留档", st == 200 and vs == [], f"count={len(vs)}")
    http_json("PATCH", f"/apps/{VID}", {"persona": "第二版：更克制"})
    st, vs = http_json("GET", f"/apps/{VID}/versions")
    # ⚠️ 2026-10-30（O2）：版本列表不再下发全量 snapshot（每条含整段人格，前端从未使用）。
    #    判据从「payload 里有 snapshot.persona」改成「快照里的旧值能被头部字段反映出来」，
    #    回滚真的恢复旧人格由 G4 端到端兜住 —— 只验字段存在反而更弱。
    report("G2 实质修改留 v1（快照为旧值）",
           len(vs) == 1 and vs[0]["version"] == 1 and vs[0].get("persona_head") == "第一版人格"
           and "snapshot" not in vs[0],
           f"count={len(vs)} persona_head={str((vs[0].get('persona_head')))[:12] if vs else None}")
    http_json("PATCH", f"/apps/{VID}", {"persona": "第三版：更活泼"})
    st, vs = http_json("GET", f"/apps/{VID}/versions")
    report("G3 再改留 v2", len(vs) == 2 and vs[0]["version"] == 2, f"count={len(vs)}")
    v1_id = vs[1]["id"]
    st, obj = http_json("POST", f"/apps/{VID}/versions/{v1_id}/restore")
    st2, vs2 = http_json("GET", f"/apps/{VID}/versions")
    report("G4 回滚恢复旧人格", st == 200 and obj.get("persona") == "第一版人格",
           f"persona={str(obj.get('persona'))[:12]!r}")
    report("G5 回滚本身留档（可撤销）",
           len(vs2) == 3 and "回滚到 v1" in (vs2[0].get("note") or ""),
           f"count={len(vs2)} note={vs2[0].get('note') if vs2 else None}")
    st, _ = http_json("POST", f"/apps/{VID}/versions/999999/restore")
    report("G6 回滚不存在版本=404", st == 404, f"st={st}")
    # 空应用用量看板零值口径
    st, stt = http_json("GET", f"/apps/{VID}/stats")
    report("G7 空应用 stats 零值", st == 200 and stt.get("question_count") == 0
           and stt.get("hit_rate") is None and len(stt.get("daily") or []) == 7,
           f"q={stt.get('question_count')} daily={len(stt.get('daily') or [])}")
    http_json("DELETE", f"/apps/{VID}")

    # ---- AI 辅助 ----
    st, _ = http_json("POST", "/apps/assist", {"kind": "hack", "material_ids": [MA]})
    report("A0 非法 kind=400", st == 400, f"st={st}")
    st, _ = http_json("POST", "/apps/assist", {"kind": "polish", "field": "persona", "text": ""})
    report("A0b 空文本优化=400", st == 400, f"st={st}")
    st, _ = http_json("POST", "/apps/assist", {"kind": "draft", "material_ids": []})
    report("A0c 空资料草稿=400", st == 400, f"st={st}")

    t0 = time.time()
    st, obj = http_json("POST", "/apps/assist", {"kind": "draft", "material_ids": [MA]}, timeout=120)
    ok = (st == 200 and obj.get("name") and obj.get("persona")
          and obj.get("spirit") in ("book", "flame", "halo", "sprout", "star", "horn")
          and isinstance(obj.get("questions"), list) and 1 <= len(obj["questions"]) <= 3)
    report("A1 草稿（真 LLM）", ok, f"st={st} name={obj.get('name')!r} spirit={obj.get('spirit')} qs={len(obj.get('questions') or [])} {time.time()-t0:.1f}s")

    t0 = time.time()
    st, obj = http_json("POST", "/apps/assist",
                        {"kind": "polish", "field": "persona", "name": "《淬火权杖》领读人",
                         "text": "你是领读人，讲话克制一点"}, timeout=120)
    ok = st == 200 and "【" in obj.get("text", "") and len(obj.get("text", "")) >= 50
    report("A2 优化（真 LLM）", ok, f"st={st} len={len(obj.get('text', ''))} {time.time()-t0:.1f}s")

    # ---- 试聊预览 ----
    st, _ = http_json("POST", "/apps/preview/stream", {"question": "", "material_ids": [MA]})
    report("V1 空问题=400", st == 400, f"st={st}")
    st, _ = http_json("POST", "/apps/preview/stream", {"question": "q", "material_ids": []})
    report("V2 空资料试聊=400", st == 400, f"st={st}")

    cfg = {"name": "《淬火权杖》领读人", "persona": "你是《淬火权杖研究》的领读人，只讲书内内容。",
           "boundaries": "只答本书范围", "miss_text": "星尘法则：此炉只答淬火权杖相关内容，此题不在炉中。",
           "material_ids": [MA]}
    t0 = time.time()
    ev = sse_post("/apps/preview/stream", {**cfg, "question": "淬火权杖是什么？"})
    meta = next((e for e in ev if e.get("_ev") == "meta"), {})
    text = "".join(e.get("t", "") for e in ev if e.get("_ev") == "token")
    done = any(e.get("_ev") == "done" for e in ev)
    report("V3 书内试聊命中", meta.get("kb_hit") is True and len(text) > 20 and done,
           f"kb_hit={meta.get('kb_hit')} len={len(text)} {time.time()-t0:.1f}s")

    t0 = time.time()
    ev = sse_post("/apps/preview/stream", {**cfg, "question": "推荐几只科技股吧"})
    meta = next((e for e in ev if e.get("_ev") == "meta"), {})
    text = "".join(e.get("t", "") for e in ev if e.get("_ev") == "token")
    report("V4 书外试聊未命中+定制口径", meta.get("kb_hit") is False and "星尘" in text,
           f"kb_hit={meta.get('kb_hit')} 含星尘={'星尘' in text} len={len(text)} {time.time()-t0:.1f}s")

    # R1 分辨力：只绑 A 问 B 的内容 → 必须未命中（where=None 泄漏则必命中）
    ev = sse_post("/apps/preview/stream", {**cfg, "question": "星辉辅食怎么添加？"})
    meta = next((e for e in ev if e.get("_ev") == "meta"), {})
    report("V5 检索锁分辨力（绑A问B=未命中）", meta.get("kb_hit") is False,
           f"kb_hit={meta.get('kb_hit')}（True=检索锁破，FAIL）")

    # ---- P1-1 实体召回根治（端到端）：构造跨材料同名实体引用的泄漏路径 ----
    # 应用只绑 A，但把「淬火权杖」的 EntityRef 指一条到 B 的块——若实体路不吃 where，B 必进 sources
    conn = sqlite3.connect(str(TMP / "app.db"))
    chunkB = conn.execute("SELECT id FROM material_chunks WHERE material_id=? ORDER BY id LIMIT 1", (MB,)).fetchone()[0]
    conn.execute("INSERT INTO entities (name, type, material_id, created_at) VALUES ('淬火权杖', 'concept', ?, datetime('now'))", (MA,))
    conn.execute("INSERT INTO entity_refs (entity, ref_type, ref_id, material_id, created_at) VALUES ('淬火权杖', 'chunk', ?, ?, datetime('now'))", (chunkB, MB))
    conn.commit(); conn.close()
    ev = sse_post("/chat/ask/stream", {"question": "淬火权杖", "app_id": AID}, timeout=150)
    srcs = (next((e for e in ev if e.get("_ev") == "sources"), {}) or {}).get("sources") or []
    leaks = [s for s in srcs if s.get("material_id") == MB]
    report("E1 实体路不越界（HTTP 端到端）", not leaks and len(srcs) >= 1,
           f"sources={len(srcs)} 泄漏={len(leaks)}")

    # ---- P1 子进程函数级单测：根治分辨力 + 草稿素材优先级（不走 HTTP，确定性） ----
    # U1: search() 带 where 时实体路贡献命中且不越界；不带 where 的对照组必须含 B（证明泄漏路径真实存在）
    # U2: _material_excerpt 优先级 learn_note_ch（最新版）> summary > chunks
    unit = TMP / "unit"
    # ⚠️ 每轮必须整目录重置：残留库会让 id 累积（第 N 轮 ma/mb id=2N-1/2N），
    #    top_k=8 截断把对照组的 B 挤出命中 → 分辨力断言假红（2026-10-28 实证第 5 轮翻红）
    if unit.exists():
        shutil.rmtree(unit)
    unit.mkdir(exist_ok=True)
    (unit / "files").mkdir(exist_ok=True); (unit / "chroma").mkdir(exist_ok=True)
    # 子进程也会走向量路加载 BGE —— 必须硬链接模型，否则联网下载卡死（老坑）
    for src in REAL_BGE.rglob("*"):
        if src.is_file():
            dst = unit / "models" / "bge-small-zh-v1.5" / src.relative_to(REAL_BGE)
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists():
                os.link(src, dst)
    code = (
        "import sys\n"
        "sys.path.insert(0, '.')\n"
        "from app.database import Base, engine, SessionLocal\n"
        "import app.models as M\n"
        "Base.metadata.create_all(engine)\n"
        "db = SessionLocal()\n"
        "ma = M.Material(title='甲', parsed_status='success'); mb = M.Material(title='乙', parsed_status='success')\n"
        "db.add(ma); db.add(mb); db.flush()\n"
        "ca = M.MaterialChunk(material_id=ma.id, content='淬火权杖在甲书的论述'); cb = M.MaterialChunk(material_id=mb.id, content='淬火权杖在乙书的论述')\n"
        "db.add(ca); db.add(cb); db.flush()\n"
        "db.add(M.Entity(name='淬火权杖', type='concept', material_id=ma.id))\n"
        "db.add(M.EntityRef(entity='淬火权杖', ref_type='chunk', ref_id=ca.id, material_id=ma.id))\n"
        "db.add(M.EntityRef(entity='淬火权杖', ref_type='chunk', ref_id=cb.id, material_id=mb.id))\n"
        "db.add(M.AIAsset(material_id=ma.id, type='summary', content='摘要标记SUMMARY'))\n"
        "db.add(M.AIAsset(material_id=ma.id, type='learn_note_ch', content='星徽章OLD', anchor={'ch':1,'name':'一'}, version=1))\n"
        "db.add(M.AIAsset(material_id=ma.id, type='learn_note_ch', content='星徽章NEW', anchor={'ch':1,'name':'一'}, version=2))\n"
        "db.commit()\n"
        "from app.services import search as ss, apps\n"
        "hits = ss.search('淬火权杖', db, where={'material_id': {'$in': [ma.id]}})\n"
        "assert hits and all(h.get('material_id') == ma.id for h in hits), hits\n"
        "assert any(h.get('from_entity') for h in hits), '实体路未贡献命中——分辨力缺失'\n"
        "hits_all = ss.search('淬火权杖', db)\n"
        "assert any(h.get('material_id') == mb.id for h in hits_all), '对照组无 B——泄漏路径不存在，断言无分辨力'\n"
        "ex = apps._material_excerpt(db, ma)\n"
        "assert '星徽章NEW' in ex and 'OLD' not in ex and '摘要标记' not in ex, ex[:140]\n"
        "print('UNIT_OK')\n"
    )
    env2 = dict(os.environ)
    env2["ASC_DATA_DIR"] = str(unit); env2["ASC_FILES_DIR"] = str(unit / "files")
    env2["ASC_DB_URL"] = f"sqlite:///{unit / 'app.db'}"; env2["ASC_CHROMA_DIR"] = str(unit / "chroma")
    env2["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run([sys.executable, "-c", code], cwd=str(BACKEND), env=env2,
                       capture_output=True, text=True, timeout=120)
    report("U1+U2 实体根治分辨力 + 素材优先级（子进程）", "UNIT_OK" in r.stdout,
           ((r.stdout or "") + (r.stderr or ""))[-160:].replace("\n", " "))

    # ---- Phase 2：应用对话链路与隔离（chat.py 第四装配分支） ----
    # S1 书内提问：kb 命中 + suggestions 事件 + 会话/消息 app_id 落库
    ev = sse_post("/chat/ask/stream", {"question": "淬火权杖是什么？", "app_id": AID}, timeout=150)
    src_ev = next((e for e in ev if e.get("_ev") == "sources"), {})
    tok = "".join(e.get("t", "") for e in ev if e.get("_ev") == "token")
    sugg_ev = [e for e in ev if e.get("_ev") == "suggestions"]
    sid = src_ev.get("session_id")
    conn = sqlite3.connect(str(TMP / "app.db"))
    sess_app = conn.execute("SELECT app_id FROM chat_sessions WHERE id=?", (sid,)).fetchone()[0]
    msg_apps = {r[0] for r in conn.execute("SELECT DISTINCT app_id FROM chat_messages WHERE session_id=?", (sid,))}
    conn.close()
    report("S1 应用书内命中", src_ev.get("kb_hit") is True and src_ev.get("answer_kind") == "kb" and len(tok) > 10,
           f"kind={src_ev.get('answer_kind')} len={len(tok)}")
    report("S1b app_id 落库（会话+消息）", sess_app == AID and msg_apps == {AID},
           f"sess={sess_app} msgs={msg_apps}")
    report("S1c 追问建议事件（suggest_on 默认开）", len(sugg_ev) >= 1, f"count={len(sugg_ev)}")

    # S2 书外提问：kb_miss + 人格化拒答（含定制口径标记）
    ev = sse_post("/chat/ask/stream", {"question": "推荐几只科技股吧", "app_id": AID}, timeout=150)
    src_ev = next((e for e in ev if e.get("_ev") == "sources"), {})
    tok = "".join(e.get("t", "") for e in ev if e.get("_ev") == "token")
    report("S2 应用书外=kb_miss+定制口径", src_ev.get("answer_kind") == "kb_miss" and "星尘" in tok,
           f"kind={src_ev.get('answer_kind')} 含星尘={'星尘' in tok}")

    # S3 会话隔离：主列表不见应用会话；应用列表只见自己的
    st, main_list = http_json("GET", "/chat/sessions")
    st2, app_list = http_json("GET", f"/chat/sessions?app_id={AID}")
    main_ids = {s.get("id") for s in main_list} if isinstance(main_list, list) else set()
    app_ids = {s.get("id") for s in app_list} if isinstance(app_list, list) else set()
    report("S3 会话隔离", sid not in main_ids and sid in app_ids,
           f"主列表含它={sid in main_ids} 应用列表含它={sid in app_ids}")
    # 主问答会话不受影响：不带 app_id 的提问落 NULL，且进主列表
    ev = sse_post("/chat/ask/stream", {"question": "你好"}, timeout=150)
    main_sid = next((e for e in ev if e.get("_ev") == "sources"), {}).get("session_id")
    conn = sqlite3.connect(str(TMP / "app.db"))
    m_app = conn.execute("SELECT app_id FROM chat_sessions WHERE id=?", (main_sid,)).fetchone()[0]
    conn.close()
    st, main_list2 = http_json("GET", "/chat/sessions")
    main_ids2 = {s.get("id") for s in main_list2} if isinstance(main_list2, list) else set()
    report("S3b 主问答会话 NULL 且进主列表", m_app is None and main_sid in main_ids2,
           f"app_id={m_app} 在主列表={main_sid in main_ids2}")

    # S4 跨应用会话守卫：用 A 的会话 + B 应用的 app_id → 400
    st, app2 = http_json("POST", "/apps", {"name": "星辉辅食顾问", "persona": "只答辅食", "material_ids": [MB]})
    AID2 = app2.get("id")
    st, _ = http_json("POST", "/chat/ask/stream", {"question": "hi", "app_id": AID2, "session_id": sid})
    report("S4 跨应用会话=400", st == 400, f"st={st}")

    # S5 应用对话挂技能 → 400（内置技能 term_translate 恒在）
    st, _ = http_json("POST", "/chat/ask/stream", {"question": "hi", "app_id": AID, "skill_id": "term_translate"})
    report("S5 应用挂技能=400", st == 400, f"st={st}")

    # M2+S6 模型解析与追问开关：PATCH 后 ask → 无 suggestions 事件 + usage 模型=应用模型
    first_model = None
    try:
        conf = json.loads((TMP / "llm_settings.json").read_text(encoding="utf-8"))
        first_model = (conf.get("llm_models") or [None])[0]
    except Exception:
        pass
    if not first_model:
        report("M2 模型解析", None, "SKIP：未配置多模型")   # 基线缺失记 SKIP，不给假 PASS
    else:
        http_json("PATCH", f"/apps/{AID}", {"model_id": first_model["id"], "suggest_on": False})
        before = sqlite3.connect(str(TMP / "app.db")).execute(
            "SELECT COUNT(*) FROM llm_usage WHERE kind='chat'").fetchone()[0]
        ev = sse_post("/chat/ask/stream", {"question": "淬火权杖形态有几种？", "app_id": AID}, timeout=150)
        sugg_ev2 = [e for e in ev if e.get("_ev") == "suggestions"]
        conn = sqlite3.connect(str(TMP / "app.db"))
        row = conn.execute("SELECT model FROM llm_usage WHERE kind='chat' ORDER BY id DESC LIMIT 1").fetchone()
        after = conn.execute("SELECT COUNT(*) FROM llm_usage WHERE kind='chat'").fetchone()[0]
        conn.close()
        report("S6 suggest_on=False 不产追问", len(sugg_ev2) == 0,
               f"count={len(sugg_ev2)}（分辨力对照=S1c 已证开关开时有事件）")
        report("M2 模型解析=应用默认", after > before and row and row[0] == first_model.get("model"),
               f"usage_model={row[0] if row else None} 期望={first_model.get('model')}")
        http_json("PATCH", f"/apps/{AID}", {"model_id": "", "suggest_on": True})   # 还原

    # P1-4 闲置搭话：人格化一句话 + ≤2 建议问题；跨域会话 400
    t0 = time.time()
    st, nj = http_json("POST", f"/apps/{AID}/nudge", {"session_id": sid}, timeout=120)
    report("N1 搭话生成（真 LLM）", st == 200 and nj.get("text") and len(nj.get("questions", [])) <= 2,
           f"st={st} text={str(nj.get('text'))[:24]!r} qs={len(nj.get('questions') or [])} {time.time()-t0:.1f}s")
    st, _ = http_json("POST", f"/apps/{AID}/nudge", {"session_id": main_sid})
    report("N2 搭话用主问答会话=400", st == 400, f"st={st}")

    # P2 用量看板（真实对话数据：S1/E1 命中 + S2 未命中 + M2 一问）
    st, stt = http_json("GET", f"/apps/{AID}/stats")
    report("G8 stats 口径（含命中与未命中）",
           st == 200 and stt.get("question_count", 0) >= 3 and stt.get("hit_count", 0) >= 1
           and stt.get("miss_count", 0) >= 1 and stt.get("session_count", 0) >= 1
           and isinstance(stt.get("hit_rate"), float),
           f"q={stt.get('question_count')} hit={stt.get('hit_count')} miss={stt.get('miss_count')} rate={stt.get('hit_rate')}")

    http_json("DELETE", f"/apps/{AID2}")

    # ---- 删除级联 ----
    st, obj = http_json("DELETE", f"/apps/{AID}")
    report("D1 删除应用", st == 200 and obj.get("ok"), f"st={st}")
    st, _ = http_json("GET", f"/apps/{AID}")
    report("D2 删后 404", st == 404, f"st={st}")

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
