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
ids = [x["id"] for x in body["items"]]
ok("A1 GET /skills 返回 200", r.status_code == 200, r.status_code)
ok("A2 真实内置技能 = 7 个", len(ids) == 7, ids)
ok("A3 七个 id 齐全", set(ids) == {"term_translate", "argue_check", "law_compare", "concept_compare", "feynman_explain", "error_analysis", "socratic_ask"}, ids)
ok("A4 无文件被拒（种子文件都合法）", body["rejected"] == [], body["rejected"])
ok("A5 按 priority 降序（20/18/15/14/12/10/8）", ids == ["term_translate", "concept_compare", "argue_check", "feynman_explain", "error_analysis", "law_compare", "socratic_ask"], ids)
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

    # ---- B7 12 条常驻上限
    for i in range(15):
        sid = "s%02d" % i
        open(os.path.join(builtin_tmp, sid + ".skill.md"), "w", encoding="utf-8", newline="\n").write(
            skill_text(sid, priority=i, desc="上限测试", trigger="上限"))
    svc.invalidate()
    g = client.get("/api/skills").json()
    ok("B7 总共 16 个技能（15 + good_one）", len(svc.load_all()["skills"]) == 16,
       len(svc.load_all()["skills"]))
    # P2 起契约变更：items **返回全部**（含停用技能，否则用户关掉就再也看不到），
    # 12 条上限改由 `resident` 标记表达 —— 判据必须跟着搬家，否则测的还是旧契约。
    ok("B7b 列表返回全部 16 条（不再截断）", len(g["items"]) == 16, len(g["items"]))
    ok("B7b2 常驻标记只给 12 条", sum(1 for x in g["items"] if x.get("resident")) == 12,
       sum(1 for x in g["items"] if x.get("resident")))
    ok("B7c truncated=4（16-12）", g["truncated"] == 4, g["truncated"])
    pri = [x["priority"] for x in g["items"] if x.get("resident")]
    ok("B7d 常驻按 priority 降序（保留最高 12 个）", pri == sorted(pri, reverse=True), pri)

    # ---- B8 safe_stem 的拦截能力（纵深防御；ID_RE 下不可达，此处单独验证工具本身）
    from app.core.filename import safe_stem
    ok("B8 safe_stem 能清掉路径穿越", safe_stem("../evil") != "../evil", safe_stem("../evil"))
    ok("B8b safe_stem 能清掉 NTFS ADS 冒号", ":" not in safe_stem("a:b"), safe_stem("a:b"))

finally:
    svc.BUILTIN_DIR = _orig_builtin
    svc.settings.data_dir = _orig_data_dir
    svc.invalidate()
    shutil.rmtree(tmp, ignore_errors=True)

# =====================================================================
print("\n===== Part C · 隔离是否真的恢复 =====")
after = client.get("/api/skills").json()
ok("C1 探针结束后回到真实 7 个技能（没污染实例）",
   [x["id"] for x in after["items"]] == ["term_translate", "concept_compare", "argue_check", "feynman_explain", "error_analysis", "law_compare", "socratic_ask"],
   [x["id"] for x in after["items"]])

print("\n===== Part D · 打包判据 =====")
skip("D1 打包后 _internal 内技能数 > 0",
     "需要真正打一次包才能验；spec 的 glob 收集已落地，留到发包前跑")

print("\n==== 探针结果：PASS %d / FAIL %d / SKIP %d ====" % (len(oks), len(fails), len(skips)))
if fails:
    print("失败项：")
    for f in fails:
        print("  -", f)
