"""陪练打磨（P1/P2/P7）行为验证 —— 只读 DB + mock LLM，不触网、不碰检索层。

⚠️ 刻意**不 import** app.services.vector / search：另一进程打开 Chroma 会被判集合
   损坏并删除重建（见 MEMORY.md 的验收纪律）。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "ai-study-companion" / "backend"))

from app.database import SessionLocal                     # noqa: E402
from app.models import DrillSession, DrillTurn            # noqa: E402
from app.services import drill as drill_svc               # noqa: E402
from app.services import llm                              # noqa: E402

PASS, FAIL = [], []


def chk(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + ((" | " + str(detail)) if detail and not cond else ""))


# ---------- 1. 评分官拿得到素材（P1） ----------
db = SessionLocal()
captured = {}


def fake_chat(msgs, **kw):
    captured["msgs"] = msgs
    captured["kind"] = kw.get("kind")
    return json.dumps({"score": 88, "verdict": "correct",
                       "dimensions": {"正确性": 90, "完整度": 85, "表达": 88},
                       "comment": "ok", "missing_points": [],
                       "followup": {"question": "", "expected_points": []}}, ensure_ascii=False)


orig = llm.summary_chat
llm.summary_chat = fake_chat
try:
    # 1.1 带素材
    llm.judge_drill_answer("问题A", ["要点1"], "作答A", material_excerpt="素材里写着：VIP 年卡 198 元。")
    user_msg = captured["msgs"][1]["content"]
    chk("P1 有素材时 prompt 含【素材片段】区块", "【素材片段】" in user_msg)
    chk("P1 素材正文真的进了 prompt", "VIP 年卡 198 元" in user_msg)
    chk("P1 记账 kind 仍是 drill_judge", captured["kind"] == "drill_judge", captured["kind"])
    sys_prompt = captured["msgs"][0]["content"]
    chk("P1 提示词禁止把「超纲但正确」写进 missing_points",
        "超纲" in sys_prompt or ("超出期望要点" in sys_prompt and "missing_points" in sys_prompt))
    chk("P1 提示词要求「与素材矛盾必须扣分」", "矛盾" in sys_prompt)

    # 1.2 不带素材（无来源材料）
    llm.judge_drill_answer("问题B", ["要点1"], "作答B")
    user_msg2 = captured["msgs"][1]["content"]
    chk("P1 无素材时**不**出现空区块", "【素材片段】" not in user_msg2)

    # 1.3 _judge_excerpt 真值（取一场有来源材料的真实轮次）
    turn = (db.query(DrillTurn).filter(DrillTurn.source_material_id.isnot(None)).first())
    if turn is None:
        chk("P1 _judge_excerpt 取到真实素材", False, "库里没有带 source_material_id 的轮次")
    else:
        ex = drill_svc._judge_excerpt(db, turn)
        chk("P1 _judge_excerpt 非空且不超预算",
            bool(ex) and len(ex) <= drill_svc.JUDGE_EXCERPT_CHARS, f"len={len(ex)}")
        # 反向：哨兵值必须返回空串（否则会拿 -1 去查材料）
        class _T:
            source_material_id = drill_svc.SENTINEL_MATERIAL_ID
        chk("P1 哨兵 material_id 返回空串", drill_svc._judge_excerpt(db, _T) == "")

    # ---------- 2. 追问分流（P2） ----------
    class _S:
        id = 999999                 # 不存在的场次 → existed 查询必为空
        config = {"follow_up": "shallow"}

    class _Turn:
        is_followup = False
        outline_index = 0
        verdict = "wrong"

    t = _Turn()
    t.verdict = "wrong"
    chk("P2 shallow 策略下 wrong **不**追问", drill_svc._should_followup(db, _S, t) is False)
    t.verdict = "partial"
    chk("P2 shallow 策略下 partial 仍追问", drill_svc._should_followup(db, _S, t) is True)
    t.verdict = "correct"
    chk("P2 correct 不追问", drill_svc._should_followup(db, _S, t) is False)
    t.verdict = "wrong"
    _S.config = {"follow_up": "always"}
    chk("P2 always 策略下 wrong 仍追问（不被 P2 误伤）",
        drill_svc._should_followup(db, _S, t) is True)

    # ---------- 3. 提示下发（P3） ----------
    s = db.query(DrillSession).filter(DrillSession.outline.isnot(None)).first()
    if s and (s.outline or []):
        h = drill_svc._outline_hint(s, 0)
        expect = str((s.outline[0] or {}).get("hint") or "").strip()
        chk("P3 _outline_hint 与题纲 hint 逐字相等", h == expect, f"{h!r} vs {expect!r}")
        chk("P3 越界下标返回空串", drill_svc._outline_hint(s, 999) == "")

    # ---------- 4. 趋势完成度（P7） ----------
    tr = drill_svc.session_trend(db)
    ok = all(k in x for x in tr["sessions"] for k in ("graded", "answered", "outline_count"))
    chk("P7 trend 每场都带 graded/answered/outline_count", ok)
    chk("P7 n_sessions 与 sessions 长度一致", tr["n_sessions"] == len(tr["sessions"]),
        f'{tr["n_sessions"]} vs {len(tr["sessions"])}')
    # 反向：graded 必须 ≤ answered（未作答却有分 = 数据错）
    chk("P7 graded 不超过 answered",
        all(x["graded"] <= x["answered"] for x in tr["sessions"]),
        [(x["id"], x["graded"], x["answered"]) for x in tr["sessions"]])
finally:
    llm.summary_chat = orig
    db.close()

print(f"\n==== {len(PASS)} PASS / {len(FAIL)} FAIL ====")
if FAIL:
    print("FAILED:", FAIL)
sys.exit(1 if FAIL else 0)
