"""P0 验收探针：10 条判据（含 5 条负向）。

纪律：
  · 只断言"渲染出来了"= 假绿 → 每条都要么造种子、要么反向断言
  · `want == 0` 的断言天生有缺口（needle 写错也是 0）→ 每条负向都配一个 want≥1 守卫
  · 隔离实例：临时目录 + monkeypatch，绝不碰真实 app/skills 与 data/skills
"""
import os
import shutil
import sys
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))  # tests/probes/x.py → 工作区根
BACKEND = os.path.join(BASE, "ai-study-companion", "backend")
sys.path.insert(0, BACKEND)
os.environ.setdefault("ASC_DATA_DIR", os.path.join(BACKEND, "data"))

from fastapi import FastAPI                      # noqa: E402
from fastapi.testclient import TestClient         # noqa: E402
from app.services import skills as svc            # noqa: E402
from app.routers import skills as skills_router  # noqa: E402

# 独立小 app：不加载主 app，避免初始化 DB/模型，判定只针对 skills 路由
app = FastAPI()
app.include_router(skills_router.router, prefix="/api")
client = TestClient(app)

fails, oks, skips = [], [], []


def ok(name, cond, detail=""):
    (oks if cond else fails).append(name)
    print(("PASS " if cond else "FAIL ") + name + (("  | " + str(detail)) if detail else ""))


def skip(name, why):
    skips.append(name)
    print("SKIP " + name + "  | " + why)


def skill_text(sid, *, priority=10, trigger="测试", desc="测试用技能", ver=1,
               triggers=None, instruction="这是一个用于验收的测试技能。"):
    import json as _json
    man = {
        "schema_version": ver, "id": sid, "name": sid, "icon": "skill", "kind": "chat",
        "description": desc, "triggers": triggers if triggers is not None else [trigger],
        "priority": priority, "citation": "optional", "builtin": True,
    }
    return "```skill\n" + _json.dumps(man, ensure_ascii=False) + "\n```\n\n" + instruction + "\n"


# =====================================================================
print("\n===== Part A · 真实内置技能（证明随包内容是对的）=====")
svc.invalidate()
r = client.get("/api/skills")
body = r.json()
# ⚠️ 断言只对**内置**集：导入的技能会进同一清单（P6 起真实库里就有 17 个 imported），
#    用全集断言「=7 个」会把「用户导入了技能」误判成回归。
all_ids = [x["id"] for x in body["items"]]
ids = [x["id"] for x in body["items"] if x.get("builtin")]
baseline_ids = list(all_ids)   # C1 的前后对比基线（探针不许污染实例）
ok("A1 GET /skills 返回 200", r.status_code == 200, r.status_code)
ok("A2 真实内置技能 = 13 个", len(ids) == 13, ids)
ok("A3 十三个 id 齐全", set(ids) == {"term_translate", "outline_map", "concept_compare", "worked_example", "memory_cards", "argue_check", "feynman_explain", "data_interpretation", "error_analysis", "mindmap", "law_compare", "study_plan", "socratic_ask"}, ids)
ok("A4 无文件被拒（种子文件都合法）", body["rejected"] == [], body["rejected"])
ok("A5 按 priority 降序（20→8）", ids == ["term_translate", "outline_map", "concept_compare", "worked_example", "memory_cards", "argue_check", "feynman_explain", "data_interpretation", "error_analysis", "mindmap", "law_compare", "study_plan", "socratic_ask"], ids)
ok("A6 列表**不含** instructions", all("instructions" not in x for x in body["items"]))
ok("A7 返回 user_dir 供页面显示", bool(body.get("user_dir")), body.get("user_dir"))

d = client.get("/api/skills/term_translate").json()
ok("A8 详情才含 instructions", "instructions" in d and len(d["instructions"]) > 400, len(d.get("instructions", "")))
ok("A9 不存在的技能 → 404", client.get("/api/skills/nope").status_code == 404)

m1 = client.post("/api/skills/match", json={"question": "这个术语怎么译"}).json()
ok("A10 match 命中 term_translate", m1["suggest"] and m1["suggest"]["id"] == "term_translate", m1)
ok("A11 why 有内容（不是空串）", bool(m1["suggest"] and m1["suggest"]["why"]), m1["suggest"])
m2 = client.post("/api/skills/match", json={"question": "今天天气怎么样"}).json()
# ⚠️ 这条是 want==0 型断言 → 必须配 A10（want>=1）作守卫；A10 已 PASS 才算数
ok("A12 无关问题不硬塞技能", m2["suggest"] is None and m2["matched"] == 0, m2,
   )

new_skill_cases = [
    ("请把这一章的知识框架梳理一下", "outline_map"),
    ("这道例题怎么算", "worked_example"),
    ("把这些考点做成记忆卡片", "memory_cards"),
    ("这张图表的趋势怎么解读", "data_interpretation"),
    ("帮我做一份学习计划", "study_plan"),
    ("把这部分画成思维导图", "mindmap"),
]
for i, (question, want) in enumerate(new_skill_cases, start=13):
    m = client.post("/api/skills/match", json={"question": question}).json()
    ok(f"A{i} 新增技能触发词命中 {want}",
       m["suggest"] and m["suggest"]["id"] == want and m["suggest"]["via"] == "rule", m)

# ---- 新增内置技能的静态契约：权限、首轮工具、死占位符、token 预算
builtin_objs = [svc.get_skill(sid) for sid in ids]
ok("A19 13 个内置技能对象都能从注册表取回", all(x is not None for x in builtin_objs), ids)
save_note_ids = {s.id for s in builtin_objs if s and "save_note" in s.capabilities}
ok("A20 save_note 仍只给允许写笔记的内置技能", save_note_ids == {"term_translate", "memory_cards"}, save_note_ids)
tool_first_ids = {s.id for s in builtin_objs if s and s.tool_first}
ok("A21 tool_first 只给找全/读材料类技能",
   tool_first_ids == {"term_translate", "outline_map", "concept_compare", "worked_example",
                      "memory_cards", "data_interpretation", "mindmap", "law_compare"},
   tool_first_ids)
placeholder_ids = [s.id for s in builtin_objs if s and "{" in s.instructions]
ok("A22 内置技能正文无 {placeholder} 死占位", placeholder_ids == [], placeholder_ids)
ok("A23 13 个内置技能都在指令 token 预算内",
   all(s and svc.est_tokens(s.instructions) <= svc.MAX_INSTRUCTION_TOKENS for s in builtin_objs),
   [(s.id, svc.est_tokens(s.instructions)) for s in builtin_objs if s])

# =====================================================================
print("\n===== Part B · 负向用例（临时目录，不动真实文件）=====")
tmp = tempfile.mkdtemp(prefix="asc_skillprobe_")
builtin_tmp = os.path.join(tmp, "builtin")
user_tmp = os.path.join(tmp, "user")
os.makedirs(builtin_tmp)
os.makedirs(user_tmp)

_orig_builtin = svc.BUILTIN_DIR
_orig_data_dir = svc.settings.data_dir
try:
    svc.BUILTIN_DIR = builtin_tmp
    svc.settings.data_dir = tmp          # user_dir() = tmp/skills（不存在 → 空）
    svc.invalidate()

    # B0 空目录：空态必须真的空（配 B1 的 want>=1 守卫，两条一起才成立）
    empty = client.get("/api/skills").json()
    ok("B0 空目录 → items 为空", empty["items"] == [], empty["items"])
    ok("B0b 空目录 → rejected 也为空（是空不是错）", empty["rejected"] == [], empty["rejected"])

    # 造 1 个合法种子，作为后续所有负向的 want>=1 守卫
    with open(os.path.join(builtin_tmp, "good_one.skill.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(skill_text("good_one"))
    svc.invalidate()
    guard = client.get("/api/skills").json()
    ok("B1 合法种子被加载（负向守卫）", [x["id"] for x in guard["items"]] == ["good_one"],
       [x["id"] for x in guard["items"]])

    # ---- B2 路径穿越：JSON 里 id = "../evil"
    p = os.path.join(builtin_tmp, "evil_path.skill.md")
    open(p, "w", encoding="utf-8", newline="\n").write(skill_text("../evil"))
    svc.invalidate()
    g = client.get("/api/skills").json()
    loaded = [x["id"] for x in g["items"]]
    ok("B2 守卫：列表里仍有合法技能（不是全空导致假 PASS）", loaded == ["good_one"], loaded)
    ok("B2b id='../evil' 未进入列表", "../evil" not in loaded and all("/" not in i for i in loaded), loaded)
    reasons = " ".join(x["reason"] for x in g["rejected"])
    ok("B2c 被拒且原因提到 id", any("id" in x["reason"] for x in g["rejected"]), reasons[:120])
    os.remove(p)

    # ---- B3 非法正则
    p = os.path.join(builtin_tmp, "bad_re.skill.md")
    open(p, "w", encoding="utf-8", newline="\n").write(skill_text("bad_re", triggers=["([unclosed"]))
    svc.invalidate()
    g = client.get("/api/skills").json()
    ok("B3 守卫：合法技能仍在", [x["id"] for x in g["items"]] == ["good_one"], [x["id"] for x in g["items"]])
    ok("B3b 非法正则 → 加载期拒绝（不是 match 时炸）",
       any("bad_re" in x["file"] and "正则" in x["reason"] for x in g["rejected"]), g["rejected"])
    # 关键：坏技能在库时，match 必须照常工作（说明拒绝发生在加载期，不是延迟到调用）
    mb = client.post("/api/skills/match", json={"question": "测试"}).json()
    ok("B3c 存在坏文件时 match 仍可用（未污染注册表）", mb["suggest"] is not None, mb)
    os.remove(p)

    # ---- B4 文件名与 id 不一致
    p = os.path.join(builtin_tmp, "misnamed.skill.md")
    open(p, "w", encoding="utf-8", newline="\n").write(skill_text("other_id"))
    svc.invalidate()
    g = client.get("/api/skills").json()
    ok("B4 守卫：合法技能仍在", [x["id"] for x in g["items"]] == ["good_one"], [x["id"] for x in g["items"]])
    ok("B4b 文件名≠id → 拒绝且原因点明不一致",
       any("不一致" in x["reason"] for x in g["rejected"]), g["rejected"])
    os.remove(p)

    # ---- B5 schema_version=2
    p = os.path.join(builtin_tmp, "v2skill.skill.md")
    open(p, "w", encoding="utf-8", newline="\n").write(skill_text("v2skill", ver=2))
    svc.invalidate()
    g = client.get("/api/skills").json()
    ok("B5 守卫：合法技能仍在", [x["id"] for x in g["items"]] == ["good_one"], [x["id"] for x in g["items"]])
    ok("B5b schema_version=2 → 拒绝并提示版本",
       any("schema_version" in x["reason"] for x in g["rejected"]), g["rejected"])
    os.remove(p)

    # ---- B6 同名冲突：用户技能与内置同 id → 内置优先 + 记一条拒绝（不静默）
    os.makedirs(os.path.join(tmp, "skills"), exist_ok=True)
    open(os.path.join(tmp, "skills", "good_one.skill.md"), "w", encoding="utf-8", newline="\n").write(
        skill_text("good_one", desc="来自用户目录的同名文件"))
    svc.invalidate()
    g = client.get("/api/skills").json()
    ok("B6 守卫：同 id 只出现一次（不重复）", [x["id"] for x in g["items"]] == ["good_one"],
       [x["id"] for x in g["items"]])
    ok("B6b 内置优先（描述来自内置而非用户）",
       next(x for x in g["items"] if x["id"] == "good_one")["source"] == "builtin")
    ok("B6c 冲突不静默（rejected 里有一条说明）",
       any("同 id" in x["reason"] for x in g["rejected"]), g["rejected"])
    os.remove(os.path.join(tmp, "skills", "good_one.skill.md"))

    # ---- B7 13 条语义建议候选上限
    for i in range(15):
        sid = "s%02d" % i
        open(os.path.join(builtin_tmp, sid + ".skill.md"), "w", encoding="utf-8", newline="\n").write(
            skill_text(sid, priority=i, desc="上限测试", trigger="上限"))
    svc.invalidate()
    g = client.get("/api/skills").json()
    ok("B7 总共 16 个技能（15 + good_one）", len(svc.load_all()["skills"]) == 16,
       len(svc.load_all()["skills"]))
    # P2 起契约变更：items **返回全部**（含停用技能，否则用户关掉就再也看不到），
    # 13 条上限由历史字段 `resident` 标记表达，实际语义是「语义建议候选」。
    ok("B7b 列表返回全部 16 条（不再截断）", len(g["items"]) == 16, len(g["items"]))
    ok("B7b2 语义候选标记只给 13 条", sum(1 for x in g["items"] if x.get("resident")) == 13,
       sum(1 for x in g["items"] if x.get("resident")))
    ok("B7c truncated=3（16-13）", g["truncated"] == 3, g["truncated"])
    pri = [x["priority"] for x in g["items"] if x.get("resident")]
    ok("B7d 语义候选按 priority 降序（保留最高 13 个）", pri == sorted(pri, reverse=True), pri)

    # ---- B8 safe_stem 的拦截能力（纵深防御；ID_RE 下不可达，此处单独验证工具本身）
    from app.core.filename import safe_stem
    ok("B8 safe_stem 能清掉路径穿越", safe_stem("../evil") != "../evil", safe_stem("../evil"))
    ok("B8b safe_stem 能清掉 NTFS ADS 冒号", ":" not in safe_stem("a:b"), safe_stem("a:b"))

    # ---- B9 外部手动放文件：列表缓存必须按目录指纹自动失效（不能依赖 svc.invalidate）
    fresh_path = os.path.join(builtin_tmp, "fresh_external.skill.md")
    with open(fresh_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(skill_text("fresh_external", desc="外部第一次放入", trigger="外刷"))
    g = client.get("/api/skills").json()
    ok("B9 外部新增文件后，直接 GET 即可见到", any(x["id"] == "fresh_external" for x in g["items"]),
       [x["id"] for x in g["items"]][-4:])
    with open(fresh_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(skill_text("fresh_external", desc="外部第二次修改", trigger="外刷"))
    st = os.stat(fresh_path)
    os.utime(fresh_path, ns=(st.st_atime_ns, st.st_mtime_ns + 1))
    g = client.get("/api/skills").json()
    got = next((x for x in g["items"] if x["id"] == "fresh_external"), {})
    ok("B9b 外部修改同名字件后，描述同步更新", got.get("description") == "外部第二次修改", got.get("description"))
    os.remove(fresh_path)
    g = client.get("/api/skills").json()
    ok("B9c 外部删除文件后，列表同步移除", all(x["id"] != "fresh_external" for x in g["items"]),
       [x["id"] for x in g["items"]][-4:])

    # ---- B10 批量导入护栏：数量 / 单文件大小 / 扫描条目都要能提前拒
    folder_tmp = tempfile.mkdtemp(prefix="asc_skill_import_guard_")
    try:
        old_max = skills_router.IMPORT_FOLDER_MAX_FILES
        skills_router.IMPORT_FOLDER_MAX_FILES = 3
        for i in range(4):
            sid = "guard_%02d" % i
            with open(os.path.join(folder_tmp, sid + ".skill.md"), "w", encoding="utf-8", newline="\n") as f:
                f.write(skill_text(sid, desc="导入护栏", trigger="护栏"))
        rr = client.post("/api/skills/import-folder", json={"path": folder_tmp, "dry_run": True})
        ok("B10 候选文件超上限 → 400 且说明原因", rr.status_code == 400 and "超过" in rr.json().get("detail", ""),
           rr.text[:120])
    finally:
        skills_router.IMPORT_FOLDER_MAX_FILES = old_max
        shutil.rmtree(folder_tmp, ignore_errors=True)

    folder_tmp = tempfile.mkdtemp(prefix="asc_skill_import_size_")
    try:
        old_size = skills_router.IMPORT_FOLDER_MAX_BYTES
        skills_router.IMPORT_FOLDER_MAX_BYTES = 64
        with open(os.path.join(folder_tmp, "too_big.skill.md"), "w", encoding="utf-8", newline="\n") as f:
            f.write(skill_text("too_big", desc="超过大小上限", trigger="超大"))
        rr = client.post("/api/skills/import-folder", json={"path": folder_tmp, "dry_run": True})
        rows = rr.json().get("results", []) if rr.status_code == 200 else []
        ok("B10b 单文件超上限 → 预览里标 failed，不整批 500",
           rr.status_code == 200 and any(r["status"] == "failed" and "超过" in r["reason"] for r in rows),
           rr.text[:160])
    finally:
        skills_router.IMPORT_FOLDER_MAX_BYTES = old_size
        shutil.rmtree(folder_tmp, ignore_errors=True)

    folder_tmp = tempfile.mkdtemp(prefix="asc_skill_import_scan_")
    try:
        old_scan = skills_router.IMPORT_FOLDER_SCAN_LIMIT
        skills_router.IMPORT_FOLDER_SCAN_LIMIT = 5
        for i in range(6):
            open(os.path.join(folder_tmp, "plain_%02d.txt" % i), "w", encoding="utf-8").write("x")
        rr = client.post("/api/skills/import-folder", json={"path": folder_tmp, "dry_run": True})
        ok("B10c 目录条目超扫描上限 → 400 且不继续递归", rr.status_code == 400 and "条目过多" in rr.json().get("detail", ""),
           rr.text[:120])
    finally:
        skills_router.IMPORT_FOLDER_SCAN_LIMIT = old_scan
        shutil.rmtree(folder_tmp, ignore_errors=True)

    # ---- B11 浏览器文件夹选择模式（相对路径 + 内容，不依赖本机绝对路径）
    picked_files = [
        {"file": "picked/pick_one.skill.md", "text": skill_text("pick_one", desc="文件夹选择", trigger="选夹")},
        {"file": "picked/dupe/pick_one.skill.md", "text": skill_text("pick_one", desc="同 id 重复", trigger="选夹")},
        {"file": "picked/bad.skill.md", "text": "not a skill"},
    ]
    rr = client.post("/api/skills/import-files", json={"files": picked_files, "dry_run": True})
    rows = rr.json().get("results", []) if rr.status_code == 200 else []
    ok("B11 文件夹选择 dry_run 返回 ready/dup/failed 三态",
       rr.status_code == 200 and [r["status"] for r in rows] == ["ready", "dup", "failed"], rr.text[:180])
    rr = client.post("/api/skills/import-files", json={"files": picked_files, "dry_run": False})
    imported = svc.get_skill("pick_one")
    ok("B11b 文件夹选择确认导入：只落首份，重复不误写",
       rr.status_code == 200 and rr.json().get("ok_count") == 1 and imported is not None and imported.source == "imported",
       rr.text[:180])

    old_size = skills_router.IMPORT_FOLDER_MAX_BYTES
    try:
        skills_router.IMPORT_FOLDER_MAX_BYTES = 64
        rr = client.post("/api/skills/import-files", json={
            "files": [{"file": "picked/too_big.skill.md", "text": skill_text("too_big2", desc="太大", trigger="大")}],
            "dry_run": True,
        })
        rows = rr.json().get("results", []) if rr.status_code == 200 else []
        ok("B11c 文件夹选择单文件超限 → 标 failed 不 500",
           rr.status_code == 200 and rows and rows[0]["status"] == "failed" and "超过" in rows[0]["reason"],
           rr.text[:160])
    finally:
        skills_router.IMPORT_FOLDER_MAX_BYTES = old_size

    old_total = skills_router.IMPORT_FILES_MAX_TOTAL_BYTES
    try:
        skills_router.IMPORT_FILES_MAX_TOTAL_BYTES = 10
        rr = client.post("/api/skills/import-files", json={"files": picked_files[:1], "dry_run": True})
        ok("B11d 文件夹选择总大小超限 → 400 并建议改走路径扫描",
           rr.status_code == 400 and "路径扫描" in rr.json().get("detail", ""), rr.text[:140])
    finally:
        skills_router.IMPORT_FILES_MAX_TOTAL_BYTES = old_total

finally:
    svc.BUILTIN_DIR = _orig_builtin
    svc.settings.data_dir = _orig_data_dir
    svc.invalidate()
    shutil.rmtree(tmp, ignore_errors=True)

# =====================================================================
print("\n===== Part C · 隔离是否真的恢复 =====")
after = client.get("/api/skills").json()
ok("C1 探针结束后清单与探针前**逐一致**（没污染实例；不硬编码个数，真实库会随导入增长）",
   [x["id"] for x in after["items"]] == baseline_ids,
   [x["id"] for x in after["items"]][:8])

print("\n===== Part D · 打包判据 =====")
skip("D1 打包后 _internal 内技能数 > 0",
     "需要真正打一次包才能验；spec 的 glob 收集已落地，留到发包前跑")

print("\n==== 探针结果：PASS %d / FAIL %d / SKIP %d ====" % (len(oks), len(fails), len(skips)))
if fails:
    print("失败项：")
    for f in fails:
        print("  -", f)
