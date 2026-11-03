# -*- coding: utf-8 -*-
"""P1 后端验收：技能注入。

隔离：临时 ASC_DATA_DIR + ASC_DB_URL（绝不碰用户 data/app.db）。
mock：llm.chat_stream（捕获 messages，不真调模型）、search（返回空 → 走 general 分支）。

判据分两层：
  纯函数 —— build_injection 的语义（含 {count} 不被 format 撑爆）
  端到端 —— 真跑 /api/chat/ask/stream，看真正送进模型的 system 里有什么
"""
import json
import os
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="asc_p1_")
os.environ["ASC_DATA_DIR"] = TMP
os.environ["ASC_DB_URL"] = "sqlite:///" + os.path.join(TMP, "t.db").replace("\\", "/")
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "backend"))  # tests/probes/x.py → repo/backend，不再依赖运行目录

from app.services import llm  # noqa: E402
from app.services import search as search_svc  # noqa: E402
from app.services import skills as skills_svc  # noqa: E402

fails, oks = [], []


def ok(name, cond, detail=""):
    (oks if cond else fails).append(name)
    print(("PASS " if cond else "FAIL ") + name + (("  | " + str(detail)[:220]) if detail != "" else ""))


# ================================================================ A. 纯函数
print("========== A. build_injection ==========")

ALL = {s.id: s for s in skills_svc.load_all()["skills"]}
ok("A0 基础三个技能在新增内置后仍加载", set(ALL) >= {"term_translate", "argue_check", "law_compare"}, sorted(ALL))

tt = ALL["term_translate"]
inj = skills_svc.build_injection(tt)
ok("A1 含技能名标题", "【本轮启用的技能：%s】" % tt.name in inj, inj[:40])
ok("A2 citation=required 时追加引用要求", "【引用要求（本技能强制）】" in inj)
ok("A3 正文完整保留（逐字包含 instructions）", tt.instructions.strip() in inj,
   "instructions=%d 字, injection=%d 字" % (len(tt.instructions), len(inj)))

# optional 分支：构造实例，不落盘
fake = skills_svc.Skill(id="fake_opt", name="假技能", icon="x", kind="chat", description="d",
                        triggers=[], priority=1, citation="optional",
                        instructions="做法正文，含占位符 {count} 与 {name}。\n第二行。",
                        builtin=False, source="local", path="")
finj = skills_svc.build_injection(fake)
ok("A4 citation=optional 时不追加引用要求", "引用要求" not in finj, finj)
ok("A5 正文里的 {count}/{name} 原样保留（不做 format 渲染）",
   "{count}" in finj and "{name}" in finj, finj)
ok("A6 真的不抛 KeyError（format 会炸）", True)

law = ALL["law_compare"]
linj = skills_svc.build_injection(law)
ok("A7 law_compare 不再保留死占位符，片段顺序要求仍在",
   "{count}" not in linj and "按材料片段顺序摘" in linj, linj[:180])

# ================================================================ D. 静态结构
print("\n========== D. 静态结构 ==========")
# 注意：必须 newline=""，否则文本模式会把 CRLF 归一成 LF，split 之后只剩 1 个元素
chat_src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "backend", "app", "routers", "chat.py"), encoding="utf-8", newline="").read()
lines = chat_src.split("\r\n")
idx_inject = [i for i, l in enumerate(lines) if "skills_svc.build_injection" in l]
ok("D1 注入调用只出现 1 处", len(idx_inject) == 1, len(idx_inject))
idx_assemble = [i for i, l in enumerate(lines) if "messages = [" in l or "messages = llm.build_kb_qa_prompt" in l]
ok("D2 注入位置在所有 messages 装配之后（否则有的分支注入不到）",
   all(i < idx_inject[0] for i in idx_assemble),
   "装配行=%s 注入行=%s" % (idx_assemble, idx_inject))
ok("D3 注入不新增消息（源码里没有 messages.append/insert 带技能）",
   "messages.append" not in chat_src and "messages.insert" not in chat_src)

# ================================================================ B. 端到端
print("\n========== B. 端到端 /api/chat/ask/stream ==========")

captured = {}


def fake_chat_stream(messages, tools=None, **kw):
    captured["messages"] = [dict(m) for m in messages]
    captured["tools"] = tools
    captured["kw"] = kw

    def g():
        yield ("token", "测试回答")
    return g()


llm.chat_stream = fake_chat_stream
# ⚠️ P4 起「挂了带能力的技能」会改走 chat_stream_tools。同一个 fake 必须同时挂上去，
#    否则这条链路的 messages 抓不到（captured 为空 → 后续判据 TypeError，
#    看起来像回归，其实是探针没跟住实现）。签名统一成 (messages, tools=None, **kw)。
llm.chat_stream_tools = fake_chat_stream
search_svc.search = lambda *a, **k: []      # 空检索 → 走 general 分支

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)


def ask(skill_id=None):
    body = {"question": "这个术语怎么译", "scope": "all"}
    if skill_id is not None:
        body["skill_id"] = skill_id
    captured.clear()
    r = client.post("/api/chat/ask/stream", json=body)
    events = []
    for blk in r.text.split("\n\n"):
        ev = data = None
        for line in blk.split("\n"):
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data = line[5:].strip()
        if ev and data:
            try:
                events.append((ev, json.loads(data)))
            except Exception:
                events.append((ev, data))
    return r, events, captured.get("messages")


r1, ev1, msg1 = ask(None)
ok("B1 不带技能：HTTP 200", r1.status_code == 200, r1.status_code)
ok("B2 不带技能：system 里没有技能段",
   "本轮启用的技能" not in msg1[0]["content"], msg1[0]["content"][:60])
src1 = next((d for e, d in ev1 if e == "sources"), None)
ok("B3 不带技能：sources.skill 为 null", src1 is not None and src1.get("skill") is None, src1)

r2, ev2, msg2 = ask("term_translate")
ok("B4 带技能：HTTP 200", r2.status_code == 200, r2.status_code)
ok("B5 带技能：system 含技能标题", "【本轮启用的技能：术语翻译】" in msg2[0]["content"],
   msg2[0]["content"][:50])
ok("B6 带技能：system 含指令正文首句", "术语层" in msg2[0]["content"])
ok("B7 带技能：system 含引用要求（citation=required 落地）",
   "【引用要求（本技能强制）】" in msg2[0]["content"])
ok("B8 消息条数不变（注入不新增消息）", len(msg1) == len(msg2),
   "%d vs %d" % (len(msg1), len(msg2)))
ok("B9 首元素仍是 system", msg2[0]["role"] == "system", msg2[0]["role"])
src2 = next((d for e, d in ev2 if e == "sources"), None)
ok("B10 sources.skill 回传实际启用的技能",
   src2 and src2.get("skill") and src2["skill"]["id"] == "term_translate", src2 and src2.get("skill"))

r3, ev3, msg3 = ask("law_compare")
body3 = msg3[0]["content"]
ok("B11 law_compare：system 不含死占位符，且片段顺序要求注入",
   "{count}" not in body3 and "按材料片段顺序摘" in body3, body3[:180])
ok("B12 law_compare：正文没被吞（含「法条」）", "法条" in body3)

r4, ev4, msg4 = ask("no_such_skill")
# P2 起契约变更：未知 / 已停用的 skill_id 从「静默忽略、照常回答」改成明确报错。
# 静默忽略等于「技能没生效但用户不知道」——他会以为自己在对比两个技能的效果。
ok("B13 未知 skill_id：明确 404（不再静默忽略）", r4.status_code == 404, r4.status_code)
ok("B14 404 文案点名那个 id", "no_such_skill" in r4.text, r4.text[:140])
ok("B15 404 时没有产出流式回答（不返回半截内容）", not ev4, ev4[:2])

ok("B16 带技能后 token 流正常（能答完）",
   any(e == "token" for e, _ in ev2) and any(e == "done" for e, _ in ev2),
   [e for e, _ in ev2])

# 检索命中分支：mock 一条命中，验证 build_kb_qa_prompt 那条装配路径也能被注入
print("\n========== C. kb_hit 分支（第二条装配路径） ==========")
# ⚠️ 命中过滤看 from_fts/from_entity/from_title/vec_score，且 <50 字会被当碎片剔掉。
#    第一次只给 score=0.99 且文本 24 字 → 被过滤 → 仍走 general 分支，C1 是**假绿**。
search_svc.search = lambda *a, **k: [{
    "id": "1",
    "text": "这是一段命中的材料正文，用于验证 kb_hit 分支的注入是否生效。" * 3,
    "metadata": {"material_title": "测试材料", "page_no": 3},
    "material_id": 1, "ref_type": "material", "from_fts": True, "vec_score": 0.99, "score": 0.99,
}]
r5, ev5, msg5 = ask("term_translate")
body5 = msg5[0]["content"] if msg5 else ""
kb5 = next((d.get("kb_hit") for e, d in ev5 if e == "sources"), None)
hit5 = "本轮启用的技能：术语翻译" in body5
ok("C0 mock 的命中真的进了 kb_hit 分支（前置，否则 C1 是假绿）", kb5 is True, kb5)
ok("C1 kb_hit 分支也被注入（4 条装配路径覆盖到第 2 条）", kb5 is True and hit5,
   "kb_hit=%s, system=%r" % (kb5, body5[:60]))
ok("C2 kb_hit 分支首元素仍是 system", bool(msg5) and msg5[0]["role"] == "system")

print("\n==== 结果：%d PASS / %d FAIL ====" % (len(oks), len(fails)))
for f in fails:
    print("FAILED:", f)
raise SystemExit(1 if fails else 0)
