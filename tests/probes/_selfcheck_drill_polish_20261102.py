"""陪练打磨 · 判据分辨力自检（M1–M3）。

做法：在**内存里**把三条新行为换回改动前的语义（不落盘、不动源码），
      用**同一套判据**再跑一遍 —— 每条判据必须变红，否则说明它恒真。

M1 judge 忽略 material_excerpt（= 改动前的评分官）
M2 _should_followup 对 wrong 也追问（= 改动前）
M3 session_trend 不下发完成度（= 改动前）
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.database import SessionLocal                  # noqa: E402
from app.models import DrillSession, DrillTurn         # noqa: E402
from app.services import drill as drill_svc            # noqa: E402
from app.services import llm                           # noqa: E402

PASS, FAIL = [], []


def chk(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + ((" | " + str(detail)) if detail and not cond else ""))


db = SessionLocal()
cap = {}


def fake_chat(msgs, **kw):
    cap["msgs"] = msgs
    return json.dumps({"score": 60, "verdict": "partial", "dimensions": {},
                       "comment": "ok", "missing_points": [],
                       "followup": {"question": "", "expected_points": []}}, ensure_ascii=False)


# ---------- 基线：新代码下三条判据必须绿 ----------
orig_chat, orig_follow, orig_trend = llm.summary_chat, drill_svc._should_followup, drill_svc.session_trend
llm.summary_chat = fake_chat


class _S:
    id = 999999
    config = {"follow_up": "shallow"}


class _T:
    is_followup = False
    outline_index = 0
    verdict = "wrong"


try:
    llm.judge_drill_answer("q", ["p1"], "a", material_excerpt="素材片段正文ABC")
    chk("基线 M1：素材进了 prompt", "【素材片段】" in cap["msgs"][1]["content"])
    chk("基线 M2：wrong 不追问", drill_svc._should_followup(db, _S, _T()) is False)
    tr = drill_svc.session_trend(db)
    chk("基线 M3：trend 带 graded", all("graded" in x for x in tr["sessions"]))

    # ---------- M1：旧评分官（忽略 excerpt） ----------
    def old_judge(question, expected_points, user_answer, material_excerpt=""):
        # 接收但**不使用** excerpt —— 这正是「参数加了、素材没喂进去」的退化形态
        pts = "\n".join(f"- {p}" for p in (expected_points or []))
        llm.summary_chat([{"role": "system", "content": "x"},
                          {"role": "user", "content": f"【问题】{question}\n【期望要点】{pts}\n"
                                                      f"【学习者作答】{user_answer}"}], kind="drill_judge")
        return {"score": 0, "verdict": "wrong"}

    _orig_judge = llm.judge_drill_answer
    llm.judge_drill_answer = old_judge
    llm.judge_drill_answer("q", ["p1"], "a", material_excerpt="素材片段正文ABC")
    chk("M1 变异被咬住：旧评分官 prompt 里**没有**素材区块",
        "【素材片段】" not in cap["msgs"][1]["content"])
    llm.judge_drill_answer = _orig_judge

    # ---------- M2：旧的追问判据 ----------
    def old_follow(db_, s_, turn_):
        policy = (s_.config or {}).get("follow_up") or "shallow"
        if policy == "never" or turn_.is_followup:
            return False
        if policy == "always":
            return True
        return turn_.verdict in ("partial", "wrong")     # ← 旧写法

    drill_svc._should_followup = old_follow
    chk("M2 变异被咬住：旧判据下 wrong 会追问（新判据必红）",
        drill_svc._should_followup(db, _S, _T()) is True)
    drill_svc._should_followup = orig_follow

    # ---------- M3：旧 trend（不算完成度） ----------
    def old_trend(db_, limit=20):
        d = orig_trend(db_, limit)
        for x in d["sessions"]:
            x.pop("graded", None)
            x.pop("answered", None)
            x.pop("outline_count", None)
        return d

    drill_svc.session_trend = old_trend
    tr2 = drill_svc.session_trend(db)
    chk("M3 变异被咬住：旧 trend 缺 graded 字段（新判据必红）",
        not all("graded" in x for x in tr2["sessions"]))
    drill_svc.session_trend = orig_trend

    # ---------- 恢复后复绿（证明变异确实是原因，不是别的坏了） ----------
    llm.judge_drill_answer("q", ["p1"], "a", material_excerpt="素材片段正文ABC")
    chk("恢复后 M1 复绿", "【素材片段】" in cap["msgs"][1]["content"])
    chk("恢复后 M2 复绿", drill_svc._should_followup(db, _S, _T()) is False)
    chk("恢复后 M3 复绿", all("graded" in x for x in drill_svc.session_trend(db)["sessions"]))
finally:
    llm.summary_chat = orig_chat
    drill_svc._should_followup = orig_follow
    drill_svc.session_trend = orig_trend
    db.close()

# 三条变异必须全部「被咬住」；任何一条没红 = 该判据恒真
bitten = [x for x in PASS if x.startswith("M")]
print(f"\n==== {len(PASS)} PASS / {len(FAIL)} FAIL ====")
ok = len(bitten) == 3 and not FAIL
print("变异全部被咬住:" , "YES" if ok else "NO")
sys.exit(0 if ok else 1)
