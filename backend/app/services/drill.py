"""AI 陪练服务（PRD 模块 J · V1.0）

职责：范围解析 / 素材组装 / 题纲·出题·评分编排 / 会话生命周期 / 并发闸 / 错题回写。

⚠️ 本模块的两条硬约束（来自 PRD 附录 A + 2.2.6）：
1. **会话生命周期的唯一权威是 DB**（`drill_sessions.status`），内存中的 `_DrillJob`
   只是「执行器 + SSE 订阅总线」；并发闸**按 DB 统计**，不用进程内计数。
   （不得照搬 `simple_learn._LearnJob`：它以 material_id 为键、进程内 TTL 回收，
    且 `_purge_jobs` 只回收 `status != running` → running 永不回收、重启即孤儿。）
2. 错题回写必须用 **`source="drill"`**（复用 `quiz` 会与「测一测 / 简单学」互删，
   连复习进度一起清空且不可恢复），`material_id` 无法确定时写**哨兵值 -1**（不得写 0）。
"""
import asyncio
import json
import queue
import threading
import time
from datetime import datetime, timedelta

from fastapi import HTTPException

from ..database import SessionLocal
from ..models import (AIAsset, DrillSession, DrillTurn, Material, MaterialChunk,
                      Note, ReviewCard)
from . import llm

# ---------- 参数（PRD 3.2.5 / 3.3.5 / 4.3） ----------

DEFAULT_QUESTION_COUNT = 8
MIN_QUESTION_COUNT = 3
MAX_QUESTION_COUNT = 20
FOLLOW_UP_SHALLOW = "shallow"     # 答得浅才追问（默认）
FOLLOW_UP_ALWAYS = "always"       # 每题都追问
FOLLOW_UP_NEVER = "never"         # 不追问
OUTLINE_BUDGET_CHARS = 8000       # 单次题纲输入字数预算
OUTLINE_SAMPLE_MATERIALS = 12     # 全库范围先抽样的材料份数（12-15，与题量同量级）
MAX_CONCURRENT_SESSIONS = 3       # 并发闸上限（按 DB 统计）
ORPHAN_WINDOW_MIN = 10            # 孤儿会话回收窗口（分钟）≥ 题纲生成超时上限
SSE_HEARTBEAT_SEC = 10.0
# SSE 等待队列的轮询间隔。⚠️ 生成器必须 async 且「get_nowait + await sleep」：
# 同步生成器被 Starlette 丢进线程池迭代 → 客户端断开后 finally 永不执行 →
# job.subs 里的订阅者永久滞留（2026-10-14 实测：断开 4.3s 后仍为 1）。
SSE_POLL_SEC = 0.1
JOB_TTL_SEC = 600                 # 已结束任务在内存中保留 10 分钟（供附着取结果）
SENTINEL_MATERIAL_ID = -1         # ⚠️ 回写卡 material_id 的哨兵值（0 已被「AI 问答」占用）

# ---------- 难度自适应参数（C 档） ----------
DIFFICULTY_LEVELS = ("easy", "medium", "hard")
# 题纲难度来自 LLM，可能写成「Medium」「中等」「难」等任意形态 → 落库前统一到三档。
# ⚠️ 必须归一化：前端拿 turn.difficulty 与 outline[i].difficulty 比对来判断
#    「这题是不是被自适应调过」；不归一化时 "Medium" != "medium" 会让**每题**都
#    显示「已加难」—— 那是误导性的假信息，比不显示更糟。
_DIFF_ALIASES = {
    "easy": "easy", "简单": "easy", "容易": "easy", "基础": "easy", "低": "easy",
    "medium": "medium", "中等": "medium", "中": "medium", "一般": "medium", "普通": "medium",
    "hard": "hard", "困难": "hard", "较难": "hard", "难": "hard", "高": "hard",
}


def norm_difficulty(value) -> str:
    """任意来源的难度 → DIFFICULTY_LEVELS 之一；无法识别时退化为 medium（与前端同口径）。"""
    s = str(value if value is not None else "").strip().lower()
    return _DIFF_ALIASES.get(s, "medium")


ADAPT_UP_SCORE = 80               # 默认「答得好」的判定线（管理中心可覆盖）
ADAPT_DOWN_SCORE = 50             # 默认「答得差」的判定线
ADAPT_STREAK = 2                  # 默认连续 N 题同向 → 调一档
ADAPT_MAX_SHIFT = 2               # 默认相对基准档最多调 2 档（防一路加难跑飞）

# 管理中心可调的键 → (字段名, 硬默认, 下限, 上限)
_ADAPT_FIELDS = (
    ("drill_adapt_up_score", ADAPT_UP_SCORE, 50, 100),
    ("drill_adapt_down_score", ADAPT_DOWN_SCORE, 0, 79),
    ("drill_adapt_streak", ADAPT_STREAK, 1, 10),
    ("drill_adapt_max_shift", ADAPT_MAX_SHIFT, 0, 2),
)


def adapt_params() -> tuple[int, int, int, int]:
    """难度自适应参数 (升档线, 降档线, 连续题数, 最大档差) —— 管理中心可调。

    ⚠️ 配置缺失 / 非法 / 读取异常一律**回退模块默认常量**：
       出题链路不能因为一个设置项坏了就出不了题（宁可退回默认行为）。
    """
    conf = {}
    try:
        from . import settings_store
        conf = settings_store.load() or {}
    except Exception:       # noqa: BLE001
        conf = {}
    out = []
    for key, default, lo, hi in _ADAPT_FIELDS:
        try:
            v = int(conf.get(key, default))
        except (TypeError, ValueError):
            v = default
        out.append(max(lo, min(hi, v)))
    return (out[0], out[1], out[2], out[3])

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}

VALID_SCOPES = ("all", "material", "note", "folder")


class _Fatal(Exception):
    """不可恢复的业务失败（会话置 failed，可重试）"""


# ---------- 公共小工具 ----------

def sse_event(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def llm_ready() -> bool:
    """LLM 是否已配置（陪练走 summary 模型，与「测一测」同链路）"""
    from . import settings_store
    return settings_store.resolve_usage("summary") is not None


def _folder_to_material_ids(db, folder_ids: list[int]) -> list[int]:
    """文件夹（含子文件夹）→ 材料 id：直接复用 chat 的实现，避免两份递归逻辑分叉。"""
    from ..routers.chat import _folder_to_material_ids as _expand
    return _expand(db, folder_ids)


def _summary_text(db, material_id: int) -> str:
    a = (db.query(AIAsset)
         .filter(AIAsset.material_id == material_id, AIAsset.type == "summary")
         .order_by(AIAsset.version.desc()).first())
    return (a.content or "").strip() if a else ""


def _material_fallback_text(db, material_id: int, limit: int = 3000) -> str:
    """无摘要时退回原文块（⚠️ 只作为兜底，不主动截原文前 N 字作首选口径）"""
    rows = (db.query(MaterialChunk)
            .filter(MaterialChunk.material_id == material_id)
            .order_by(MaterialChunk.page_no, MaterialChunk.id).all())
    return ("\n".join(r.content or "" for r in rows))[:limit]


def _match_material_id(db, title: str):
    t = (title or "").strip()
    if not t:
        return None
    m = db.query(Material).filter(Material.title == t).first()
    return m.id if m else None


# ---------- 范围解析与素材组装（PRD 3.1 / 3.3.5） ----------

def scope_label(s: DrillSession) -> str:
    """范围的人类可读描述（喂给 LLM 作上下文，并作回写标题兜底）"""
    refs = s.scope_refs or []
    names = [r.get("title") for r in refs if r.get("title")]
    base = {"all": "整个知识库", "material": "指定材料", "note": "指定笔记",
            "folder": "指定文件夹"}.get(s.scope, "知识库")
    return f"{base}：{'、'.join(names)}" if names else base


def scope_targets(db, scope: str, refs: list) -> tuple[list[int], list[int]]:
    """范围 → (material_ids, note_ids)。

    ⚠️ 展开结果为空即返回空（调用方**必须拒绝**，严禁把 where=None 传给检索层 —— 会静默检索全库）。
    """
    ids = [int(r["id"]) for r in (refs or []) if r.get("id") is not None]
    if scope == "all":
        mids = [m.id for m in db.query(Material)
                .filter(Material.parsed_status == "success").all()]
        return mids, []
    if scope == "material":
        return [i for i in ids if db.get(Material, i) is not None], []
    if scope == "note":
        return [], [i for i in ids if db.get(Note, i) is not None]
    if scope == "folder":
        return (_folder_to_material_ids(db, ids) if ids else []), []
    return [], []


def preview_scope(db, scope: str, refs: list) -> dict:
    """范围摘要（PRD 3.1.5）：材料数 / 笔记数 / 内容量估算 / 被排除数（解析未完成）。

    ⚠️ 绝不在此处走检索（验收 #3 要求：空范围时**无检索调用记录**）。
    """
    if scope == "note":
        nids = [int(r["id"]) for r in (refs or []) if r.get("id") is not None]
        notes = [n for n in (db.get(Note, i) for i in nids) if n and (n.content or "").strip()]
        return {"material_count": 0, "note_count": len(notes), "excluded_count": 0,
                "content_chars": sum(len(n.content or "") for n in notes)}

    if scope == "all":
        rows = db.query(Material).filter(Material.parsed_status == "success").all()
        excluded = db.query(Material).filter(Material.parsed_status != "success").count()
    else:
        ids = [int(r["id"]) for r in (refs or []) if r.get("id") is not None]
        if scope == "folder":
            ids = _folder_to_material_ids(db, ids) if ids else []
        rows, excluded = [], 0
        for i in ids:
            m = db.get(Material, i)
            if not m:
                continue
            if m.parsed_status == "success":
                rows.append(m)
            else:
                excluded += 1
    return {"material_count": len(rows), "note_count": 0, "excluded_count": excluded,
            "content_chars": sum(len(_summary_text(db, m.id)) for m in rows)}


def _allocate(items: list, budget: int, sample: int | None = None) -> list:
    """先抽样、再按内容长度**加权分配**配额；配额不足以容纳整份 → **整份跳过**。

    ⚠️ 禁止「每份材料等额配额」：全库数十份材料下等额只有约 210 字/份，
    而摘要通常 200-500 字 → 每份摘要都被腰斩，「摘要优先」退化为碎片拼盘（PRD 3.3.5）。
    """
    pool = list(items)
    if sample and len(pool) > sample:
        pool = sorted(pool, key=lambda it: -len(it[2]))[:sample]
    total_w = sum(len(it[2]) for it in pool) or 1
    kept = [it for it in pool if budget * len(it[2]) / total_w >= len(it[2])]
    if not kept:
        # 极端：所有配额都不足（材料极少 + 摘要超长）→ 按长度升序尽量塞满预算
        kept, acc = [], 0
        for it in sorted(pool, key=lambda x: len(x[2])):
            if kept and acc + len(it[2]) > budget:
                break
            kept.append(it)
            acc += len(it[2])
    return kept


def build_outline_input(db, scope: str, refs: list, budget: int = OUTLINE_BUDGET_CHARS):
    """组装题纲输入素材（PRD 3.3.5）。返回 (text, sources)，text 为空串表示范围不可用。"""
    if scope == "note":
        _, nids = scope_targets(db, scope, refs)
        parts, sources = [], []
        for nid in nids:
            n = db.get(Note, nid)
            if not n or not (n.content or "").strip():
                continue
            parts.append(f"【笔记】{n.title}\n{n.content.strip()}")
            sources.append({"type": "note", "id": n.id, "title": n.title})
        return ("\n\n".join(parts))[:budget], sources

    mids, _ = scope_targets(db, scope, refs)
    if not mids:
        return "", []
    items = []   # (material_id, title, text)
    for mid in mids:
        m = db.get(Material, mid)
        if not m:
            continue
        txt = _summary_text(db, mid)
        if not txt:
            txt = _material_fallback_text(db, mid)
        if txt.strip():
            items.append((mid, m.title, txt))
    if not items:
        return "", []
    sample = OUTLINE_SAMPLE_MATERIALS if scope == "all" else None
    kept = _allocate(items, budget, sample)
    parts = [f"【材料】{t}\n{x}" for (_mid, t, x) in kept]
    sources = [{"type": "material", "id": mid, "title": t} for (mid, t, _x) in kept]
    return ("\n\n".join(parts))[:budget], sources


def default_title(db, scope: str, refs: list) -> str:
    """会话标题兜底（PRD 3.2.4：用户留空 → 后端自动生成，保证字段非空）"""
    refs = refs or []
    if scope in ("material", "note") and len(refs) == 1:
        return f"《{refs[0].get('title') or '内容'}》陪练"[:50]
    base = {"all": "全库陪练", "material": "材料陪练",
            "note": "笔记陪练", "folder": "文件夹陪练"}.get(scope, "陪练")
    return (base if not refs else f"{base} · {len(refs)} 项")[:50]


# ---------- 会话生命周期 / 并发闸（PRD 2.2.6） ----------

def count_active(db) -> int:
    """并发闸：按 DB 统计窗口内的进行中会话（不用进程内计数）"""
    window = timedelta(minutes=ORPHAN_WINDOW_MIN)
    return (db.query(DrillSession)
            .filter(DrillSession.status.in_(("outlining", "running")),
                    DrillSession.updated_at > datetime.utcnow() - window)
            .count())


def recover_orphans(db) -> int:
    """启动时回收孤儿会话：status ∈ {outlining,running} 且 updated_at 超窗口 → failed。

    否则重启后这些会话永远停在「进行中」且点不开（僵尸卡片），且永久占用并发槽位
    （`updated_at` 不再刷新 → 槽位只进不出）。
    """
    cutoff = datetime.utcnow() - timedelta(minutes=ORPHAN_WINDOW_MIN)
    rows = (db.query(DrillSession)
            .filter(DrillSession.status.in_(("outlining", "running")),
                    DrillSession.updated_at < cutoff).all())
    for s in rows:
        s.status = "failed"
        s.error = "服务重启中断"
    if rows:
        db.commit()
    return len(rows)


# ---------- 序列化 ----------

def turn_to_dict(t: DrillTurn, show_answer: bool = True) -> dict:
    """单轮序列化。

    ⚠️ `show_answer=False` 时必须把要点置空。本函数是**只读回看**路径
    （`GET /sessions/{id}` 的 turns）；实时 SSE 走 `_run_answer` 里的内联 dict
    （字段名 `expect_points`，同样裁剪）。两条路径必须同源 —— 否则用户关掉
    「参考答案」后当场看不到要点，**一刷新 / 从历史进入就把标准答案全露出来**。
    """
    return {
        "id": t.id, "seq": t.seq, "outline_index": t.outline_index,
        "is_followup": bool(t.is_followup), "question": t.question,
        "expected_points": (t.expected_points or []) if show_answer else [],
        "source_material_id": t.source_material_id, "source_title": t.source_title or "",
        "user_answer": t.user_answer, "score": t.score, "verdict": t.verdict,
        "dimensions": t.dimensions, "comment": t.comment or "",
        "missing_points": t.missing_points or [],
        # 本题**实际使用**的难度（自适应调整后的结果）；空 = 旧数据未记录
        "difficulty": t.difficulty or "",
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


def session_to_dict(s: DrillSession, db=None, with_turns: bool = False) -> dict:
    up_score, down_score, streak, max_shift = adapt_params()
    d = {
        "id": s.id, "title": s.title, "scope": s.scope, "scope_refs": s.scope_refs or [],
        "config": s.config or {}, "status": s.status, "outline": s.outline or [],
        "current_seq": s.current_seq or 0, "total_score": s.total_score,
        "written_back_at": s.written_back_at.isoformat() if s.written_back_at else None,
        "error": s.error or "",
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
        # 难度自适应参数（当前生效值）—— 前端据此**复算**「这题为什么被调难/调易」；
        # 复算结果与实际调整方向不一致时前端不展示依据（宁可不说，也不给假依据）。
        "adapt": {"up_score": up_score, "down_score": down_score,
                  "streak": streak, "max_shift": max_shift},
    }
    if with_turns and db is not None:
        rows = (db.query(DrillTurn).filter(DrillTurn.session_id == s.id)
                .order_by(DrillTurn.seq).all())
        show_answer = bool((s.config or {}).get("show_answer", True))
        d["turns"] = [turn_to_dict(t, show_answer=show_answer) for t in rows]
    return d


# ---------- 后台任务（执行器 + SSE 订阅总线） ----------

class _DrillJob:
    """一次陪练阶段任务（题纲生成 / 评分推进）的内存状态。

    ⚠️ 它**不是**权威状态：权威在 `drill_sessions.status` / `drill_turns`。
    这里只负责「把进度实时推给当前订阅者」+ 「任务不存在时的降级判断」。
    """

    def __init__(self, sid: int, stage: str = "outline"):
        self.sid = sid
        self.status = "running"      # running / done / failed
        self.stage = stage           # outline / question / score / done
        self.error = ""
        self.subs: list = []         # list[queue.Queue]
        self.finished_at = 0.0
        self.buf: list = []          # 事件回放缓冲（中途回来补发前面的进度）

    def broadcast(self, kind: str, payload: dict) -> None:
        ev = (kind, payload)
        self.buf.append(ev)
        if len(self.buf) > 200:
            self.buf = self.buf[-200:]
        for q in list(self.subs):
            q.put(ev)


_DRILL_JOBS: dict[int, _DrillJob] = {}
_DRILL_LOCK = threading.Lock()


def _purge_jobs() -> None:
    """清理已结束且超过 TTL 的任务（调用方须持有 _DRILL_LOCK）"""
    now = time.time()
    for sid, j in list(_DRILL_JOBS.items()):
        if j.status != "running" and now - j.finished_at > JOB_TTL_SEC:
            _DRILL_JOBS.pop(sid, None)


def has_running_job(sid: int) -> bool:
    with _DRILL_LOCK:
        j = _DRILL_JOBS.get(sid)
        return bool(j and j.status == "running")


def get_job(sid: int):
    with _DRILL_LOCK:
        return _DRILL_JOBS.get(sid)


def _new_job_locked(sid: int, stage: str) -> _DrillJob:
    _purge_jobs()
    old = _DRILL_JOBS.get(sid)
    if old and old.status == "running":
        return old                      # 已有任务在跑 → 附着
    job = _DrillJob(sid, stage)
    _DRILL_JOBS[sid] = job
    return job


def start_outline_job(sid: int, resume: bool = False) -> _DrillJob:
    """启动（或附着）题纲生成 / 断点续跑任务"""
    with _DRILL_LOCK:
        job = _new_job_locked(sid, "outline")
        fresh = job.finished_at == 0.0 and not job.buf
    if fresh:
        threading.Thread(target=_run_outline, args=(sid, job, resume), daemon=True).start()
    return job


def start_answer_job(sid: int, seq: int, answer: str, skip: bool) -> _DrillJob:
    """启动（或附着）评分推进任务"""
    with _DRILL_LOCK:
        job = _new_job_locked(sid, "score")
        fresh = job.finished_at == 0.0 and not job.buf
    if fresh:
        threading.Thread(target=_run_answer, args=(sid, seq, answer, skip, job),
                         daemon=True).start()
    return job


async def event_stream(job: _DrillJob):
    """SSE 生成器：先补发回放缓冲（中途回来能看到已有进度），再实时推送。

    ⚠️ 快照缓冲与登记订阅者必须在**同一把锁**内完成，否则会漏事件或重复发。
    ⚠️ 必须 async（原因见 SSE_POLL_SEC 注释）：同步生成器在客户端断开后 finally
       永不执行 → `job.subs` 里的订阅者永久滞留，长任务 + 反复进出页面会堆积
       死订阅队列。
    """
    q: queue.Queue = queue.Queue()
    with _DRILL_LOCK:
        backlog = list(job.buf)
        job.subs.append(q)
    try:
        for ev in backlog:
            yield sse_event(*ev)
        if job.status == "done":
            return
        if job.status == "failed":
            yield sse_event("error", {"message": job.error})
            return
        last = time.monotonic()
        while True:
            try:
                kind, payload = q.get_nowait()
            except queue.Empty:
                if time.monotonic() - last >= SSE_HEARTBEAT_SEC:
                    last = time.monotonic()
                    yield ": ping\n\n"
                await asyncio.sleep(SSE_POLL_SEC)
                continue
            last = time.monotonic()
            yield sse_event(kind, payload)
            if kind in ("done", "error"):
                return
    finally:
        with _DRILL_LOCK:
            if q in job.subs:
                job.subs.remove(q)


# ---------- 任务实现 ----------

def _mark_failed(sid: int, msg: str) -> None:
    """会话置 failed（独立会话，避免用已被 rollback 的会话对象）"""
    db = SessionLocal()
    try:
        s = db.get(DrillSession, sid)
        if s and s.status != "finished":
            s.status = "failed"
            s.error = (msg or "生成失败")[:255]
            s.updated_at = datetime.utcnow()
            db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def _run_outline(sid: int, job: _DrillJob, resume: bool) -> None:
    db = SessionLocal()
    try:
        s = db.get(DrillSession, sid)
        if not s:
            raise _Fatal("陪练记录不存在或已删除")

        if resume and (s.outline or []):
            # 断点续跑：题纲已在 DB，不重新生成（省一次 LLM，也不清空已有作答）
            job.stage = "question"
            job.broadcast("outline", {
                "points": [{"point": p.get("point"), "difficulty": p.get("difficulty", "medium")}
                           for p in s.outline],
                "count": len(s.outline)})
            last = (db.query(DrillTurn).filter(DrillTurn.session_id == sid)
                    .order_by(DrillTurn.seq.desc()).first())
            if last and last.user_answer is None:
                _broadcast_question(job, s, last)
            elif last:
                _advance(db, s, job, last)
            else:
                _emit_question(db, s, job, 0, False)
            job.status = "done"
            job.broadcast("done", {"status": s.status})
            return

        s.status = "outlining"
        s.error = ""
        s.updated_at = datetime.utcnow()
        db.commit()

        job.stage = "outline"
        job.broadcast("stage", {"stage": "outline", "text": "正在整理考点…"})

        text, _sources = build_outline_input(db, s.scope, s.scope_refs or [])
        if not text.strip():
            raise _Fatal("范围内容为空，无法出题")

        count = int((s.config or {}).get("question_count") or DEFAULT_QUESTION_COUNT)
        points = llm.generate_drill_outline(text, count, scope_label=scope_label(s))
        clean = []
        for p in points:
            pt = str(p.get("point") or "").strip()
            if not pt:
                continue
            clean.append({"point": pt,
                          "difficulty": norm_difficulty(p.get("difficulty")),
                          "hint": str(p.get("hint") or ""),
                          "source": str(p.get("source") or "")})
        if not clean:
            raise _Fatal("考点生成失败，请重试")

        # ⚠️ 只在拿到**合格题纲之后**才落库（PRD 附录 A #4）
        s.outline = clean
        s.status = "running"
        s.current_seq = 1
        s.updated_at = datetime.utcnow()
        db.commit()

        job.broadcast("outline", {
            "points": [{"point": p["point"], "difficulty": p["difficulty"]} for p in clean],
            "count": len(clean)})
        _emit_question(db, s, job, 0, False)
        job.status = "done"
        job.broadcast("done", {"status": "running", "next": "question"})
    except _Fatal as e:
        db.rollback()
        _mark_failed(sid, str(e))
        job.status = "failed"
        job.error = str(e)
        job.broadcast("error", {"message": str(e)})
    except BaseException as e:      # noqa: BLE001
        db.rollback()
        msg = str(getattr(e, "detail", None) or e or "生成失败")[:200]
        _mark_failed(sid, msg)
        job.status = "failed"
        job.error = msg
        job.broadcast("error", {"message": msg})
    finally:
        job.finished_at = time.time()
        db.close()


def _run_answer(sid: int, seq: int, answer: str, skip: bool, job: _DrillJob) -> None:
    db = SessionLocal()
    try:
        s = db.get(DrillSession, sid)
        if not s:
            raise _Fatal("陪练记录不存在或已删除")
        turn = (db.query(DrillTurn)
                .filter(DrillTurn.session_id == sid, DrillTurn.seq == seq).first())
        if not turn:
            raise _Fatal("轮次不存在，请刷新后重试")

        total = len(s.outline or [])
        cur = (turn.outline_index or 0) + 1

        if skip:
            job.stage = "score"
            job.broadcast("stage", {"stage": "score", "text": "已跳过本题…"})
            turn.user_answer = ""           # "" == 跳过（NULL == 未作答）
            turn.score = None
            turn.verdict = None
            turn.dimensions = None
            turn.comment = "本题已跳过"
            turn.missing_points = []
            s.updated_at = datetime.utcnow()
            db.commit()
            job.broadcast("score", {"seq": seq, "skipped": True, "score": None,
                                    "verdict": None, "comment": turn.comment,
                                    "progress": {"cur": cur, "total": total}})
            _advance(db, s, job, turn)
        else:
            job.stage = "score"
            job.broadcast("stage", {"stage": "score", "text": "AI 正在评分…"})
            turn.user_answer = answer
            db.commit()
            try:
                res = llm.judge_drill_answer(turn.question, turn.expected_points or [], answer)
            except Exception as e:      # noqa: BLE001  含 HTTPException
                # 降级（PRD 3.4.7 / 3.5）：该题标「未评分」且**不阻断后续**。
                # ⚠️ 绝不走 job.status="failed"：那会让整场停在原地、要用户手点重试，
                #    而 resume 分支见到 user_answer 已落库会静默跳过 → 该题永久未评分。
                fail_msg = str(getattr(e, "detail", None) or e or "本题评分失败")[:200]
                turn.score = None
                turn.verdict = None
                turn.dimensions = None
                turn.comment = "本题评分失败，未计入总分"
                turn.missing_points = []
                s.updated_at = datetime.utcnow()
                db.commit()
                job.broadcast("score", {
                    "seq": seq, "turn_id": turn.id, "outline_index": turn.outline_index or 0,
                    "skipped": False, "failed": True, "score": None, "verdict": None,
                    "comment": turn.comment, "message": fail_msg,
                    "progress": {"cur": cur, "total": total}})
                _advance(db, s, job, turn)
            else:
                turn.score = res["score"]
                turn.verdict = res["verdict"]
                turn.dimensions = res["dimensions"]
                turn.comment = res["comment"]
                turn.missing_points = res["missing_points"]
                s.updated_at = datetime.utcnow()
                db.commit()
                show_answer = bool((s.config or {}).get("show_answer", True))
                job.broadcast("score", {
                    "seq": seq, "skipped": False, "score": res["score"], "verdict": res["verdict"],
                    "dimensions": res["dimensions"], "comment": res["comment"],
                    "missing_points": res["missing_points"],
                    "expect_points": (turn.expected_points or []) if show_answer else [],
                    "progress": {"cur": cur, "total": total}})
                if _should_followup(db, s, turn) and res.get("followup"):
                    _emit_question(db, s, job, turn.outline_index or 0, True, res["followup"])
                else:
                    _advance(db, s, job, turn)
        job.status = "done"
        job.broadcast("done", {"status": s.status})
    except _Fatal as e:
        db.rollback()
        job.status = "failed"
        job.error = str(e)
        job.broadcast("error", {"message": str(e)})
    except BaseException as e:      # noqa: BLE001
        db.rollback()
        msg = str(getattr(e, "detail", None) or e or "评分失败")[:200]
        job.status = "failed"
        job.error = msg
        job.broadcast("error", {"message": msg})
    finally:
        job.finished_at = time.time()
        db.close()


def _broadcast_question(job: _DrillJob, s: DrillSession, turn: DrillTurn) -> None:
    job.stage = "question"
    total = len(s.outline or [])
    job.broadcast("question", {
        "turn_id": turn.id, "seq": turn.seq, "outline_index": turn.outline_index,
        "is_followup": bool(turn.is_followup), "question": turn.question,
        # 实际难度：前端拿它与 outline[i].difficulty 比对 → 不一致才显示「已加难/已降难」。
        # 追问沿用母题难度，所以同考点不会出现两个不同的档位。
        "difficulty": turn.difficulty or "",
        "progress": {"cur": (turn.outline_index or 0) + 1, "total": total},
    })


def _adaptive_difficulty(db, s: DrillSession, outline_index: int,
                         graded_rows=None) -> tuple[str, int]:
    """按**前序得分**给本题定难度（C 档 · 难度自适应）。

    规则（口径写死在此，便于解释与复现）：
      - 以题纲里该考点的 `difficulty` 为**基准档**；
      - 取本题之前**已评分且非追问**的轮次，从末尾往前数**连续**同向段：
          · 连续 ≥2 题 ≥80 分 → +1 档；连续 ≥4 题 → +2 档
          · 连续 ≥2 题 ≤50 分 → −1 档；连续 ≥4 题 → −2 档
        （连续段里出现中间分即中断 —— 不会把隔开的两次高分「攒」成一次升档）
      - 结果钳在 easy..hard；偏移量钳在 ±ADAPT_MAX_SHIFT。

    ⚠️ **无状态**：每次出题时由已有轮次**重算**，不另存 streak 计数器。
        这样断点续跑 / 服务重启 / 重放历史都能得到同一结果，不会因丢内存状态而漂移。
    ⚠️ 未评分的轮次（跳过 / 评分失败）score 为 NULL，天然不参与连续段 —— 不惩罚也不奖励。

    返回 (实际难度, 相对基准档的偏移)；偏移 0 表示未调整。

    参数 `graded_rows`：调用方若已持有本场轮次列表，传进来可**省掉一次查询**；
    须与内部 SQL 同口径（非追问 + 已评分 + outline_index 非空且 < 本题下标）。
    不传则内部自查 —— 保持本函数可独立调用/可测。
    """
    outline = s.outline or []
    base = "medium"
    if 0 <= outline_index < len(outline):
        base = norm_difficulty((outline[outline_index] or {}).get("difficulty"))
    base_level = DIFFICULTY_LEVELS.index(base)

    # 关掉自适应 → 完全等于旧行为（出题难度 = 题纲静态难度）
    if (s.config or {}).get("adaptive", True) is False:
        return base, 0

    up_score, down_score, streak, max_shift = adapt_params()

    rows = graded_rows
    if rows is None:
        rows = (db.query(DrillTurn)
                .filter(DrillTurn.session_id == s.id,
                        DrillTurn.is_followup.is_(False),
                        DrillTurn.outline_index < outline_index,
                        DrillTurn.score.isnot(None))
                .order_by(DrillTurn.seq).all())
    up = down = 0
    for sc in reversed([r.score for r in rows]):
        if sc >= up_score:
            if down:
                break
            up += 1
        elif sc <= down_score:
            if up:
                break
            down += 1
        else:
            break

    shift = 0
    if up >= streak * 2:
        shift = 2
    elif up >= streak:
        shift = 1
    elif down >= streak * 2:
        shift = -2
    elif down >= streak:
        shift = -1
    shift = max(-max_shift, min(max_shift, shift))

    used_level = max(0, min(len(DIFFICULTY_LEVELS) - 1, base_level + shift))
    return DIFFICULTY_LEVELS[used_level], used_level - base_level


def _parent_difficulty(db, s: DrillSession, outline_index: int) -> str | None:
    """追问沿用**母题**难度（取该考点上一条非追问轮次的 difficulty）。"""
    row = (db.query(DrillTurn)
           .filter(DrillTurn.session_id == s.id,
                   DrillTurn.outline_index == outline_index,
                   DrillTurn.is_followup.is_(False))
           .order_by(DrillTurn.seq.desc()).first())
    return (row.difficulty or None) if row else None


def _emit_question(db, s: DrillSession, job: _DrillJob, outline_index: int,
                   is_followup: bool = False, followup: dict | None = None) -> DrillTurn:
    """出题（含追问）：落一行 drill_turn 并广播 question 事件。

    ⚠️ 出题失败**绝不作废整场**（PRD 3.4.7）：重试 1 次（在 llm 层）后仍失败 →
    该考点降级为「跳过」，落在 _skip_failed_point，然后推进到下一考点。
    """
    job.stage = "question"
    job.broadcast("stage", {"stage": "question", "text": "正在出题…"})

    outline = s.outline or []
    item = outline[outline_index] if 0 <= outline_index < len(outline) else {}
    # ⚠️ 一次取全量轮次，供 seq 计算与难度自适应**共用** —— 原先是「count() 取
    #    max_seq」+「_adaptive_difficulty 内部再查一次已评分轮次」两个独立查询，
    #    每题多打一次库。行数 == count()，行为不变。
    sess_turns = (db.query(DrillTurn).filter(DrillTurn.session_id == s.id)
                  .order_by(DrillTurn.seq).all())
    if is_followup and followup:
        # 追问的题干来自评分结果（不另调 LLM）→ 此分支不会失败。
        # 难度**沿用母题**：追问是同一考点的深挖；此刻重算自适应会因为「母题刚被评分、
        # 连续段变了」而给出与母题不同的档位，同一考点自相矛盾。
        q = followup["question"]
        pts = list(followup.get("expected_points") or [])
        used_diff = norm_difficulty(_parent_difficulty(db, s, outline_index)
                                    or item.get("difficulty"))
    else:
        try:
            ctx, _ = build_outline_input(db, s.scope, s.scope_refs or [])
            # 复用上面已取的轮次；过滤口径与 _adaptive_difficulty 内部 SQL 一致
            # （含「outline_index 为 NULL 被排除」—— NULL < x 在 SQL 里为假）。
            graded = [r for r in sess_turns
                      if (not r.is_followup) and r.score is not None
                      and r.outline_index is not None
                      and r.outline_index < outline_index]
            used_diff, _shift = _adaptive_difficulty(db, s, outline_index,
                                                     graded_rows=graded)
            obj = llm.generate_drill_question(ctx, item.get("point") or "综合理解",
                                              hint=item.get("hint") or "",
                                              difficulty=used_diff)
            q = obj["question"]
            pts = list(obj["expected_points"])
        except Exception as e:      # noqa: BLE001  含 HTTPException
            return _skip_failed_point(db, s, job, outline_index, item, e)

    src_title = str(item.get("source") or "").strip()
    src_mid = _match_material_id(db, src_title) if src_title else None
    if src_mid is None and s.scope == "material" and len(s.scope_refs or []) == 1:
        r = (s.scope_refs or [])[0]
        if r.get("type") == "material" and r.get("id") is not None:
            src_mid = int(r["id"])
            src_title = src_title or (r.get("title") or "")

    # seq = 已有轮次数 + 1（与旧 count() 口径一致，只是复用上面已取的列表）
    turn = DrillTurn(session_id=s.id, seq=len(sess_turns) + 1, outline_index=outline_index,
                     is_followup=bool(is_followup), question=q,
                     expected_points=pts, source_material_id=src_mid, source_title=src_title,
                     difficulty=used_diff)
    db.add(turn)
    s.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(turn)
    _broadcast_question(job, s, turn)
    return turn


def _skip_failed_point(db, s: DrillSession, job: _DrillJob, outline_index: int,
                       item: dict, err: Exception) -> DrillTurn:
    """出题失败降级（PRD 3.4.7）：落一条「出题失败」轮次，然后推进到下一考点。

    ⚠️ **不要删掉这个考点**：结果页的分母是题纲数，凭空少一题会让「共完成 N / M 题」
    自相矛盾。落成一条 user_answer=""（跳过语义）的卡片，用户能看到发生了什么。
    ⚠️ 发 **score** 而不是 **question**：前者表示「这一轮结束了」，后者会让前端出现
    一个假的待作答题（用户能对着一个失败的问题打字）。
    """
    max_seq = (db.query(DrillTurn).filter(DrillTurn.session_id == s.id).count()) or 0
    turn = DrillTurn(session_id=s.id, seq=max_seq + 1, outline_index=outline_index,
                     is_followup=False, question="（本题出题失败，已跳过）",
                     expected_points=[], source_material_id=None,
                     source_title=str(item.get("source") or ""))
    turn.user_answer = ""           # "" == 跳过（NULL == 未作答）
    turn.score = None
    turn.verdict = None
    turn.dimensions = None
    turn.comment = "本题出题失败，已跳过"
    turn.missing_points = []
    db.add(turn)
    s.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(turn)

    total = len(s.outline or [])
    job.broadcast("stage", {"stage": "question", "text": "本题出题失败，已跳过"})
    job.broadcast("score", {
        "seq": turn.seq, "turn_id": turn.id, "outline_index": outline_index,
        "question": turn.question, "skipped": True, "failed": True,
        "score": None, "verdict": None, "comment": turn.comment,
        "progress": {"cur": outline_index + 1, "total": total}})
    _advance(db, s, job, turn)
    return turn


def _should_followup(db, s: DrillSession, turn: DrillTurn) -> bool:
    """是否追问同一考点（PRD 3.2.5 / 3.6.3）"""
    policy = (s.config or {}).get("follow_up") or FOLLOW_UP_SHALLOW
    if policy == FOLLOW_UP_NEVER:
        return False
    if turn.is_followup:
        return False        # 同一考点最多追问 1 次
    existed = (db.query(DrillTurn)
               .filter(DrillTurn.session_id == s.id,
                       DrillTurn.outline_index == turn.outline_index,
                       DrillTurn.is_followup.is_(True)).first())
    if existed:
        return False
    if policy == FOLLOW_UP_ALWAYS:
        return True
    return turn.verdict in ("partial", "wrong")


def _advance(db, s: DrillSession, job: _DrillJob, turn: DrillTurn) -> None:
    """推进到下一考点；已无剩余考点 → 结算。"""
    nxt = (turn.outline_index or 0) + 1
    total = len(s.outline or [])
    if nxt < total:
        s.current_seq = nxt + 1
        s.updated_at = datetime.utcnow()
        db.commit()
        _emit_question(db, s, job, nxt, False)
    else:
        summary = _finalize(db, s, "complete")
        job.broadcast("finish", summary)


# ---------- 结算与错题回写（PRD 3.7 / 3.8） ----------

def write_back(db, s: DrillSession, weak_turns: list[DrillTurn]) -> int:
    """错题 → 复习卡（同事务；调用方负责 commit）。

    ⚠️⚠️ source 必须为 "drill"：复用 "quiz" 会与「测一测 / 简单学」互删，
    连 level/ease/review_count 复习进度一起清空且不可恢复（PRD 附录 A #1）。
    ⚠️ material_id 无法确定时写哨兵值 -1（不得写 0，0 已被「AI 问答」筛选占用）。
    """
    cards = []
    for t in weak_turns:
        mid = t.source_material_id if t.source_material_id is not None else SENTINEL_MATERIAL_ID
        title = ("陪练 · " + (t.source_title or s.title or "")).strip()
        answer_text = "\n".join(f"- {p}" for p in (t.expected_points or []))
        cards.append(ReviewCard(
            note_id=None, material_id=mid, material_title=title[:255],
            question=t.question, answer=answer_text or "（本题未产出标准要点）",
            type="qa", source="drill", level=0, ease=2.5,
            next_review_at=datetime.utcnow()))
    if cards:
        db.add_all(cards)
    s.written_back_at = datetime.utcnow()
    return len(cards)


def _finalize(db, s: DrillSession, reason: str = "complete") -> dict:
    """结算：总分 + 薄弱点 + 错题回写（幂等）。调用方负责 commit 前的一致性。"""
    turns = (db.query(DrillTurn).filter(DrillTurn.session_id == s.id)
             .order_by(DrillTurn.seq).all())
    graded = [t for t in turns if t.score is not None]
    scores = [t.score for t in graded]
    s.total_score = round(sum(scores) / len(scores), 1) if scores else None
    s.status = "finished"
    if reason == "abort":
        s.error = ""
    s.updated_at = datetime.utcnow()

    weak = [t for t in turns if t.verdict in ("partial", "wrong")]
    written = 0
    if not s.written_back_at:
        written = write_back(db, s, weak)
    db.commit()

    answered = len([t for t in turns if t.user_answer is not None])
    return {
        "session_id": s.id, "status": "finished", "reason": reason,
        "total_score": s.total_score,
        "graded_count": len(graded),
        "answered_count": answered,
        "outline_count": len(s.outline or []),
        "weak_count": len(weak),
        "written_count": written,
        "written_back_at": s.written_back_at.isoformat() if s.written_back_at else None,
    }


def finalize_session(db, sid: int, reason: str = "abort") -> dict:
    """供路由调用：显式结束（提前结束）或补结算"""
    s = db.get(DrillSession, sid)
    if not s:
        raise HTTPException(404, "陪练记录不存在或已删除")
    if s.status == "finished":
        turns = db.query(DrillTurn).filter(DrillTurn.session_id == sid).all()
        return {
            "session_id": s.id, "status": "finished", "reason": "already",
            "total_score": s.total_score,
            "graded_count": len([t for t in turns if t.score is not None]),
            "answered_count": len([t for t in turns if t.user_answer is not None]),
            "outline_count": len(s.outline or []),
            "weak_count": len([t for t in turns if t.verdict in ("partial", "wrong")]),
            "written_count": 0,
            "written_back_at": s.written_back_at.isoformat() if s.written_back_at else None,
        }
    return _finalize(db, s, reason)


# ---------- 跨场次趋势（C 档优化） ----------

TREND_MAX_SESSIONS = 20           # 趋势只看最近 N 场（太长会被早期噪声稀释）
TREND_TOP_WEAK = 8                # 薄弱要点条数上限

# 漏点归一的字符/词表 —— LLM 每次措辞都不同（「未提到成本」vs「没考虑成本」），
# 精确字符串聚合会把同一个短板拆成几十个「只出现 1 次」的碎片，top 列表就废了。
_POINT_DROP_CHARS = "，。；、,.;:：！!？?（）()【】[]「」「」\"'　 ·-—"
_POINT_LEAD = (
    "未充分", "未提及", "未提到", "未涉及", "未覆盖", "未考虑", "未说明", "未回答",
    "没有提到", "没有提及", "没有涉及", "没有考虑", "没有说明", "没有覆盖",
    "没提到", "没提及", "没涉及", "没考虑", "没说明", "没覆盖",
    "缺乏", "缺少", "缺失", "遗漏", "忽略", "漏掉", "漏了", "不够", "未", "没", "不",
)
_POINT_VERB = ("提到", "提及", "涉及", "考虑", "覆盖", "说明", "回答", "阐述", "点出",
               "给出", "包括", "包含", "强调", "明确")
# 剥掉动词后残留的助词/补语（「忽略了成本」→「了成本」还需再剥一层）
_POINT_SKIP = ("了", "掉", "的", "地", "得")
_POINT_TAIL = ("维度", "方面", "部分", "内容", "这一点")


def point_key(text) -> str:
    """漏点 → 归一化 key（跨场次聚合的分组依据）。

    「未提到成本维度」/「没考虑成本」→ 同一个 key「成本」。
    ⚠️ 前端 DrillView.vue 里有一份**同口径**的 pointKey()（用于单场报告），
       两者必须一致 —— `_probe_drill_c_20261008.py` 用同一组样例跑两边做 parity 断言。
    """
    s = str(text if text is not None else "").strip().lower()
    for ch in _POINT_DROP_CHARS:
        s = s.replace(ch, "")
    # 反复剥「否定前缀 + 动词 + 尾巴」，直到不动（如 未 → 提到 → 维度）
    for _ in range(4):
        before = s
        for sk in _POINT_SKIP:
            if s.startswith(sk) and len(s) > len(sk):
                s = s[len(sk):]
                break
        for pre in _POINT_LEAD:
            if s.startswith(pre) and len(s) > len(pre):
                s = s[len(pre):]
                break
        for v in _POINT_VERB:
            if s.startswith(v) and len(s) > len(v):
                s = s[len(v):]
                break
        for t in _POINT_TAIL:
            if s.endswith(t) and len(s) > len(t):
                s = s[:-len(t)]
                break
        if s == before:
            break
    return s or str(text if text is not None else "").strip().lower()


def session_trend(db, limit: int = TREND_MAX_SESSIONS) -> dict:
    """跨场次趋势：把已落库的漏点按「归一化要点」跨场聚合，看短板是否在收敛。

    ⚠️ **零新增存储**：漏点本就是逐题落库的，报告不该只活在前端 —— 这里只是换个
       聚合粒度重算一遍，不引入新表/新列，因此也没有迁移成本。
    近半 / 前半 按**场次时间**对半切：delta = 近半次数 − 前半次数（负数 = 在收敛）。
    """
    limit = max(1, min(50, int(limit or TREND_MAX_SESSIONS)))
    rows = (db.query(DrillSession)
            .filter(DrillSession.status == "finished")
            .order_by(DrillSession.created_at.desc())
            .limit(limit).all())
    rows = list(reversed(rows))        # 时间正序，前端从左到右画
    sessions = []
    for s in rows:
        stamp = s.created_at or s.updated_at
        sessions.append({"id": s.id, "title": s.title,
                         "date": stamp.isoformat() if stamp else "",
                         "score": s.total_score})
    if len(rows) < 2:
        return {"n_sessions": len(rows), "sessions": sessions, "weak": [], "split": 0}

    half = len(rows) // 2
    recent_ids = {s.id for s in rows[half:]}
    all_ids = {s.id for s in rows}
    turns = db.query(DrillTurn).filter(DrillTurn.session_id.in_(all_ids)).all()

    agg: dict = {}
    for t in turns:
        for p in (t.missing_points or []):
            raw = str(p).strip()
            if not raw:
                continue
            k = point_key(raw)
            rec = agg.get(k)
            if rec is None:
                rec = agg[k] = {"text": raw, "recent": 0, "earlier": 0, "_len": len(raw)}
            elif len(raw) < rec["_len"]:      # 展示最短的原始写法（通常最凝练）
                rec["text"] = raw
                rec["_len"] = len(raw)
            if t.session_id in recent_ids:
                rec["recent"] += 1
            else:
                rec["earlier"] += 1
    weak = [{"text": v["text"], "recent": v["recent"], "earlier": v["earlier"],
             "total": v["recent"] + v["earlier"], "delta": v["recent"] - v["earlier"]}
            for v in agg.values()]
    weak.sort(key=lambda x: (-x["total"], x["text"]))
    return {"n_sessions": len(rows), "sessions": sessions,
            "weak": weak[:TREND_TOP_WEAK], "split": half}
