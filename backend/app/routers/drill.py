"""AI 陪练路由（PRD 模块 J · V1.0）

会话 CRUD + 范围摘要 + 两条 SSE 流（开始/作答）+ 结算。

⚠️ SSE 端点**不写** `db: Session = Depends(get_db)`：请求级会话要等响应结束才释放，
   而这里返回的是可能持续数十秒的流 → 会一直占着池连接（同 simple_learn 的处理）。
   改为自开短会话做校验，校验完立刻还回去；真正的写库在后台任务的独立会话里。
"""
import os
import tempfile

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import SessionLocal, get_db
from ..models import DrillSession, DrillTurn
from ..services import drill as drill_svc

router = APIRouter(prefix="/drill", tags=["drill"])


# ---------- 请求体 ----------

class ScopePreviewReq(BaseModel):
    scope: str = "all"
    scope_refs: list[dict] | None = None


class CreateReq(BaseModel):
    title: str | None = None
    scope: str = "all"
    scope_refs: list[dict] | None = None
    config: dict | None = None


class AnswerReq(BaseModel):
    seq: int
    answer: str = ""
    skip: bool = False


class FinishReq(BaseModel):
    reason: str = "abort"      # complete / abort


class TTSReq(BaseModel):
    text: str = ""
    voice: str | None = None    # 不传则沿用设置的播客主持音色


# ---------- 范围摘要 ----------

@router.post("/scope/preview")
def preview_scope(req: ScopePreviewReq, db: Session = Depends(get_db)):
    """范围内容量估算（PRD 3.1.5）。

    ⚠️ 纯 DB 统计，**不触发任何检索** —— 验收 #3 要求空范围时「无检索调用记录」。
    frontend 用 material_count/note_count/content_chars 判断是否禁用「开始陪练」。
    """
    if req.scope not in drill_svc.VALID_SCOPES:
        raise HTTPException(400, "范围类型不合法")
    return drill_svc.preview_scope(db, req.scope, req.scope_refs or [])


# ---------- 会话 CRUD ----------

@router.post("/sessions")
def create_session(req: CreateReq, db: Session = Depends(get_db)):
    """新建陪练会话（PRD 3.2）。校验：LLM 已配置 + 范围内容非空 + 题量合法。"""
    if not drill_svc.llm_ready():
        raise HTTPException(400, "[NEED_SETUP]请先在「管理中心」配置模型")
    if req.scope not in drill_svc.VALID_SCOPES:
        raise HTTPException(400, "范围类型不合法")
    refs = [r for r in (req.scope_refs or []) if isinstance(r, dict)]
    if req.scope in ("material", "note", "folder") and not refs:
        raise HTTPException(400, "所选范围内没有可陪练的内容，请重新选择")

    # 空范围必须拒绝，严禁退化为全库（PRD 3.1.7）
    text, _sources = drill_svc.build_outline_input(db, req.scope, refs)
    if not text.strip():
        raise HTTPException(400, "所选范围没有可用于出题的内容")

    cfg = dict(req.config or {})
    try:
        count = int(cfg.get("question_count") or drill_svc.DEFAULT_QUESTION_COUNT)
    except (TypeError, ValueError):
        count = drill_svc.DEFAULT_QUESTION_COUNT
    if not (drill_svc.MIN_QUESTION_COUNT <= count <= drill_svc.MAX_QUESTION_COUNT):
        raise HTTPException(400, f"题量须在 {drill_svc.MIN_QUESTION_COUNT}-"
                                 f"{drill_svc.MAX_QUESTION_COUNT} 之间")
    cfg["question_count"] = count
    if cfg.get("follow_up") not in (drill_svc.FOLLOW_UP_SHALLOW, drill_svc.FOLLOW_UP_ALWAYS,
                                    drill_svc.FOLLOW_UP_NEVER):
        cfg["follow_up"] = drill_svc.FOLLOW_UP_SHALLOW
    cfg["show_answer"] = bool(cfg.get("show_answer", True))
    # 难度自适应（C 档）：按前序得分动态升降档。
    # 显式传 false 即完全回到旧行为（出题难度 = 题纲里的静态难度）。
    cfg["adaptive"] = bool(cfg.get("adaptive", True))

    title = (req.title or "").strip()[:50] or drill_svc.default_title(db, req.scope, refs)
    if not title:
        title = "陪练"      # 保证字段非空（PRD 3.2.4）
    s = DrillSession(title=title, scope=req.scope, scope_refs=refs,
                     config=cfg, status="draft")
    db.add(s)
    db.commit()
    db.refresh(s)
    return drill_svc.session_to_dict(s)


@router.get("/sessions")
def list_sessions(q: str | None = None, offset: int = 0, limit: int = 20,
                  db: Session = Depends(get_db)):
    query = db.query(DrillSession)
    if q and q.strip():
        query = query.filter(DrillSession.title.like(f"%{q.strip()}%"))
    total = query.count()
    rows = (query.order_by(DrillSession.updated_at.desc(), DrillSession.id.desc())
            .offset(max(0, offset)).limit(min(100, max(1, limit))).all())
    return {"items": [drill_svc.session_to_dict(s) for s in rows], "total": total}


@router.get("/sessions/{sid}")
def get_session(sid: int, db: Session = Depends(get_db)):
    s = db.get(DrillSession, sid)
    if not s:
        raise HTTPException(404, "陪练记录不存在或已删除")
    return drill_svc.session_to_dict(s, db=db, with_turns=True)


@router.delete("/sessions/{sid}")
def delete_session(sid: int, db: Session = Depends(get_db)):
    """删除会话：轮次级联删除；**已回写的复习卡保留**（PRD 3.9.4）"""
    s = db.get(DrillSession, sid)
    if not s:
        raise HTTPException(404, "陪练记录不存在或已删除")
    db.delete(s)
    db.commit()
    return {"ok": True}


# ---------- SSE：开始 / 作答 ----------

def _sse_response(job):
    return StreamingResponse(drill_svc.event_stream(job), media_type="text/event-stream",
                             headers=drill_svc.SSE_HEADERS)


@router.post("/sessions/{sid}/start/stream")
def start_stream(sid: int):
    """开始（或重试 / 断点续跑）陪练：生成题纲 → 出第 1 题。

    三种进入路径：
    - draft：全新生成（先校验范围内容非空）。
    - failed：**复用同一会话**重试（清掉上次半成品轮次）。
    - running 且内存任务已不在（重启 / 已回收）：**断点续跑**，不重生成题纲、不清作答。
    """
    db = SessionLocal()
    resume = False
    try:
        s = db.get(DrillSession, sid)
        if not s:
            raise HTTPException(404, "陪练记录不存在或已删除")
        if s.status == "finished":
            raise HTTPException(400, "该陪练已结束")
        if not drill_svc.llm_ready():
            raise HTTPException(400, "[NEED_SETUP]请先在「管理中心」配置模型")

        running_job = drill_svc.has_running_job(sid)
        active = s.status in ("outlining", "running")
        if not active and not running_job:
            if drill_svc.count_active(db) >= drill_svc.MAX_CONCURRENT_SESSIONS:
                raise HTTPException(429, "同时进行的陪练过多，请稍后再试")

        if s.status == "running" and not running_job and (s.outline or []):
            resume = True
        elif s.status in ("draft", "failed"):
            text, _src = drill_svc.build_outline_input(db, s.scope, s.scope_refs or [])
            if not text.strip():
                raise HTTPException(400, "所选范围没有可用于出题的内容")
            if s.status == "failed":
                db.query(DrillTurn).filter(DrillTurn.session_id == sid).delete()
                s.written_back_at = None
                s.total_score = None
                s.error = ""
                db.commit()
    finally:
        db.close()
    job = drill_svc.start_outline_job(sid, resume=resume)
    return _sse_response(job)


@router.post("/sessions/{sid}/answer/stream")
def answer_stream(sid: int, req: AnswerReq):
    """提交作答 → 评分卡 → 追问 / 下一题；最后一题后自动结算。"""
    if not req.skip:
        ans = (req.answer or "").strip()
        if not ans:
            raise HTTPException(400, "先写点什么再提交")
        if len(ans) > 2000:
            raise HTTPException(400, "作答过长，请精简到 2000 字以内")
    db = SessionLocal()
    try:
        s = db.get(DrillSession, sid)
        if not s:
            raise HTTPException(404, "陪练记录不存在或已删除")
        if s.status == "finished":
            raise HTTPException(400, "该陪练已结束")
        if not drill_svc.llm_ready():
            raise HTTPException(400, "[NEED_SETUP]请先在「管理中心」配置模型")
        turn = (db.query(DrillTurn)
                .filter(DrillTurn.session_id == sid, DrillTurn.seq == req.seq).first())
        if not turn:
            raise HTTPException(404, "轮次不存在，请刷新后重试")
        if turn.user_answer is not None:
            raise HTTPException(409, "该题已提交过，请刷新后继续")
    finally:
        db.close()
    job = drill_svc.start_answer_job(sid, req.seq, (req.answer or "").strip(), req.skip)
    return _sse_response(job)


# ---------- 语音（C 档）：念题 TTS + 口述作答转写 ASR ----------

# 单次录音上限：2000 字上限的口述大约 8 分钟，12MB 足够；
# 超限直接拒，避免异常大的 body 把 whisper 拖死（单机本地服务，不做分片续传）。
MAX_ASR_BYTES = 12 * 1024 * 1024

# 单次念题字数上限：题干通常几十字，5000 足够覆盖长追问与材料原文引用。
# ⚠️ 超限**明确报错**而不是静默截断 —— 用户听到念一半会以为语音功能坏了，且听不出少在哪。
MAX_TTS_CHARS = 5000

# 念题分句缓存目录的**文件数上限**（超出时按 mtime 从旧到新淘汰 = LRU）。
# ⚠️ 必须封顶：题干各不相同，每次念题都会落一个新的 mp3 分片，不清理即无界增长。
#    但仍保留最近若干条 ——「这题没听清、再听一遍」是高频动作，命中缓存可秒开。
TTS_CACHE_MAX_FILES = 300


@router.post("/asr")
async def transcribe(file: UploadFile = File(...)):
    """录音 → 文本（口述作答）。

    ⚠️ 模型未安装时**不**走 parser 的「自动下载 base」兜底：用户正等着填答案，
       静默拉 145MB 既无进度也不可控 → 明确 400 引导去「管理中心 → 语音模型」。
    ⚠️ 返回纯文本，前端**填入作答框**由用户校对后再提交 —— ASR 必有错字，
       直接提交会把错字送去评分（这是产品决策，不是实现细节）。
    """
    from ..core.config import settings
    from ..services import asr as asr_svc
    from ..services import parser as parser_svc

    if asr_svc.installed_size() is None:
        raise HTTPException(400, "[NEED_ASR]语音模型尚未下载，请到「管理中心 → 语音模型」下载后重试")

    data = await file.read()
    if not data:
        raise HTTPException(400, "没有收到录音数据，请重试")
    if len(data) > MAX_ASR_BYTES:
        raise HTTPException(400, "录音过长，请分次录入（单次上限约 8 分钟）")

    suffix = os.path.splitext(file.filename or "")[1].lower() or ".webm"
    fd, tmp = tempfile.mkstemp(prefix="drill_asr_", suffix=suffix, dir=str(settings.data_dir))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        # ⚠️ 必须丢线程池：whisper 转写是 CPU 密集的**同步**调用，直接在 async 函数里跑
        #    会把整个事件循环卡住数秒 —— 期间其它请求、SSE 流、健康检查全部无响应。
        #    （/tts 是同步 def，FastAPI 自动丢线程池；这里不能靠那个。）
        text = await run_in_threadpool(parser_svc.transcribe_short, tmp)
    except HTTPException:
        raise
    except RuntimeError as e:
        # 加载层发现模型其实不在（TOCTOU）→ 与路由预判同一口径，仍按 400 引导下载
        msg = str(e)
        if msg.startswith("[NEED_ASR]"):
            raise HTTPException(400, msg)
        raise HTTPException(502, "语音识别失败：" + msg[:120])
    except Exception as e:      # noqa: BLE001
        raise HTTPException(502, "语音识别失败：" + str(e)[:120])
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass
    return {"text": (text or "").strip()}


@router.post("/tts")
def speak(req: TTSReq):
    """题干 → mp3（念题）。复用播客那套 edge-tts：分句缓存 + 间歇性失败重试。"""
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(400, "没有可朗读的内容")
    if len(text) > MAX_TTS_CHARS:
        raise HTTPException(400, f"题干过长（{len(text)} 字），暂不支持朗读")

    from ..core.config import settings
    from ..services import settings_store
    from ..services import tts as tts_svc

    conf = settings_store.load()
    provider = conf.get("tts_provider") or tts_svc.DEFAULT_PROVIDER
    voice = tts_svc.resolve_voice(req.voice or conf.get("tts_voice_host"), "host",
                                  provider=provider)
    cache_dir = settings.data_dir / "tts_cache_drill"
    try:
        audio, _timings = tts_svc.synthesize(
            [{"speaker": "host", "text": text}],
            voice_map={"host": voice},
            provider=provider,
            cache_dir=cache_dir,
        )
    except tts_svc.TTSPermanent as e:
        # 确定性错误（模型未下载 / 音色无效）：原样透出可执行指引，不伪装成「稍后重试」
        raise HTTPException(400, str(e))
    except tts_svc.TTSError as e:
        raise HTTPException(503, e.friendly)
    except Exception as e:      # noqa: BLE001
        raise HTTPException(503, "语音合成失败：" + str(e)[:120])
    finally:
        # 顺手做一次 LRU 裁剪（best-effort）：裁剪失败绝不能影响这次播放
        try:
            tts_svc.prune_cache(cache_dir, TTS_CACHE_MAX_FILES)
        except Exception:       # noqa: BLE001
            pass
    # 同题重复点「念题」靠上面的分句缓存秒开，这里不必让浏览器再缓存一份
    return Response(content=audio, media_type="audio/mpeg",
                    headers={"Cache-Control": "no-store"})


# ---------- 跨场次趋势（C 档优化） ----------

@router.get("/trend")
def trend(limit: int = 20, db: Session = Depends(get_db)):
    """跨场次趋势：薄弱要点是否在收敛 / 总分走向。

    ⚠️ 零新增存储 —— 直接聚合已落库的 drill_turns.missing_points。
       报告原先只在前端算，于是「跨场次」这件事在架构上不可能做到。
    """
    return drill_svc.session_trend(db, limit)


# ---------- 结算 ----------

@router.post("/sessions/{sid}/finish")
def finish_session(sid: int, req: FinishReq, db: Session = Depends(get_db)):
    """结束陪练（提前结束或补结算）：出总分 + 薄弱点 + 错题回写（幂等）。"""
    reason = req.reason if req.reason in ("complete", "abort") else "abort"
    return drill_svc.finalize_session(db, sid, reason)
