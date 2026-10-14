"""AI 视频路由：作品 CRUD / 兼容性预检 / 讲稿与视觉步点生成（SSE）/ 闸门 / 产物。

【与播客的关系（PRD 2.1 硬性约束 3 + 4.5）】
视频对播客是**只读消费**：只读 `podcasts.audio_name / script / duration_sec`。
本文件**绝不**调用 `podcasts._finalize_audio` —— 它把结果直接写进 Podcast 的
audio_name / duration_sec / audio_sig / status，视频调用它等于把视频的音频指纹
写进播客记录，播客界面会立刻误报「音频对应的是修改前的文案」。
视频的音轨落库一律走 `services/video.py::finalize_video`。

【「启动或附着」的任务模型（照搬播客那套已被血泪验证过的做法）】
生成是长任务（LLM 多次调用 + 定位 + 补步），**必须**与客户端连接解耦：
用户离开页面任务照跑、落盘照写；回到页面重新调 `/plan/stream` 即附着到
正在跑的任务上继续看进度。绝不在 SSE 生成器里做落盘（连接一断，几十秒白烧）。
"""
import hashlib
import json
import queue
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db, SessionLocal
from ..models import Video, Podcast, Material, Note, ActivityLog
from ..services import video as svc
from ..services import backgrounds as bg_svc
from ..services import bgm as bgm_svc
from ..services import skin_previews as preview_svc
from ..services import locate as locate_svc
from ..services import podcast as pod_svc
from ..services import settings_store
from ..services import tts
from ..core.filename import safe_stem

router = APIRouter(prefix="/videos", tags=["videos"])

# 渲染并发上限（PRD 4.3：渲染是 CPU 密集且与视频时长同量级，并发只让两条都变慢）
RENDER_CONCURRENCY = 1

# 等锁时长：同作品的生成/编辑操作都是「点一下等一会儿」，1 秒足够串行化两次相邻保存；
# 1 秒还拿不到说明确实有长任务在跑 → 直接拒绝比排队语义更清楚。
LOCK_WAIT_SEC = 1.0

HEARTBEAT_SEC = 10.0
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}

# 附加要求 / 粘贴文本上限（PRD 3.1.5）
INSTRUCTION_MAX = 500
SOURCE_TYPES = pod_svc.SOURCE_TYPES

# 背景音乐音量钳制范围（dB）。⚠️ 与 routers/podcasts 必须同口径 ——
# BGM 素材库是全局的（/api/podcasts/bgm），两边各存一份会导致同一首歌两种行为。
BGM_VOLUME_MIN, BGM_VOLUME_MAX, BGM_VOLUME_DEFAULT = -40, -3, -20


# ---------- 并发保护（per-video，与播客同构） ----------

_video_locks: dict = {}
_video_locks_guard = threading.Lock()


def _video_lock(vid: int) -> threading.Lock:
    with _video_locks_guard:
        lock = _video_locks.get(vid)
        if lock is None:
            lock = threading.Lock()
            _video_locks[vid] = lock
        return lock


def _forget_video_lock(vid: int) -> None:
    with _video_locks_guard:
        _video_locks.pop(vid, None)


def _forget_video_jobs(vid: int) -> None:
    """删作品时一并清掉它的**内存任务缓存**（防复用主键带来的定时炸弹）。

    ⚠️ 为什么必须清：SQLite 的 INTEGER PRIMARY KEY 在删除**表里最大那一行**之后会
       **复用 id** —— 删掉一部作品再新建，新作品极可能拿到同一个 id。而
       `_PLAN_JOBS` / `_RENDER_JOBS` 正是以这个可复用 id 为键：新作品一调
       `/plan/status` 或 `/render/status` 就会读到**已删作品**的 status / stage /
       error（渲染侧更狠：前端无条件读 `report`，会把已删作品的「对齐质量」显示在
       新作品上）。锁已经清了（`_forget_video_lock`），任务缓存也必须一起清。
    """
    with _PLAN_JOBS_LOCK:
        _PLAN_JOBS.pop(vid, None)
    with _RENDER_LOCK:
        _RENDER_JOBS.pop(vid, None)
        _RENDER_QUEUE[:] = [i for i in _RENDER_QUEUE if i != vid]


class _Exclusive:
    """独占同一作品的重操作；抢不到锁返回 409（排队会让两条请求撞在同一个文件上）。"""

    def __init__(self, vid: int):
        self.vid = vid
        self.lock = _video_lock(vid)

    def __enter__(self):
        if not self.lock.acquire(timeout=LOCK_WAIT_SEC):
            raise HTTPException(
                409, "该作品正在执行另一项操作（生成 / 编辑 / 渲染），请稍等片刻再试")
        return self

    def __exit__(self, *exc):
        self.lock.release()
        return False


# ---------- SSE ----------

def _track(type_: str, ref_id: int, duration_seconds: int = 0,
           detail: dict | None = None) -> None:
    """行为埋点（PRD 4.2）。

    ⚠️ 用**独立会话**且绝不抛错：埋点是观测不是业务 —— 它失败不该打挂业务动作，
       它的提交/回滚也绝不能连带业务会话里的待定变更。
    ⚠️ 埋点边界（PRD 4.2）：detail 只装行为量与长度（stage / completed / bytes …），
       绝不装讲稿正文与来源内容。
    """
    try:
        d = json.dumps(detail, ensure_ascii=False)[:255] if detail else ""
        db = SessionLocal()
        try:
            db.add(ActivityLog(type=type_, ref_id=int(ref_id or 0),
                               duration_seconds=int(duration_seconds or 0), detail=d))
            db.commit()
        finally:
            db.close()
    except BaseException:
        pass


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


# ---------- 序列化 ----------

def _tts_conf() -> dict:
    conf = settings_store.load()
    return {
        "provider": conf.get("tts_provider") or tts.DEFAULT_PROVIDER,
        "rate": conf.get("tts_rate") or tts.DEFAULT_RATE,
        "gap_ms": int(conf.get("tts_gap_ms") or tts.DEFAULT_GAP_MS),
        "host": conf.get("tts_voice_host") or tts.DEFAULT_VOICES["host"],
        "expert": conf.get("tts_voice_expert") or tts.DEFAULT_VOICES["expert"],
    }


def _source_missing(v, alive: tuple | None) -> bool | None:
    """来源是否已从库里消失（按 id 精查全表，不受 /options 的条数窗口影响）。"""
    if alive is None:
        return None
    mats, notes = alive
    refs = v.source_refs or []
    ids, pool_notes = [], False
    for r in refs:
        if isinstance(r, dict) and r.get("id") is not None:
            try:
                ids.append(int(r["id"]))
            except (TypeError, ValueError):
                continue
    if not ids:
        return False
    pool_notes = v.source_type == "notes"
    pool = notes if pool_notes else mats
    return any(i not in pool for i in ids)


def _alive_sets(db: Session) -> tuple:
    mats = {i for (i,) in db.query(Material.id).all()}
    notes = {i for (i,) in db.query(Note.id).all()}
    return mats, notes


def video_to_dict(v: Video, db: Session | None = None, *, full: bool = True,
                   alive: tuple | None = None, tts_conf: dict | None = None) -> dict:
    """作品序列化。

    full=False 时**不下发 script / steps / points / shots** —— 这几项是大头
    （一条 3 分钟作品的讲稿 + 几十个步点就是几 KB），列表接口在生成 / 编辑 / 删除后
    都会被重拉一次，体积会随作品数线性放大。列表只给卡片需要的字段。
    """
    if alive is None and db is not None:
        alive = _alive_sets(db)
    conf = tts_conf or _tts_conf()
    steps = v.steps or []
    points = v.points or []
    gate = v.gate_report or None
    d = {
        "id": v.id,
        "title": v.title,
        "source_type": v.source_type,
        "source_refs": v.source_refs or [],
        "skin": v.skin,
        "inner_skin": v.inner_skin or "",
        # 皮肤预设变体（电台浅色/深色各 3 套；空 = 默认套）。下发归一值：
        # 库里若残留非法值，界面显示的应与渲染口径一致（都落回默认套）
        "skin_variant": svc.norm_variant(v.skin, getattr(v, "skin_variant", "") or ""),
        "skin_variant_label": svc.variant_label(v.skin, getattr(v, "skin_variant", "") or ""),
        # 画面署名（电台皮肤上屏的那行字）。空 = 回落作品标题。
        # ⚠️ 同时下发 **effective**（真正会渲出来的字）：前端不必自己再拼一遍
        #    「空则取 title」—— 两处各拼一遍必然漂移成界面与画面不一致。
        "brand_text": svc.norm_brand_text(getattr(v, "brand_text", "")),
        "brand_text_effective": svc.brand_text_for(v),
        "brand_text_max": svc.BRAND_TEXT_MAX,
        "skin_label": svc.skin_label(v),
        "avatar_id": v.avatar_id or "",
        "avatar_info": svc.avatar_info(v.avatar_id or "") if (v.skin or "") == "talking_head" else None,
        "avatar_pos": v.avatar_pos or "right",
        "avatar_pos_label": svc.AVATAR_POS_LABEL.get(v.avatar_pos or "right", ""),
        # 自定义背景（只在解压型皮肤下有意义）。其余皮肤根本不消费它，
        # 下发只会让前端多一条要维护的判据。
        # ⚠️ 绑定的背景已被删掉时 `bg_info.exists=False`：前端必须**如实说明**，
        #    不能让它看起来像「没选过」——否则用户重新选一张就悄悄改了这条作品的画面。
        "bg_id": getattr(v, "bg_id", "") or "",
        "bg_info": bg_svc.bg_info(getattr(v, "bg_id", "") or "")
        if (v.skin or "") in svc.BG_SKINS else None,
        "audio_source": v.audio_source,
        "voice_map": dict(v.voice_map or {}),
        # 背景音乐（仅「本片单独生成」音轨消费；复用播客时音轨里已含播客自己的 BGM，
        # 字段照实下发但前端不显示选择行）。
        # ⚠️ 与播客同口径：素材被删后 bgm_id 保原值（不静默改作品），
        #    bgm_name 回落到记住的曲名，bgm_missing=True 让界面如实说「已失效」。
        "bgm_id": getattr(v, "bgm_id", "") or "",
        "bgm_volume": v.bgm_volume if getattr(v, "bgm_volume", None) is not None
        else BGM_VOLUME_DEFAULT,
        "bgm_name": bgm_svc.track_name(getattr(v, "bgm_id", "") or "")
        or (getattr(v, "bgm_label", "") or ""),
        "bgm_missing": bool((getattr(v, "bgm_id", "") or "").startswith("u-"))
        and not bgm_svc.exists(getattr(v, "bgm_id", "") or ""),
        "podcast_id": v.podcast_id,
        "audio_name": v.audio_name or "",
        "audio_bytes": v.audio_bytes or 0,
        "duration_ms": v.duration_ms or 0,
        "style": v.style,
        "gen_mode": v.gen_mode,
        "target_minutes": v.target_minutes,
        "instruction": v.instruction or "",
        "status": v.status,
        "progress": v.progress or 0.0,
        "error": v.error or "",
        "video_name": v.video_name or "",
        "video_bytes": v.video_bytes or 0,
        "width": v.width or 0,
        "height": v.height or 0,
        "fps": v.fps or 0,
        "frame_count": v.frame_count or 0,
        "render_seconds": float(v.render_seconds or 0.0),
        "poster_name": v.poster_name or "",
        "has_video": bool(v.video_name) and svc.video_abspath(v.id, v.video_name).exists(),
        # 渲染队列的实时态（内存；不在队列里就是 0 / 无）
        "render_position": _render_pos(v.id),
        "render_pct": _render_pct(v.id),
        "render_stage": _render_stage(v.id),
        "has_audio": _audio_exists(v),
        "has_timeline": svc.script_has_timeline(v.script or []),
        "segment_count": len([s for s in (v.script or []) if (s.get("text") or "").strip()]),
        "shot_count": len(v.shots or []),
        "step_count": len(steps),
        "point_count": len(points),
        "event_count": (gate or {}).get("events", 0),
        "gate_passed": (gate or {}).get("passed"),
        "gate_max_still_ms": (gate or {}).get("max_still_ms"),
        "gate_hit_rate": (gate or {}).get("locate_hit_rate"),
        # 跳过闸门（PRD 3.9.7）：连续失败次数决定「跳过」按钮是否可用；
        # gate_skipped 标注当前这份成片是不是跳过自检渲染的。
        "gate_fail_count": int(v.gate_fail_count or 0),
        "gate_skipped": bool(v.gate_skipped),
        # 音轨是否为「上一版文案」的产物（复用模式看播客自己，新建模式看本片讲稿）
        "audio_stale": svc.audio_stale(v, conf, v.voice_map or {}),
        # 派生资源的同步状态（PRD 3.11.4）：空列表 = 成片与当前配置一致
        "stale_kinds": svc.stale_kinds(db, v) if db is not None else [],
        "source_missing": _source_missing(v, alive),
        "rendered_at": v.rendered_at.isoformat() if v.rendered_at else None,
        "created_at": v.created_at.isoformat() if v.created_at else None,
        "updated_at": v.updated_at.isoformat() if v.updated_at else None,
    }
    d["stale_labels"] = [svc.STALE_LABEL.get(k, k) for k in d["stale_kinds"]]
    if full:
        d["script"] = v.script or []
        d["shots"] = v.shots or []
        d["steps"] = steps
        d["points"] = points
        d["gate_report"] = gate
    return d


def _audio_exists(v: Video) -> bool:
    try:
        if v.audio_source == "podcast":
            p = _get_podcast(v)
            if not p or not p.audio_name:
                return False
            return pod_svc.audio_abspath(p.id, p.audio_name).exists()
        if not v.audio_name:
            return False
        return svc.video_abspath(v.id, v.audio_name).exists()
    except BaseException:
        return False


def _get_podcast(v: Video, db: Session | None = None) -> Podcast | None:
    if not v.podcast_id:
        return None
    if db is not None:
        return db.get(Podcast, v.podcast_id)
    s = SessionLocal()
    try:
        return s.get(Podcast, v.podcast_id)
    finally:
        s.close()


def _audio_abspath(v: Video):
    """音轨绝对路径（复用模式指向**播客目录**，不复制文件 —— PRD 验收 #2）。"""
    if v.audio_source == "podcast":
        p = _get_podcast(v)
        if not p or not p.audio_name:
            return None
        return pod_svc.audio_abspath(p.id, p.audio_name)
    if not v.audio_name:
        return None
    return svc.video_abspath(v.id, v.audio_name)


def _render_ready() -> bool:
    """渲染器是否可用（阶段 2）。

    ⚠️ 光「模块存在」不够：还必须真有 Chromium 可执行文件 + PyAV。
       否则用户点下渲染、等几十秒才收到一句失败 —— 不如在按钮上直接禁用并说明原因
       （PRD 2.2.7：绝不给一个点了注定失败的按钮）。
    ⚠️ 能力探测（而不是路由里写死开关）：阶段 2 只新增 `services/renderer.py`
       即可点亮，不必回来改路由与前端契约。
    """
    try:
        from ..services import renderer
        if not renderer.find_chromium():
            return False
        import av  # noqa: F401
        return True
    except BaseException:
        return False


def _render_reason() -> str:
    """渲染器不可用时的原因（可用时返回空串）。前端直接拿去显示。"""
    try:
        from ..services import renderer
    except BaseException as e:
        return f"渲染器未就绪（{type(e).__name__}）"
    try:
        if not renderer.find_chromium():
            return "找不到可用的 Chromium，无法出片（渲染器需要本机浏览器内核）"
    except BaseException:
        return "无法定位 Chromium"
    try:
        import av  # noqa: F401
    except BaseException:
        return "缺少视频编码组件 PyAV，无法出片"
    return ""


def _llm_ready() -> bool:
    """LLM 是否已配置 —— 必须与视频生成**真正走的链路**同口径。

    ⚠️ 视频的讲稿 / 结构图 / 锚点三个生成函数都走 `summary_chat`
       （见 llm.py 的「视频不重造链路」注释）→ 这里问的必须是 `summary`，
       与 `drill.py::llm_ready`（陪练）同一口径。
    ⚠️ `resolve_usage` 定义在 **settings_store**，不在 llm 里 ——
       写成 `llm.resolve_usage(...)` 会抛 AttributeError 被 `except` 吞掉 →
       `llm_ready` **恒为 False** → 前端永远显示「未配置大模型」并禁用生成按钮。
       这是一种**静默失效**（用户看到的是"按钮点不了"，不是报错），已实测捕获。
    """
    try:
        return settings_store.resolve_usage("summary") is not None
    except BaseException:
        return False


def _with_used_by(avatars: list, db: Session) -> list:
    """给形象清单补 used_by（有几个作品正在引用）。

    ⚠️ 一条 group by 拿全部计数：逐个形象查 DB 会变成 N 次往返（形象多时明显）。
    ⚠️ 统计失败不能带挂整个 options —— 补 0 兜底（used_by 只是提示信息，不是安全判据）。
    """
    cnt = {}
    try:
        rows = (db.query(Video.avatar_id, func.count(Video.id))
                .filter(Video.avatar_id.isnot(None), Video.avatar_id != "")
                .group_by(Video.avatar_id).all())
        cnt = {str(a): int(n) for a, n in rows if a}
    except BaseException:
        cnt = {}
    for it in avatars:
        it["used_by"] = cnt.get(it["id"], 0)
    return avatars


def _bg_options(db: Session) -> list:
    """可选背景清单（内置 + 用户已上传），带 used_by。

    ⚠️ 必须是**全量**：只给内置的话，上传过的背景离开页面后就再也选不到
       （作品还引用着，界面里却找不到）—— 与 `list_avatars` 同一条教训。
    ⚠️ used_by 让「删除」按钮能如实告知「有几个作品还在用它」；统计失败补 0，
       不带挂整个 options（它只是提示信息，不是安全判据）。
    """
    lib = bg_svc.list_backgrounds()
    items = [*lib["builtin"], *lib["mine"]]
    cnt = {}
    try:
        rows = (db.query(Video.bg_id, func.count(Video.id))
                .filter(Video.bg_id.isnot(None), Video.bg_id != "")
                .group_by(Video.bg_id).all())
        cnt = {str(b): int(n) for b, n in rows if b}
    except BaseException:
        cnt = {}
    for it in items:
        it["used_by"] = cnt.get(it["id"], 0)
    return items


# ---------- 选项（页面初始化） ----------

@router.get("/options")
def options(db: Session = Depends(get_db)):
    """可选来源 / 播客（仅有音频的）/ 音色 / 时长档 / 视觉方案 / 形象 / 语音配置。"""
    mats = (db.query(Material).filter(Material.parsed_status == "success")
            .order_by(Material.created_at.desc()).limit(300).all())
    notes = db.query(Note).order_by(Note.updated_at.desc()).limit(300).all()
    # ⚠️ 只列出**已合成音频**的播客（PRD 验收 #1）：没音频的选了必然在生成时被拦，
    #    摆在列表里只会让用户白点一次。
    pods = (db.query(Podcast).filter(Podcast.audio_name != "")
            .order_by(Podcast.updated_at.desc()).limit(200).all())
    conf = _tts_conf()

    pod_items = []
    for p in pods:
        exists = pod_svc.audio_abspath(p.id, p.audio_name).exists()
        if not exists:
            continue          # 文件丢了的也列出来 = 让用户选中后再报错，体验更差
        pod_items.append({
            "id": p.id, "title": p.title, "duration_sec": p.duration_sec or 0,
            "audio_bytes": p.audio_bytes or 0, "style": p.style,
            "gen_mode": getattr(p, "gen_mode", "") or "",
            "segment_count": len([s for s in (p.script or []) if (s.get("text") or "").strip()]),
            "has_script": bool([s for s in (p.script or []) if (s.get("text") or "").strip()]),
            "audio_stale": pod_svc.audio_is_stale(p, rate=conf["rate"], gap_ms=conf["gap_ms"],
                                                  provider=conf["provider"]),
            "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        })

    # 视觉方案示例图：**清单立刻返回**（有多少给多少），缺图时在后台线程里现渲 ——
    # 绝不在这里等：起一次 Chromium + 渲 7 张要 8–15 秒，等它必然把整个视频页拖成超时。
    # 首次调用会起一个后台线程；渲染完成后下次打开页面（或调 /skin-previews/list）就能看到。
    skin_previews_ensure()
    previews = preview_svc.list_previews()["items"]

    return {
        "source_types": [{"key": k, "label": v} for k, v in SOURCE_TYPES.items()],
        "materials": [{"id": m.id, "title": m.title, "format": m.format,
                       "page_count": m.page_count or 0} for m in mats],
        "notes": [{"id": n.id, "title": n.title, "chars": len(n.content or "")} for n in notes],
        "podcasts": pod_items,
        "voices": tts.list_voices(conf["provider"] if "provider" in conf else tts.DEFAULT_PROVIDER),
        "length_presets": [{"minutes": k, "label": v["label"], "segments": v["segments"]}
                           for k, v in pod_svc.LENGTH_PRESETS.items()],
        "skins": [
            {"key": "source", "label": "材料原件", "desc": "画面里是你的资料，讲到哪就框住原文哪一处",
             "need": "需要材料有文字层或可识别的页图", "group": "info"},
            {"key": "graph", "label": "结构图", "desc": "结构图逐层展开，右侧要点栏逐条点亮",
             "need": "任何来源都能做", "group": "info"},
            {"key": "graph_paper", "label": "结构图 · 纸感",
             "desc": "暖白纸张 + 纵向知识条目，图标与手绘笔触逐条显现",
             "need": "任何来源都能做", "group": "info"},
            {"key": "graph_clean", "label": "结构图 · 清透",
             "desc": "浅底白卡 + 紫主色：树状 / 紫表头表格 / 时间轴按版式逐条显现",
             "need": "任何来源都能做", "group": "info"},
            # ⚠️⚠️ 「数字人口播」（talking_head）已**暂停入口**（2026-10-14，产品决定：
            #    方案重想好之后再启动研发与规划）—— 用户暂时不能再新建 / 改选这个视觉风格。
            #    渲染链路（stage.py / stage_tpl.html / video.py）、已有作品播放与重新生成
            #    全部保留；恢复时把下面两行加回本列表，并**同步恢复 precheck 处**（在
            #    precheck 的 skins 列表里搜 talking_head 可见同款注释，两处必须一起动）。
            # {"key": "talking_head", "label": "数字人口播", "desc": "有讲述者在场，另一侧同步承载内容层",
            #  "need": "需再选一个内容层（材料原件 / 结构图）", "group": "info"},
            {"key": "radio", "label": "电台 · 浅色", "desc": "米色底 + 线条插画轮换 + 人声波形，边听边看",
             "need": "任何来源都能做", "group": "relax"},
            {"key": "night", "label": "电台 · 深色", "desc": "夜景插画 + 旋转唱片 + 人声波形，适合睡前",
             "need": "任何来源都能做", "group": "relax"},
            # ⚠️⚠️⚠️ "氛围"（ambient）已**暂停入口**（2026-10-14，产品决定）
            #    —— 用户暂时不能再新建 / 改选这个视觉风格
            #    （与「数字人口播」talking_head 同款处理）。
            #    渲染链路（stage.py / stage_tpl_ambient.html / video.py）、
            #    已有作品播放与重新生成全部保留；
            #    恢复时把下面两行加回本列表，并**同步恢复 precheck 处**
            #    （在 precheck 的 skins 列表里搜 ambient 可见同款注释，
            #    两处必须一起动）。
            # {"key": "ambient", "label": "氛围", "desc": "光晕缓缓流动，可叠加材料原件柔化浮现",
            #  "need": "任何来源都能做", "group": "relax"},
        ],
        # 视觉方案示例图（`skin` → 图）：让用户在选之前就看见风格。
        # ⚠️ 可能是**种子图**（随包兜底）而不是当前模板的现渲图 —— 前端不区分地显示，
        #    `source` 字段只用于排查。
        "previews": previews,
        "inner_skins": [{"key": "source", "label": "材料原件"}, {"key": "graph", "label": "结构图"}],
        # 电台浅色/深色的预设变体（各 3 套；swatch/ink 仅供前端画色板胶囊，
        # 渲染真值在舞台模板的 [data-variant] 覆盖里）
        "skin_variants": {k: list(vv) for k, vv in svc.SKIN_VARIANTS.items()},
        # ambient 的「内容层」白名单（含 none 纯氛围；与口播的 INNER_SKINS 是两套，绝不可混）
        "ambient_inner_skins": [{"key": "none", "label": "纯氛围"}, {"key": "source", "label": "材料原件"}],
        # ⚠️ 必须是 list_avatars()（预置 + 已上传），不能只给 BUILTIN_AVATARS：
        #    只给预置的话，上传过的形象离开页面后就再也选不到了
        # ⚠️ 带 used_by：删除形象前要能如实告诉用户「有几个作品还在用它」，
        #    否则删完才发现作品变了脸。
        "avatars": _with_used_by(svc.list_avatars(), db),
        # 自定义背景（解压型皮肤）：全量清单 + 哪些皮肤支持（前端据此决定入口出不出现）
        "backgrounds": _bg_options(db),
        "bg_skins": list(svc.BG_SKINS),
        "avatar_positions": [{"key": k, "label": v} for k, v in svc.AVATAR_POS_LABEL.items()],
        "output": {"width": svc.OUT_W, "height": svc.OUT_H, "fps": svc.OUT_FPS},
        "tts": conf,
        "gen_modes": [
            {"key": "fast", "label": "快速（素材直接成稿）"},
            {"key": "two_step", "label": "两步（先提炼再成稿）"},
            {"key": "verbatim", "label": "原文朗读（不改写、不丢字）"},
        ],
        "hints": {
            "instruction_max": INSTRUCTION_MAX,
            "avatar_upload": "请上传竖版图片（宽高比 ≥ 3:4，短边 ≥ 512px）。请勿上传他人肖像。",
            "bg_upload": "请上传横版或竖版图片（短边 ≥ 720px）。请确认你有权使用这张图。",
            "render_concurrency": RENDER_CONCURRENCY,
        },
        # 能力开关：渲染器未接入时前端必须禁用「开始渲染」并说明原因，
        # 不能给一个点了必然失败的按钮。
        "render_ready": _render_ready(),
        "render_reason": _render_reason(),
        "llm_ready": _llm_ready(),
    }


# ---------- 列表 / 详情 ----------

@router.get("")
def list_videos(db: Session = Depends(get_db)):
    rows = db.query(Video).order_by(Video.updated_at.desc()).all()
    alive = _alive_sets(db)
    conf = _tts_conf()
    return [video_to_dict(v, db, full=False, alive=alive, tts_conf=conf) for v in rows]


@router.get("/{vid}")
def get_video(vid: int, db: Session = Depends(get_db)):
    v = db.get(Video, vid)
    if not v:
        raise HTTPException(404, "视频作品不存在或已删除")
    return video_to_dict(v, db)


# ---------- 创建 / 更新 / 删除 ----------

class CreateReq(BaseModel):
    title: str = ""
    skin: str = "source"
    inner_skin: str = ""
    skin_variant: str = ""          # 电台浅色/深色的预设变体（空 = 默认套）
    # 画面署名（电台浅色/深色画面底部那行字）。空 = 回落作品标题（见 svc.brand_text_for）
    brand_text: str = ""
    avatar_id: str = ""
    avatar_pos: str = "right"
    bg_id: str = ""
    audio_source: str = "podcast"
    podcast_id: int | None = None
    source_type: str = "article"
    ref_ids: list[int] | None = None
    highlight_colors: list[str] | None = None
    pasted_text: str = ""
    voice_map: dict | None = None
    # 背景音乐（仅 self 音轨生效）：bgm_id="" 不加；volume 单位 dB（钳制 -40~-3）
    bgm_id: str = ""
    bgm_volume: int | None = None
    style: str = "solo"
    gen_mode: str = "fast"
    target_minutes: int = 3
    instruction: str = ""


def _norm_skin(skin: str, inner: str) -> tuple:
    s = (skin or "source").strip()
    if s not in svc.SKINS:
        s = "source"
    i = ""
    if s == "talking_head":
        i = (inner or "").strip()
        if i not in svc.INNER_SKINS:
            # ⚠️⚠️ 绝不落库空内容层：那是个**永远生成不出来**的死状态
            #    （口播是叠加层，没有内容层就没画面）。兜底 graph —— 由讲稿产出，
            #    任何来源都能做（与 `resolve_inner` 同一口径）。
            i = svc.INNER_DEFAULT
    elif s == "ambient":
        i = (inner or "").strip()
        if i not in svc.AMBIENT_INNERS:
            # ambient 的内容层**可空**：none（纯氛围）是一等公民，兜底 none（与 resolve_inner 同口径）。
            i = svc.AMBIENT_INNER_DEFAULT
    # radio / night / graph_paper / graph_clean 无 inner（纯陪伴画面 / 结构图自成一屏）→ i 保持 ""
    return s, i


def _norm_voice_map(vm) -> dict:
    """音色映射归一化：只保留**合法音色 id**（白名单，与 tts.VOICE_CATALOG 同源）。

    空 dict 表示「跟随设置页」。非法 id 一律丢弃 —— 生成时 `tts.resolve_voice` 虽会兜底，
    但库里留着垃圾会让界面显示出根本不存在的音色（且选中作品时回填成乱码）。
    """
    src = vm if isinstance(vm, dict) else {}
    host = str(src.get("host") or "").strip()
    return {"host": host} if host in tts.VOICE_IDS else {}


def _norm_bg_id(bid) -> str:
    """背景 id 归一化：只做**格式**校验（白名单正则），不查文件是否存在。

    ⚠️⚠️ **故意不**把「文件已不在」的 id 清空 —— 与 `avatar_id` 同一纪律：
       背景被删掉后，引用它的作品 `bg_id` 保原值，渲染时由 `stage.py` 回落到
       皮肤自带画面，界面用 `bg_info.exists=False` 如实告知。
       清空等于**静默改作品**：用户删了一张背景，一批作品的画面被悄悄换掉，
       而他对那些作品并没有做任何操作。
    ⚠️ 形态不合法的（空 / 非 `u-` 前缀 / 含路径分隔符或 `:`）一律归一为 ""：
       这种值不可能来自本系统前端，落库只会变成一个害人的死 id。
    """
    return (bid or "").strip()[:64] if bg_svc.valid_id(bid) else ""


def _remember_bgm(v: Video, bid: str) -> None:
    """记住 BGM 选择 + 曲名（与 podcasts._remember_bgm 同口径）。

    ⚠️ 素材被删后 bgm_id 保原值（绝不静默改作品）；bgm_label 记下最后成功选择
       时的曲名，界面才能如实显示「《XX》（已失效）」而不是一个死 id。
    """
    same = bid == (v.bgm_id or "")
    v.bgm_id = bid
    if not (same and (v.bgm_label or "")):
        v.bgm_label = bgm_svc.track_name(bid) if bid else ""


def _norm_bgm_req(bid, volume) -> tuple:
    """BGM 请求参数归一化：空 = 不加；非空必须在素材库真实存在（内置/上传都算）。

    ⚠️ 与 `_norm_bg_id` 不同：这里**查存在性**。背景图丢了渲染能回落皮肤自带画面，
       而 BGM 丢了音轨混不出来 —— 生成时才报不如在保存时就拦（与播客同口径）。
    """
    bid = (bid or "").strip()
    if bid and not bgm_svc.exists(bid):
        raise HTTPException(400, "所选背景音乐不存在，请重新选择")
    vol = max(BGM_VOLUME_MIN, min(BGM_VOLUME_MAX, int(volume
              if volume is not None else BGM_VOLUME_DEFAULT)))
    return bid, vol


@router.post("")
def create_video(req: CreateReq, db: Session = Depends(get_db)):
    """创建作品。⚠️ PRD 4.2：`video_create` 连**被拦截的尝试**一起记
    （转化漏斗的分母），所以真正的创建逻辑在 `_create_video`，这里只管成败都记账。"""
    try:
        return _create_video(req, db)
    except HTTPException:
        _track("video_create", 0, detail={"blocked": 1})
        raise


def _create_video(req: CreateReq, db: Session = Depends(get_db)):
    """创建作品（status=draft）。标题留空则按「《来源名》讲解视频」生成后写入。"""
    skin, inner = _norm_skin(req.skin, req.inner_skin)
    audio_source = "podcast" if (req.audio_source or "podcast") == "podcast" else "self"

    refs: list = []
    title = ""
    if audio_source == "podcast":
        if not req.podcast_id:
            raise HTTPException(400, "请选择一条已合成音频的播客")
        p = db.get(Podcast, req.podcast_id)
        if not p:
            raise HTTPException(404, "所选播客不存在")
        if not (p.audio_name or ""):
            raise HTTPException(400, "这条播客还没有音频，请先生成音频")
        if not pod_svc.audio_abspath(p.id, p.audio_name).exists():
            raise HTTPException(400, "音轨文件已丢失，可去播客页重新生成")
        if not [s for s in (p.script or []) if (s.get("text") or "").strip()]:
            raise HTTPException(400, "这条播客还没有讲稿")
        refs = list(p.source_refs or [])
        title = p.title
    else:
        st = req.source_type if req.source_type in SOURCE_TYPES else "article"
        # 素材抽取在**创建时**就跑一次：既拿到来源快照，也能把「选错来源」挡在
        # 创建这一步（而不是等生成时才报错）。抽样口径与最终生成一致。
        try:
            t, _text, refs = pod_svc.collect_source(
                db, st, list(req.ref_ids or []),
                highlight_colors=req.highlight_colors,
                pasted_text=req.pasted_text,
                verbatim=(req.gen_mode == "verbatim"))
            title = t
        except HTTPException:
            raise
        except ValueError as e:
            raise HTTPException(400, str(e))

    name = (req.title or "").strip()[:50] or f"《{title or '未命名'}》讲解视频"
    # 背景音乐：保存时就拦「素材不存在」（生成时才报太迟）。仅 self 音轨消费它。
    bgm_id, bgm_volume = _norm_bgm_req(req.bgm_id, req.bgm_volume)
    v = Video(
        title=name,
        source_type=req.source_type if audio_source == "self" else "article",
        source_refs=refs,
        skin=skin, inner_skin=inner,
        # 皮肤变体：按**归一后**的皮肤校验白名单（非法值归 "" = 默认套，不报错）
        skin_variant=svc.norm_variant(skin, req.skin_variant),
        # 画面署名：**不按皮肤清空**（口径见 svc.norm_brand_text）—— 先填了字再换皮肤，
        # 字要留着；换回电台时它就还在，不用重填。
        brand_text=svc.norm_brand_text(req.brand_text),
        avatar_id=(req.avatar_id or "").strip()[:64] if skin == "talking_head" else "",
        avatar_pos=(req.avatar_pos if req.avatar_pos in svc.AVATAR_POS else "right"),
        # 自定义背景：只做**格式**校验，**不清空**「文件已不在」的 id ——
        # 与 avatar_id 同一纪律（不静默改作品；渲染时由 stage 回落皮肤自带画面）。
        bg_id=_norm_bg_id(req.bg_id),
        audio_source=audio_source,
        voice_map=_norm_voice_map(req.voice_map),
        bgm_id=bgm_id,
        bgm_volume=bgm_volume,
        bgm_label=bgm_svc.track_name(bgm_id) if bgm_id else "",
        podcast_id=req.podcast_id if audio_source == "podcast" else None,
        # ⚠️ 播客来源在**创建时**就把音轨元信息带上：否则卡片 / 详情 / 预检一律显示
        #    0 秒，要等用户点一次「生成」才被补上（用户会以为这个作品是坏的）。
        audio_bytes=int(p.audio_bytes or 0) if audio_source == "podcast" else 0,
        duration_ms=int(p.duration_sec or 0) * 1000 if audio_source == "podcast" else 0,
        audio_sig=(p.audio_sig or "") if audio_source == "podcast" else "",
        style=req.style if req.style in ("dialogue", "solo") else "solo",
        gen_mode=req.gen_mode if req.gen_mode in ("two_step", "fast", "verbatim") else "fast",
        # ⚠️ 钳制口径必须与 PUT 一致：否则 POST 传 999 / -5 会原样落库 —— 界面显示
        #    999 分钟、实际按 3 分钟档生成（video_plan 只认 3/5/10/20，其余静默退档）。
        target_minutes=max(1, min(60, int(req.target_minutes or 3))),
        instruction=(req.instruction or "")[:INSTRUCTION_MAX],
        status="draft",
    )
    db.add(v)
    db.commit()
    db.refresh(v)
    _track("video_create", v.id)
    return video_to_dict(v, db)


class UpdateReq(BaseModel):
    title: str | None = None
    skin: str | None = None
    inner_skin: str | None = None
    skin_variant: str | None = None
    # 画面署名：传 "" = 改回「跟随作品标题」；None = 不动（与 bgm_id 同款显式语义）
    brand_text: str | None = None
    avatar_id: str | None = None
    avatar_pos: str | None = None
    bg_id: str | None = None
    instruction: str | None = None
    # 视觉参数变更后**不删除旧成片**，只让它被标注为「对应上一个版本的配置」（PRD 3.11.4）
    podcast_id: int | None = None
    source_type: str | None = None
    ref_ids: list[int] | None = None
    highlight_colors: list[str] | None = None
    pasted_text: str = ""
    gen_mode: str | None = None
    target_minutes: int | None = None
    style: str | None = None
    # 本片音色（作品级）：{"host": 音色id}。传 {} = 清空选择、回到跟随设置页。
    voice_map: dict | None = None
    # 背景音乐：传 "" = 改为不加；None = 不动（与 voice_map 的「显式才改」同款语义）
    bgm_id: str | None = None
    bgm_volume: int | None = None


@router.put("/{vid}")
def update_video(vid: int, req: UpdateReq, db: Session = Depends(get_db)):
    v = db.get(Video, vid)
    if not v:
        raise HTTPException(404, "视频作品不存在或已删除")
    with _Exclusive(vid):
        db.refresh(v)
        if req.title is not None and req.title.strip():
            v.title = req.title.strip()[:50]
        # 画面署名："" 是**有意义的值**（改回跟随作品标题），所以判 None 而不是判真值。
        if req.brand_text is not None:
            v.brand_text = svc.norm_brand_text(req.brand_text)
        if req.instruction is not None:
            v.instruction = (req.instruction or "")[:INSTRUCTION_MAX]
        if req.avatar_pos is not None and req.avatar_pos in svc.AVATAR_POS:
            v.avatar_pos = req.avatar_pos
        if req.avatar_id is not None:
            v.avatar_id = (req.avatar_id or "").strip()[:64]
        if req.bg_id is not None:
            v.bg_id = _norm_bg_id(req.bg_id)
        if req.skin is not None:
            s, i = _norm_skin(req.skin, req.inner_skin if req.inner_skin is not None
                              else v.inner_skin)
            v.skin, v.inner_skin = s, i
            # 换了皮肤，旧变体大概率不在新皮肤的白名单里 → 归一（多数情况回到默认套）
            v.skin_variant = svc.norm_variant(s, v.skin_variant)
        elif req.inner_skin is not None:
            s, i = _norm_skin(v.skin, req.inner_skin)
            v.skin, v.inner_skin = s, i
        if req.skin_variant is not None:
            v.skin_variant = svc.norm_variant(v.skin, req.skin_variant)
        if req.style is not None and req.style in ("dialogue", "solo"):
            v.style = req.style
        if req.voice_map is not None:
            v.voice_map = _norm_voice_map(req.voice_map)
        if req.bgm_id is not None or req.bgm_volume is not None:
            bid, vol = _norm_bgm_req(
                req.bgm_id if req.bgm_id is not None else (v.bgm_id or ""),
                req.bgm_volume if req.bgm_volume is not None else v.bgm_volume)
            _remember_bgm(v, bid)
            v.bgm_volume = vol
        if req.gen_mode is not None and req.gen_mode in ("two_step", "fast", "verbatim"):
            v.gen_mode = req.gen_mode
        if req.target_minutes is not None:
            v.target_minutes = max(1, min(60, int(req.target_minutes)))
        if req.podcast_id is not None or req.source_type is not None or req.ref_ids is not None:
            _apply_source(db, v, req)
        db.commit()
        db.refresh(v)
    return video_to_dict(v, db)


def _apply_source(db, v: Video, req: UpdateReq) -> None:
    """换来源：重跑一次素材抽取，更新来源快照（**不动讲稿与步点**）。

    ⚠️ 换来源后讲稿与视觉步点必然不再对应 → 标记为「需重新生成」由用户显式触发，
       这里不做隐式重生成（一次 LLM + 定位的成本不该藏在一次 PUT 背后）。
    """
    if req.podcast_id is not None:
        p = db.get(Podcast, req.podcast_id)
        if not p:
            raise HTTPException(404, "所选播客不存在")
        if not (p.audio_name or "") or not pod_svc.audio_abspath(p.id, p.audio_name).exists():
            raise HTTPException(400, "这条播客还没有音频，请先生成音频")
        v.audio_source, v.podcast_id, v.audio_name = "podcast", p.id, ""
        v.audio_bytes = int(p.audio_bytes or 0)
        v.duration_ms = int(p.duration_sec or 0) * 1000
        v.audio_sig = p.audio_sig or ""
        v.source_refs = list(p.source_refs or [])
        return
    if req.source_type is not None or req.ref_ids is not None:
        # ⚠️ 只带 ref_ids 不带 source_type 时**沿用当前来源类型**：否则下面两个分支
        #    都进不去 → 接口返回 200 但什么都没做（调用方以为换来源已经生效）。
        if req.source_type is None:
            st = v.source_type if v.source_type in SOURCE_TYPES else "article"
        else:
            st = req.source_type if req.source_type in SOURCE_TYPES else "article"
        try:
            _t, _x, refs = pod_svc.collect_source(
                db, st, list(req.ref_ids or []),
                highlight_colors=req.highlight_colors, pasted_text=req.pasted_text,
                verbatim=(v.gen_mode == "verbatim"))
        except ValueError as e:
            raise HTTPException(400, str(e))
        v.audio_source, v.podcast_id = "self", None
        v.source_type, v.source_refs = st, refs
        # ⚠️ 换来源 → 旧音轨必然不再对应：元信息要一起清。只换 source_* 会留下旧的
        #    duration_ms / audio_sig（实测残留 12000ms），让 UI 时长与磁盘预检按
        #    **旧**音轨算。
        v.audio_name, v.audio_bytes = "", 0
        v.audio_sig, v.duration_ms = "", 0


@router.delete("/{vid}")
def delete_video(vid: int, db: Session = Depends(get_db)):
    v = db.get(Video, vid)
    if not v:
        raise HTTPException(404, "视频作品不存在或已删除")
    with _Exclusive(vid):
        # 先删数据库记录、再清文件：文件清理失败不该让用户连记录都删不掉。
        # ⚠️ 删除作品**不影响**其来源材料 / 笔记 / 播客（PRD 2.3.2）。
        db.delete(v)
        db.commit()
        svc.remove_video_files(vid)
    _forget_video_lock(vid)
    _forget_video_jobs(vid)
    return {"ok": True}


class ScriptReq(BaseModel):
    script: list


@router.put("/{vid}/script")
def save_script(vid: int, req: ScriptReq, db: Session = Depends(get_db)):
    """逐句编辑讲稿。

    ⚠️ **不触发音频重新合成**（PRD 3.4.5 约束 1）：改讲稿 → 只重新定位该句 →
       提示「音轨已与讲稿不一致」。是否重合成由用户显式点。
    ⚠️ 编辑后原闸门报告**作废**（必须重跑闸门才能再入队）。
    """
    v = db.get(Video, vid)
    if not v:
        raise HTTPException(404, "视频作品不存在或已删除")
    with _Exclusive(vid):
        db.refresh(v)
        before = svc.script_text_sig(v.script or [])
        v.script = svc.normalize_script(req.script, v.script or [])
        changed = svc.script_text_sig(v.script) != before
        if changed:
            v.gate_report = None
            if v.status in ("gate_check", "finished"):
                v.status = "gate_check" if v.status == "gate_check" else "finished"
        db.commit()
        db.refresh(v)
    d = video_to_dict(v, db)
    d["text_changed"] = changed
    d["need_relocate"] = changed
    return d


# ---------- 兼容性预检（PRD 3.2） ----------

def _first_material_id(v: Video) -> int | None:
    for r in (v.source_refs or []):
        if isinstance(r, dict) and r.get("type") == "material" and r.get("id") is not None:
            try:
                return int(r["id"])
            except (TypeError, ValueError):
                continue
    return None


@router.get("/{vid}/precheck")
def precheck(vid: int, db: Session = Depends(get_db)):
    """视觉方案的**兼容性预检**（硬规则，不是建议 —— PRD 3.2.5）。

    返回每个方案的可用性与原因 + 预计密度。UI 据此置灰并说明原因，
    绝不能给用户一个「注定做不出来」的选项。
    """
    v = db.get(Video, vid)
    if not v:
        raise HTTPException(404, "视频作品不存在或已删除")

    mid = _first_material_id(v)
    loc_info = {"ok": False, "kind": "", "reason": "来源不是材料，无法定位原件位置", "pages": 0}
    if mid is not None:
        loc_info = locate_svc.availability(db, mid)

    dur = int(v.duration_ms or 0)
    segs = len([s for s in (v.script or []) if (s.get("text") or "").strip()])
    plan = svc.video_plan(int(v.target_minutes or 3))
    est_segs = segs or plan["segments"]
    est_events = est_segs * 3           # 经验值：每句 1–4 个 anchor，均值约 3
    est_shots = max(svc.MIN_SHOTS, min(svc.MAX_SHOTS,
                                       int(round(dur / (svc.SHOT_SECONDS * 1000))) if dur else 3))
    ok_disk, free, need = svc.disk_ok(dur, getattr(v, "skin", ""))

    skins = [
        {"key": "source", "ok": bool(loc_info["ok"]), "reason":
            ("本材料 " + (f"{loc_info['pages']} 页，" if loc_info["pages"] else "") + loc_info["reason"])
            if loc_info["ok"] else (loc_info["reason"] + "，建议改用结构图"),
         "pages": loc_info["pages"], "kind": loc_info["kind"]},
        {"key": "graph", "ok": True, "reason": "结构图由讲稿产出，任何来源都能做"},
        {"key": "graph_paper", "ok": True,
         "reason": "纸感结构图与结构图同源（暖白纸张 + 纵向知识条目），任何来源都能做"},
        {"key": "graph_clean", "ok": True,
         "reason": "清透结构图与结构图同源（浅底白卡 + 紫主色信息图），任何来源都能做"},
        # ⚠️⚠️ 「数字人口播」（talking_head）已**暂停入口**（2026-10-14，产品决定：
        #    方案重想好之后再启动研发与规划）—— 与 /options 的 skins 列表同步注释
        #    （本文件 491 行附近），恢复时两处必须一起加回。
        # {"key": "talking_head", "ok": bool(loc_info["ok"]), "reason":
        #     "需要先选一个内容层（材料原件 / 结构图）" if not loc_info["ok"]
        #     else "可叠加在任一内容层之上；口型为**示意动画**，非唇形同步",
        #  "inner_ok": {"source": bool(loc_info["ok"]), "graph": True}},
        {"key": "radio", "ok": True, "reason": "浅色电台：线条插画 + 人声波形，任何来源都能做"},
        {"key": "night", "ok": True, "reason": "深色电台：夜景插画 + 旋转唱片 + 人声波形，任何来源都能做"},
        # ⚠️⚠️⚠️ "氛围"（ambient）已**暂停入口**（2026-10-14，产品决定）
        #    —— 与 /options 的 skins 列表同步注释（本文件 /options 列表内），
        #    恢复时两处必须一起加回。
        # {"key": "ambient", "ok": True, "reason":
        #     ("氛围：光晕打底，可叠加「材料原件」内容层" if loc_info["ok"]
        #      else "氛围：光晕打底（这份来源做不了「材料原件」内容层）"),
        #  "inner_ok": {"none": True, "source": bool(loc_info["ok"])}},
    ]
    return {
        "locate": loc_info,
        "skins": skins,
        "estimate": {
            "segments": est_segs, "events": est_events, "shots": est_shots,
            "duration_ms": dur,
            "longest_still_estimate_ms": int(round(dur / max(1, est_events))) if est_events else 0,
        },
        "disk": {"ok": ok_disk, "free": free, "need": need},
        "audio": {"exists": _audio_exists(v), "duration_ms": dur},
        "render_ready": _render_ready(),
        "render_reason": _render_reason(),
        "render_needed": _render_needed(v),
    }


# ---------- 生成任务（启动或附着） ----------

class _PlanJob:
    """一次「讲稿与视觉步点生成」的服务端状态；SSE 流只是它的进度订阅者。

    ⚠️ 与播客同构：**绝不在 SSE 生成器里做落盘**。用户离开页面 → 生成器被取消
       → 算出来的讲稿与步点随之丢弃，几十秒的 LLM 调用全白烧。
       任务线程自己开 DB 会话、持作品锁直到落库完成，与连接是否还在无关。
    """

    def __init__(self):
        self.status = "running"        # running / done / failed
        self.stage = ""                # audio / script / anchor / locate / plan / gate
        self.detail: dict = {}
        self.error = ""
        self.video: dict | None = None
        self.subs: list = []
        self.finished_at: float = 0.0

    def emit(self, stage: str, detail: dict | None = None) -> None:
        self.stage, self.detail = stage, (detail or {})
        self.broadcast("stage", {"stage": self.stage, "detail": self.detail})

    def broadcast(self, kind: str, payload: dict) -> None:
        for q in list(self.subs):        # 快照遍历：订阅者可能同时在上/下线
            q.put((kind, payload))


_PLAN_JOBS: dict = {}
_PLAN_JOBS_LOCK = threading.Lock()
_PLAN_JOB_TTL_SEC = 600


def _purge_plan_jobs() -> None:
    now = time.time()
    for vid, j in list(_PLAN_JOBS.items()):
        if j.status != "running" and now - j.finished_at > _PLAN_JOB_TTL_SEC:
            _PLAN_JOBS.pop(vid, None)


def _start_or_attach_plan(vid: int) -> _PlanJob:
    with _PLAN_JOBS_LOCK:
        _purge_plan_jobs()
        job = _PLAN_JOBS.get(vid)
        if job and job.status == "running":
            return job
        job = _PlanJob()
        _PLAN_JOBS[vid] = job
    threading.Thread(target=_run_plan_job, args=(vid, job), daemon=True).start()
    return job


def _run_plan_job(vid: int, job: _PlanJob) -> None:
    lock = _video_lock(vid)
    if not lock.acquire(timeout=LOCK_WAIT_SEC):
        job.status = "failed"
        job.error = "该作品正在执行另一项操作，请稍等片刻再试"
        job.broadcast("err", {"message": job.error})
        return
    try:
        db = SessionLocal()
        try:
            v = db.get(Video, vid)
            if not v:
                job.status = "failed"
                job.error = "视频作品不存在或已删除"
                job.broadcast("err", {"message": job.error})
                return
            v.status = "scripting"
            v.error = ""
            v.progress = 0.0
            db.commit()
            t_plan = time.time()
            try:
                _do_plan(db, v, job)
                _track("video_render", v.id, int(time.time() - t_plan), {"stage": "plan"})
            except HTTPException as e:
                v.status = "failed"
                v.error = str(e.detail)[:200]
                db.commit()
                job.status, job.error = "failed", v.error
                job.broadcast("err", {"message": job.error})
                return
            except BaseException as e:      # noqa: BLE001
                v.status = "failed"
                v.error = f"生成失败：{str(e)[:180]}"
                db.commit()
                job.status, job.error = "failed", v.error
                job.broadcast("err", {"message": job.error})
                return
            job.status = "done"
            job.video = video_to_dict(v, db)
            job.broadcast("done", {"video": job.video})
        finally:
            db.close()
    finally:
        job.finished_at = time.time()
        lock.release()


def _gen_visuals(db, v: Video, emit, refill: bool = False) -> tuple:
    """视觉步点生成（③ 锚点 + ④ 定位与步点）：全流程生成与「一键补步」**共用**。

    ⚠️ 绝不复制一份另写：补步修出来的步点若和重新生成的不是同一套逻辑，
       闸门会出现「这次过、下次不过」的漂移。
    ⚠️ emit(stage, detail)：全流程里是 job.emit；补步接口里是 noop。
    ⚠️ refill=True（补步）时按上次闸门的实际产出**上调事件目标**：
       同一目标重试 LLM 大概率产出差不多的数量，等于白重试。
    """
    # ⚠️ 有效视觉方案走**唯一出口**（`stage._inner_of` 同源）：口播必须看内容层，
    #    空/非法内容层兜底 graph，绝不落进下面的兜底 raise（那是死状态）。
    inner = svc.resolve_visual(v)
    steps, points, hits = [], [], {"tried": 0, "hit": 0, "unlocated": []}

    if inner == "source":
        # ⚠️ 顺序是**先建定位器 → 抽原文 → 再生成锚点**，不能颠倒：
        #    锚点生成必须拿到原文，否则模型只能猜「哪个短语在原文里原样存在」——
        #    命中率上不去，闸门判据 4（≥95%）永远过不了，
        #    「材料原件」皮肤就等于不可用（见 llm.generate_video_anchors 的 docstring）。
        mid = _first_material_id(v)
        if mid is None:
            raise HTTPException(400, "这份来源不是材料，无法做原件定位，请改用结构图方案")
        loc, reason = locate_svc.build_locator(db, mid)
        if loc is None:
            raise HTTPException(400, f"{reason}，请改用结构图方案")
        try:
            excerpt = ""
            try:
                excerpt = locate_svc.source_excerpt(loc)
            except BaseException:
                excerpt = ""          # 抽不到原文只影响命中率，不该阻断整条链路
            lines = [str(s.get("text") or "") for s in (v.script or [])
                     if (s.get("text") or "").strip()]
            emit("anchor", {"segments": len(lines), "source_chars": len(excerpt)})
            anchors = []
            try:
                from ..services import llm
                anchors = llm.retry_video_call(
                    lambda: llm.generate_video_anchors(lines, excerpt))
            except BaseException:
                anchors = []      # 锚点生成失败 → 回退到「用句子自己的词块定位」，不阻断全流程
            by_idx = {int(a.get("i") or 0): list(a.get("anchors") or []) for a in anchors}

            emit("locate", {"pages": loc.page_count, "kind": loc.kind})
            hits = svc.build_source_steps(loc, v.script or [], by_idx)
        finally:
            loc.close()
        emit("plan", {"steps": len(hits["steps"])})
        steps = svc.public_steps(hits["steps"])

    elif inner in svc.GRAPH_SKINS:
        # ⚠️ JSON 里的 `graph_paper`（结构图·纸感）与 `graph` 走**同一条**生成分支：
        #    两者都只要 points（不要锚点），差别只在舞台页怎么画。绝不为纸感另写一份 ——
        #    两份逻辑漂移会让「这次过闸门、下次不过」。
        # ⚠️ 事件数目标必须按**时长**算（`needed_events`），不能按句数：
        #    实测 19 句 / 200 秒的播客按句数生成只有 9 个事件 → 最长静止 42.3 秒，
        #    闸门判据 1 / 2 / 3 三条同时失败。
        need = svc.needed_events(v.duration_ms, len(v.shots or []))
        if refill:
            prev = int((v.gate_report or {}).get("events") or 0)
            if prev and prev < need:
                # 上次实际产出低于目标 → 目标按缺口上调（同一目标重试 ≈ 白试）
                need = need + (need - prev)
        emit("plan", {"segments": len(v.script or []), "events_target": need})
        lines = "\n".join(f"{i}. {s.get('text') or ''}"
                          for i, s in enumerate(v.script or [], 1) if (s.get("text") or "").strip())
        from ..services import llm
        # 分镜边界（句号）交给模型：它据此为每个镜头出标题与版式（B1 结构图真结构化）。
        # 表校验不过时 generate_video_points 内部自动退回旧扁平格式，不阻断链路。
        shots_hint = svc.shot_seg_ranges(v.script or [], v.shots or [])
        obj = llm.retry_video_call(
            lambda: llm.generate_video_points(lines, len(v.script or []), need,
                                              shots=shots_hint))
        points = svc.build_graph_points(v.script or [], obj.get("elements") or [])
        # 回写镜头标题/分屏（scenes 为空 = 旧格式输出 → shots 原样，行为不变）
        # ⚠️⚠️ **必须把 script 传进去**：分屏的时间区间是「句号 → 毫秒」换算出来的，
        #      漏传 script 会让 `_screens_to_ms` 直接返回空 → 每镜只剩一屏 ——
        #      **功能静默失效**：画面上没有任何报错，只是永远不换屏（最难查的那类）。
        v.shots = svc.apply_scene_meta(v.shots or [], obj.get("scenes") or [],
                                       v.script or [])
        steps = svc.graph_steps(points)
        hits = {"tried": 1, "hit": 1, "unlocated": []}

    elif inner in ("radio", "night", "ambient"):
        # 解压型皮肤：不需要锚点定位 / 结构图元素 —— 画面由舞台页模板 + 音轨包络直接产出。
        # ⚠️ 这一步**不调用任何 LLM**（不需要 points / anchors），steps / points 为空；
        #    密度类闸门判据（最长静止 / 平均间隔 / 事件总数）对它们不适用（见 gate_check 分档）。
        steps, points = [], []
        hits = {"tried": 0, "hit": 0, "unlocated": []}

    else:
        # 兜底：`svc.resolve_visual` 只会返回 VISUAL_KINDS 之一，走到这里说明皮肤常量
        # 与实现漂移了。⚠️ 消息必须把**两个值**都打出来 —— 旧文案只打 `v.skin`，
        # 于是「口播层选了、内容层没选」被显示成「未知的视觉方案：talking_head」：
        # talking_head 明明是已知皮肤，用户和排查的人都被带偏（实测踩到）。
        raise HTTPException(500, f"视觉方案解析失败：skin={v.skin!r} inner={v.inner_skin!r}")

    return steps, points, hits


def _gate_tally(v: Video, passed: bool) -> None:
    """闸门连续失败计数（PRD 3.9.7）：≥3 才允许「跳过闸门」；通过即清零。

    ⚠️ 必须是「连续」：历史累计会把「改好了又改坏」误算成反复不通过。
    ⚠️ gate_skipped 标注的是**当前这份成片**：闸门一旦通过，下一版成片就是
       过了自检的，标注必须清掉（不然后端会替一部已过检的片子继续示警）。
    """
    if passed:
        v.gate_fail_count = 0
        v.gate_skipped = False
    else:
        v.gate_fail_count = int(v.gate_fail_count or 0) + 1


def _do_plan(db, v: Video, job: _PlanJob) -> None:
    """音轨 → 讲稿与分镜 → 锚点 → 定位与步点 → 闸门（PRD 2.2.2 子流程一）。"""
    conf = _tts_conf()

    # ---- ① 音轨 ----
    job.emit("audio", {"mode": v.audio_source})
    if v.audio_source == "podcast":
        r = svc.resolve_audio(db, v)
        if not r["ok"]:
            raise HTTPException(400, r["error"])
        p = r["podcast"]
        # ⚠️ **绝不重新合成**（PRD 2.1 硬性约束 2）：直接引用播客的音频与逐句时间轴
        v.audio_name, v.audio_bytes = "", int(r["bytes"])
        v.duration_ms = int(r["duration_ms"] or 0) or svc.duration_ms_from_file(r["path"])
        v.audio_sig = p.audio_sig or ""
        v.script = [dict(s) for s in (p.script or [])]
        v.style = p.style or v.style
    else:
        if not _audio_exists(v):
            job.emit("audio", {"mode": "self", "phase": "source"})
            _make_self_audio(db, v, job)
        else:
            v.duration_ms = v.duration_ms or svc.duration_ms_from_file(
                svc.video_abspath(v.id, v.audio_name))
    db.commit()

    if not svc.script_has_timeline(v.script or []):
        raise HTTPException(400, "讲稿缺少逐句时间轴：请先合成音轨（时间轴唯一来源是音轨）")

    # ---- ② 分镜 ----
    job.emit("script", {"segments": len(v.script or [])})
    v.shots = svc.build_shots(v.script or [])
    db.commit()

    # ---- ③ 锚点 + ④ 定位与步点（与「一键补步」共用 `_gen_visuals`，绝不复制另写） ----
    t0 = time.time()
    steps, points, hits = _gen_visuals(db, v, job.emit)
    _track("video_render", v.id, int(time.time() - t0), {"stage": "locate"})
    v.steps, v.points = steps, points

    # ---- ⑤ 闸门 ----
    job.emit("gate", {})
    t1 = time.time()
    report = svc.gate_check(v, steps, v.shots or [], hits, conf)
    _track("video_render", v.id, int(time.time() - t1), {"stage": "gate"})
    v.gate_report = report
    _gate_tally(v, bool(report["passed"]))
    v.status, go_render = _status_after_gate(db, v, bool(report["passed"]))
    v.error = "" if report["passed"] else _gate_error(report)
    if go_render:
        v.progress = 0.0
    db.commit()
    db.refresh(v)
    if go_render:
        try:
            _enqueue_render(v.id)
        except BaseException:
            pass        # 入队失败不该让「已生成好讲稿」这件事整体失败


def _gate_error(report: dict) -> str:
    """把未通过的判据翻成一句可读提示。

    ⚠️ 排序刻意不是「报告顺序」，而是**用户能动手的顺序**：`hit_rate`（高亮框命中率）
       是唯一「换一份材料 / 换皮肤就能解决」的判据，其余密度判据基本都是它的连带结果；
       按报告顺序截断（`[:3]`）会把它挤掉 —— 实测 catch 到的文案就是这么来的：
       '质量自检未通过：最长静止、平均间隔、视觉事件总数'
       （用户看到三个自己无从下手的词，却看不到真正的根因）。
       稳定排序，其余判据保持报告顺序。
    """
    items = [it for it in (report.get("items") or []) if not it.get("ok")]
    items.sort(key=lambda it: 0 if it.get("key") == "hit_rate" else 1)
    bad = [it["label"] for it in items]
    return "质量自检未通过：" + "、".join(bad[:3])[:160]


def _render_needed(v: Video) -> bool:
    """现在这份作品还需要（重新）渲染吗？

    ⚠️ 判据用**指纹**，不用时间戳：时间戳表达不了「哪个参数改了」，也表达不了
       「重新生成但内容没变」（PRD 3.11.4 第 1 条）。
    """
    if (v.status or "") != "finished":
        return True
    if not v.video_name or not svc.video_abspath(v.id, v.video_name).exists():
        return True
    return (v.signature or "") != svc.signature(v)


def _status_after_gate(db, v: Video, passed: bool) -> tuple:
    """闸门跑完后的目标状态 + 是否要入渲染队列。**唯一口径**（`_do_plan` / `rerun_gate` 共用）。

    - 闸门没过 → `gate_check`（留现场给用户改，绝不自动重试）
    - 过了、但成片已是最新 → `finished`：**重跑闸门不该把已是最新的成片再烧一遍 CPU**
      （分钟级代价；用户点一次「重新检查质量」就触发重渲染是不可接受的）
    - 过了、但渲染器不可用 → `gate_check`：**绝不置 queued** —— 没有执行者的 queued
      会一直挂到启动回收窗口（≥ 单条渲染超时）过去才被判 failed，
      凭空造出「排队中 → 莫名其妙失败」的僵尸（PRD 2.2.5 / 2.2.6）。
    - 其余 → `queued` + 入队
    """
    if not passed:
        return "gate_check", False
    if (v.status or "") == "finished" and not _render_needed(v):
        return "finished", False
    if not _render_ready():
        return "gate_check", False
    return "queued", True


def _make_self_audio(db, v: Video, job: _PlanJob) -> None:
    """本片单独生成音轨：素材抽取 → 讲稿 → TTS → 回读真实时长。

    ⚠️ 落库走 `svc.finalize_video`（只写 Video 自己的列），
       **绝不**调 `podcasts._finalize_audio`（见 routers/videos 模块 docstring）。
    """
    from ..services import llm
    st = v.source_type if v.source_type in SOURCE_TYPES else "article"
    refs = v.source_refs or []
    ref_ids = [int(r["id"]) for r in refs if isinstance(r, dict) and r.get("id") is not None]
    colors = None
    for r in refs:
        if isinstance(r, dict) and r.get("colors"):
            colors = list(r["colors"])
    pasted = ""
    for r in refs:
        if isinstance(r, dict) and r.get("type") == "text":
            pasted = str(r.get("content") or "")
    verbatim = v.gen_mode == "verbatim"
    title, text, new_refs = pod_svc.collect_source(
        db, st, ref_ids, highlight_colors=colors, pasted_text=pasted, verbatim=verbatim)
    if new_refs:
        v.source_refs = new_refs

    if verbatim:
        script = pod_svc.split_verbatim(text)
    else:
        job.emit("script", {"phase": "brief"})
        plan = svc.video_plan(int(v.target_minutes or 3))
        brief = text
        if v.gen_mode == "two_step":
            brief = pod_svc.make_brief(title, text, int(v.target_minutes or 3))
        script = llm.retry_video_call(lambda: llm.generate_video_script(
            title, brief, plan["script_chars"], plan["segments"],
            instruction=v.instruction or "", style=v.style or "solo"))
        # 字数偏离目标过大 → 与播客同样迭代一次（复用同一套 _ensure_length）
        script = pod_svc._ensure_length(
            script, plan, title, v.instruction or "",
            lambda fix: llm.generate_video_script(
                title, brief, plan["script_chars"], plan["segments"],
                instruction=fix, style=v.style or "solo"))

    conf = _tts_conf()
    v.script = svc.normalize_script(script, None)
    job.emit("audio", {"phase": "tts", "segments": len(v.script)})
    try:
        audio, timings = tts.synthesize(
            v.script, _voice_map(v, conf), rate=conf["rate"], gap_ms=conf["gap_ms"],
            provider=conf["provider"], cache_dir=svc.parts_dir(v.id),
            on_progress=lambda d, t: job.emit("audio", {"phase": "tts", "done": d, "total": t}))
    except tts.TTSPermanent as e:
        # 确定性错误：原样透出可执行指引（如「模型未下载 → 去下载」），不包装成稍后重试
        raise HTTPException(400, str(e)[:200])
    except tts.TTSError as e:
        raise HTTPException(502, e.friendly)
    except BaseException as e:      # noqa: BLE001
        raise HTTPException(502, f"语音合成失败：{str(e)[:160]}")

    # 背景音乐：在**整轨**层面混入（分句缓存保持纯人声 —— 换 BGM / 调音量只需重混
    # 这一次，不必重新合成任何一句；与播客 _apply_bgm 同法，含编码延迟补偿）。
    if (getattr(v, "bgm_id", "") or "").strip():
        audio, timings = _mix_bgm(v, audio, timings)

    merged = [{**seg, "start_ms": t["start_ms"], "end_ms": t["end_ms"],
               "voice": t["voice"], "voice_name": t["voice_name"]}
              for seg, t in zip(v.script, timings)]
    name = svc.save_audio(v.id, audio)
    svc.finalize_video(db, v, audio=audio, audio_name=name, script=merged,
                       duration_ms=tts.duration_ms(audio))


def _mix_bgm(v: Video, audio: bytes, timings: list) -> tuple:
    """把背景音乐混进纯人声音轨，并补偿二次编码引入的延迟（与 podcasts._apply_bgm 同法）。

    ⚠️ 只在「本片单独生成」路径被调用：复用播客的音轨里已含播客自己的 BGM，
       再叠一层会双音乐打架（2026-10-12 产品决策：复用模式不支持视频级 BGM）。
    """
    from ..services import mixer
    try:
        bed_path = bgm_svc.resolve(v.bgm_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    try:
        vocal, _ = mixer.decode(audio)
        bed, _ = mixer.decode(bed_path)
        vol = float(v.bgm_volume if v.bgm_volume is not None else BGM_VOLUME_DEFAULT)
        mixed = mixer.mix(vocal, bed, volume_db=vol, ducking=True)
        out = mixer.encode_mp3(mixed)
    except mixer.AudioError as e:
        raise HTTPException(502, f"背景音乐处理失败：{str(e)[:160]}")
    except HTTPException:
        raise
    except Exception as e:      # noqa: BLE001
        raise HTTPException(502, f"背景音乐混音失败：{str(e)[:160]}")

    # ⚠️ 混音重编码 = libmp3lame 在流开头插入固定 45ms 静音（实测常量）→
    #    音频内容整体后移，逐句时间戳必须同步补偿，否则字幕/聚光灯/点亮全部提前 45ms。
    d = mixer.ENCODER_DELAY_MS
    shifted = [{**t, "start_ms": round((t.get("start_ms") or 0) + d),
                "end_ms": round((t.get("end_ms") or 0) + d)}
               for t in timings]
    return out, shifted


def _voice_map(v: Video, conf: dict) -> dict:
    """本片音色映射：视频是讲解型，默认单人（host 音色）。

    ⚠️ **作品级音色优先于设置页**：用户在生成台选过就以作品为准，没选才跟随设置页。
       「重新生成依赖的参数必须先持久化」—— 选中作品时必须能还原它当时用的音色，
       回填不完整比完全不回填更危险。
    """
    own = str(((v.voice_map or {}) or {}).get("host") or "").strip()
    # ⚠️ 带 provider：zipvoice 音色（zv:xxx）与 edge 音色是两套命名空间，漏传会拿到
    #    不匹配的默认音色 → 合成报「音色无效」
    _prov = conf.get("provider") or tts.DEFAULT_PROVIDER
    return {"host": tts.resolve_voice(own or conf.get("host"), "host", provider=_prov),
            "expert": tts.resolve_voice(conf.get("expert"), "expert", provider=_prov)}


@router.post("/{vid}/plan/stream")
def plan_stream(vid: int, db: Session = Depends(get_db)):
    """SSE：`stage`×N → `done` | `error`。

    「启动或附着」：已有任务在跑就直接附着（重复点击 / 离开页面后回来走的是这条路），
    不会起第二个任务。
    ⚠️ 前置校验（404）必须在返回响应**之前**做完：一旦进入生成器响应头已发出，
       错误只能作为 error 事件回传，拿不到 HTTP 状态码。
    """
    v = db.get(Video, vid)
    if not v:
        raise HTTPException(404, "视频作品不存在或已删除")
    job = _start_or_attach_plan(vid)
    return StreamingResponse(_plan_events(job), media_type="text/event-stream",
                             headers=SSE_HEADERS)


def _plan_events(job: _PlanJob):
    q: queue.Queue = queue.Queue()
    job.subs.append(q)
    try:
        meta = {"stage": job.stage, "detail": job.detail}
        yield _sse("meta", meta)
        if job.status == "done" and job.video:
            yield _sse("done", {"video": job.video})
            return
        if job.status == "failed":
            yield _sse("error", {"message": job.error})
            return
        last = time.time()
        while True:
            try:
                kind, payload = q.get(timeout=0.5)
            except queue.Empty:
                if time.time() - last >= HEARTBEAT_SEC:
                    last = time.time()
                    yield ": ping\n\n"
                continue
            last = time.time()
            if kind == "stage":
                yield _sse("stage", payload)
            elif kind == "done":
                yield _sse("done", payload)
                return
            elif kind == "err":
                yield _sse("error", payload)
                return
    finally:
        try:
            job.subs.remove(q)
        except ValueError:
            pass


@router.get("/{vid}/plan/status")
def plan_status(vid: int, db: Session = Depends(get_db)):
    """附着前的状态查询（回到页面时先问一句「还在跑吗」）。"""
    if not db.get(Video, vid):
        raise HTTPException(404, "视频作品不存在或已删除")
    with _PLAN_JOBS_LOCK:
        job = _PLAN_JOBS.get(vid)
    if not job:
        return {"running": False}
    d = {"running": job.status == "running", "status": job.status,
         "stage": job.stage, "detail": job.detail, "error": job.error}
    if job.status == "done" and job.video:
        d["video"] = job.video
    return d


@router.post("/{vid}/gate")
def rerun_gate(vid: int, db: Session = Depends(get_db)):
    """重跑质量闸门（改讲稿 / 补步点后调用；**不重新生成讲稿与锚点**）。"""
    v = db.get(Video, vid)
    if not v:
        raise HTTPException(404, "视频作品不存在或已删除")
    with _Exclusive(vid):
        db.refresh(v)
        conf = _tts_conf()
        # 沿用上一次的「未命中句」——重跑闸门不该把用户已经看到的失败清单洗掉
        hits = {"tried": 1, "hit": 1,
                "unlocated": list((v.gate_report or {}).get("unlocated") or [])}
        report = svc.gate_check(v, v.steps or [], v.shots or [], hits, conf)
        v.gate_report = report
        _gate_tally(v, bool(report["passed"]))
        v.status, go_render = _status_after_gate(db, v, bool(report["passed"]))
        v.error = "" if report["passed"] else _gate_error(report)
        if go_render:
            v.progress = 0.0
        db.commit()
        db.refresh(v)
        if go_render:
            try:
                _enqueue_render(v.id)
            except BaseException:
                pass
    return video_to_dict(v, db)


# ---------- 闸门修复动作（PRD 3.9.5 判据 1/3/4/5 + 3.9.6） ----------

class ManualStepReq(BaseModel):
    seg: int = 0                # 讲稿句序号（1 起，与闸门报告 unlocated 同口径）
    anchor: str = ""            # 用户从材料原文里**原样**挑的连续短语


def _after_gate_rerun(db, v: Video, report: dict) -> dict:
    """补步/手动定位后的统一收尾：落报告 → 计数 → 定状态 →（过了就）入队渲染。"""
    v.gate_report = report
    _gate_tally(v, bool(report["passed"]))
    v.status, go_render = _status_after_gate(db, v, bool(report["passed"]))
    v.error = "" if report["passed"] else _gate_error(report)
    if go_render:
        v.progress = 0.0
    db.commit()
    db.refresh(v)
    if go_render:
        try:
            _enqueue_render(v.id)
        except BaseException:
            pass                # 入队失败不该让「已补好的步点」整体失败
    return video_to_dict(v, db)


@router.post("/{vid}/steps/refill")
def refill_steps(vid: int, db: Session = Depends(get_db)):
    """一键补步（PRD 3.9.6「可自动修复 → 一键补步点 / 重新定位」）。

    与全流程生成**共用** `_gen_visuals`（只是跳过音轨/讲稿阶段），补完立刻重跑闸门。
    ⚠️ 步点是渲染画面的内容来源但**不在成片指纹里**：补步后必须把 signature 置空，
       否则 `_render_needed` 会认为旧成片仍是最新 → 用户拿到一部「没补进步点」的片子。
    """
    v = db.get(Video, vid)
    if not v:
        raise HTTPException(404, "视频作品不存在或已删除")
    if not svc.script_has_timeline(v.script or []):
        raise HTTPException(400, "讲稿缺少逐句时间轴：请先生成讲稿与音轨")
    with _Exclusive(vid):
        db.refresh(v)
        t0 = time.time()
        steps, points, hits = _gen_visuals(db, v, lambda *a, **k: None, refill=True)
        _track("video_render", v.id, int(time.time() - t0), {"stage": "locate"})
        v.steps, v.points = steps, points
        if v.video_name:
            v.signature = ""      # 步点变了 → 旧成片对应旧步点，闸门过了必须重渲染
        report = svc.gate_check(v, steps, v.shots or [], hits, _tts_conf())
        return _after_gate_rerun(db, v, report)


@router.post("/{vid}/steps/manual")
def manual_step(vid: int, req: ManualStepReq, db: Session = Depends(get_db)):
    """手动指定未命中句的位置（PRD 3.9.5 判据 4 的修复入口）。

    ⚠️ 输入是「原文里原样存在的短语」而不是坐标：让用户在页面上徒手画框既难用
       又不准，而「从原文里挑一句」是用户天然会做的动作（与讲稿锚点同构）。
    """
    v = db.get(Video, vid)
    if not v:
        raise HTTPException(404, "视频作品不存在或已删除")
    inner = v.inner_skin if v.skin == "talking_head" else v.skin
    if inner != "source":
        raise HTTPException(400, "只有「材料原件」方案才有手动定位")
    script = v.script or []
    seg = int(req.seg or 0)
    if seg < 1 or seg > len(script) or not (script[seg - 1].get("text") or "").strip():
        raise HTTPException(400, "句序号超出讲稿范围")
    anchor = (req.anchor or "").strip()
    if len(anchor) < 2:
        raise HTTPException(400, "请从材料原文里挑一段至少 2 字的连续短语")
    with _Exclusive(vid):
        db.refresh(v)
        mid = _first_material_id(v)
        if mid is None:
            raise HTTPException(400, "这份来源不是材料，无法做原件定位")
        loc, reason = locate_svc.build_locator(db, mid)
        if loc is None:
            raise HTTPException(400, reason)
        try:
            found = loc.locate(anchor)
        finally:
            loc.close()
        if not found:
            raise HTTPException(
                400, "在材料原件里找不到这段文字：请确认是从原文里**原样**摘的连续片段")
        segrow = script[seg - 1]
        start = int(segrow.get("start_ms") or 0)
        end = int(segrow.get("end_ms") or (start + 1000))
        # 同一句已有步点先撤掉（以手动指定为准），再把命中**均分到该句时间跨度上**
        steps = [dict(s) for s in (v.steps or []) if int(s.get("seg") or 0) != seg]
        picks = list(found)[:svc.MAX_STEPS_PER_SEG]
        k = len(picks)
        span = max(0, end - start)
        times = ([start + round(span * j / k) for j in range(k)] if k > 1 else [start])
        for h, t in zip(picks, times):
            steps.append({"at": int(t), "page": h["page"], "anchor": anchor,
                          "hit": h.get("hit") or anchor, "sc": h.get("sc", 1.0),
                          "x": h["x"], "y": h["y"], "w": h["w"], "h": h["h"], "seg": seg})
        steps.sort(key=lambda s: (int(s.get("at") or 0), int(s.get("seg") or 0)))
        v.steps = steps
        if v.video_name:
            v.signature = ""      # 步点变了 → 旧成片对应旧步点，闸门过了必须重渲染
        # 这句已解决 → 从未命中清单里去掉（其余未命中句保留）
        rep = dict(v.gate_report or {})
        rep["unlocated"] = [i for i in (rep.get("unlocated") or []) if int(i) != seg]
        v.gate_report = rep
        report = svc.gate_check(v, steps, v.shots or [],
                                {"tried": 1, "hit": 1, "unlocated": rep["unlocated"]},
                                _tts_conf())
        return _after_gate_rerun(db, v, report)


# ---------- 渲染队列（PRD 2.2.5 / 4.3 / 验收 #34） ----------

class _RenderJob:
    """一次渲染的服务端状态；SSE 流只是它的进度订阅者。

    ⚠️ 与 `_PlanJob` 同构：**绝不在 SSE 生成器里干活**。渲染与视频时长同量级
       （分钟级），用户离开页面后必须照样跑完并落库。
    """

    def __init__(self, vid: int):
        self.vid = vid
        self.status = "queued"        # queued / running / done / failed
        self.stage = ""               # stage / capture / mux / finalize
        self.pct = 0
        self.detail: dict = {}
        self.error = ""
        self.video: dict | None = None
        # 渲染报告快照（skew / 帧数 / 空洞 / 重复帧占比…）。
        # ⚠️ 必须**带出去**：PRD 3.8.6 的对齐自检不能在「过了就扔」——
        #    没有这个出口，界面与验收都只能看到「出了个片」，看不到「对得准不准」，
        #    而自检本身一旦失效（判据被改坏）谁都发现不了。
        self.report: dict = {}
        self.subs: list = []
        self.finished_at = 0.0

    def emit(self, stage: str, pct: int | None = None,
             detail: dict | None = None) -> None:
        self.stage = stage
        if pct is not None:
            self.pct = max(0, min(100, int(pct)))
        if detail:
            self.detail = detail
        self.broadcast("stage", {"stage": self.stage, "pct": self.pct,
                                 "detail": self.detail})

    def broadcast(self, kind: str, payload: dict) -> None:
        for q in list(self.subs):
            q.put((kind, payload))


_RENDER_JOBS: dict = {}
_RENDER_QUEUE: list = []          # 等待渲染的作品 id（FIFO）
_RENDER_LOCK = threading.Lock()
_RENDER_WAKE = threading.Event()
_RENDER_THREAD: threading.Thread | None = None
_RENDER_JOB_TTL_SEC = 1800


def _purge_render_jobs() -> None:
    now = time.time()
    for vid, j in list(_RENDER_JOBS.items()):
        if j.status not in ("queued", "running") and now - j.finished_at > _RENDER_JOB_TTL_SEC:
            _RENDER_JOBS.pop(vid, None)


def _render_pos(vid: int) -> int:
    """排队位次 = **前面还有几部**（含正在渲染的那一部）；0 = 没在等（在跑 / 不在队列）。

    ⚠️ 不能写成 `_RENDER_QUEUE.index(vid) + 1`：调度器把当前那部**弹出队列**之后
       它就 index 不到了，于是「排在正在渲染那部后面」的第二部会被算成 index 0
       → 位次 1 → 界面只能说「排队中」，说不出前面还有几部（**少算一部**）。
       而 PRD 验收 #34 要的正是「第二条进入排队并**显示位次**」。
    """
    try:
        with _RENDER_LOCK:
            idx = _RENDER_QUEUE.index(vid)
            running = any(j.status == "running" for j in _RENDER_JOBS.values())
    except ValueError:
        return 0
    return idx + 1 + (1 if running else 0)


def _render_pct(vid: int) -> int:
    j = _RENDER_JOBS.get(vid)
    return int(j.pct) if j is not None else 0


def _render_stage(vid: int) -> str:
    j = _RENDER_JOBS.get(vid)
    return j.stage if j is not None else ""


def _enqueue_render(vid: int) -> _RenderJob:
    """把作品放进渲染队列（**幂等**：已在队列/正在跑的直接返回同一个 job）。"""
    global _RENDER_THREAD
    with _RENDER_LOCK:
        _purge_render_jobs()
        job = _RENDER_JOBS.get(vid)
        if job is not None and job.status in ("queued", "running"):
            return job
        job = _RenderJob(vid)
        _RENDER_JOBS[vid] = job
        _RENDER_QUEUE.append(vid)
        if _RENDER_THREAD is None or not _RENDER_THREAD.is_alive():
            _RENDER_THREAD = threading.Thread(target=_render_dispatcher,
                                              daemon=True, name="video-render")
            _RENDER_THREAD.start()
        _RENDER_WAKE.set()
    return job


def _render_dispatcher() -> None:
    """渲染调度器：**一个线程串行消费**整条队列。

    ⚠️ 为什么不是「每条一个线程 + 信号量」：PRD 验收 #34 要求「同时提交 2 条，
       第二条进入排队且**不启动第二个渲染进程**」。信号量能保证并发数，但很难
       保证第二条不会先把浏览器进程建起来（实测每次 launch 都是几百 MB）。
       单调度线程是**结构性**保证 —— 渲染器里那条「同一浏览器必须串行」的硬约束
       也就顺带被这条队列覆盖了。
    ⚠️ 调度线程**不持 DB 会话**（渲染期间不占连接，分钟级）。
    """
    while True:
        vid = None
        with _RENDER_LOCK:
            while _RENDER_QUEUE:
                cand = _RENDER_QUEUE.pop(0)
                j = _RENDER_JOBS.get(cand)
                if j is not None and j.status == "queued":
                    vid = cand
                    break
            job = _RENDER_JOBS.get(vid) if vid is not None else None
        if vid is None:
            _RENDER_WAKE.wait(2.0)
            _RENDER_WAKE.clear()
            continue
        try:
            _run_render_job(vid, job)
        except BaseException as e:      # noqa: BLE001 —— 调度线程绝不能死
            job.status, job.error = "failed", f"渲染线程异常：{str(e)[:150]}"
            job.finished_at = time.time()
            job.broadcast("err", {"message": job.error})


def _fail_render(db, v, job: _RenderJob, msg: str) -> None:
    """渲染失败收口：DB 置 failed + 广播。**只有一处**写这个状态。"""
    if v is not None:
        v.status = "failed"
        v.progress = 0.0
        v.error = msg[:200]
        db.commit()
    job.status, job.error = "failed", msg[:200]
    job.finished_at = time.time()
    job.broadcast("err", {"message": job.error})


def _run_render_job(vid: int, job: _RenderJob) -> None:
    """真渲染：**分三段、三个短会话**，渲染期间不持 DB 连接、不持作品锁。

    ⚠️ 不持作品锁是有意的：渲染是分钟级，若全程持锁，用户在这几分钟里连
       改一句话都会被拒（409）。而「渲染期间改了讲稿」这件事本身**不需要**锁来防 ——
       它由成片指纹（`signature`）表达：落库时写的是**开工那一刻**的指纹快照，
       界面据此显示「成片对应上一版 + 一键重建」（PRD 3.11.4）。
    """
    from ..services import renderer as renderer_svc
    from ..services import stage as stage_svc

    # ---- ① 开工快照（短会话） ----
    db = SessionLocal()
    try:
        v = db.get(Video, vid)
        if v is None:
            job.status, job.error = "failed", "视频作品不存在或已删除"
            job.finished_at = time.time()
            job.broadcast("err", {"message": job.error})
            return
        if not _render_ready():
            _fail_render(db, v, job, "渲染器不可用：缺少 Chromium 或 PyAV 组件")
            return
        audio = _audio_abspath(v)
        if audio is None or not audio.exists():
            _fail_render(db, v, job, "音轨文件不存在，请先生成音轨")
            return
        dur = int(v.duration_ms or 0)
        if dur <= 0:
            dur = svc.duration_ms_from_file(audio)
        if dur <= 0:
            _fail_render(db, v, job, "音轨时长读不出来，请重新生成音轨")
            return
        sig = svc.signature(v)          # ⚠️ 开工快照，不是完工时现算
        v.status = "rendering"
        v.progress = 0.0
        v.error = ""
        db.commit()
    finally:
        db.close()

    job.status = "running"
    job.emit("stage", 1)
    out_mp4 = svc.video_abspath(vid, "video.mp4")
    out_poster = svc.poster_abspath(vid, "poster.jpg")

    try:
        # ---- ② 舞台页 + 渲染（不碰 DB 的部分） ----
        db2 = SessionLocal()
        try:
            v2 = db2.get(Video, vid)
            if v2 is None:
                raise RuntimeError("视频作品在渲染开始前被删除")
            html = stage_svc.build_stage(db2, v2)
        finally:
            db2.close()
        job.emit("capture", 1)
        rep = renderer_svc.render(
            html, audio, out_mp4, out_poster, dur,
            on_progress=lambda p: job.emit("capture", max(1, min(99, p))))
        job.report = dict(rep or {})
        job.emit("mux", 99)
    except BaseException as e:          # noqa: BLE001
        db3 = SessionLocal()
        try:
            v3 = db3.get(Video, vid)
            if v3 is not None and v3.status == "rendering":
                _fail_render(db3, v3, job, f"渲染失败：{str(e)[:170]}")
                db3.commit()
            else:
                job.status, job.error = "failed", str(e)[:200]
                job.finished_at = time.time()
                job.broadcast("err", {"message": job.error})
        finally:
            db3.close()
        return

    # ---- ③ 落库（短会话） ----
    db4 = SessionLocal()
    try:
        v4 = db4.get(Video, vid)
        if v4 is None:
            # 渲染期间作品被删了：产物立刻清掉，别留孤儿文件
            svc.remove_video_files(vid)
            job.status, job.error = "failed", "视频作品在渲染期间被删除"
            job.finished_at = time.time()
            job.broadcast("err", {"message": job.error})
            return
        job.emit("finalize", 99)
        svc.finalize_render(
            db4, v4,
            video_name=out_mp4.name, poster_name=out_poster.name,
            video_bytes=rep["bytes"], duration_ms=rep["duration_ms"],
            width=rep["width"], height=rep["height"], fps=rep["fps"],
            frame_count=rep["frame_count"], render_seconds=rep["elapsed_s"],
            sig=sig)
        job.video = video_to_dict(v4, db4)
        # PRD 4.2：渲染阶段耗时（定位瓶颈）+ 渲染完成（算渲染倍率 render_seconds/duration）。
        # ⚠️ 埋点不含内容，只装行为量与长度。
        _track("video_render", vid, int(rep.get("elapsed_s") or 0), {"stage": "render"})
        _track("video_finish", vid, int(rep.get("elapsed_s") or 0),
               {"duration_ms": int(rep["duration_ms"]), "video_bytes": int(rep["bytes"]),
                "render_seconds": round(float(rep["elapsed_s"] or 0), 1)})
    finally:
        db4.close()
    job.status = "done"
    job.pct = 100
    job.stage = "done"
    job.finished_at = time.time()
    job.broadcast("done", {"video": job.video, "report": job.report})


def _render_blockers(v: Video, allow_skip: bool = False) -> str:
    """渲染前的硬性前置（返回空串 = 通过）。与预检 / 闸门同口径。

    ⚠️ 跳过闸门（PRD 3.9.7）只放行「质量未通过」这一项 —— 音轨缺失 / 磁盘不足是
       物理前置，跳不过；且必须**连续失败 ≥3 次**（gate_fail_count）才允许跳。
    """
    rep = v.gate_report or {}
    if not rep:
        raise HTTPException(400, "还没有跑过质量闸门：请先生成讲稿与视觉步点")
    if not rep.get("passed"):
        can_skip = allow_skip and int(v.gate_fail_count or 0) >= 3
        if not can_skip:
            raise HTTPException(400, _gate_error(rep))
    if not _audio_exists(v):
        raise HTTPException(400, "音轨文件不存在，请先生成或重新选择来源")
    ok, free, need = svc.disk_ok(int(v.duration_ms or 0), getattr(v, "skin", ""))
    if not ok:
        raise HTTPException(
            400, f"磁盘空间不足：预计需要约 {need / 1048576:.0f} MB，"
                 f"当前可用约 {free / 1048576:.0f} MB")
    return ""


def _render_start(v: Video, db: Session, skip_gate: bool = False) -> _RenderJob:
    """校验 → 入队。已在渲染/排队中的直接附着（幂等）。"""
    if not _render_ready():
        raise HTTPException(400, _render_reason() or "渲染器不可用")
    with _RENDER_LOCK:
        cur = _RENDER_JOBS.get(v.id)
        if cur is not None and cur.status in ("queued", "running"):
            return cur
    _render_blockers(v, allow_skip=skip_gate)
    job = _enqueue_render(v.id)
    dirty = False
    if (v.status or "") != "rendering":
        v.status = "queued"
        v.error = ""
        v.progress = 0.0
        dirty = True
    if skip_gate and not (v.gate_report or {}).get("passed"):
        # 记录在案（PRD 3.9.7）：跳过不是「通过了」，是「用户知情地不要质量保障」
        # —— 标注跟着当前这份成片走，直到闸门下一次通过（_gate_tally 清零）。
        v.gate_skipped = True
        _track("video_render", v.id, 0, {"stage": "gate", "skipped": 1})
        dirty = True
    if dirty:
        db.commit()
        db.refresh(v)
    return job


class RenderReq(BaseModel):
    # 跳过质量闸门（PRD 3.9.7）：仅当连续失败 ≥3 次才生效，否则与不带一样被拦
    skip_gate: bool = False


@router.post("/{vid}/render")
def start_render(vid: int, req: RenderReq | None = None, db: Session = Depends(get_db)):
    """开始（或排队）渲染。返回 job 概览 + 位次，前端据此显示「前面还有 N 部」。"""
    v = db.get(Video, vid)
    if not v:
        raise HTTPException(404, "视频作品不存在或已删除")
    job = _render_start(v, db, skip_gate=bool(req and req.skip_gate))
    return {"status": job.status, "position": _render_pos(vid),
            "pct": job.pct, "stage": job.stage}


@router.post("/{vid}/render/stream")
def render_stream(vid: int, db: Session = Depends(get_db)):
    """SSE：`stage`×N → `done` | `error`（与 `/plan/stream` 同构）。

    ⚠️ 前置校验必须在返回响应**之前**做完：一旦进入生成器响应头已发出，
       错误只能作为 error 事件回传，拿不到 HTTP 状态码。
    """
    v = db.get(Video, vid)
    if not v:
        raise HTTPException(404, "视频作品不存在或已删除")
    job = _render_start(v, db)
    return StreamingResponse(_render_events(job, vid), media_type="text/event-stream",
                             headers=SSE_HEADERS)


def _render_events(job: _RenderJob, vid: int):
    q: queue.Queue = queue.Queue()
    job.subs.append(q)
    try:
        yield _sse("meta", {"status": job.status, "stage": job.stage,
                            "pct": job.pct, "position": _render_pos(vid)})
        if job.status == "done" and job.video:
            yield _sse("done", {"video": job.video, "report": job.report})
            return
        if job.status == "failed":
            yield _sse("error", {"message": job.error})
            return
        last = time.time()
        while True:
            try:
                kind, payload = q.get(timeout=0.5)
            except queue.Empty:
                if time.time() - last >= HEARTBEAT_SEC:
                    last = time.time()
                    yield ": ping\n\n"
                continue
            last = time.time()
            if kind == "stage":
                payload = dict(payload)
                payload["position"] = _render_pos(vid)
                yield _sse("stage", payload)
            elif kind == "done":
                yield _sse("done", payload)
                return
            elif kind == "err":
                yield _sse("error", payload)
                return
    finally:
        try:
            job.subs.remove(q)
        except ValueError:
            pass


@router.get("/{vid}/render/status")
def render_status(vid: int, db: Session = Depends(get_db)):
    """附着前的状态查询（回到页面先问一句「还在跑吗 / 排到第几位」）。"""
    v = db.get(Video, vid)
    if not v:
        raise HTTPException(404, "视频作品不存在或已删除")
    with _RENDER_LOCK:
        job = _RENDER_JOBS.get(vid)
    d = {"running": False, "status": "", "stage": "", "pct": 0, "position": 0,
         "error": "", "render_ready": _render_ready(), "reason": _render_reason()}
    if job is not None:
        d.update({"status": job.status, "stage": job.stage, "pct": job.pct,
                  "position": _render_pos(vid), "error": job.error,
                  "running": job.status in ("queued", "running")})
        if job.report:
            d["report"] = job.report
        if job.status == "done" and job.video:
            d["video"] = job.video
    return d


def resume_queued_renders() -> int:
    """启动时把「排队中」的作品重新入队（PRD 2.2.6 的补口）。

    ⚠️ 不做这件事的话：队列里的片子在重启后**没有执行者**，而启动回收的窗口
       （默认 30 分钟，必须 ≥ 单条渲染超时）又不会立刻清它 —— 表现为「排队中」
       卡住半小时后才变成一句「服务重启中断」。
    """
    n = 0
    db = SessionLocal()
    try:
        rows = db.query(Video).filter(Video.status == "queued").all()
        for v in rows:
            if not _render_ready():
                break
            try:
                _render_blockers(v)
            except HTTPException:
                continue
            _enqueue_render(v.id)
            n += 1
    except BaseException:
        return n
    finally:
        db.close()
    return n


# ---------- 产物 ----------

@router.get("/{vid}/audio")
def get_audio(vid: int, db: Session = Depends(get_db)):
    v = db.get(Video, vid)
    if not v:
        raise HTTPException(404, "视频作品不存在或已删除")
    path = _audio_abspath(v)
    if not path or not path.exists():
        raise HTTPException(404, "音轨文件不存在，请先生成或重新选择来源")
    return FileResponse(path, media_type="audio/mpeg", filename=f"{v.title}.mp3",
                        headers={"Cache-Control": "private, no-cache"})


@router.get("/{vid}/srt")
def export_srt(vid: int, db: Session = Depends(get_db)):
    """导出 SRT 字幕。时间轴与视觉步点**同源**（同一条音轨时间轴，PRD 验收 #7）。"""
    v = db.get(Video, vid)
    if not v:
        raise HTTPException(404, "视频作品不存在或已删除")
    body = pod_svc.script_to_srt(v.script or [])
    if not body.strip():
        raise HTTPException(400, "还没有字幕可用：请先生成讲稿与音轨（字幕时间轴来自音轨）")
    fname = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", v.title or "").strip() or "视频字幕"
    fallback = re.sub(r"[^A-Za-z0-9._-]+", "_", fname).strip("_") or "video"
    disposition = (f'attachment; filename="{fallback}.srt"; '
                   f"filename*=UTF-8''{quote(fname + '.srt')}")
    return PlainTextResponse(body, media_type="text/plain; charset=utf-8",
                             headers={"Content-Disposition": disposition})


@router.get("/{vid}/vtt")
def get_vtt(vid: int, db: Session = Depends(get_db)):
    """播放器内字幕（WebVTT）。与 SRT 导出**同一条时间轴**（同一份 script），只换封装：
    浏览器的 <track> 只认 WebVTT、不认 SRT（PRD 3.10.5「字幕可开关」）。
    ⚠️ 绝不能带 Content-Disposition: attachment —— 那会把它变成下载而不是字幕轨。
    """
    v = db.get(Video, vid)
    if not v:
        raise HTTPException(404, "视频作品不存在或已删除")
    body = pod_svc.script_to_vtt(v.script or [])
    if body.strip() == "WEBVTT":
        raise HTTPException(400, "还没有字幕可用：请先生成讲稿与音轨（字幕时间轴来自音轨）")
    return PlainTextResponse(body, media_type="text/vtt; charset=utf-8",
                             headers={"Cache-Control": "private, no-cache"})


@router.get("/{vid}/file")
def get_file(vid: int, db: Session = Depends(get_db)):
    v = db.get(Video, vid)
    if not v or not v.video_name:
        raise HTTPException(404, "还没有渲染成片")
    path = svc.video_abspath(v.id, v.video_name)
    if not path.exists():
        raise HTTPException(404, "视频文件已丢失，可重新渲染")
    return FileResponse(path, media_type="video/mp4", filename=f"{safe_stem(v.title)}.mp4",
                        headers={"Cache-Control": "private, no-cache",
                                 "Accept-Ranges": "bytes"})


@router.get("/{vid}/poster")
def get_poster(vid: int, db: Session = Depends(get_db)):
    v = db.get(Video, vid)
    if not v or not v.poster_name:
        raise HTTPException(404, "还没有封面帧")
    path = svc.poster_abspath(v.id, v.poster_name)
    if not path.exists():
        raise HTTPException(404, "封面帧文件已丢失")
    return FileResponse(path, media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=60"})


@router.post("/{vid}/reveal")
def reveal_in_folder(vid: int, db: Session = Depends(get_db)):
    """在系统文件管理器中定位成片（开发态浏览器直连也能用，逻辑统一）。"""
    v = db.get(Video, vid)
    if not v:
        raise HTTPException(404, "视频作品不存在或已删除")
    target = None
    if v.video_name:
        p = svc.video_abspath(v.id, v.video_name)
        if p.exists():
            target = p
    if target is None:
        target = _audio_abspath(v)
    if target is None or not target.exists():
        raise HTTPException(404, "还没有可定位的产物文件")
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(target)])
        elif sys.platform.startswith("win"):
            # ⚠️ explorer 的 /select 必须与路径连写，且退出码恒为 1（不能据此判失败）
            subprocess.Popen(f'explorer /select,"{target}"', shell=False)
        else:
            subprocess.Popen(["xdg-open", str(target.parent)])
    except Exception as e:
        raise HTTPException(500, f"无法打开文件位置：{str(e)[:120]}")
    return {"ok": True, "path": str(target), "folder": str(target.parent)}


@router.get("/{vid}/export")
def export_video(vid: int, db: Session = Depends(get_db)):
    """导出成片。

    ⚠️ `talking_head` 皮肤的成片**必须带烧录标识**（PRD 3.7.6 / 验收 #40）——
       标识由渲染层烧进画面（不是元数据），导出即所见。
    """
    v = db.get(Video, vid)
    if not v:
        raise HTTPException(404, "视频作品不存在或已删除")
    if not v.video_name:
        raise HTTPException(400, "还没有渲染成片，无法导出")
    path = svc.video_abspath(v.id, v.video_name)
    if not path.exists():
        raise HTTPException(404, "视频文件已丢失，可重新渲染")
    stem = safe_stem(v.title, fallback="视频")
    disposition = (f'attachment; filename="video.mp4"; '
                   f"filename*=UTF-8''{quote(stem + '.mp4')}")
    _track("video_export", v.id, 0, {"video_bytes": int(v.video_bytes or 0)})
    return FileResponse(path, media_type="video/mp4",
                        headers={"Content-Disposition": disposition,
                                 "Cache-Control": "private, no-cache"})


@router.post("/{vid}/to-note")
def to_note(vid: int, db: Session = Depends(get_db)):
    """转笔记：讲稿 + 分镜 + 要点 → 一篇笔记。

    ⚠️ 幂等（PRD 验收 #39）：用 `anchor.key` 标记来源，同一作品同一版本只生成一篇。
    ⚠️ 视频**不写回复习卡**（不新增 review_cards.source 枚举）—— 见 PRD 2.1 硬性约束 3。
    """
    v = db.get(Video, vid)
    if not v:
        raise HTTPException(404, "视频作品不存在或已删除")
    payload = svc.to_note_payload(v)
    # ⚠️⚠️ 幂等键必须覆盖**笔记的全部输入**，不能只覆盖讲稿。
    #    `to_note_payload` 的输出含 标题 / 视觉方案 / 时长 / 分镜明细 / 结构要点；
    #    而 `v.id` 是**可复用主键**（SQLite 删掉表里最大那一行后会复用）。历史实现
    #    用 `video:{id}:{讲稿指纹}` 作键 → 「删作品甲 → 新建乙复用同一 id → 讲稿
    #    逐字相同但分镜/要点不同」时，乙会命中甲留下的旧笔记（返回 created=False
    #    且内容张冠李戴，已实测坐实）。改用**内容指纹**：
    #    同一作品、同一篇内容，只生成一篇。
    key = f"video:{v.id}:{hashlib.md5(payload['content'].encode('utf-8')).hexdigest()[:12]}"
    # 幂等判据用**指纹**而不是「有没有笔记」：讲稿改了 / 分镜变了就是新的一版，
    # 应该能再沉淀一篇（而不是静默复用旧的那篇）。
    rows = db.query(Note).all()
    for n in rows:
        a = n.anchor or {}
        if not isinstance(a, dict) or a.get("kind") != "video_script":
            continue
        hit = a.get("key") == key
        # 兼容**存量笔记**（键还是旧的「id+讲稿指纹」格式）：键不同时，只有内容
        # **逐字相同**才认领 —— 既不重复生成，也绝不会把「同讲稿、不同分镜/要点」
        # 的旧笔记误认成当前这一版（旧实现在这个组合下会张冠李戴）。
        if not hit and a.get("video_id") == v.id and (n.content or "") == payload["content"]:
            hit = True
        if hit:
            _track("video_to_note", v.id, 0, {"created": 0})
            return {"ok": True, "note_id": n.id, "created": False}
    note = Note(material_id=None, title=payload["title"],
                content=payload["content"], source_type="video_script",
                anchor={"kind": "video_script", "key": key, "video_id": v.id})
    db.add(note)
    db.commit()
    db.refresh(note)
    _track("video_to_note", v.id, 0, {"created": 1})
    return {"ok": True, "note_id": note.id, "created": True}


# ---------- 形象上传（talking_head 用） ----------

# 上传端 aid 生成式是 `u-{14位时间戳}-{4位数字}`；白名单按同源收紧，
# 但留了位宽余量，避免将来生成式微调后取图集体 404。
_AVATAR_ID_RE = re.compile(r"^u-[0-9]{8,20}-[0-9]{1,8}$")


@router.delete("/avatar/{avatar_id}")
def delete_avatar(avatar_id: str, db: Session = Depends(get_db)):
    """删除一个**用户上传的**形象。

    ⚠️ 预置形象（preset_01/02/03）不是文件、永远不可删 —— 白名单正则只放行 `u-` 开头的 id
       从根上挡住；显式再判一次 preset_ 是为了给出可读文案（而不是笼统 404）。
    ⚠️ 不静默改引用它的作品：删掉后那些作品渲染时回落到**预置形象**（preset_01），
       但作品表的 avatar_id 保持原值（改不改由用户自己决定）。返回 used_by 让前端如实告知。
    ⚠️ 成功与否以服务层返回的状态为准（见 svc.delete_avatar 四态），不能用「没抛异常」判。
    """
    aid = (avatar_id or "").strip()
    if not _AVATAR_ID_RE.match(aid):
        if aid.startswith("preset_"):
            raise HTTPException(400, "预置形象不可删除")
        raise HTTPException(404, "形象不存在")
    used_by = db.query(func.count(Video.id)).filter(Video.avatar_id == aid).scalar() or 0
    st = svc.delete_avatar(aid)
    if st == "failed":
        raise HTTPException(500, "删除失败，请稍后重试")
    if st == "invalid":
        raise HTTPException(404, "形象不存在")
    _track("video_avatar_delete", 0, 0, {"used_by": int(used_by)})
    return {"ok": True, "avatar_id": aid, "used_by": int(used_by)}


@router.get("/avatar/{avatar_id}/file")
def get_avatar_file(avatar_id: str):
    """形象原图（**预置形象 + 用户上传**；列表缩略图共用这个入口）。

    ⚠️ 预置形象是包内 `services/assets/avatars/*.jpg`（有后缀、MIME 也正确）；
       上传的落盘是 `{aid}.img`（无后缀）→ MIME 必须嗅探（见 sniff_image_mime），
       否则浏览器按 octet-stream 处理，`<img>` 可能不显示。
    ⚠️ `avatar_id` 来自 URL 路径 → 白名单校验（与 upload_avatar 的生成式同源），
       再兜一层「解析后的真实路径必须仍在 avatars_dir() 下」防路径穿越。
    ⚠️ FileResponse 提供 ETag / Last-Modified；再显式加长 Cache-Control —— url 带
       `?v={mtime}`，同一 url 的内容永不改变（换图后 mtime 变、url 跟着变），
       所以可以放心长缓存。列表每次刷新都会请求这些图，没有它就会反复重下整张照片。
    """
    aid = (avatar_id or "").strip()
    # ⚠️ 预置形象必须**先走**：它的 id（`preset_01`）**不匹配**上传项的 `u-` 白名单正则，
    #    放到后面就会被 404 掉，列表与舞台页的预置缩略图全裂。
    #    路径由 `svc.preset_file()` 从 BUILTIN_AVATARS 的 file 白名单里取，
    #    **不拼接 URL 传来的任何字符串** —— 路径穿越在这个接口上同样致命。
    pre = svc.preset_file(aid)
    if pre is not None:
        try:
            head = pre.read_bytes()[:16]
        except BaseException:
            raise HTTPException(404, "形象不存在")
        resp = FileResponse(pre, media_type=svc.sniff_image_mime(head))
        resp.headers["Cache-Control"] = "private, max-age=31536000"
        return resp
    if not _AVATAR_ID_RE.match(aid):
        raise HTTPException(404, "形象不存在")
    root = svc.avatars_dir()
    try:
        p = (root / f"{aid}.img").resolve()
        inside = p.parent == root.resolve()
    except BaseException:
        raise HTTPException(404, "形象不存在")
    if not inside or not p.is_file():
        raise HTTPException(404, "形象不存在")
    try:
        head = p.read_bytes()[:16]
    except BaseException:
        raise HTTPException(404, "形象不存在")
    resp = FileResponse(p, media_type=svc.sniff_image_mime(head))
    resp.headers["Cache-Control"] = "private, max-age=31536000"
    return resp


@router.get("/avatar/{avatar_id}/motion")
def get_avatar_motion(avatar_id: str):
    """预置形象的**动作视频**（无缝循环 WebM）。

    ⚠️ 出片时走的是另一条路（`stage.py::_avatar_motion` 把文件拷进作品目录 + 相对路径），
       因为渲染页是 `file://` 加载的 —— 绝对 URL 在那种上下文里根本取不到后端。
       这个接口是给**前端与验收探针**用的（确认「这个形象动得起来」）。
    ⚠️ 与 `get_avatar_file` 同一条纪律：**先走预置白名单**，否则 `preset_01` 会被
       上传项的 `u-` 正则 404 掉。
    ⚠️ 只支持预置：上传形象的动作视频属未来扩展位（`_avatars/{aid}.motion`），此处一律 404。
    """
    p = svc.preset_video((avatar_id or "").strip())
    if p is None:
        raise HTTPException(404, "该形象没有动作视频")
    resp = FileResponse(p, media_type="video/webm")
    resp.headers["Cache-Control"] = "private, max-age=31536000"
    return resp


@router.post("/avatar/upload")
async def upload_avatar(file: UploadFile = File(...)):
    """上传形象单图。

    ⚠️ 合规（PRD 3.7.6）：形象只允许预置素材或**用户本人上传**；
       上传入口必须提示「请勿上传他人肖像」。
    ⚠️ 竖版约束：宽高比 ≥ 3:4、短边 ≥ 512px —— 横向图放进 16:9 里会被压成一条。
    """
    raw = await file.read()
    if len(raw) > svc.AVATAR_UPLOAD_MAX_MB * 1024 * 1024:
        raise HTTPException(400, f"图片过大（上限 {svc.AVATAR_UPLOAD_MAX_MB}MB）")
    try:
        import io
        from PIL import Image
        with Image.open(io.BytesIO(raw)) as im:
            w, h = im.size
    except BaseException:
        raise HTTPException(400, "无法识别这张图片，请换一张试试")
    if w <= 0 or h <= 0:
        raise HTTPException(400, "图片尺寸异常")
    if h < w * 4 / 3 - 1e-6 or min(w, h) < 512:
        raise HTTPException(
            400, "请上传竖版图片（宽高比 ≥ 3:4，短边 ≥ 512px）")
    aid = f"u-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{abs(hash(raw)) % 10000:04d}"
    (svc.avatars_dir() / f"{aid}.img").write_bytes(raw)
    return {"ok": True, "avatar_id": aid,
            "note": "请确认这是你本人或你有权使用的形象；请勿上传他人肖像。"}


# ---------- 自定义背景（解压型皮肤 radio / night / ambient） ----------

def _bg_file_or_404(bg_id: str) -> Path:
    """解析背景原图路径；非法 id / 越界 / 文件不存在一律 404。

    ⚠️ id 来自 URL 路径 → 先过白名单正则，再兜一层「解析后的真实路径必须仍在
       `user_dir()` 下」防路径穿越（与 get_avatar_file 同一手法）。
    """
    b = (bg_id or "").strip()
    if not bg_svc.valid_id(b):
        raise HTTPException(404, "背景不存在")
    root = bg_svc.user_dir()
    try:
        p = (root / f"{b}.img").resolve()
        inside = p.parent == root.resolve()
    except BaseException:
        raise HTTPException(404, "背景不存在")
    if not inside or not p.is_file():
        raise HTTPException(404, "背景不存在")
    return p


def _image_response(path: Path, mime: str) -> FileResponse:
    """图片响应：显式长缓存。

    ⚠️ url 带 `?v=<指纹>`，同一 url 的内容永不改变（换图后指纹变、url 跟着变），
       所以可以放心长缓存 —— 面板每次刷新都会请求这些图，没有它就会反复重下整张照片。
    """
    resp = FileResponse(path, media_type=mime)
    resp.headers["Cache-Control"] = "private, max-age=31536000"
    return resp


@router.post("/background/upload")
async def upload_background(file: UploadFile = File(...)):
    """上传一张自定义背景图（解压型皮肤）。

    ⚠️ 与形象上传的**合规口径不同**：背景通常是风景 / 书桌 / 抽象图，不涉及肖像，
       所以这里**不写**「请勿上传他人肖像」（写了反而误导）；改提示「确认你有权使用」。
    ⚠️ 尺寸下限（短边 ≥ 720）是**必须**的：背景按 `cover` 铺满 1920×1080，
       小图会被放大到糊 —— 与其让用户出片后才发现，不如在上传时就拒掉。
    ⚠️ 幂等：同一张图重复上传返回同一条记录（与 bgm.add_user_track 同一策略）。
    """
    raw = await file.read()
    try:
        entry = bg_svc.add_user_background(file.filename or "", raw)
    except ValueError as e:
        raise HTTPException(400, str(e))
    _track("video_bg_upload", 0, detail={"bytes": int(entry.get("bytes") or 0)})
    return {"ok": True, "bg_id": entry["id"], "w": entry.get("w"), "h": entry.get("h"),
            "note": "请确认你有权使用这张图；背景只用于你自己的作品。"}


@router.delete("/background/{bg_id}")
def delete_background(bg_id: str, db: Session = Depends(get_db)):
    """删除一个**用户上传的**背景。内置背景不是文件、永不可删。

    ⚠️ 不静默改引用它的作品：删掉后那些作品渲染时回落到皮肤自带画面，
       但作品表的 bg_id 保持原值（改不改由用户自己决定）。返回 used_by 让前端如实告知。
    ⚠️ 成功与否以服务层返回的状态为准（四态），不能用「没抛异常」判 ——
       本机删除走 safe-delete 护栏（抛 SystemExit，`except Exception` 接不住）。
    """
    b = (bg_id or "").strip()
    if not bg_svc.valid_id(b):
        raise HTTPException(404, "背景不存在")
    used_by = db.query(func.count(Video.id)).filter(Video.bg_id == b).scalar() or 0
    st = bg_svc.delete_user_background(b)
    if st == "failed":
        raise HTTPException(500, "删除失败，请稍后重试")
    if st == "invalid":
        raise HTTPException(404, "背景不存在")
    _track("video_bg_delete", 0, detail={"used_by": int(used_by)})
    return {"ok": True, "bg_id": b, "used_by": int(used_by)}


@router.get("/background/{bg_id}/file")
def get_background_file(bg_id: str):
    """背景**原图**（渲染器注入的 CSS 背景 + 「查看大图」共用这个入口）。

    ⚠️ 落盘是 `{bid}.img`（无后缀）→ MIME 必须嗅探（见 sniff_image_mime），
       否则浏览器按 octet-stream 处理，`<img>` / CSS 背景可能不显示。
    """
    p = _bg_file_or_404(bg_id)
    return _image_response(p, svc.sniff_image_mime(p.read_bytes()[:16]))


@router.get("/background/{bg_id}/thumb")
def get_background_thumb(bg_id: str):
    """背景**缩略图**（面板列表用）。

    ⚠️ 缩略图缺失时**回落原图**（`url_of` 的判定与这里同源）——
       绝不返回 404 让列表出现一个破图框。
    """
    p = _bg_file_or_404(bg_id)
    t = bg_svc.bg_thumb_abspath(bg_id)
    try:
        if t is not None and t.is_file():
            return _image_response(t, "image/jpeg")
    except BaseException:
        pass
    return _image_response(p, svc.sniff_image_mime(p.read_bytes()[:16]))


# ---------- 视觉方案示例图 ----------

def skin_previews_ensure() -> None:
    """触发一次后台渲染（幂等、非阻塞）。任何异常都吞在这里。

    ⚠️ 单独包一层 try 是为了让 `/options` **永远不会因为示例图而失败**：
       示例图是锦上添花，缺了最多是不显示样例，绝不该让整个选项接口 500。
    """
    try:
        preview_svc.ensure_async()
    except BaseException:
        pass


@router.get("/skin-previews/list")
def skin_previews_list():
    """示例图清单（**只读缓存，立刻返回**）。

    ⚠️ 刻意做成**两段**路径：`GET /videos/{vid}` 是单段且声明在前，
       单段路径会先被它匹配掉（`vid` 是 int → 422）。
    ⚠️ 这个接口**不触发渲染**（那是 `/options` 的职责）——它只是「现在有什么」的快照，
       供前端在后台渲染完成后刷新一次。
    """
    return preview_svc.list_previews()


@router.get("/skin-previews/{name}")
def skin_preview_file(name: str):
    """一张示例图（前端 `<img src>` 直连；与清单里的 url 一一对应）。

    ⚠️ `name` 来自 URL 路径 → 先卡 `.jpg` 后缀、再让服务层用一个**白名单**集合判皮肤名，
       两道都过才去拼路径（不存在路径穿越面）。
    """
    n = (name or "").strip()
    if not n.endswith(".jpg"):
        raise HTTPException(404, "示例图不存在")
    p = preview_svc.preview_file(n[:-4])
    if p is None:
        raise HTTPException(404, "示例图不存在")
    return _image_response(p, "image/jpeg")
