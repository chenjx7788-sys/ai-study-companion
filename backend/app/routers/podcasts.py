"""AI 播客路由：素材 → 知识简报 → 对话脚本（可编辑确认）→ 本地音频

流程设计（脚本确认是核心）：
  1. POST /podcasts/generate   创建并生成简报 + 脚本，返回 status=script_ready
  2. PUT  /podcasts/{id}/script 用户逐句编辑、增删、改说话人 → 保存
  3. POST /podcasts/{id}/synthesize 确认后才合成音频（不点就不浪费 TTS 时间）

「生成脚本」与「合成音频」是两个独立按钮，中间天然形成确认节点。
"""
import json
import os
import queue
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Podcast, Note, Material, Highlight, ReviewCard
from ..services import podcast as svc
from ..services import tts
from ..services import bgm
from ..services import mixer
from ..services import settings_store

router = APIRouter(prefix="/podcasts", tags=["podcasts"])

# 背景音乐音量范围（dB，相对人声）。上限 -3dB 已经接近与人声等响，再高必然抢话。
BGM_VOLUME_MIN, BGM_VOLUME_MAX, BGM_VOLUME_DEFAULT = -40, -3, -20


# ---------- 并发保护 ----------
# 「生成 / 合成 / 保存」都是多步写操作（写分句缓存 → 拼音频 → 回写指纹），而按钮
# loading 只能挡住本页的重复点击：多标签页、刷新后重试、网络重发都会绕开它。
# 两个并发请求会同时写同一份 parts/ 与 audio.mp3，可能产出半截音频，或让 audio_sig
# 与实际文件不匹配（此后「上一版」判定会永久错乱，且很难排查）。
# 用进程内的 per-pid 锁把同一作品的这类操作串行化。后端是单进程 uvicorn
# （launcher.py 的 uvicorn.run 未开 workers），进程内锁足够；若改成多 worker 需换文件锁。
_podcast_locks: dict[int, threading.Lock] = {}
_podcast_locks_guard = threading.Lock()

# 等锁时长：正常情况下同一作品的操作都是「点一下等一会儿」，1 秒足够串行化两次相邻的
# 保存；若 1 秒内还拿不到锁，说明确实有长任务（合成）在跑，直接拒绝比排队更清楚。
LOCK_WAIT_SEC = 1.0

# 流式任务的心跳间隔：LLM 单次调用 20-60s，期间若不发任何字节，连接会「看起来死了」
# （也更容易被中间层掐断）。心跳是 SSE 注释行（`: ping`），前端解析时会直接忽略。
HEARTBEAT_SEC = 10.0


def _podcast_lock(pid: int) -> threading.Lock:
    with _podcast_locks_guard:
        lock = _podcast_locks.get(pid)
        if lock is None:
            lock = threading.Lock()
            _podcast_locks[pid] = lock
        return lock


def _forget_podcast_lock(pid: int) -> None:
    """作品删除后回收锁对象（纯内存卫生，漏掉也只是多一个 Lock）"""
    with _podcast_locks_guard:
        _podcast_locks.pop(pid, None)


@contextmanager
def _exclusive(pid: int):
    """独占同一作品的生成 / 合成 / 保存；抢不到锁返回 409，而不是排队。

    排队会让第二条请求撞在同一个文件上；直接拒绝 + 提示稍后重试，语义最清楚。
    """
    lock = _podcast_lock(pid)
    if not lock.acquire(timeout=LOCK_WAIT_SEC):
        raise HTTPException(
            409, "该作品正在执行另一项操作（生成 / 合成 / 保存），请稍等片刻再试")
    try:
        yield
    finally:
        lock.release()


# ---------- SSE ----------
# 与 routers/ai.py、routers/stats.py 同规格：同一个前端的 streamSSE() 直接复用
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


# ---------- 序列化 ----------

def _alive_sets(db: Session) -> tuple[set[int], set[int]]:
    """当前库里仍存在的材料 / 笔记 id 集合（判定作品来源是否失效用）"""
    mats = {i for (i,) in db.query(Material.id).all()}
    notes = {i for (i,) in db.query(Note.id).all()}
    return mats, notes


def _source_missing(p: Podcast, alive: tuple[set[int], set[int]] | None) -> bool | None:
    """作品的来源是否已不在库里。

    ⚠️ 不能用 /options 返回的材料 / 笔记列表来做存活校验：它只给最近 300 条，
    材料或笔记超过 300 条后，基于老来源的作品会被**误报**「原来源已不在库里」。
    这里按 id 对全表判断，与条数无关。alive 为 None（拿不到 db）时返回 None，
    前端在字段为 null 时不误报。
    """
    if alive is None:
        return None
    mats, notes = alive
    refs = p.source_refs or []
    ids: list[int] = []
    for r in refs:
        if isinstance(r, dict) and r.get("id") is not None:
            try:
                ids.append(int(r["id"]))
            except (TypeError, ValueError):
                continue
    if not ids:
        return False      # review 这类来源快照里没有 id，无所谓「失效」
    pool = notes if p.source_type == "notes" else mats
    return any(i not in pool for i in ids)


def podcast_to_dict(p: Podcast, db: Session | None = None, *,
                    full: bool = True,
                    alive: tuple[set[int], set[int]] | None = None,
                    tts_conf: dict | None = None) -> dict:
    """作品序列化。

    full=False 时**不下发 script / brief**：这两项是大头（一条 3 分钟作品的脚本
    就有几千字），而列表接口在生成 / 保存 / 合成 / 改 BGM / 删除后都会被重拉一次，
    体积会随作品数线性放大。列表只给卡片需要的字段，点开作品再拉详情。

    tts_conf 传当前语音设置：音频指纹已纳入语速与句间停顿（见 svc.script_signature），
    列表里逐条读设置文件没必要，由调用方算一次传进来。
    """
    script = p.script or []
    has_audio = bool(p.audio_name) and svc.audio_abspath(p.id, p.audio_name).exists()
    if alive is None and db is not None:
        alive = _alive_sets(db)
    conf = tts_conf or _tts_conf()
    d = {
        "id": p.id,
        "title": p.title,
        "source_type": p.source_type,
        "source_refs": p.source_refs or [],
        "style": p.style,
        "target_minutes": p.target_minutes,
        "instruction": p.instruction or "",
        "voice_map": p.voice_map or {},
        "brief": p.brief or "",
        "script": script,
        "status": p.status,
        "error": p.error or "",
        "audio_name": p.audio_name or "",
        "audio_bytes": p.audio_bytes or 0,
        "duration_sec": p.duration_sec or 0,
        "bgm_id": p.bgm_id or "",
        "bgm_volume": p.bgm_volume if p.bgm_volume is not None else BGM_VOLUME_DEFAULT,
        # 曲目名随记录一起返回：BGM 被删后界面仍能显示**原曲名**（配「已失效」标记），
        # 而不是甩一个裸 id 或一句「已失效」给用户。实时查得到就用实时名（曲目改过名取新的），
        # 查不到再回落到记录里记住的那个名字（bgm_label）。
        "bgm_name": bgm.track_name(p.bgm_id or "") or (p.bgm_label or ""),
        # 引用的曲目现在还在不在 —— 不在的话重新合成必然失败。
        # 只对用户上传的（u-）判定：内置氛围音由 exists()/resolve() 按需现场重建，
        # 永远不会真缺；拿列表比对会误报，还会在列表接口里触发一次素材生成。
        "bgm_missing": (p.bgm_id or "").startswith("u-") and not bgm.exists(p.bgm_id),
        # 来源是否已从库里消失（按 id 精查，不受 /options 的 300 条窗口影响）
        "source_missing": _source_missing(p, alive),
        # 音频是否为「上一版脚本」的产物：为真时界面须显式标注，避免用户以为
        # 听到的就是当前文案（音频与脚本是两份数据，改脚本后必然分叉）
        "audio_stale": svc.audio_is_stale(p, rate=conf["rate"], gap_ms=conf["gap_ms"]),
        "script_chars": svc.script_chars(script),
        "estimate_sec": svc.estimate_seconds(script),
        "segment_count": len(script),
        "has_audio": has_audio,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }
    if not full:
        # 列表接口不下发正文（见 docstring）：点击作品时再走 /podcasts/{pid}
        d.pop("script", None)
        d.pop("brief", None)
    return d


def _precheck_script(raw: list | None) -> dict:
    """合成前预检：空句会被丢弃、超长句会被截断 —— 两件事都要让用户看得见。

    返回 {"total", "empty", "overlong", "dropped_chars"}，total 是**清洗后**的句数。
    """
    empty = overlong = dropped = kept = 0
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            empty += 1
            continue
        kept += 1
        if len(text) > MAX_SEG_CHARS:
            overlong += 1
            dropped += len(text) - MAX_SEG_CHARS
    return {"total": kept, "empty": empty, "overlong": overlong,
            "dropped_chars": dropped}


def _tts_conf() -> dict:
    conf = settings_store.load()
    return {
        "provider": conf.get("tts_provider") or tts.DEFAULT_PROVIDER,
        "rate": conf.get("tts_rate") or tts.DEFAULT_RATE,
        "gap_ms": int(conf.get("tts_gap_ms") or tts.DEFAULT_GAP_MS),
        "host": conf.get("tts_voice_host") or tts.DEFAULT_VOICES["host"],
        "expert": conf.get("tts_voice_expert") or tts.DEFAULT_VOICES["expert"],
    }


def _normalize_voice_map(vm: dict | None) -> dict:
    """把前端传来的音色映射归一到 {host, expert}，非法值回退默认"""
    conf = _tts_conf()
    vm = vm or {}
    return {
        "host": tts.resolve_voice(vm.get("host") or conf["host"], "host"),
        "expert": tts.resolve_voice(vm.get("expert") or conf["expert"], "expert"),
    }


# 单句文本上限：过长会让时间戳与逐句高亮都失去意义，统一在 normalize 处裁剪
MAX_SEG_CHARS = 400


def _normalize_script(raw: list, prev: list[dict] | None = None) -> list[dict]:
    """清洗前端提交的脚本：过滤空句、归一说话人、裁剪过长文本。

    prev 传「保存前的旧脚本」时，对 (说话人, 文本) 未变的句子**沿用旧时间戳与音色
    回填值**。否则每次保存都会把整份脚本的 start_ms / end_ms 剥光 —— 哪怕只改标题、
    只动一句话，其余句子的「点句跳转 / 逐句高亮」也会一起失效。

    时间戳不在音频指纹里（见 svc.script_signature），所以沿用它们不会干扰
    audio_stale 判定，是纯收益改动；文本真的变了的句子不带时间戳，前端本来就会因
    start_ms 为空而跳过跳转。
    """
    # 按 (说话人, 文本) 建旧句索引。同一键重复出现时按顺序各取一份，
    # 让「重复句」也能各自对上原来的时间戳。
    prev_map: dict[tuple[str, str], list[dict]] = {}
    for s in prev or []:
        if not isinstance(s, dict):
            continue
        t = str(s.get("text") or "").strip()[:MAX_SEG_CHARS]
        if not t:
            continue
        sp = "expert" if s.get("speaker") == "expert" else "host"
        prev_map.setdefault((sp, t), []).append(s)

    out = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        speaker = "expert" if item.get("speaker") == "expert" else "host"
        seg: dict = {"speaker": speaker, "text": text[:MAX_SEG_CHARS]}
        bucket = prev_map.get((speaker, seg["text"]))
        if bucket:
            old = bucket.pop(0)
            for k in ("start_ms", "end_ms", "voice", "voice_name"):
                if old.get(k) is not None:
                    seg[k] = old[k]
        out.append(seg)
    return out


# ---------- 选项（页面初始化） ----------

@router.get("/options")
def options(db: Session = Depends(get_db)):
    """可选来源 / 音色 / 时长档位 / 语音服务配置"""
    mats = (db.query(Material).filter(Material.parsed_status == "success")
            .order_by(Material.created_at.desc()).limit(300).all())
    notes = db.query(Note).order_by(Note.updated_at.desc()).limit(300).all()
    due = db.query(ReviewCard).filter(ReviewCard.next_review_at <= datetime.utcnow()).count()
    conf = _tts_conf()

    # 有划线的文档（按材料聚合计数），供「划线精读」选择。
    # 一条 GROUP BY（＋ outerjoin 取标题）完成：早先是「distinct 取全表 id → 再
    # in_() 查全表」的两次扫描 + Python 计数，而且划线文档数一旦超过 SQLite 的
    # 变量上限（999）就直接 500。
    rows = (db.query(Highlight.material_id, Material.title, func.count(Highlight.id))
            .outerjoin(Material, Material.id == Highlight.material_id)
            .filter(Highlight.material_id.isnot(None))
            .group_by(Highlight.material_id).all())
    # 材料已被删除的（理论上有外键兜底，这里防御）不进候选 —— 与旧行为一致
    highlight_mats = sorted(
        ({"id": mid, "title": title, "count": n} for mid, title, n in rows if title),
        key=lambda x: -x["count"])

    return {
        "materials": [{"id": m.id, "title": m.title, "format": m.format,
                       "page_count": m.page_count or 0} for m in mats],
        "notes": [{"id": n.id, "title": n.title, "material_id": n.material_id,
                   "source_type": n.source_type,
                   "updated_at": n.updated_at.isoformat() if n.updated_at else None} for n in notes],
        "highlight_materials": highlight_mats,
        "review_due": due,
        "voices": tts.list_voices(),
        "styles": [{"id": k, **v} for k, v in svc.STYLE_PRESETS.items()],
        "lengths": [{"minutes": k, **v} for k, v in svc.LENGTH_PRESETS.items()],
        # 单句字数上限随 options 下发：前端不必再手抄一份 400（两处常量迟早漂移）
        "default_style": "dialogue",
        "max_seg_chars": MAX_SEG_CHARS,
        "defaults": {"voice_map": {"host": conf["host"], "expert": conf["expert"]},
                     "rate": conf["rate"], "gap_ms": conf["gap_ms"],
                     "provider": conf["provider"]},
        "highlight_semantics": svc.HIGHLIGHT_SEMANTICS,
        "supported_providers": list(tts.SUPPORTED_PROVIDERS),
    }


@router.post("/test-voice")
def test_voice():
    """语音服务连通性自检"""
    return tts.probe()


# ⚠️ 必须注册在 /{pid} 之前：否则 "/voices/..." 会先匹配到 /{pid} 并因 int 转换失败而 422
@router.get("/voices/{voice_id}/preview")
def voice_preview(voice_id: str):
    """试听指定音色。首次现场合成（数秒），之后命中缓存秒开。"""
    conf = _tts_conf()
    try:
        audio = tts.preview(voice_id, rate=conf["rate"], provider=conf["provider"],
                            cache_dir=svc.previews_root())
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(502, f"试听生成失败：{str(e)[:160]}")
    return Response(content=audio, media_type="audio/mpeg",
                    headers={"Cache-Control": "public, max-age=86400"})


# ---------- 背景音乐 ----------
# ⚠️ 整组必须注册在 /{pid} 之前："/bgm" 与 "/{pid}" 都是单段路径，
# 若先注册 /{pid}，FastAPI 会把 "bgm" 当作 pid 去转 int，直接 422。

@router.get("/bgm")
def list_bgm():
    """背景音乐曲库：内置氛围音（程序合成，零版权）+ 用户上传"""
    data = bgm.list_tracks()
    data["volume"] = {"min": BGM_VOLUME_MIN, "max": BGM_VOLUME_MAX,
                      "default": BGM_VOLUME_DEFAULT}
    return data


@router.post("/bgm/upload")
async def upload_bgm(file: UploadFile = File(...)):
    """上传自己的背景音乐（mp3/wav/m4a/flac/ogg），服务端统一转码标准化"""
    try:
        data = await file.read()
        return bgm.add_user_track(file.filename or "", data)
    except (ValueError, mixer.AudioError) as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"上传失败：{str(e)[:160]}")


@router.delete("/bgm/{bgm_id}")
def delete_bgm(bgm_id: str):
    if bgm_id.startswith("bi-"):
        raise HTTPException(400, "内置氛围音不可删除")
    if not bgm.delete_user_track(bgm_id):
        raise HTTPException(404, "背景音乐不存在")
    return {"ok": True}


@router.get("/bgm/{bgm_id}/preview")
def preview_bgm(bgm_id: str):
    """试听背景音乐：截前 12 秒，首次现场截取，之后命中缓存"""
    try:
        audio = bgm.preview_clip(bgm_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(502, f"试听生成失败：{str(e)[:160]}")
    return Response(content=audio, media_type="audio/mpeg",
                    headers={"Cache-Control": "public, max-age=86400"})


# ---------- 列表 / 详情 ----------

@router.get("")
def list_podcasts(db: Session = Depends(get_db)):
    rows = db.query(Podcast).order_by(Podcast.created_at.desc()).all()
    # 存活集合与语音设置只算一次，供全部记录复用（否则 N 条记录要各读一次文件）
    alive, conf = _alive_sets(db), _tts_conf()
    return [podcast_to_dict(p, full=False, alive=alive, tts_conf=conf) for p in rows]


@router.get("/{pid}")
def get_podcast(pid: int, db: Session = Depends(get_db)):
    p = db.get(Podcast, pid)
    if not p:
        raise HTTPException(404, "播客不存在")
    return podcast_to_dict(p, db)


# ---------- 生成 ----------

# 生成脚本的阶段与「名义步数」。⚠️ 字数校正的轮数不固定（0~2 轮），所以它算作
# 「撰写脚本」这一步的子状态、不额外占一步 —— 否则进度条会忽长忽短甚至超过 100%。
_GEN_STEPS = {
    "two_step": {"source": 1, "brief": 2, "script": 3, "polish": 3, "save": 3},
    "fast":     {"source": 1, "script": 2, "polish": 2, "save": 2},
}
_GEN_TOTAL = {"two_step": 3, "fast": 2}


def _gen_progress(key: str, detail: dict | None, mode: str) -> dict:
    """阶段 key → 前端可直接显示的一句话 + 「第几步 / 共几步」"""
    d = detail or {}
    if key == "source":
        label = "正在抽取素材…"
    elif key == "brief":
        label = "正在提炼知识简报…"
    elif key == "script":
        label = "正在撰写播客脚本…"
    elif key == "polish":
        short = "偏短" if int(d.get("chars") or 0) < int(d.get("target") or 0) else "偏长"
        # ⚠️ short 本身已含「偏」字，格式串里不要再拼一个（否则出现「偏偏短」）
        label = (f"当前 {d.get('chars')} 字（目标 {d.get('target')} 字，{short}），"
                 f"正在校正第 {d.get('round')} 轮…")
    elif key == "save":
        label = "正在保存…"
    else:
        label = "正在生成…"
    steps = _GEN_STEPS.get(mode) or _GEN_STEPS["two_step"]
    total = _GEN_TOTAL.get(mode) or 3
    return {"key": key, "label": label, "index": steps.get(key, total), "total": total}


def _remember_bgm(p: Podcast, bid: str) -> None:
    """写入 BGM 选择，并**记住曲名**（曲目被删后界面仍能显示原曲名）。

    ⚠️ 曲目没变时不重算 label：素材已被删除时 track_name() 返回空串，重算会
    把记住的原曲名抹掉 —— 而「调音量」「改标题点保存」都不会换曲目。
    """
    bid = (bid or "").strip()
    same = bid == (p.bgm_id or "")
    p.bgm_id = bid
    if not (same and (p.bgm_label or "")):
        p.bgm_label = bgm.track_name(bid) if bid else ""


class GenerateReq(BaseModel):
    source_type: str = "article"
    ref_ids: list[int] = []
    highlight_colors: list[str] | None = None
    style: str = "dialogue"
    target_minutes: int = 3
    voice_map: dict | None = None
    mode: str = "two_step"        # two_step 两步式 / fast 快速模式
    instruction: str = ""
    title: str = ""
    bgm_id: str = ""              # 空 = 不加背景音乐
    bgm_volume: int = BGM_VOLUME_DEFAULT


def _prepare_generation(db: Session, p: Podcast, req: GenerateReq) -> tuple[str, str, str]:
    """生成第一步：抽素材 + 把「生成配置」写进记录（**不发 LLM，不提交**）。

    单独拆出来有两个原因：
    1. 抽素材要读库、可能因参数问题失败（没选文档 / 划线颜色为空 / 素材为空），
       这类错误必须在**返回流式响应之前**以 HTTP 状态码回给前端；
    2. 剩下的只有纯 LLM 计算，可以安全地丢到 worker 线程（ORM 对象不跨线程用）。

    返回 (标题, 素材正文, 风格)。
    """
    try:
        title, source_text, refs = svc.collect_source(
            db, req.source_type, req.ref_ids, req.highlight_colors)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not source_text.strip():
        raise HTTPException(400, "素材内容为空，无法生成")

    p.title = svc.sanitize_title(req.title or title)
    p.source_type = req.source_type
    p.source_refs = refs
    style = req.style if req.style in svc.STYLE_PRESETS else "dialogue"
    p.style = style
    p.target_minutes = req.target_minutes
    p.voice_map = _normalize_voice_map(req.voice_map)
    p.instruction = (req.instruction or "").strip()
    # 背景音乐与来源/音色同层，都是「作品配置」，生成时一并记录
    bid = (req.bgm_id or "").strip()
    if bid and not bgm.exists(bid):
        raise HTTPException(400, f"背景音乐不存在：{bid}")
    _remember_bgm(p, bid)
    p.bgm_volume = max(BGM_VOLUME_MIN, min(BGM_VOLUME_MAX, int(req.bgm_volume)))
    p.error = ""
    return title, source_text, style


def _build_content(req: GenerateReq, title: str, source_text: str, style: str,
                   on_stage=None) -> tuple[str, list[dict]]:
    """生成第二步：**纯 LLM 计算**，返回 (简报, 脚本)。

    ⚠️ 这里刻意不碰数据库 —— 所以可以放进 worker 线程执行，不受 SQLAlchemy
    session 的线程限制；写库统一由 _commit_generation() 在请求/生成器线程完成。
    """
    if req.mode == "fast":
        # 快速模式：跳过简报，素材直接出脚本（省一次调用，信息密度略降）
        return "", svc.make_script_direct(title, source_text, req.target_minutes,
                                          req.instruction, style=style,
                                          on_stage=on_stage)
    brief = svc.make_brief(title, source_text, req.target_minutes, on_stage=on_stage)
    script = svc.make_script(brief, req.target_minutes, topic=title,
                             instruction=req.instruction, style=style,
                             on_stage=on_stage)
    return brief, script


def _commit_generation(db: Session, p: Podcast, brief: str, script: list[dict]) -> None:
    """生成第三步：写回记录并提交（必须在持有 db 的线程里执行）。"""
    p.brief = brief
    p.script = script
    p.status = "script_ready"
    # 脚本已重写 → 分句缓存必然失效，必须清掉（否则重新合成会误用旧片段）
    svc.clear_parts(p.id)
    # 旧音频保留不删：界面会标注「音频对应的是修改前的文案」，供用户对照后再重新合成。
    # 早先在这里直接删文件，会让播放条凭空消失，用户不知道发生了什么。
    db.commit()
    db.refresh(p)


def _generate_into(db: Session, p: Podcast, req: GenerateReq, on_stage=None) -> None:
    """在既有记录上重新生成简报与脚本（原地覆盖，保留 id 与音频路径）。

    ⚠️ 同步版本保持可用：直接打接口的脚本 / 老前端不受影响。
    前端已切到 /generate/stream 与 /{pid}/regenerate/stream（有阶段进度）。
    """
    title, source_text, style = _prepare_generation(db, p, req)
    brief, script = _build_content(req, title, source_text, style, on_stage=on_stage)
    _commit_generation(db, p, brief, script)


@router.post("/generate/stream")
def generate_stream(req: GenerateReq, db: Session = Depends(get_db)):
    """SSE 流式生成脚本（新建）：`meta` → `progress`×N → `done` | `error`

    生成要过 1~2 次 LLM（每次 20-60s），同步接口在这段时间里只有一句
    「正在生成脚本...」。这里把「抽素材 → 提炼简报 → 撰写脚本 → 字数校正」逐步回传。

    ⚠️ 抽素材与建记录都在**返回响应之前**完成：一旦进入生成器，响应头已发出，
    参数类错误就只能当事件回传、拿不到 HTTP 状态码了。
    """
    p = Podcast(status="draft")
    db.add(p)
    db.commit()
    db.refresh(p)
    pid = p.id
    try:
        title, source_text, style = _prepare_generation(db, p, req)
    except BaseException:
        # 创建即失败 → 不留半成品（与同步 /generate 口径一致）
        db.rollback()
        db.delete(p)
        db.commit()
        svc.remove_podcast_files(pid)
        raise
    return StreamingResponse(
        _generation_stream(db, p, req, pid, title, source_text, style, fresh=True),
        media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/generate")
def generate(req: GenerateReq, db: Session = Depends(get_db)):
    """新建播客并生成脚本（两步式：先简报后脚本）"""
    p = Podcast(status="draft")
    db.add(p)
    db.commit()
    db.refresh(p)
    pid = p.id          # 先取下来：记录删掉之后再读 p.id 会碰到已失效的实例
    try:
        _generate_into(db, p, req)
    except BaseException:
        # 新建即失败 → 不留半成品：记录与刚建出来的目录一起清掉。
        # ⚠️ 旧写法是「先写 status='failed' 并 commit，紧接着又 db.delete」——
        # 写下一个马上被删的状态，自相矛盾；而且只接 HTTPException，
        # LLM 网络异常那类错误会漏网，在列表里留下永远停在 draft 的垃圾记录
        # 和空目录。（remove_podcast_files 绝不抛错，不会盖掉原始异常。）
        db.rollback()
        db.delete(p)
        db.commit()
        svc.remove_podcast_files(pid)
        raise
    return podcast_to_dict(p, db)


@router.post("/{pid}/regenerate")
def regenerate(pid: int, req: GenerateReq, db: Session = Depends(get_db)):
    """重新生成脚本（保留播客 id，旧音频作废）"""
    p = db.get(Podcast, pid)
    if not p:
        raise HTTPException(404, "播客不存在")
    # 与合成互斥：生成会重写脚本并清空 parts/，和正在跑的合成抢同一批文件
    with _exclusive(pid):
        db.refresh(p)
        _generate_into(db, p, req)
    return podcast_to_dict(p, db)


@router.post("/{pid}/regenerate/stream")
def regenerate_stream(pid: int, req: GenerateReq, db: Session = Depends(get_db)):
    """SSE 流式重新生成脚本（保留播客 id，旧音频作废）。

    ⚠️ 生成失败时**不改动记录**（与同步版一致）：原脚本与音频都还在，
    失败只以 error 事件 + 提示的形式告诉用户，不让一次失败损坏已有内容。
    """
    p = db.get(Podcast, pid)
    if not p:
        raise HTTPException(404, "播客不存在")
    title, source_text, style = _prepare_generation(db, p, req)
    return StreamingResponse(
        _generation_stream(db, p, req, pid, title, source_text, style, fresh=False),
        media_type="text/event-stream", headers=SSE_HEADERS)


def _generation_stream(db: Session, p: Podcast, req: GenerateReq, pid: int,
                       title: str, source_text: str, style: str, fresh: bool):
    """生成脚本的 SSE 流。

    fresh=True 表示这次是「新建」：失败要把刚建的记录与目录一起清掉（不留半成品）。
    LLM 计算放 worker 线程（纯计算、不碰库），写库与落盘都在生成器线程完成 ——
    与 /synthesize/stream 同一套结构。
    """
    total = _GEN_TOTAL.get(req.mode) or 3
    yield _sse("meta", {"podcast_id": pid, "mode": req.mode, "style": style,
                        "total_steps": total})

    lock = _podcast_lock(pid)
    if not lock.acquire(timeout=LOCK_WAIT_SEC):
        yield _sse("error", {"message":
                             "该作品正在执行另一项操作（生成 / 合成 / 保存），请稍等片刻再试"})
        return
    pool = ThreadPoolExecutor(max_workers=1)
    q: queue.Queue = queue.Queue()
    try:
        yield _sse("progress", _gen_progress("source", None, req.mode))

        def worker():
            try:
                brief, script = _build_content(
                    req, title, source_text, style,
                    on_stage=lambda key, detail=None: q.put(("stage", (key, detail))))
                q.put(("ok", (brief, script)))
            except BaseException as e:      # noqa: BLE001
                q.put(("err", e))

        pool.submit(worker)
        while True:
            try:
                kind, payload = q.get(timeout=HEARTBEAT_SEC)
            except queue.Empty:
                yield ": ping\n\n"          # 心跳：LLM 单次 20-60s，见 HEARTBEAT_SEC
                continue
            if kind == "stage":
                key, detail = payload
                yield _sse("progress", _gen_progress(key, detail, req.mode))
                continue
            if kind == "err":
                err = payload
                msg = str(getattr(err, "detail", None) or err)[:200]
                if fresh:
                    # 新建即失败 → 不留半成品（与同步 /generate 口径一致）
                    db.rollback()
                    db.delete(p)
                    db.commit()
                    svc.remove_podcast_files(pid)
                # 重新生成失败：**不动记录**，原脚本与音频都还在（见接口 docstring）
                yield _sse("error", {"message": msg})
                return
            brief, script = payload
            try:
                _commit_generation(db, p, brief, script)
            except Exception as e:
                msg = f"脚本保存失败：{str(e)[:160]}"
                p.status = "failed"
                p.error = msg
                db.commit()
                yield _sse("error", {"message": msg})
                return
            yield _sse("done", {"podcast": podcast_to_dict(p, db)})
            return
    finally:
        # wait=True：客户端中途断开时 worker 可能还在跑 LLM，等它收尾再放锁，
        # 否则紧接着的第二次生成会与它抢同一个作品
        pool.shutdown(wait=True, cancel_futures=True)
        lock.release()


# ---------- 脚本编辑（确认环节） ----------

class ScriptSaveReq(BaseModel):
    script: list[dict] | None = None
    title: str | None = None
    voice_map: dict | None = None


@router.put("/{pid}/script")
def save_script(pid: int, req: ScriptSaveReq, db: Session = Depends(get_db)):
    """保存人工编辑后的脚本。改脚本不需要重新提炼，但会让已合成的音频作废。"""
    p = db.get(Podcast, pid)
    if not p:
        raise HTTPException(404, "播客不存在")

    # 与合成互斥：合成期间清空 parts/ 会让正在跑的那一次读到缺失的分句缓存
    with _exclusive(pid):
        db.refresh(p)

        if req.script is not None:
            # prev 传旧脚本：文本没变的句子沿用旧时间戳，避免「只改一句，
            # 整份脚本的逐句跳转与高亮全废」
            script = _normalize_script(req.script, prev=p.script or [])
            if not script:
                raise HTTPException(400, "脚本不能为空")
            changed = script != (p.script or [])
            p.script = script
            if changed:
                # 文本变了 → 分句缓存全部失效（必须清，否则重新合成会误用旧片段）
                svc.clear_parts(p.id)
                # 旧音频保留，由 audio_stale（指纹比对）标记为「上一版」

        if req.title is not None and req.title.strip():
            p.title = svc.sanitize_title(req.title)
        if req.voice_map is not None:
            vm = _normalize_voice_map(req.voice_map)
            if vm != (p.voice_map or {}):
                # 换音色不必重新生成脚本，但分句缓存按 key（含音色）本就不会命中，
                # 仍主动清理一次避免旧片段堆积；音频由指纹比对标记为过期。
                svc.clear_parts(p.id)
            p.voice_map = vm

        db.commit()
        db.refresh(p)
    return podcast_to_dict(p, db)


def _apply_bgm(db: Session, p: Podcast, audio: bytes,
               merged: list[dict]) -> tuple[bytes, list[dict]]:
    """把背景音乐混进已合成的人声，并补偿编码延迟对时间戳的影响。

    放在最后一步做（而不是逐句混）：分句缓存里存的是**纯人声**，
    所以换 BGM / 调音量不需要重新合成任何一句，只重跑这一次混音。
    """
    try:
        path = bgm.resolve(p.bgm_id)
    except ValueError as e:
        p.status = "failed"
        p.error = str(e)
        db.commit()
        raise HTTPException(400, str(e))

    try:
        vocal, _ = mixer.decode(audio)
        bed, _ = mixer.decode(path)
        volume = float(p.bgm_volume if p.bgm_volume is not None else BGM_VOLUME_DEFAULT)
        mixed = mixer.mix(vocal, bed, volume_db=volume, ducking=True)
        out = mixer.encode_mp3(mixed)
    except mixer.AudioError as e:
        p.status = "failed"
        p.error = f"背景音乐处理失败：{str(e)[:160]}"
        db.commit()
        raise HTTPException(502, p.error)
    except Exception as e:
        p.status = "failed"
        p.error = f"背景音乐混音失败：{str(e)[:160]}"
        db.commit()
        raise HTTPException(502, p.error)

    # ⚠️ 混音会重新编码，而 libmp3lame 在流开头插入固定 45ms 静音（实测常量），
    # 于是音频里所有内容整体后移 → 逐句时间戳必须同步补偿，
    # 否则「点句子跳转 / 逐句高亮」会整体滞后 45ms。
    d = mixer.ENCODER_DELAY_MS
    shifted = [{**s,
                "start_ms": round((s.get("start_ms") or 0) + d),
                "end_ms": round((s.get("end_ms") or 0) + d)}
               for s in merged]
    return out, shifted


# ---------- 合成音频 ----------

class SynthReq(BaseModel):
    voice_map: dict | None = None
    rate: str | None = None
    gap_ms: int | None = None
    # None = 不改动；"" = 关闭背景音乐。允许在这里传，是为了让「只想换 BGM
    # 重新合成」不必再走一次生成脚本（那会白白多花一次 LLM 调用）。
    bgm_id: str | None = None
    bgm_volume: int | None = None


def _resolve_synth_conf(p: Podcast, req: SynthReq) -> dict:
    """合成参数归一（同步接口与流式接口共用）。BGM 不存在时抛 400。"""
    conf = _tts_conf()
    voice_map = _normalize_voice_map(req.voice_map or p.voice_map)
    rate = req.rate or conf["rate"]
    gap_ms = req.gap_ms if req.gap_ms is not None else conf["gap_ms"]
    gap_ms = max(0, min(int(gap_ms), 1500))
    if req.bgm_id is not None:
        bid = (req.bgm_id or "").strip()
        if bid and not bgm.exists(bid):
            raise HTTPException(400, f"背景音乐不存在：{bid}")
        _remember_bgm(p, bid)
    if req.bgm_volume is not None:
        p.bgm_volume = max(BGM_VOLUME_MIN, min(BGM_VOLUME_MAX, int(req.bgm_volume)))
    return {"voice_map": voice_map, "rate": rate, "gap_ms": gap_ms,
            "provider": conf["provider"]}


def _finalize_audio(db: Session, p: Podcast, audio: bytes, script: list[dict],
                    timings: list[dict], voice_map: dict) -> None:
    """合成结果落库：时间戳回填 → BGM 混音 → 写音频 → 记指纹 → 提交。

    同步接口与 SSE 流式接口共用这一份，避免两条路径各写一遍导致口径漂移。
    """
    merged = [{**seg, "start_ms": t["start_ms"], "end_ms": t["end_ms"],
               "voice": t["voice"], "voice_name": t["voice_name"]}
              for seg, t in zip(script, timings)]

    # 背景音乐：在分句缓存之后混音（缓存存的是纯人声，换 BGM 无需重合成）
    if (p.bgm_id or "").strip():
        audio, merged = _apply_bgm(db, p, audio, merged)

    name = svc.save_audio(p.id, audio)
    try:
        svc.save_script_json(p.id, json.dumps(
            {"title": p.title, "voice_map": voice_map, "segments": merged},
            ensure_ascii=False, indent=2))
    except Exception:
        pass   # 副本写失败不影响主流程

    p.script = merged
    p.voice_map = voice_map
    p.audio_name = name
    p.audio_bytes = len(audio)
    p.duration_sec = round(tts.duration_ms(audio) / 1000)
    # 记录音频对应的脚本+音色指纹：此后若再改动脚本或音色，界面即可判定音频已过期
    p.audio_sig = svc.script_signature(merged, voice_map, p.bgm_id or "", p.bgm_volume)
    p.status = "done"
    p.error = ""
    db.commit()
    db.refresh(p)


@router.post("/{pid}/synthesize")
def synthesize(pid: int, req: SynthReq, db: Session = Depends(get_db)):
    """确认脚本后合成音频（耗时较长：逐句并发合成 + 字节拼接 + 落盘）

    ⚠️ 保留同步版本不做改动：直接打接口的脚本 / 老前端仍可用。
    前端已切到 /synthesize/stream（有逐句进度）。
    """
    p = db.get(Podcast, pid)
    if not p:
        raise HTTPException(404, "播客不存在")

    # 同一作品的合成互斥：并发跑两次会同时写 parts/ 与 audio.mp3，产物可能只有半截，
    # 且两边各自回写 audio_sig → 指纹与实际文件永久对不上。
    with _exclusive(pid):
        db.refresh(p)
        script = _normalize_script(p.script or [])
        if not script:
            raise HTTPException(400, "还没有脚本，请先生成或编写脚本")

        conf = _resolve_synth_conf(p, req)
        svc.drop_legacy_parts(p.id)      # 旧命名的分句缓存不会再命中，顺手清

        try:
            audio, timings = tts.synthesize(script, conf["voice_map"], rate=conf["rate"],
                                            gap_ms=conf["gap_ms"], provider=conf["provider"],
                                            cache_dir=svc.parts_dir(p.id))
        except tts.TTSError as e:
            # 成功句已缓存 → 再次点击只会补失败的部分，不必整段重做
            p.status = "failed"
            p.error = e.friendly
            db.commit()
            raise HTTPException(502, p.error)
        except Exception as e:
            p.status = "failed"
            p.error = f"语音合成失败：{str(e)[:180]}"
            db.commit()
            raise HTTPException(502, p.error)

        _finalize_audio(db, p, audio, script, timings, conf["voice_map"])
    return podcast_to_dict(p, db)


@router.post("/{pid}/synthesize/stream")
def synthesize_stream(pid: int, req: SynthReq, db: Session = Depends(get_db)):
    """SSE 流式合成：`meta`（预检）→ `progress`×N → `done` | `error`

    逐句合成十几句要 30–60s（10 分钟档更久），同步接口在这段时间里只有一句
    「合成中…」，用户无法判断是在跑还是卡死。这里把每完成一句的进度回传。
    
    ⚠️ 前置校验（404 / 400）必须在返回响应之前做完：一旦进入生成器，响应头已发出，
    错误只能作为 error 事件回传，拿不到 HTTP 状态码。
    """
    p = db.get(Podcast, pid)
    if not p:
        raise HTTPException(404, "播客不存在")
    precheck = _precheck_script(p.script or [])
    script = _normalize_script(p.script or [])
    if not script:
        raise HTTPException(400, "还没有脚本，请先生成或编写脚本")
    conf = _resolve_synth_conf(p, req)

    def gen():
        # 预检走 meta 通道：utils/sse.js 已有 meta 回调（stats 流的元信息也走它），
        # 不必为一个新事件去改公共工具、也不必让所有调用方多传一个占位参数
        yield _sse("meta", precheck)

        # 锁在生成器里拿：响应一返回，路由函数就退出了，锁必须在流的生命周期里持有。
        lock = _podcast_lock(pid)
        if not lock.acquire(timeout=LOCK_WAIT_SEC):
            yield _sse("error", {"message":
                                 "该作品正在执行另一项操作（生成 / 合成 / 保存），请稍等片刻再试"})
            return
        pool = ThreadPoolExecutor(max_workers=1)
        q: queue.Queue = queue.Queue()
        try:
            db.refresh(p)
            # 配置先落库：BGM / 音量属于「这条作品」的选择，即使合成失败也不该丢
            db.commit()
            svc.drop_legacy_parts(pid)   # 旧命名的分句缓存不会再命中，合成前顺手清

            def worker():
                try:
                    audio, timings = tts.synthesize(
                        script, conf["voice_map"], rate=conf["rate"],
                        gap_ms=conf["gap_ms"], provider=conf["provider"],
                        cache_dir=svc.parts_dir(pid),
                        on_progress=lambda d, t: q.put(("progress", {"done": d, "total": t})))
                    q.put(("ok", (audio, timings)))
                except BaseException as e:      # noqa: BLE001
                    q.put(("err", e))

            pool.submit(worker)
            while True:
                kind, payload = q.get()
                if kind == "progress":
                    yield _sse("progress", payload)
                    continue
                if kind == "err":
                    err = payload
                    msg = getattr(err, "friendly", None) or f"语音合成失败：{str(err)[:180]}"
                    p.status = "failed"
                    p.error = str(msg)[:200]
                    db.commit()
                    yield _sse("error", {"message": str(msg)})
                    return
                audio, timings = payload
                try:
                    _finalize_audio(db, p, audio, script, timings, conf["voice_map"])
                except HTTPException as e:      # _apply_bgm 已写过 status/error
                    yield _sse("error", {"message": str(e.detail)})
                    return
                except Exception as e:
                    p.status = "failed"
                    p.error = f"合成结果处理失败：{str(e)[:160]}"
                    db.commit()
                    yield _sse("error", {"message": p.error})
                    return
                yield _sse("done", {"podcast": podcast_to_dict(p, db)})
                return
        finally:
            # ⚠️ wait=True：客户端中途断开时，worker 可能正在写 parts/ 与 audio.mp3，
            # 必须等它收尾再放锁，否则紧接着的第二次合成会和它抢同一批文件。
            pool.shutdown(wait=True, cancel_futures=True)
            lock.release()

    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


# ---------- 音频 / 文件 ----------

@router.get("/{pid}/audio")
def get_audio(pid: int, db: Session = Depends(get_db)):
    p = db.get(Podcast, pid)
    if not p or not p.audio_name:
        raise HTTPException(404, "还没有生成音频")
    path = svc.audio_abspath(p.id, p.audio_name)
    if not path.exists():
        raise HTTPException(404, "音频文件不存在，可能已被移动或删除")
    # 显式声明缓存策略：URL 上带了 ?v=<音频字节数>（重新合成后字节数变化 →
    # URL 变化 → <audio> 才会丢掉上一次缓冲的音频），但字节数相同的情形仍需
    # 能命中缓存，所以用 no-cache（每次都条件请求，命中则 304），而不是 max-age。
    return FileResponse(path, media_type="audio/mpeg", filename=f"{p.title}.mp3",
                        headers={"Cache-Control": "private, no-cache"})


@router.post("/{pid}/reveal")
def reveal_in_folder(pid: int, db: Session = Depends(get_db)):
    """在系统文件管理器中定位音频文件（方便复制分享）。

    走后端而非 pywebview js_api：开发态浏览器直连也能用，逻辑统一。
    """
    p = db.get(Podcast, pid)
    if not p or not p.audio_name:
        raise HTTPException(404, "还没有生成音频")
    path = svc.audio_abspath(p.id, p.audio_name)
    if not path.exists():
        raise HTTPException(404, "音频文件不存在，可能已被移动或删除")

    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
        elif os.name == "nt":
            # explorer 的 /select 必须与路径连写，且退出码恒为 1（不能据此判失败）
            subprocess.Popen(f'explorer /select,"{path}"', shell=False)
        else:
            subprocess.Popen(["xdg-open", str(path.parent)])
    except Exception as e:
        raise HTTPException(500, f"无法打开文件位置：{str(e)[:120]}")

    return {"ok": True, "path": str(path), "folder": str(path.parent)}


@router.get("/{pid}/srt")
def export_srt(pid: int, db: Session = Depends(get_db)):
    """导出 SRT 字幕（时间轴来自合成时回填的 start_ms / end_ms）。

    ⚠️ 必须先合成过一次：没时间戳就做不出字幕，此时给 400 而不是导出一份对不上的空文件。
    """
    p = db.get(Podcast, pid)
    if not p:
        raise HTTPException(404, "播客不存在")
    body = svc.script_to_srt(p.script or [])
    if not body.strip():
        raise HTTPException(400, "还没有字幕可用：请先合成一次音频（字幕时间轴来自音频）")
    fname = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", p.title or "").strip() or "播客字幕"
    fallback = re.sub(r"[^A-Za-z0-9._-]+", "_", fname).strip("_") or "podcast"
    disposition = (f'attachment; filename="{fallback}.srt"; '
                   f"filename*=UTF-8''{quote(fname + '.srt')}")
    return PlainTextResponse(body, media_type="text/plain; charset=utf-8",
                             headers={"Content-Disposition": disposition})


@router.get("/{pid}/path")
def get_path(pid: int, db: Session = Depends(get_db)):
    """返回音频绝对路径（前端「复制路径」用）"""
    p = db.get(Podcast, pid)
    if not p or not p.audio_name:
        raise HTTPException(404, "还没有生成音频")
    path = svc.audio_abspath(p.id, p.audio_name)
    return {"path": str(path), "exists": path.exists(), "folder": str(path.parent)}


@router.get("/{pid}/export")
def export_script(pid: int, db: Session = Depends(get_db)):
    """导出脚本为纯文本（下载成 .txt，方便二次编辑 / 分享文稿）

    ⚠️ 必须带 Content-Disposition: attachment。前端入口是 <a> / <a download>，
    没有这个头浏览器只会把纯文本显示在新标签页里，而不是下载 —— 与按钮名不符。
    中文文件名走 filename*（RFC 5987）；HTTP 头是 latin-1 编码，
    所以 ASCII 兜底名必须**只含 ASCII**，否则响应头编码就炸。
    """
    p = db.get(Podcast, pid)
    if not p:
        raise HTTPException(404, "播客不存在")
    head = f"{p.title}\n{'=' * 32}\n\n"
    body = svc.script_to_text(p.script or [])
    # 去掉文件名非法字符（Windows 不允许 \ / : * ? " < > |）
    fname = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", p.title or "").strip() or "播客文稿"
    fallback = re.sub(r"[^A-Za-z0-9._-]+", "_", fname).strip("_") or "podcast"
    disposition = (f'attachment; filename="{fallback}.txt"; '
                   f"filename*=UTF-8''{quote(fname + '.txt')}")
    return PlainTextResponse(head + body, media_type="text/plain; charset=utf-8",
                             headers={"Content-Disposition": disposition})


# ---------- 更新 / 删除 ----------

class UpdateReq(BaseModel):
    title: str | None = None
    bgm_id: str | None = None
    bgm_volume: int | None = None


@router.put("/{pid}")
def update_podcast(pid: int, req: UpdateReq, db: Session = Depends(get_db)):
    p = db.get(Podcast, pid)
    if not p:
        raise HTTPException(404, "播客不存在")
    if req.title is not None and req.title.strip():
        p.title = svc.sanitize_title(req.title)
    if req.bgm_id is not None:
        bid = (req.bgm_id or "").strip()
        # 空串 = 关闭背景音乐；非空则必须真实存在，避免存进一个永远合成失败的值
        if bid and not bgm.exists(bid):
            raise HTTPException(400, f"背景音乐不存在：{bid}")
        _remember_bgm(p, bid)
    if req.bgm_volume is not None:
        p.bgm_volume = max(BGM_VOLUME_MIN, min(BGM_VOLUME_MAX, int(req.bgm_volume)))
    db.commit()
    db.refresh(p)
    return podcast_to_dict(p, db)


@router.delete("/{pid}")
def delete_podcast(pid: int, db: Session = Depends(get_db)):
    p = db.get(Podcast, pid)
    if not p:
        raise HTTPException(404, "播客不存在")
    # 与合成互斥：合成中删文件，会让正在跑的那次把产物写进已删除的作品目录
    with _exclusive(pid):
        # 先删数据库记录、再清文件：文件清理失败不应让用户连记录都删不掉
        db.delete(p)
        db.commit()
        svc.remove_podcast_files(pid)
    _forget_podcast_lock(pid)
    return {"ok": True}
