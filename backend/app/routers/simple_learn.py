"""简单学习路由（V 新增）：选材料 → 一键生成「结构化学习笔记 + 出题入复习队列」

定位：把已有的「解析 → 笔记 → 复习」能力，打成一个叫「简单学习」的一键入口。
一期文档材料全套：结构化笔记（learn_note）落 AIAsset，题目（source="quiz"）入 ReviewCard。

生成是**服务端后台任务**（见 _LearnJob）：
- LLM 生成笔记 + 出题要几十秒到几分钟，不能卡在一次同步 HTTP 请求里；
- 用户离开页面，任务线程**自己开 DB 会话继续跑完、落库照写**（与连接是否还在无关）；
- 回到页面重新调用 stream 接口即可「附着」到正在跑的任务上继续看进度。

⚠️ 与 /review/quiz 的关系：这里不经过 review 的 HTTP 接口，直接复用
llm.generate_quiz + 同一套 ReviewCard 字段口径建卡（source="quiz"，重删旧卡）。
"""
import json
import queue
import threading
import time
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db, SessionLocal
from ..models import Material, MaterialChunk, AIAsset, ReviewCard
from ..services import llm

router = APIRouter(prefix="/simple-learn", tags=["simple-learn"])

DEFAULT_QUIZ_COUNT = 8
HEARTBEAT_SEC = 10.0
JOB_TTL_SEC = 600          # 已结束任务保留 10 分钟，够「离开又回来」取到结果
KEEP_NOTE_VERSIONS = 3     # 讲义只保留最近 N 版（每版约 7k 字，无限累积会持续吃库）
KEEP_CH_VERSIONS = 2    # 分章讲义每章只留最近 2 版（章数多，版本控制要比整篇更紧）
MAX_CONCURRENT_JOBS = 3    # 全局并发闸：同时「生成中」的任务数上限
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


class RunReq(BaseModel):
    material_id: int
    quiz_count: int | None = None


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


# ---------- 工具 ----------

def _get_material_or_404(db: Session, material_id: int) -> Material:
    m = db.get(Material, material_id)
    if not m:
        raise HTTPException(404, "材料不存在")
    if m.parsed_status != "success":
        raise HTTPException(400, f"材料状态为 {m.parsed_status}，简单学习暂不可用")
    return m


def _load_chunks(db: Session, material_id: int) -> list[dict]:
    rows = (db.query(MaterialChunk)
            .filter(MaterialChunk.material_id == material_id)
            .order_by(MaterialChunk.page_no, MaterialChunk.id).all())
    return [{"content": r.content, "page_no": r.page_no, "section_path": r.section_path} for r in rows]


def _latest_note(db: Session, material_id: int) -> AIAsset | None:
    return (db.query(AIAsset)
            .filter(AIAsset.material_id == material_id, AIAsset.type == "learn_note")
            .order_by(AIAsset.version.desc()).first())


def _prune_note_versions(db: Session, material_id: int, keep: int = KEEP_NOTE_VERSIONS) -> int:
    """只保留最近 keep 版 learn_note，返回删除条数（调用方负责 commit）。

    ⚠️ 每次「重新生成」都新增一行 AIAsset，旧版永不清理 → 反复重生成会持续吃库。
    保留 3 版：够「和上一版对照」，又不至于无限涨。
    ⚠️ AIAsset 没有关联文件（正文就在 content 列），所以只删 DB 行即可。
    ⚠️⚠️ 必须先 flush()：调用方都是刚 db.add(新讲义) 还没 commit，
        而 SessionLocal 是 **autoflush=False** → 不 flush 时下面的查询**看不到刚加的那版**
        → 裁剪永远慢一拍，表稳定在 keep+1 版而**不是** keep 版
        （实测：连跑 5 次后留 4 版 [2,3,4,5]，预期 3 版 [3,4,5]）。
    """
    db.flush()
    rows = (db.query(AIAsset)
            .filter(AIAsset.material_id == material_id, AIAsset.type == "learn_note")
            .order_by(AIAsset.version.desc()).all())
    for a in rows[keep:]:
        db.delete(a)
    return max(0, len(rows) - keep)


# ---------- 分章讲义（长材料按章生成；每章一条 AIAsset） ----------

def _chapter_notes(db: Session, material_id: int) -> dict:
    """每章最新一版讲义：{章序号(int): AIAsset}（type=learn_note_ch，anchor.ch 为章序号）。"""
    rows = (db.query(AIAsset)
            .filter(AIAsset.material_id == material_id, AIAsset.type == "learn_note_ch")
            .order_by(AIAsset.version.desc()).all())
    latest = {}
    for a in rows:
        ch = (a.anchor or {}).get("ch")
        if isinstance(ch, int) and ch not in latest:
            latest[ch] = a
    return latest


def _prune_chapter_versions(db: Session, material_id: int, ch: int,
                            keep: int = KEEP_CH_VERSIONS) -> int:
    """只留某章最近 keep 版（同 _prune_note_versions：先 flush 否则查询看不到新版）。"""
    db.flush()
    rows = [a for a in (db.query(AIAsset)
                        .filter(AIAsset.material_id == material_id,
                                AIAsset.type == "learn_note_ch")
                        .order_by(AIAsset.version.desc()).all())
            if (a.anchor or {}).get("ch") == ch]
    for a in rows[keep:]:
        db.delete(a)
    return max(0, len(rows) - keep)


def _chapter_manifest(db: Session, material_id: int, chunks: list[dict]) -> dict:
    """章节清单：每章的生成状态 + 默认自动生成的章数（前端章条的数据源）。

    structured=False（无章节结构）时 chapters 为空，前端走整篇 UI（不显示章条）。
    auto_count ≥ 1：第一章自身就超限时，整跑也至少生成第一章（章内截断兜底）。
    """
    chapters = llm.split_chapters(chunks)
    notes = _chapter_notes(db, material_id)
    items = []
    for i, ch in enumerate(chapters):
        n = notes.get(i)
        items.append({"i": i, "name": ch["name"], "chars": ch["chars"],
                      "generated": n is not None, "version": (n.version if n else 0)})
    auto = llm.chapter_prefix_count(chapters, llm.LEARN_NOTE_MAX_CHARS) if chapters else 0
    return {"structured": bool(chapters), "chapters": items,
            "auto_count": max(auto, 1) if chapters else 0,
            "total_chars": len(llm.format_chunks(chunks))}


def _assembled_note(db: Session, material_id: int, chunks: list[dict]) -> dict | None:
    """把各章讲义拼成「一整篇」视图（/note 读回、转笔记、前端展示共用）。

    version = 各章 version 之和：任一章新生成/重生成都会让它变大
    → 前端「讲义已更新 · 更新笔记」按 version 比对照常工作（⚠️ 只比 version，不比正文）。
    无章节讲义时返回 None（调用方退回整篇 learn_note）。
    """
    notes = _chapter_notes(db, material_id)
    if not notes:
        return None
    manifest = _chapter_manifest(db, material_id, chunks)
    items = manifest["chapters"]
    missing = [c["name"] for c in items if not c["generated"]]
    if missing:
        shown = "、".join(f"《{n}》" for n in missing[:6])
        tail = f" 等 {len(missing)} 章" if len(missing) > 6 else ""
        head = (f"> 📖 **分章讲义**：本材料共约 {manifest['total_chars']:,} 字、"
                f"{len(items)} 章，已生成 {len(notes)}/{len(items)} 章；"
                f"未生成：{shown}{tail}（可在本页下方逐章补生成）。")
    else:
        head = (f"> 📖 **分章讲义**：本材料共约 {manifest['total_chars']:,} 字、"
                f"{len(items)} 章，已全部生成。")
    parts = [head] + [notes[i].content for i in sorted(notes)]
    newest = max(notes.values(), key=lambda a: a.created_at or datetime.min)
    return {"id": newest.id, "material_id": material_id, "type": "learn_note",
            "content": "\n\n".join(parts),
            "version": sum((n.version or 1) for n in notes.values()),
            "created_at": newest.created_at.isoformat() if newest.created_at else None}


def _gen_one_chapter(db: Session, mid: int, title: str, doc_kind: str,
                     chapters: list[dict], ch_idx: int, job) -> AIAsset:
    """生成一章并落库（整跑分章模式与单章补生成共用）。token 逐段广播给 SSE 订阅者。"""
    parts: list[str] = []
    for piece in llm.generate_learn_note_chapter_stream(
            chapters[ch_idx], title=title, doc_kind=doc_kind,
            chapter_no=ch_idx + 1, chapter_total=len(chapters)):
        parts.append(piece)
        job.note_buf.append(piece)
        job.broadcast("token", {"t": piece, "chapter": ch_idx})
    prev = _chapter_notes(db, mid).get(ch_idx)
    note = AIAsset(material_id=mid, type="learn_note_ch", content="".join(parts),
                   anchor={"ch": ch_idx, "name": chapters[ch_idx]["name"]},
                   version=(prev.version + 1) if prev else 1)
    db.add(note)
    _prune_chapter_versions(db, mid, ch_idx)
    db.commit()
    db.refresh(note)
    return note


def _quiz_source_text(db: Session, material_id: int, chunks: list[dict]) -> str:
    """出题的输入文本：**讲义优先**，没有讲义才退回材料原文块。

    ⚠️ 为什么不能用材料原文：原文动辄几十万字，而 llm.generate_quiz 内部只取前 6000 字
    → 题只覆盖原文的几个百分点。实测材料 41：讲义覆盖 52%、出题仅 13% ⇒ **题与讲义对不上**
    （用户读完讲义去做题，题目却来自另一段内容）。
    讲义本身是全文压缩（几千字量级），拿它出题 ⇒ 出题覆盖面与讲义严格一致。
    ⚠️ 分章讲义（learn_note_ch）优先于整篇讲义：长材料的讲义本来就按章存。
    """
    ch_notes = _chapter_notes(db, material_id)
    if ch_notes:
        return "\n\n".join(n.content for _, n in sorted(ch_notes.items()))
    a = _latest_note(db, material_id)
    if a and (a.content or "").strip():
        return a.content
    return "\n".join(c["content"] for c in chunks)


def note_to_dict(a: AIAsset) -> dict:
    return {
        "id": a.id, "material_id": a.material_id, "type": a.type,
        "content": a.content, "version": a.version,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


# ---------- 后台任务（与播客合成 _SynthJob 同款模式） ----------

class _LearnJob:
    """一次「简单学习」生成任务的服务端状态。SSE 流只是它的「进度订阅者」。

    LLM 生成笔记是一次性调用（非流式），进度按**阶段**回报（准备 → 生成笔记 → 出题），
    不伪造百分比——阶段粒度诚实反映「进行到哪一步」。
    """

    def __init__(self):
        self.status = "running"      # running / done / failed
        self.stage = "prepare"       # prepare / note / quiz / done
        self.error = ""
        self.note: dict | None = None
        self.quiz: dict | None = None
        self.subs: list = []         # list[queue.Queue]
        self.finished_at: float = 0.0
        self.note_buf: list[str] = []   # 流式讲义已生成的 token 累积（供「中途返回附着」补发）

    def broadcast(self, kind: str, payload: dict) -> None:
        for q in list(self.subs):
            q.put((kind, payload))


_LEARN_JOBS: dict[int, _LearnJob] = {}
_JOBS_LOCK = threading.Lock()


def _purge_jobs() -> None:
    """清理超时任务（调用方须已持有 _JOBS_LOCK）"""
    now = time.time()
    for mid, j in list(_LEARN_JOBS.items()):
        if j.status != "running" and now - j.finished_at > JOB_TTL_SEC:
            _LEARN_JOBS.pop(mid, None)


def _run_learn_job(mid: int, title: str, quiz_count: int, job: _LearnJob) -> None:
    """生成任务线程：LLM 调用 + 落库都在这里，与 SSE 连接解耦。

    ⚠️ 必须用自己开的 DB 会话：请求级会话随响应结束关闭，活不到任务结束。
    """
    db = SessionLocal()
    try:
        # 阶段 1/3：准备材料
        job.stage = "prepare"
        job.broadcast("progress", {"status": "running", "stage": "prepare"})
        chunks = _load_chunks(db, mid)
        # 材料内容性质（P0 工艺分流）：决定讲义走哪套工艺。
        # 空串 = auto（未判定 / 老数据 / 图片·音视频之外未判定）→ 走改造前的通用工艺。
        _m = db.get(Material, mid)
        doc_kind = (_m.doc_kind if _m else "") or "auto"
        if not chunks:
            job.status = "failed"
            job.error = "材料没有可学习的内容，请先完成解析"
            job.broadcast("error", {"message": job.error})
            return

        # 阶段 2/3：生成结构化笔记（流式边生成边广播 token）
        job.stage = "note"
        # 分章模式：有章节结构且整篇超限 → 逐章生成（每章完整、单独落库），
        # 默认只生成「整章纳入也不超限」的前缀章，其余章由用户在学习页逐章补生成。
        # 无章节结构 / 不超限 → 整篇单次生成（行为与分章化之前完全一致）。
        chapters = llm.split_chapters(chunks)
        if chapters and llm.learn_note_coverage(chunks)["truncated"]:
            auto_n = max(llm.chapter_prefix_count(chapters, llm.LEARN_NOTE_MAX_CHARS), 1)
            for ci in range(auto_n):
                job.broadcast("progress", {"status": "running", "stage": "note",
                                           "chapter": ci, "chapter_total": auto_n,
                                           "chapter_name": chapters[ci]["name"]})
                if ci:
                    # 章间分隔：前端流式累积的拼接顺序与落库拼装视图一致
                    job.note_buf.append("\n\n")
                    job.broadcast("token", {"t": "\n\n", "chapter": ci})
                _gen_one_chapter(db, mid, title, doc_kind, chapters, ci, job)
            job.note = _assembled_note(db, mid, chunks)
            job.broadcast("progress", {"status": "running", "stage": "note", "note": job.note})
        else:
            job.broadcast("progress", {"status": "running", "stage": "note"})
            # 逐 token 广播：前端据此实时渲染「正在打字」的讲义，不必等全文生成完。
            # 全文累积到 content，落库口径与旧非流式版本一致（截断提示块已由生成器垫在最前）。
            content_parts: list[str] = []
            for piece in llm.generate_learn_note_stream(chunks, title=title, doc_kind=doc_kind):
                content_parts.append(piece)
                job.note_buf.append(piece)
                job.broadcast("token", {"t": piece})
            content = "".join(content_parts)
            prev = _latest_note(db, mid)
            note = AIAsset(material_id=mid, type="learn_note", content=content,
                           version=(prev.version + 1) if prev else 1)
            db.add(note)
            # 同事务裁掉第 4 版及更早的（见 _prune_note_versions：旧版永不清理会持续吃库）
            _prune_note_versions(db, mid)
            db.commit()
            db.refresh(note)
            job.note = note_to_dict(note)
            job.broadcast("progress", {"status": "running", "stage": "note", "note": job.note})

        # 阶段 3/3：出题入队（失败降级，不连带笔记失败）
        job.stage = "quiz"
        job.broadcast("progress", {"status": "running", "stage": "quiz"})
        # 出题输入走「讲义优先」：用刚生成的讲义，而不是材料原文前 6000 字
        # （原文只喂一小段会让题目与讲义覆盖面脱节，见 _quiz_source_text）
        quiz_text = _quiz_source_text(db, mid, chunks)
        quiz = {"created": 0, "cards": []}
        if quiz_text.strip():
            try:
                qas = llm.generate_quiz(title, quiz_text, quiz_count, kind="learn_note")
                # ⚠️⚠️ 拿到**合格题目之后**才删，且删除与插入在**同一事务**中：
                #     旧写法「先 delete 再 for 插入」在 qas 为空时会静默清空该材料全部题；
                #     复习进度（level/ease/review_count/history）存在卡行内、无独立历史表 → 删了不可恢复。
                db.query(ReviewCard).filter(
                    ReviewCard.material_id == mid, ReviewCard.source == "quiz").delete()
                from .review import card_to_dict
                cards = [
                    ReviewCard(
                        note_id=None, material_id=mid, material_title=title,
                        question=qa["question"], answer=qa["explanation"],
                        type="choice", options=qa["options"], correct_index=qa["correct_index"],
                        source="quiz", level=0, next_review_at=datetime.utcnow(),
                    )
                    for qa in qas
                ]
                db.add_all(cards)
                db.commit()
                created = []
                for card in cards:
                    db.refresh(card)
                    created.append(card_to_dict(card))
                quiz = {"created": len(created), "cards": created}
            except Exception as e:      # noqa: BLE001
                # ⚠️ 出题失败**降级**，不把整个任务判 failed：
                #     讲义此时已生成并落库，判死会让用户点「重试」时白跑一遍讲义
                #     （多花一次 token，讲义 version 还会白涨）。
                #     只记 quiz.error，前端显示「讲义已生成，但出题失败」+「重试出题」。
                db.rollback()
                quiz = {"created": 0, "cards": [],
                        "error": str(getattr(e, "detail", None) or e or "出题失败")[:200]}
                job.broadcast("progress", {"status": "running", "stage": "quiz", "quiz": quiz})
        job.quiz = quiz

        job.status = "done"
        job.stage = "done"
        job.broadcast("done", {"note": job.note, "quiz": job.quiz})
    except BaseException as e:          # noqa: BLE001
        db.rollback()
        job.status = "failed"
        job.error = str(e)[:200]
        job.broadcast("error", {"message": str(e)[:200]})
    finally:
        job.finished_at = time.time()
        db.close()


def _any_job_running(mid: int) -> bool:
    """该材料是否有「整跑」或「任一章」任务在跑（启动新任务前的互斥检查）。

    ⚠️ 调用方须已持有 _JOBS_LOCK。键有两种：整跑=mid(int)，单章=(mid, ch_idx)。
    """
    for k, j in _LEARN_JOBS.items():
        if j.status != "running":
            continue
        if k == mid or (isinstance(k, tuple) and k[0] == mid):
            return True
    return False


def _start_or_attach_job(mid: int, title: str, quiz_count: int) -> _LearnJob:
    """启动生成任务；若该材料已有任务在跑 → 直接返回它（调用方附着看进度）。"""
    with _JOBS_LOCK:
        _purge_jobs()
        job = _LEARN_JOBS.get(mid)
        if job and job.status == "running":
            return job
        if _any_job_running(mid):
            raise HTTPException(409, "该材料有章节讲义正在生成中，请稍候再试")
        # 全局并发闸：不限流时可以对 N 份材料同时点生成，起 N 个线程并发打 LLM，
        # 还会连带占满连接池。超限直接 429（用户可等一个跑完再点）。
        running = sum(1 for j in _LEARN_JOBS.values() if j.status == "running")
        if running >= MAX_CONCURRENT_JOBS:
            raise HTTPException(
                429, f"同时生成的任务过多（上限 {MAX_CONCURRENT_JOBS} 个），请等一个完成后再试")
        job = _LearnJob()
        _LEARN_JOBS[mid] = job
    threading.Thread(target=_run_learn_job, args=(mid, title, quiz_count, job),
                     daemon=True).start()
    return job


# ---------- 接口 ----------

@router.post("/run/stream")
def run_stream(req: RunReq):
    """SSE 流式一键生成：`progress`（阶段） → `done` | `error`。

    本接口是「启动或附着」：生成是服务端任务（见 _LearnJob），
    **用户离开页面任务照跑、落库照写**；回到页面重新调用即附着到进行中的任务看进度。
    """
    # ⚠️ 这里**不能**写 db: Session = Depends(get_db)：FastAPI 的请求级会话要等
    #    **响应结束后**才释放，而本接口返回的是一条可能持续几分钟的 SSE 流
    #    → 整整一条池连接被占住（池 size=5 + overflow 10）。
    #    改成自己开一个**短会话**只做校验，校验完立刻还回去。
    db = SessionLocal()
    try:
        m = _get_material_or_404(db, req.material_id)
        title = m.title
    finally:
        db.close()
    quiz_count = req.quiz_count or DEFAULT_QUIZ_COUNT
    job = _start_or_attach_job(req.material_id, title, quiz_count)
    return StreamingResponse(_attach_stream(job), media_type="text/event-stream",
                             headers=SSE_HEADERS)


def _attach_stream(job: _LearnJob):
    """SSE 附着生成器：补状态快照 → 补发已生成 token → 订阅广播（整跑/单章共用）。

    ⚠️ 刻意保持**同步 def**（与 simple_learn 既有 SSE 一致）：体內只做队列转发，
       真正的阻塞 LLM/DB 都在任务线程里；改 async 反而会把队列等待搬上事件循环。
    """
    q: queue.Queue = queue.Queue()
    with _JOBS_LOCK:
        job.subs.append(q)
    try:
        # 附着瞬间补一份当前状态快照：中途回来能看到「进行到哪一步」而非干等。
        if job.status == "done":
            yield _sse("done", {"note": job.note, "quiz": job.quiz})
            return
        if job.status == "failed":
            yield _sse("error", {"message": job.error})
            return
        yield _sse("progress", {"status": "running", "stage": job.stage,
                                "note": job.note})
        # 附着瞬间补发「已生成但尚未落库」的讲义 token：中途离开又回来，
        # 前端要把前半段内容也续上（否则只看到回来后生成的另一半）。
        for piece in job.note_buf:
            yield _sse("token", {"t": piece})
        while True:
            try:
                kind, payload = q.get(timeout=HEARTBEAT_SEC)
            except queue.Empty:
                yield ": ping\n\n"
                continue
            if kind == "progress":
                yield _sse("progress", payload)
                continue
            if kind == "token":
                yield _sse("token", payload)
                continue
            if kind == "error":
                yield _sse("error", payload)
                return
            yield _sse("done", payload)
            return
    finally:
        with _JOBS_LOCK:
            if q in job.subs:
                job.subs.remove(q)


def _run_chapter_job(mid: int, ch_idx: int, title: str, job: _LearnJob) -> None:
    """单章补生成任务线程（与 _run_learn_job 同款：自开 DB 会话，与 SSE 连接解耦）。"""
    db = SessionLocal()
    try:
        job.stage = "note"
        job.broadcast("progress", {"status": "running", "stage": "note",
                                   "chapter": ch_idx})
        chunks = _load_chunks(db, mid)
        chapters = llm.split_chapters(chunks)
        if not chapters or not (0 <= ch_idx < len(chapters)):
            job.status = "failed"
            job.error = "章节不存在或材料没有章节结构"
            job.broadcast("error", {"message": job.error})
            return
        _m = db.get(Material, mid)
        doc_kind = (_m.doc_kind if _m else "") or "auto"
        note = _gen_one_chapter(db, mid, title, doc_kind, chapters, ch_idx, job)
        d = note_to_dict(note)
        d["chapter"] = ch_idx
        job.note = d
        job.status = "done"
        job.stage = "done"
        job.broadcast("done", {"note": d, "chapter": ch_idx})
    except BaseException as e:          # noqa: BLE001
        db.rollback()
        job.status = "failed"
        job.error = str(e)[:200]
        job.broadcast("error", {"message": str(e)[:200]})
    finally:
        job.finished_at = time.time()
        db.close()


def _start_chapter_job(mid: int, ch_idx: int, title: str) -> _LearnJob:
    """启动单章补生成；该章已有任务在跑 → 直接返回它（调用方附着看进度）。"""
    with _JOBS_LOCK:
        _purge_jobs()
        key = (mid, ch_idx)
        job = _LEARN_JOBS.get(key)
        if job and job.status == "running":
            return job
        if _any_job_running(mid):
            raise HTTPException(409, "该材料有讲义任务正在生成中，请稍候再试")
        running = sum(1 for j in _LEARN_JOBS.values() if j.status == "running")
        if running >= MAX_CONCURRENT_JOBS:
            raise HTTPException(
                429, f"同时生成的任务过多（上限 {MAX_CONCURRENT_JOBS} 个），请等一个完成后再试")
        job = _LearnJob()
        _LEARN_JOBS[key] = job
    threading.Thread(target=_run_chapter_job, args=(mid, ch_idx, title, job),
                     daemon=True).start()
    return job


class ChapterReq(BaseModel):
    material_id: int
    chapter: int


@router.get("/chapters")
def get_chapters(material_id: int, db: Session = Depends(get_db)):
    """章节清单 + 各章生成状态（前端章条的数据源；无章节结构时 structured=False）。"""
    m = _get_material_or_404(db, material_id)
    return _chapter_manifest(db, m.id, _load_chunks(db, m.id))


@router.post("/chapter/stream")
def chapter_stream(req: ChapterReq):
    """SSE 单章补生成：事件与 /run/stream 相同（progress/token/done/error）。

    ⚠️ 同 run_stream：**不能**挂 Depends(get_db)（SSE 流会占住池连接），
       自开短会话只做校验，校验完立刻还回去。
    """
    db = SessionLocal()
    try:
        m = _get_material_or_404(db, req.material_id)
        title = m.title
    finally:
        db.close()
    job = _start_chapter_job(req.material_id, req.chapter, title)
    return StreamingResponse(_attach_stream(job), media_type="text/event-stream",
                             headers=SSE_HEADERS)


@router.get("/run/status")
def run_status(material_id: int, db: Session = Depends(get_db)):
    """任务状态：前端回到页面时据此决定「附着看进度」还是「直接展示已有产物」。"""
    if not db.get(Material, material_id):
        raise HTTPException(404, "材料不存在")
    with _JOBS_LOCK:
        _purge_jobs()
        job = _LEARN_JOBS.get(material_id)
    if not job:
        return {"status": "idle"}
    d = {"status": job.status, "stage": job.stage, "error": job.error}
    if job.note:
        d["note"] = job.note
    if job.quiz:
        d["quiz"] = job.quiz
    return d


class QuizReq(BaseModel):
    material_id: int
    quiz_count: int | None = None


@router.post("/quiz")
def rerun_quiz(req: QuizReq, db: Session = Depends(get_db)):
    """只重新出题（讲义不动）。

    用途：讲义已生成、但出题失败时的「重试出题」。与 /run/stream 的区别是
    **不重跑 LLM 讲义那一段**（省一次 token，讲义 version 也不会白涨）。
    出题输入口径与 /run/stream 一致（**讲义优先**，无讲义才退回材料原文块），
    题目同样落 source="quiz"。
    """
    m = _get_material_or_404(db, req.material_id)
    with _JOBS_LOCK:
        _purge_jobs()
        j = _LEARN_JOBS.get(req.material_id)
    if j and j.status == "running":
        raise HTTPException(409, "该材料正在生成中，请稍候再试")

    chunks = _load_chunks(db, m.id)
    core = _quiz_source_text(db, m.id, chunks)
    if not core.strip():
        raise HTTPException(400, "材料没有可出题的内容")
    # 出题失败（含「模型未返回可用题目」）直接 500 → 旧题一张不动
    qas = llm.generate_quiz(m.title, core, req.quiz_count or DEFAULT_QUIZ_COUNT,
                            kind="learn_note")
    # ⚠️ 同 /run/stream：拿到合格题目之后才删，且删除与插入同一事务
    db.query(ReviewCard).filter(
        ReviewCard.material_id == m.id, ReviewCard.source == "quiz").delete()
    from .review import card_to_dict
    cards = [
        ReviewCard(
            note_id=None, material_id=m.id, material_title=m.title,
            question=qa["question"], answer=qa["explanation"],
            type="choice", options=qa["options"], correct_index=qa["correct_index"],
            source="quiz", level=0, next_review_at=datetime.utcnow(),
        )
        for qa in qas
    ]
    db.add_all(cards)
    db.commit()
    created = []
    for card in cards:
        db.refresh(card)
        created.append(card_to_dict(card))
    return {"created": len(created), "cards": created}


@router.get("/note")
def get_note(material_id: int, db: Session = Depends(get_db)):
    """读回该材料最新一版学习笔记（分章材料返回拼装视图；无则 404）"""
    if not db.get(Material, material_id):
        raise HTTPException(404, "材料不存在")
    asm = _assembled_note(db, material_id, _load_chunks(db, material_id))
    if asm:
        return asm
    a = _latest_note(db, material_id)
    if not a:
        raise HTTPException(404, "该材料尚未生成学习笔记")
    return note_to_dict(a)
