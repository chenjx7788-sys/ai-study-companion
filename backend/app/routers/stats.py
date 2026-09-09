"""数据统计面板路由（模块 H）：埋点 / 四维聚合 / AI 分析报告

- 学习时长、笔记回看 → ActivityLog 埋点
- 复习记忆维度复用 /review/stats（前端直接调用，此处不再重复）
- AI 报告：规则引擎判问题 + LLM 生成四段式报告，历史存 data/stats_reports.json
"""
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from ..core.config import settings as cfg
from ..models import ActivityLog, Material, Note, Highlight, AIAsset, ChatMessage, ReviewCard
from ..services import llm

router = APIRouter(prefix="/stats", tags=["stats"])

CN = timedelta(hours=8)


# ---------- 埋点 ----------

class TrackReq(BaseModel):
    type: str          # duration / note_view
    ref_id: int = 0    # material_id（duration）或 note_id（note_view）
    duration_seconds: int = 0


@router.post("/track")
def track(req: TrackReq, db: Session = Depends(get_db)):
    """行为埋点：学习时长（学习页心跳）/ 笔记回看"""
    if req.type not in ("duration", "note_view"):
        raise HTTPException(400, "type 须为 duration / note_view")
    if req.type == "duration" and (req.duration_seconds <= 0 or req.duration_seconds > 3600):
        raise HTTPException(400, "duration_seconds 须在 1-3600 之间")
    db.add(ActivityLog(type=req.type, ref_id=req.ref_id, duration_seconds=req.duration_seconds))
    db.commit()
    return {"ok": True}


# ---------- 聚合辅助 ----------

def _cn_today():
    return (datetime.utcnow() + CN).date()


def _activity_dates(db: Session):
    """跨表收集所有学习行为的北京日期集合（用于学习天数 / 连续天数）"""
    dates = set()
    for (created_at,) in db.query(Material.created_at).all():
        if created_at:
            dates.add((created_at + CN).date())
    for (created_at,) in db.query(Note.created_at).all():
        if created_at:
            dates.add((created_at + CN).date())
    for (created_at,) in db.query(ChatMessage.created_at).filter(ChatMessage.role == "user").all():
        if created_at:
            dates.add((created_at + CN).date())
    for (created_at,) in db.query(ActivityLog.created_at).all():
        if created_at:
            dates.add((created_at + CN).date())
    for (history,) in db.query(ReviewCard.history).all():
        for h in (history or []):
            try:
                dates.add((datetime.fromisoformat(h["t"]) + CN).date())
            except Exception:
                pass
    return dates


def _streak(dates: set, today):
    """从今天（北京）往前数连续有行为的天数"""
    n, d = 0, today
    while d in dates:
        n += 1
        d -= timedelta(days=1)
    return n


def _daily_series(db: Session, days: int, field, filters, value_field=None):
    """按北京日期聚合，返回近 days 天的序列 [{date, count}]（不足补零）。
    value_field 传入则按 sum(value_field) 聚合（如时长），否则按 count() 计数。"""
    today = _cn_today()
    start = datetime.combine(today - timedelta(days=days - 1), datetime.min.time()) - CN
    agg = func.sum(value_field) if value_field is not None else func.count()
    q = db.query(field, agg).filter(*filters).filter(field >= start).group_by(field).all()
    m = {}
    for d, c in q:
        if d:
            m[(d + CN).date()] = c or 0
    out = []
    for i in range(days - 1, -1, -1):
        day = today - timedelta(days=i)
        out.append({"date": day.strftime("%m-%d"), "count": m.get(day, 0)})
    return out


# ---------- 概览 + 四维 ----------

@router.get("/overview")
def overview(db: Session = Depends(get_db)):
    """概览 KPI + 三维聚合（复习维度前端复用 /review/stats）"""
    today = _cn_today()
    today_start = datetime.combine(today, datetime.min.time()) - CN
    week_start = today_start - timedelta(days=6)

    # ---- 今日 ----
    today_duration = db.query(func.coalesce(func.sum(ActivityLog.duration_seconds), 0)).filter(
        ActivityLog.type == "duration", ActivityLog.created_at >= today_start).scalar()
    today_notes = db.query(Note).filter(Note.created_at >= today_start).count()
    today_qa = db.query(ChatMessage).filter(ChatMessage.role == "user", ChatMessage.created_at >= today_start).count()
    today_hit = db.query(ChatMessage).filter(
        ChatMessage.role == "assistant", ChatMessage.kb_hit.is_(True), ChatMessage.created_at >= today_start).count()
    # 今日已复习（从 ReviewCard.history 算，北京口径）
    today_review = 0
    for (history,) in db.query(ReviewCard.history).all():
        for h in (history or []):
            try:
                if (datetime.fromisoformat(h["t"]) + CN).date() == today:
                    today_review += 1
            except Exception:
                pass

    # ---- 本周（近 7 天） ----
    week_duration = db.query(func.coalesce(func.sum(ActivityLog.duration_seconds), 0)).filter(
        ActivityLog.type == "duration", ActivityLog.created_at >= week_start).scalar()
    week_notes = db.query(Note).filter(Note.created_at >= week_start).count()
    week_qa = db.query(ChatMessage).filter(ChatMessage.role == "user", ChatMessage.created_at >= week_start).count()

    # ---- 累计 ----
    total_materials = db.query(Material).count()
    total_notes = db.query(Note).count()
    total_review_cards = db.query(ReviewCard).count()
    total_qa = db.query(ChatMessage).filter(ChatMessage.role == "user").count()

    # ---- 学习天数 / 连续天数 ----
    dates = _activity_dates(db)
    week_days = sum(1 for i in range(7) if (today - timedelta(days=i)) in dates)
    streak = _streak(dates, today)
    # 近 14 天活跃轨迹（供前端打卡圆点可视化）
    recent_active = [{"date": (today - timedelta(days=i)).strftime("%m-%d"),
                      "active": (today - timedelta(days=i)) in dates}
                     for i in range(13, -1, -1)]

    # ---- 学习投入维度 ----
    # 近 14 天时长（分钟）
    duration_daily = _daily_series(db, 14, ActivityLog.created_at,
                                   [ActivityLog.type == "duration"],
                                   value_field=ActivityLog.duration_seconds)
    for d in duration_daily:
        d["minutes"] = round(d.pop("count") / 60)
    # 材料分布（按标签）
    tag_count = {}
    for (tags,) in db.query(Material.tags).all():
        for t in (tags or []):
            tag_count[t] = tag_count.get(t, 0) + 1
    material_by_tag = [{"name": k, "value": v} for k, v in
                       sorted(tag_count.items(), key=lambda x: -x[1])[:8]]
    # 材料分布（按格式大类，数据更全，每份材料必有格式）
    FORMAT_GROUPS = {
        "文档": ["pdf", "doc", "docx", "md", "markdown"],
        "演示": ["ppt", "pptx"],
        "图片": ["jpg", "jpeg", "png", "webp", "bmp"],
        "音频": ["mp3", "wav", "m4a"],
        "视频": ["mp4"],
    }
    fmt_count = {}
    for (fmt,) in db.query(Material.format).all():
        fmt = (fmt or "").lower()
        for g, exts in FORMAT_GROUPS.items():
            if fmt in exts:
                fmt_count[g] = fmt_count.get(g, 0) + 1
                break
        else:
            fmt_count["其他"] = fmt_count.get("其他", 0) + 1
    material_by_format = [{"name": k, "value": v} for k, v in
                          sorted(fmt_count.items(), key=lambda x: -x[1]) if v > 0]

    # ---- 内容沉淀维度 ----
    # 笔记趋势：近 8 周累计（按周桶）
    note_weekly = []
    for i in range(7, -1, -1):
        w_end = today - timedelta(days=i * 7)
        w_start = w_end - timedelta(days=6)
        start_utc = datetime.combine(w_start, datetime.min.time()) - CN
        end_utc = datetime.combine(w_end, datetime.min.time()) + timedelta(days=1) - CN
        c = db.query(Note).filter(Note.created_at >= start_utc, Note.created_at < end_utc).count()
        note_weekly.append({"week": f"W{w_end.isocalendar()[1]}", "count": c})
    # 笔记来源构成 + 转笔记率
    ai_notes = db.query(Note).filter(Note.source_type == "ai_asset").count()
    manual_notes = total_notes - ai_notes
    ai_assets = db.query(AIAsset).filter(AIAsset.type.in_(["summary", "keywords", "explain", "qa"])).count()
    convert_rate = round(ai_notes / ai_assets * 100) if ai_assets else 0
    highlight_count = db.query(Highlight).count()

    # ---- 知识调用维度 ----
    qa_daily = _daily_series(db, 14, ChatMessage.created_at,
                             [ChatMessage.role == "user"])
    total_user_qa = db.query(ChatMessage).filter(ChatMessage.role == "user").count()
    total_hit = db.query(ChatMessage).filter(ChatMessage.role == "assistant", ChatMessage.kb_hit.is_(True)).count()
    hit_rate = round(total_hit / total_user_qa * 100) if total_user_qa else 0
    week_user_qa = db.query(ChatMessage).filter(ChatMessage.role == "user", ChatMessage.created_at >= week_start).count()
    week_hit = db.query(ChatMessage).filter(
        ChatMessage.role == "assistant", ChatMessage.kb_hit.is_(True), ChatMessage.created_at >= week_start).count()
    week_hit_rate = round(week_hit / week_user_qa * 100) if week_user_qa else 0

    return {
        "today": {
            "duration_seconds": int(today_duration or 0),
            "note_count": today_notes,
            "review_count": today_review,
            "qa_count": today_qa,
            "hit_rate": round(today_hit / today_qa * 100) if today_qa else 0,
        },
        "week": {
            "days": week_days,
            "duration_seconds": int(week_duration or 0),
            "note_count": week_notes,
            "qa_count": week_qa,
        },
        "total": {
            "material_count": total_materials,
            "note_count": total_notes,
            "review_card_count": total_review_cards,
            "qa_count": total_qa,
        },
        "streak": streak,
        "recent_active": recent_active,
        "dimensions": {
            "invest": {
                "duration_daily": duration_daily,
                "material_by_tag": material_by_tag,
                "material_by_format": material_by_format,
            },
            "sediment": {
                "note_weekly": note_weekly,
                "note_source": {"ai": ai_notes, "manual": manual_notes},
                "highlight_count": highlight_count,
                "convert_rate": convert_rate,
            },
            "recall": {
                "qa_daily": qa_daily,
                "hit_rate": hit_rate,
                "week_hit_rate": week_hit_rate,
            },
        },
    }


# ---------- AI 分析报告 ----------

REPORT_STORE = cfg.data_dir / "stats_reports.json"
MAX_REPORTS = 20


def _load_reports() -> list:
    if REPORT_STORE.exists():
        try:
            return json.loads(REPORT_STORE.read_text(encoding="utf-8")).get("reports", [])
        except Exception:
            return []
    return []


def _save_reports(reports: list):
    REPORT_STORE.write_text(
        json.dumps({"reports": reports}, ensure_ascii=False, indent=2), encoding="utf-8")


def _diagnose(db: Session, today):
    """规则引擎：基于聚合数据判出问题清单，返回 [(标题, 说明, 数据依据)]"""
    issues = []

    # 1. 学习断档：近 3 天零行为
    dates = _activity_dates(db)
    last3 = [(today - timedelta(days=i)) in dates for i in range(3)]
    if not any(last3):
        issues.append(("学习断档",
                       "近 3 天没有任何学习、复习、笔记或问答行为，学习节奏出现松动。",
                       "近 3 天零学习行为"))

    # 2. 只进不出：转笔记率 < 30%
    total_notes = db.query(Note).count()
    ai_notes = db.query(Note).filter(Note.source_type == "ai_asset").count()
    ai_assets = db.query(AIAsset).filter(AIAsset.type.in_(["summary", "keywords", "explain", "qa"])).count()
    convert_rate = round(ai_notes / ai_assets * 100) if ai_assets else 0
    if ai_assets and convert_rate < 30:
        issues.append(("只进不出",
                       "AI 产物较多但转笔记率偏低，读得多、留得少，知识沉淀不足。",
                       f"转笔记率 {convert_rate}%（目标 ≥60%）"))

    # 3. 复习衰减：遗忘曲线长间隔档记得率 < 70%
    levels = {i: {"total": 0, "remembered": 0} for i in range(6)}
    for c in db.query(ReviewCard).all():
        lv = c.level if c.level is not None else 0
        if 0 <= lv < 6:
            levels[lv]["total"] += 1
            if c.history:
                last_r = (c.history[-1] or {}).get("r")
                if last_r in ("easy", "simple"):
                    levels[lv]["remembered"] += 1
    decay = None
    for lv in (4, 5):  # 15 天、30 天档
        d = levels[lv]
        if d["total"] >= 3:
            rate = d["remembered"] / d["total"] * 100
            if rate < 70:
                decay = (lv, round(rate))
    if decay:
        iv = {4: "15 天", 5: "30 天"}[decay[0]]
        issues.append(("复习衰减",
                       f"遗忘曲线在 {iv} 间隔档记得率跌破 70%，早期内容正在流失。",
                       f"{iv}档记得率 {decay[1]}%"))

    # 4. 薄弱积压：weak 卡占比偏高
    total_cards = db.query(ReviewCard).count()
    weak = db.query(ReviewCard).filter(ReviewCard.review_count >= 2, ReviewCard.level <= 1).count()
    if total_cards and weak / total_cards > 0.2:
        issues.append(("薄弱积压",
                       "复习卡中薄弱卡占比偏高，部分知识点反复记不住，需要针对性强化。",
                       f"薄弱卡 {weak}/{total_cards}（占比 {round(weak/total_cards*100)}%）"))

    # 5. 命中率滑坡：本周问答命中率较上周明显下降
    week_start = datetime.combine(today - timedelta(days=6), datetime.min.time()) - CN
    prev_week_end = week_start
    prev_week_start = week_start - timedelta(days=7)
    week_qa = db.query(ChatMessage).filter(ChatMessage.role == "user", ChatMessage.created_at >= week_start).count()
    week_hit = db.query(ChatMessage).filter(ChatMessage.role == "assistant", ChatMessage.kb_hit.is_(True), ChatMessage.created_at >= week_start).count()
    prev_qa = db.query(ChatMessage).filter(ChatMessage.role == "user", ChatMessage.created_at >= prev_week_start, ChatMessage.created_at < prev_week_end).count()
    prev_hit = db.query(ChatMessage).filter(ChatMessage.role == "assistant", ChatMessage.kb_hit.is_(True), ChatMessage.created_at >= prev_week_start, ChatMessage.created_at < prev_week_end).count()
    week_rate = round(week_hit / week_qa * 100) if week_qa else None
    prev_rate = round(prev_hit / prev_qa * 100) if prev_qa else None
    if week_rate is not None and prev_rate is not None and week_qa >= 5 and prev_qa >= 5 and week_rate < prev_rate - 15:
        issues.append(("命中率滑坡",
                       "本周知识库问答命中率较上周明显下降，检索或向量索引质量可能出了问题，建议检查向量模型或回填知识库。",
                       f"命中率 {prev_rate}% → {week_rate}%"))

    return issues


def _prepare_report_context(db: Session) -> dict:
    """准备报告上下文：数据摘要 + 规则诊断 + 主题 + messages（手动/自动/流式共用）"""
    today = _cn_today()
    ov = overview(db)

    # 数据摘要（喂给 LLM 的客观文本）
    today_h = round(ov["today"]["duration_seconds"] / 3600, 1)
    week_h = round(ov["week"]["duration_seconds"] / 3600, 1)
    summary_text = (
        f"本周学习 {ov['week']['days']} 天，累计 {week_h} 小时；"
        f"新增笔记 {ov['week']['note_count']} 条（累计 {ov['total']['note_count']} 条）；"
        f"问答 {ov['week']['qa_count']} 次，知识库命中率 {ov['dimensions']['recall']['week_hit_rate']}%；"
        f"今日学习 {today_h} 小时、新增笔记 {ov['today']['note_count']} 条；"
        f"连续学习 {ov['streak']} 天。"
    )

    # 规则引擎诊断
    issues = _diagnose(db, today)
    issues_text = "\n".join(f"- {t}：{d}（{v}）" for t, d, v in issues) or "（无明显问题）"

    # 学习主题（材料标签，供进阶方法）
    topics = set()
    for (tags,) in db.query(Material.tags).all():
        for t in (tags or []):
            topics.add(t)
    topics_text = "、".join(sorted(topics)[:10])

    iso_week = today.isocalendar()
    return {
        "title": f"第 {iso_week[1]} 周学习周报",
        "summary_text": summary_text,
        "issues_detail": [{"title": t, "detail": v} for t, d, v in issues],
        "messages": llm.stats_report_messages(summary_text, issues_text, topics_text),
    }


def _save_report(title: str, summary_text: str, issues_detail: list, content: str) -> dict:
    """保存报告：同一 ISO 周只保留最新一份（手动重新生成 = 刷新本周报告，不再堆积）"""
    report = {
        "id": str(int(time.time() * 1000)),
        "title": title,
        "summary": summary_text,
        "issues": issues_detail,
        "content": content,
        "created_at": datetime.utcnow().isoformat() + "Z",
    }
    current_week = (datetime.utcnow() + CN).isocalendar()[:2]
    def _week_of(r):
        try:
            return (datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")).replace(tzinfo=None) + CN).isocalendar()[:2]
        except Exception:
            return None
    reports = [r for r in _load_reports() if _week_of(r) != current_week]
    reports.insert(0, report)
    _save_reports(reports[:MAX_REPORTS])
    return report


def generate_report_core(db: Session) -> dict:
    """生成四段式 AI 分析报告的核心逻辑（供自动周报复用）"""
    ctx = _prepare_report_context(db)
    content = llm.summary_chat(ctx["messages"], kind="stats_report")
    return _save_report(ctx["title"], ctx["summary_text"], ctx["issues_detail"], content)


SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("/report/stream")
def generate_report_stream(db: Session = Depends(get_db)):
    """SSE 流式生成 AI 分析报告：先发 meta（标题/摘要/诊断），再逐 token 输出正文，done 时落库"""
    ctx = _prepare_report_context(db)

    def gen():
        full = ""
        try:
            yield _sse("meta", {"title": ctx["title"], "summary": ctx["summary_text"],
                                "issues": ctx["issues_detail"]})
            for text in llm.summary_chat_stream(ctx["messages"], kind="stats_report"):
                full += text
                yield _sse("token", {"t": text})
        except Exception as e:
            yield _sse("error", {"message": str(e)[:200]})
            return
        report = _save_report(ctx["title"], ctx["summary_text"], ctx["issues_detail"], full)
        yield _sse("done", {"report": report})

    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/report")
def generate_report(db: Session = Depends(get_db)):
    """手动生成 AI 分析报告"""
    return generate_report_core(db)


def maybe_generate_weekly_report(db: Session) -> dict | None:
    """自动周报：应用启动时检查，若本周（ISO 周）尚未生成报告且有学习数据，则自动生成一份。
    失败静默（如未配置 LLM），不阻塞启动。"""
    current_week = (datetime.utcnow() + CN).isocalendar()[:2]
    reports = _load_reports()
    if reports:
        try:
            latest = reports[0]
            latest_week = (datetime.fromisoformat(latest["created_at"].replace("Z", "+00:00")) + CN).isocalendar()[:2]
        except Exception:
            latest_week = None
        if latest_week == current_week:
            return None   # 本周已生成过，跳过

    # 无学习数据时不生成（避免空报告）
    has_data = (db.query(Note).count() > 0
                or db.query(ChatMessage).filter(ChatMessage.role == "user").count() > 0
                or db.query(ReviewCard).count() > 0)
    if not has_data:
        return None
    try:
        return generate_report_core(db)
    except Exception:
        return None   # 自动周报失败静默（如未配 LLM）


@router.get("/reports")
def list_reports():
    """报告历史列表（倒序）"""
    return [{"id": r["id"], "title": r["title"], "summary": r["summary"],
             "issues": r.get("issues", []), "created_at": r["created_at"]}
            for r in _load_reports()]


@router.get("/reports/{report_id}")
def get_report(report_id: str):
    """单份报告详情"""
    for r in _load_reports():
        if r["id"] == report_id:
            return r
    raise HTTPException(404, "报告不存在")
