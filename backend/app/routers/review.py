"""复习路由（V2 记忆曲线）：笔记 → AI 出题（选择题/简答）→ 间隔复习

间隔策略（简化 SM-2，四档自评）：INTERVALS = [1, 2, 4, 7, 15, 30] 天
- 忘了 forgot → level 归 0（明天再来）
- 模糊 fuzzy → level 回退一档（更频繁复习，符合记忆曲线）
- 记得 easy  → level +1
- 简单 simple→ level +2（跳级，封顶 30 天）
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..core.config import settings
from ..models import ReviewCard, Note, Material, AIAsset, MaterialChunk
from ..services import llm

router = APIRouter(prefix="/review", tags=["review"])

INTERVALS = [1, 2, 4, 7, 15, 30]   # level 0-5 对应间隔天数


def card_to_dict(c: ReviewCard) -> dict:
    return {
        "id": c.id, "note_id": c.note_id,
        "material_id": c.material_id, "material_title": c.material_title,
        "question": c.question, "answer": c.answer,
        "type": c.type or "qa", "options": c.options or [],
        "correct_index": c.correct_index, "source": c.source or "note",
        "level": c.level, "review_count": c.review_count, "ease": round(c.ease or 2.5, 2),
        "next_review_at": c.next_review_at.isoformat() if c.next_review_at else None,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


class CreateCardReq(BaseModel):
    note_id: int


def _build_card(note: Note, qa: dict, db: Session) -> ReviewCard:
    material_title = ""
    if note.source_type == "chat":          # AI 问答转存的材料无关笔记
        material_title = "AI 问答"
    elif note.material_id:
        m = db.get(Material, note.material_id)
        material_title = m.title if m else "（材料已删除）"
    return ReviewCard(
        note_id=note.id, material_id=note.material_id or 0,
        material_title=material_title,
        question=qa["question"], answer=qa["explanation"],
        type="choice", options=qa["options"], correct_index=qa["correct_index"],
        level=0, next_review_at=datetime.utcnow(),
    )


@router.post("/cards")
def create_card(req: CreateCardReq, db: Session = Depends(get_db)):
    """从笔记生成复习卡片（AI 出选择题）；同一笔记只建一张，重复调用返回已有卡片"""
    note = db.get(Note, req.note_id)
    if not note:
        raise HTTPException(404, "笔记不存在")

    existing = db.query(ReviewCard).filter(ReviewCard.note_id == note.id).first()
    if existing:
        return {**card_to_dict(existing), "created": False}

    qa = llm.generate_review_question(note.title, note.content)
    card = _build_card(note, qa, db)
    db.add(card)
    db.commit()
    db.refresh(card)
    return {**card_to_dict(card), "created": True}


@router.post("/cards/recall")
def create_recall_card(req: CreateCardReq, db: Session = Depends(get_db)):
    """从笔记生成费曼复述卡（type=recall，引导主动复述而非再认）。
    与选择题卡并存：同一笔记可同时有选择题卡和复述卡，重复调用返回已有复述卡。"""
    note = db.get(Note, req.note_id)
    if not note:
        raise HTTPException(404, "笔记不存在")

    existing = (db.query(ReviewCard)
                .filter(ReviewCard.note_id == note.id, ReviewCard.type == "recall").first())
    if existing:
        return {**card_to_dict(existing), "created": False}

    qa = llm.generate_recall_question(note.title, note.content)
    material_title = ""
    if note.source_type == "chat":
        material_title = "AI 问答"
    elif note.material_id:
        m = db.get(Material, note.material_id)
        material_title = m.title if m else "（材料已删除）"
    card = ReviewCard(
        note_id=note.id, material_id=note.material_id or 0,
        material_title=material_title,
        question=qa["question"], answer=qa["answer"],
        type="recall", level=0, next_review_at=datetime.utcnow(),
    )
    db.add(card)
    db.commit()
    db.refresh(card)
    return {**card_to_dict(card), "created": True}


@router.post("/cards/{card_id}/convert")
def convert_card(card_id: int, db: Session = Depends(get_db)):
    """把简答卡转成选择题（保留 level 与复习进度）"""
    c = db.get(ReviewCard, card_id)
    if not c:
        raise HTTPException(404, "卡片不存在")
    if c.type == "choice":
        return card_to_dict(c)
    qa = llm.convert_to_choice(c.question, c.answer)
    c.type = "choice"
    c.options = qa["options"]
    c.correct_index = qa["correct_index"]
    c.answer = qa["explanation"]
    db.commit()
    return card_to_dict(c)


class CardUpdateReq(BaseModel):
    question: str | None = None
    options: list | None = None
    correct_index: int | None = None
    answer: str | None = None


@router.put("/cards/{card_id}")
def update_card(card_id: int, req: CardUpdateReq, db: Session = Depends(get_db)):
    """出题校对：人工修正题干/选项/正确项/解析（AI 出题偶有错，可手改）"""
    c = db.get(ReviewCard, card_id)
    if not c:
        raise HTTPException(404, "卡片不存在")
    if req.question is not None and req.question.strip():
        c.question = req.question.strip()
    if req.options is not None:
        c.options = [o for o in req.options if str(o).strip()]
    if req.correct_index is not None:
        c.correct_index = req.correct_index
    if req.answer is not None and req.answer.strip():
        c.answer = req.answer.strip()
    db.commit()
    return card_to_dict(c)


class SplitReq(BaseModel):
    note_id: int


@router.post("/cards/split")
def split_card(req: SplitReq, db: Session = Depends(get_db)):
    """把一条长笔记拆成多张卡（按内容长度决定 1-3 张）。删除该笔记已有卡后重建。"""
    note = db.get(Note, req.note_id)
    if not note:
        raise HTTPException(404, "笔记不存在")

    content_len = len(note.content or "")
    count = 3 if content_len > 500 else (2 if content_len > 200 else 1)

    # 删除该笔记已有卡（拆卡重建）
    old = db.query(ReviewCard).filter(ReviewCard.note_id == note.id).all()
    for c in old:
        db.delete(c)
    db.commit()

    qas = llm.generate_review_questions(note.title, note.content, count)
    created = []
    for qa in qas:
        card = _build_card(note, qa, db)
        db.add(card)
        db.commit()
        db.refresh(card)
        created.append(card_to_dict(card))
    return {"created": len(created), "cards": created}


class QuizReq(BaseModel):
    material_id: int
    count: int = 8


@router.post("/quiz")
def create_quiz(req: QuizReq, db: Session = Depends(get_db)):
    """测一测：基于资料核心内容（摘要 > 知识点 > 原文）生成单选题，并同步纳入复习队列。

    重新生成会删除该资料已有的测一测卡（source=quiz），不影响笔记出题的卡。
    """
    m = db.get(Material, req.material_id)
    if not m:
        raise HTTPException(404, "材料不存在")
    if m.parsed_status != "success":
        raise HTTPException(400, f"材料状态为 {m.parsed_status}，暂不能出题")

    # 核心内容：优先脉络摘要，其次知识点，最后原文块
    core = ""
    summ = (db.query(AIAsset).filter(AIAsset.material_id == m.id, AIAsset.type == "summary")
            .order_by(AIAsset.version.desc()).first())
    if summ:
        core = summ.content
    else:
        kw = (db.query(AIAsset).filter(AIAsset.material_id == m.id, AIAsset.type == "keywords")
              .order_by(AIAsset.version.desc()).first())
        if kw:
            core = kw.content
        else:
            chunks = db.query(MaterialChunk).filter(MaterialChunk.material_id == m.id).all()
            core = "\n".join(c.content for c in chunks)
    if not core.strip():
        raise HTTPException(400, "资料没有可出题的内容，请先生成脉络摘要或知识点")

    qas = llm.generate_quiz(m.title, core, req.count)

    # 重新生成：删除该资料已有测一测卡
    db.query(ReviewCard).filter(ReviewCard.material_id == m.id, ReviewCard.source == "quiz").delete()
    db.commit()

    created = []
    for qa in qas:
        card = ReviewCard(
            note_id=None, material_id=m.id, material_title=m.title,
            question=qa["question"], answer=qa["explanation"],
            type="choice", options=qa["options"], correct_index=qa["correct_index"],
            source="quiz", level=0, next_review_at=datetime.utcnow(),
        )
        db.add(card)
        db.commit()
        db.refresh(card)
        created.append(card_to_dict(card))
    return {"created": len(created), "cards": created}


class BatchReq(BaseModel):
    material_id: int


@router.post("/cards/batch")
def create_cards_batch(req: BatchReq, db: Session = Depends(get_db)):
    """把一份材料的全部笔记批量出题入队（已有卡的跳过）。并发 3 线程调 LLM 提速。

    注意：每张卡用独立 Session 写库（并发下共享 Session 不安全），调用方传入 db 仅用于查笔记清单。
    """
    from concurrent.futures import ThreadPoolExecutor
    from ..database import SessionLocal

    notes = db.query(Note).filter(Note.material_id == req.material_id).all()
    if not notes:
        return {"created": 0, "skipped": 0, "total": 0}

    existing_ids = {r[0] for r in db.query(ReviewCard.note_id).filter(
        ReviewCard.note_id.in_([n.id for n in notes])).all()}
    todo = [n for n in notes if n.id not in existing_ids]

    def _create_one(n):
        """单张卡：独立 Session 出题 + 写库"""
        sdb = SessionLocal()
        try:
            qa = llm.generate_review_question(n.title, n.content)
            # _build_card 需要 db 查询 Material；这里用 sdb
            sdb.add(_build_card(n, qa, sdb))
            sdb.commit()
            return True
        except Exception:
            sdb.rollback()
            return False
        finally:
            sdb.close()

    created = 0
    with ThreadPoolExecutor(max_workers=3) as pool:
        for ok in pool.map(_create_one, todo):
            if ok:
                created += 1

    return {"created": created, "skipped": len(existing_ids), "total": len(notes)}


@router.get("/cards")
def list_cards(material_id: int | None = None, tag: str | None = None,
               type: str | None = None, db: Session = Depends(get_db)):
    q = db.query(ReviewCard)
    q = _apply_filters(q, material_id, tag, type, db)
    rows = q.order_by(ReviewCard.next_review_at).all()
    return [card_to_dict(c) for c in rows]


def _apply_filters(q, material_id: int | None, tag: str | None, type_: str | None, db: Session):
    """按资料 / 材料标签 / 题型筛选复习卡"""
    if material_id:
        q = q.filter(ReviewCard.material_id == material_id)
    if tag:
        mids = [m.id for m in db.query(Material).all() if tag in (m.tags or [])]
        q = q.filter(ReviewCard.material_id.in_(mids or [-1]))
    if type_:
        q = q.filter(ReviewCard.type == type_)
    return q


@router.get("/filters")
def review_filters(db: Session = Depends(get_db)):
    """筛选项：有卡的材料列表 + 这些材料的标签集合"""
    mid_rows = db.query(ReviewCard.material_id).distinct().all()
    mids = {r[0] for r in mid_rows if r[0]}
    mats = db.query(Material).filter(Material.id.in_(mids)).all() if mids else []
    materials = [{"id": m.id, "title": m.title} for m in mats]
    # 问答笔记（material_id=0）生成的复习卡：补一个「AI 问答」筛选项，否则这类卡在材料下拉里永远选不到
    if any(r[0] == 0 for r in mid_rows):
        materials.append({"id": 0, "title": "AI 问答"})
    tags = sorted({t for m in mats for t in (m.tags or [])})
    return {"materials": materials, "tags": tags}


@router.get("/today")
def today_cards(material_id: int | None = None, tag: str | None = None,
                type: str | None = None, db: Session = Depends(get_db)):
    """今日待复习队列：到期卡片受每日上限约束（防积压），可按资料/标签/题型筛选。
    采用交错排序：按材料分组轮流取，避免同材料题连续出现（交错练习利于长期记忆）。"""
    now = datetime.utcnow()
    q = db.query(ReviewCard).filter(ReviewCard.next_review_at <= now)
    q = _apply_filters(q, material_id, tag, type, db)
    due = q.order_by(ReviewCard.next_review_at).all()
    total_due = len(due)

    # 交错排序：按材料分组（组内保持到期时间序），轮流取一张
    groups = {}
    for c in due:
        groups.setdefault(c.material_id or 0, []).append(c)
    interleaved = []
    while groups:
        for mid in list(groups.keys()):
            interleaved.append(groups[mid].pop(0))
            if not groups[mid]:
                del groups[mid]

    rows = interleaved[:settings.review_daily_limit]
    return {"cards": [card_to_dict(c) for c in rows],
            "total_due": total_due, "limit": settings.review_daily_limit}


class GradeReq(BaseModel):
    result: str   # simple 简单 / easy 记得 / fuzzy 模糊 / forgot 忘了


class QuizAnswerReq(BaseModel):
    card_id: int
    correct: bool


@router.post("/quiz-answer")
def quiz_answer(req: QuizAnswerReq, db: Session = Depends(get_db)):
    """测一测作答回写：答错立即进复习队列优先强化（到期时间=现在），
    答对跳过首轮（间隔拉长到 2 天后）。不计入 review_count/history（这是首次作答，非复习）。"""
    c = db.get(ReviewCard, req.card_id)
    if not c:
        raise HTTPException(404, "卡片不存在")
    now = datetime.utcnow()
    if req.correct:
        c.level = 1
        c.next_review_at = now + timedelta(days=2)
    else:
        c.level = 0
        c.next_review_at = now   # 立即到期 → 进复习队列强化
    db.commit()
    return card_to_dict(c)


@router.post("/cards/{card_id}/grade")
def grade_card(card_id: int, req: GradeReq, db: Session = Depends(get_db)):
    """四档自评 → 调整 level 与下次复习时间"""
    c = db.get(ReviewCard, card_id)
    if not c:
        raise HTTPException(404, "卡片不存在")
    if req.result not in ("simple", "easy", "fuzzy", "forgot"):
        raise HTTPException(400, "result 须为 simple / easy / fuzzy / forgot")

    if req.result == "forgot":
        c.level = 0
        c.ease = max((c.ease or 2.5) - 0.3, 1.3)   # 连续忘 → 间隔缩短
    elif req.result == "fuzzy":
        c.level = max(c.level - 1, 0)            # 回退一档
        c.ease = max((c.ease or 2.5) - 0.2, 1.3)
    elif req.result == "easy":
        c.level = min(c.level + 1, len(INTERVALS) - 1)
        # 记得：ease 不变
    else:  # simple
        c.level = min(c.level + 2, len(INTERVALS) - 1)   # 跳级
        c.ease = min((c.ease or 2.5) + 0.15, 3.5)  # 连续简单 → 间隔加速拉长

    now = datetime.utcnow()
    c.review_count += 1
    c.last_reviewed_at = now
    # 实际间隔 = 基础间隔 × ease/2.5（ease 2.5 时等于原固定间隔，表现好拉长、表现差缩短）
    interval_days = max(1, round(INTERVALS[c.level] * (c.ease or 2.5) / 2.5))
    c.next_review_at = now + timedelta(days=interval_days)
    # 记录复习历史（供数据洞察：连续打卡 / 遗忘曲线）
    hist = list(c.history or [])
    hist.append({"r": req.result, "t": now.isoformat(), "l": c.level, "e": round(c.ease or 2.5, 2)})
    c.history = hist
    db.commit()
    return {**card_to_dict(c), "next_interval_days": interval_days}


@router.post("/cards/{card_id}/undo-grade")
def undo_grade(card_id: int, db: Session = Depends(get_db)):
    """撤销最近一次自评（防误触）：弹出 history 末条，从倒数第二条重建 level/ease；
    撤销后卡片立即到期，重新回到待复习队列。stats 基于 history 聚合，弹出后统计自动回正。"""
    c = db.get(ReviewCard, card_id)
    if not c:
        raise HTTPException(404, "卡片不存在")
    hist = list(c.history or [])
    if not hist:
        raise HTTPException(400, "没有可撤销的复习记录")
    hist.pop()
    prev = hist[-1] if hist else None   # history 记录的是评分后的新状态，倒数第二条即撤销后的状态
    c.level = prev["l"] if prev else 0
    c.ease = prev["e"] if prev else 2.5
    c.review_count = max(0, (c.review_count or 0) - 1)
    try:
        c.last_reviewed_at = datetime.fromisoformat(prev["t"]) if prev else None
    except Exception:
        c.last_reviewed_at = None
    c.history = hist
    c.next_review_at = datetime.utcnow()   # 重新到期 → 回到待复习
    db.commit()
    return card_to_dict(c)


@router.delete("/cards/{card_id}")
def delete_card(card_id: int, db: Session = Depends(get_db)):
    c = db.get(ReviewCard, card_id)
    if not c:
        raise HTTPException(404, "卡片不存在")
    db.delete(c)
    db.commit()
    return {"ok": True}


@router.get("/cards/status")
def cards_by_status(status: str, material_id: int | None = None, tag: str | None = None,
                    type: str | None = None, db: Session = Depends(get_db)):
    """按状态下钻查看对应复习卡（点击统计数字查看相关内容），与主队列保持同一筛选口径"""
    CN = timedelta(hours=8)
    utc_now = datetime.utcnow()
    cn_today = (utc_now + CN).date()
    today_start_utc = datetime.combine(cn_today, datetime.min.time()) - CN

    q = db.query(ReviewCard)
    if status == "due":
        q = q.filter(ReviewCard.next_review_at <= utc_now)
    elif status == "reviewed_today":
        q = q.filter(ReviewCard.last_reviewed_at >= today_start_utc)
    elif status == "mastered":
        q = q.filter(ReviewCard.level >= 4)
    elif status == "weak":
        q = q.filter(ReviewCard.review_count >= 2, ReviewCard.level <= 1)
    elif status == "all":
        pass
    else:
        raise HTTPException(400, "status 须为 due / reviewed_today / mastered / weak / all")
    q = _apply_filters(q, material_id, tag, type, db)
    rows = q.order_by(ReviewCard.next_review_at).all()
    return [card_to_dict(c) for c in rows]


@router.get("/stats")
def stats(db: Session = Depends(get_db)):
    """复习统计（北京时间口径，修复 UTC 时区导致的日期归属错位）：
    - reviewed_today = 今天（北京）复习的总次数（来自 history，与前端自评 +1 口径一致）
    - daily / streak 均按北京日期聚合
    """
    CN = timedelta(hours=8)
    utc_now = datetime.utcnow()
    cn_now = utc_now + CN
    cn_today = cn_now.date()

    total = db.query(ReviewCard).count()
    due = db.query(ReviewCard).filter(ReviewCard.next_review_at <= utc_now).count()
    mastered = db.query(ReviewCard).filter(ReviewCard.level >= 4).count()
    weak = db.query(ReviewCard).filter(ReviewCard.review_count >= 2, ReviewCard.level <= 1).count()

    days = {}   # 北京日期 -> {"count", "easy", "fuzzy", "forgot"}
    reviewed_today = 0
    for c in db.query(ReviewCard).all():
        for h in (c.history or []):
            try:
                d = (datetime.fromisoformat(h["t"]) + CN).date()
            except Exception:
                continue
            b = days.setdefault(d, {"count": 0, "easy": 0, "fuzzy": 0, "forgot": 0})
            b["count"] += 1
            r = h.get("r")
            if r in ("easy", "simple"):
                b["easy"] += 1
            elif r == "fuzzy":
                b["fuzzy"] += 1
            else:
                b["forgot"] += 1
            if d == cn_today:
                reviewed_today += 1

    # 连续打卡：从今天（北京）往前，每天有复习记录则 +1
    streak = 0
    d = cn_today
    while d in days and days[d]["count"] > 0:
        streak += 1
        d -= timedelta(days=1)

    # 近 7 天趋势（北京日期）
    daily = []
    for i in range(6, -1, -1):
        day = cn_today - timedelta(days=i)
        b = days.get(day, {"count": 0, "easy": 0, "fuzzy": 0, "forgot": 0})
        daily.append({"date": day.isoformat(), **b})

    # 遗忘曲线：按间隔等级统计记得率（最近一次自评为「记得/简单」的比例），
    # 长间隔等级记得率若骤降 → 记忆正在流失，需更频繁复习
    levels = {i: {"total": 0, "remembered": 0} for i in range(len(INTERVALS))}
    for c in db.query(ReviewCard).all():
        lv = c.level if c.level is not None else 0
        if 0 <= lv < len(INTERVALS):
            levels[lv]["total"] += 1
            if c.history:
                last_r = (c.history[-1] or {}).get("r")
                if last_r in ("easy", "simple"):
                    levels[lv]["remembered"] += 1
    forgetting_curve = []
    for i in range(len(INTERVALS)):
        d = levels[i]
        rate = round(d["remembered"] / d["total"] * 100) if d["total"] else None
        forgetting_curve.append({
            "interval_days": INTERVALS[i], "total": d["total"],
            "remembered": d["remembered"], "rate": rate,
        })

    return {"total": total, "due": due, "reviewed_today": reviewed_today,
            "mastered": mastered, "weak": weak, "streak": streak, "daily": daily,
            "forgetting_curve": forgetting_curve}
