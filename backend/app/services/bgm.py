"""背景音乐素材服务

【素材从哪来 —— 两条来源，落到同一规格，混音时无需区分】

1. **内置氛围音：程序合成（numpy），不打包任何音频文件。**
   为什么不用现成音乐：本项目分发的是免费绿色版工具，内置音乐一旦有版权瑕疵，
   风险直接由用户承担。合成的氛围音零版权、零体积，而且「低存在感、不抢人声」
   本来就是播客背景音的正确形态 —— 带旋律的歌反而会干扰听讲。

2. **用户上传：** 用户用自己的音乐。上传时统一转码 + 响度归一化 + 无缝化，
   之后与内置素材走完全相同的路径。

【入库标准化（关键）】
不管来源，落盘前都做三件事，否则混音时会出问题：
- `shape_spectrum`：切掉 <100Hz 与 >6.5kHz。低频在小喇叭上放不出来只会糊成一团，
  高频（齿音区）会和人声打架。
- `make_seamless`：尾部交叉淡入头部，使素材**可无缝循环** —— 播客常比素材长。
- `normalize_rms`：统一到 -20dBFS。不同曲子原始电平差很多（雨声 vs 钢琴），
  不归一化的话用户换一首就会忽大忽小。
"""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime
from pathlib import Path

import numpy as np

from ..core.config import settings
from . import mixer

RATE = mixer.SAMPLE_RATE
LOOP_SECONDS = 12.0
TARGET_RMS_DB = -20.0

MAX_UPLOAD_MB = 40
MIN_UPLOAD_SECONDS = 5
MAX_UPLOAD_SECONDS = 20 * 60

# 内置曲目表。desc 说明「听起来是什么」，scene 给用户按场景快速挑。
BUILTIN_TRACKS: list[dict] = [
    {"id": "bi-pad-soft", "name": "柔和琴垫", "desc": "暖色和弦缓缓铺开，安静不抢话", "scene": "专注"},
    {"id": "bi-night-calm", "name": "夜晚静谧", "desc": "低频垫底，偶有清脆点缀，适合深夜", "scene": "深夜"},
    {"id": "bi-rain-light", "name": "细雨", "desc": "均匀雨声，屏蔽环境干扰", "scene": "沉浸"},
    {"id": "bi-cafe-warm", "name": "咖啡馆", "desc": "细碎人声噪底，轻松有生活感", "scene": "轻松"},
    {"id": "bi-white-soft", "name": "白噪音", "desc": "最中性，几乎感觉不到它的存在", "scene": "极简"},
    {"id": "bi-lofi-drift", "name": "Lo-fi 轻摇", "desc": "舒缓琶音，缓慢律动", "scene": "律动"},
]

_BUILD_LOCK = threading.Lock()


# ---------- 目录 ----------

def bgm_root() -> Path:
    p = Path(settings.data_dir) / "bgm"
    p.mkdir(parents=True, exist_ok=True)
    return p


def builtin_dir() -> Path:
    p = bgm_root() / "builtin"
    p.mkdir(parents=True, exist_ok=True)
    return p


def user_dir() -> Path:
    p = bgm_root() / "user"
    p.mkdir(parents=True, exist_ok=True)
    return p


def previews_dir() -> Path:
    p = bgm_root() / "previews"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _index_path() -> Path:
    return user_dir() / "index.json"


def track_path(track_id: str) -> Path | None:
    """id → 文件路径。`bi-` 内置 / `u-` 用户上传。"""
    if not track_id:
        return None
    if track_id.startswith("bi-"):
        return builtin_dir() / f"{track_id}.mp3"
    if track_id.startswith("u-"):
        return user_dir() / f"{track_id}.mp3"
    return None


# ---------- 程序合成（内置氛围音） ----------

def _gen_pad_soft(sec: float = LOOP_SECONDS) -> np.ndarray:
    """柔和琴垫：四个和弦依次铺开，长衰减 + 轻微颤音"""
    n = int(sec * RATE)
    chords = [
        # 音域刻意选在 165~392Hz：太低（C3 以下）笔记本/手机喇叭放不出来，
        # 只会白占动态余量还显得糊。
        [196.00, 261.63, 329.63, 392.00],   # G3  C4  E4  G4
        [164.81, 220.00, 261.63, 329.63],   # E3  A3  C4  E4
        [174.61, 220.00, 261.63, 349.23],   # F3  A3  C4  F4
        [196.00, 246.94, 293.66, 392.00],   # G3  B3  D4  G4
    ]
    bar = sec / len(chords)
    out = np.zeros(n, np.float64)
    for i, chord in enumerate(chords):
        start = int(i * bar * RATE)
        seg = n - start
        if seg <= 0:
            continue
        j = np.arange(seg) / RATE
        env = np.minimum(1.0, j / (bar * 0.20)) * np.exp(-j / (bar * 1.30))
        for k, f in enumerate(chord):
            amp = 1.0 / (1.0 + k * 0.85)
            vib = 1.0 + 0.0016 * np.sin(2 * np.pi * 0.7 * j + k * 1.3)
            tone = np.sin(2 * np.pi * f * vib * j)
            tone += 0.20 * np.sin(2 * np.pi * f * 2 * vib * j)
            tone += 0.07 * np.sin(2 * np.pi * f * 3 * vib * j)
            out[start:] += amp * env * tone
    return out


def _gen_night_calm(sec: float = LOOP_SECONDS) -> np.ndarray:
    """夜晚静谧：低频持续音（带泛音）+ 极慢起伏 + 偶发高音点缀

    ⚠️ 泛音不能省。纯低频正弦的能量几乎全在 100Hz 以下，笔记本/手机外放
    根本放不出来 —— 实测中频(300-3000Hz)占比只有 1.1%，听起来「没声音」。
    叠 2~5 次泛音把能量带到 300~1100Hz 后，任何设备都能听到。
    """
    n = int(sec * RATE)
    rng = np.random.default_rng(20260911)
    t = np.arange(n) / RATE
    out = np.zeros(n, np.float64)
    for i, f in enumerate((110.00, 164.81, 220.00)):
        amp = 0.50 / (1.0 + i * 0.7)
        for k, g in ((1, 1.00), (2, 0.42), (3, 0.20), (4, 0.10), (5, 0.05)):
            out += amp * g * np.sin(2 * np.pi * f * k * t + i * 0.8)
    out *= 0.55 + 0.45 * np.sin(2 * np.pi * 0.06 * t)
    for _ in range(int(sec * 1.6)):
        p = int(rng.integers(0, max(1, n - int(0.35 * RATE))))
        ln = int(rng.uniform(0.18, 0.35) * RATE)
        f = rng.uniform(1800, 3200)
        j = np.arange(ln) / RATE
        env = np.exp(-j / 0.09) * np.minimum(1.0, j / 0.012)
        out[p:p + ln] += env * np.sin(2 * np.pi * f * j) * rng.uniform(0.06, 0.14)
    return out


def _gen_rain_light(sec: float = LOOP_SECONDS) -> np.ndarray:
    """细雨：带通噪声做「沙沙」底 + 随机雨滴"""
    n = int(sec * RATE)
    rng = np.random.default_rng(30301)
    noise = rng.normal(0, 1, n).astype(np.float32)
    bed = mixer.shape_spectrum(noise, lo_hz=420, hi_hz=5200, order=1.5)

    drops = np.zeros(n, np.float32)
    for _ in range(int(sec * 22)):
        p = int(rng.integers(0, max(1, n - int(0.06 * RATE))))
        ln = int(rng.uniform(0.012, 0.05) * RATE)
        j = np.arange(ln) / RATE
        drops[p:p + ln] += (np.exp(-j / 0.008) * rng.normal(0, 1, ln)
                            * rng.uniform(0.15, 0.40))
    drops = mixer.shape_spectrum(drops, lo_hz=900, hi_hz=7000, order=1.0)

    t = np.arange(n) / RATE
    out = (bed * 0.85 + drops * 0.45) * (0.85 + 0.15 * np.sin(2 * np.pi * 0.09 * t))
    return out.astype(np.float32)


def _gen_cafe_warm(sec: float = LOOP_SECONDS) -> np.ndarray:
    """咖啡馆：多层带通噪声叠成 1/f 底噪 + 偶发杯碟清脆声"""
    n = int(sec * RATE)
    rng = np.random.default_rng(40401)
    white = rng.normal(0, 1, n)
    bed = np.zeros(n)
    for lo, hi, gain in ((80, 300, 0.50), (200, 900, 0.45),
                         (700, 2500, 0.35), (2000, 6000, 0.20)):
        bed += mixer.shape_spectrum(white, lo_hz=lo, hi_hz=hi, order=1.2) * gain
    for _ in range(int(sec * 1.8)):
        p = int(rng.integers(0, max(1, n - int(0.25 * RATE))))
        ln = int(0.25 * RATE)
        j = np.arange(ln) / RATE
        f = rng.uniform(1600, 3600)
        bed[p:p + ln] += (np.exp(-j / 0.035) * np.sin(2 * np.pi * f * j)
                          * rng.uniform(0.08, 0.20))
    t = np.arange(n) / RATE
    bed = bed * (0.80 + 0.20 * np.sin(2 * np.pi * 0.13 * t + 1.1))
    return bed


def _gen_white_soft(sec: float = LOOP_SECONDS) -> np.ndarray:
    """白噪音：温和带通的白噪，最中性"""
    n = int(sec * RATE)
    rng = np.random.default_rng(50501)
    noise = rng.normal(0, 1, n).astype(np.float32)
    out = mixer.shape_spectrum(noise, lo_hz=120, hi_hz=5500, order=1.4)
    t = np.arange(n) / RATE
    return out * (0.90 + 0.10 * np.sin(2 * np.pi * 0.05 * t))


def _gen_lofi_drift(sec: float = LOOP_SECONDS) -> np.ndarray:
    """Lo-fi 轻摇：舒缓琶音，柔和音色"""
    n = int(sec * RATE)
    notes = [220.00, 261.63, 329.63, 440.00, 329.63, 261.63,
             174.61, 220.00, 261.63, 349.23, 261.63, 220.00,
             196.00, 246.94, 329.63, 392.00, 329.63, 246.94,
             196.00, 246.94, 293.66, 392.00, 293.66, 246.94]
    step = sec / len(notes)
    out = np.zeros(n, np.float64)
    for i, f in enumerate(notes):
        start = int(i * step * RATE)
        ln = min(int(step * 2.4 * RATE), n - start)
        if ln <= 0:
            continue
        j = np.arange(ln) / RATE
        env = np.minimum(1.0, j / 0.012) * np.exp(-j / 0.55)
        tone = (np.sin(2 * np.pi * f * j) + 0.30 * np.sin(2 * np.pi * f * 2 * j)
                + 0.10 * np.sin(2 * np.pi * f * 3 * j))
        out[start:start + ln] += env * tone * 0.5
    out = mixer.shape_spectrum(out.astype(np.float32), lo_hz=90, hi_hz=4200, order=1.2)
    # 轻微「磁带抖动」，lo-fi 的味道
    t = np.arange(n) / RATE
    out *= (1.0 + 0.006 * np.sin(2 * np.pi * 0.3 * t))
    return out


_GENERATORS = {
    "bi-pad-soft": _gen_pad_soft,
    "bi-night-calm": _gen_night_calm,
    "bi-rain-light": _gen_rain_light,
    "bi-cafe-warm": _gen_cafe_warm,
    "bi-white-soft": _gen_white_soft,
    "bi-lofi-drift": _gen_lofi_drift,
}


def _standardize(pcm: np.ndarray) -> np.ndarray:
    """入库标准化：频段整形 → 无缝化 → 响度归一化（顺序不可换）。"""
    pcm = mixer.shape_spectrum(pcm, lo_hz=100, hi_hz=6500, order=1.3)
    pcm = mixer.make_seamless(pcm)
    return mixer.normalize_rms(pcm, TARGET_RMS_DB)


def ensure_builtin() -> None:
    """确保内置素材已生成（首次调用时现生成，约 2 秒；之后读缓存文件）。"""
    pending = [t for t in BUILTIN_TRACKS
               if not (builtin_dir() / f"{t['id']}.mp3").exists()]
    if not pending:
        return
    with _BUILD_LOCK:
        for t in BUILTIN_TRACKS:
            path = builtin_dir() / f"{t['id']}.mp3"
            if path.exists():
                continue
            gen = _GENERATORS.get(t["id"])
            if gen is None:
                continue
            try:
                path.write_bytes(mixer.encode_mp3(_standardize(gen())))
            except Exception:
                # 单首失败不该拖垮整个曲库（缺失的曲目只是不出现在列表里）
                try:
                    if path.exists():
                        path.unlink()
                except BaseException:
                    pass


# ---------- 用户上传 ----------

# index.json 的读取缓存：列表接口每条记录都会调 track_name()，一次列表就是 N 次读盘。
# 键取 (mtime, size)：文件被外部工具就地改写时也能靠 size 变化感知。
_index_cache: tuple[tuple[float, int], list[dict]] | None = None


def _load_index() -> list[dict]:
    global _index_cache
    p = _index_path()
    if not p.exists():
        return []
    try:
        st = p.stat()
        stamp = (st.st_mtime, st.st_size)
        if _index_cache is not None and _index_cache[0] == stamp:
            return [dict(r) for r in _index_cache[1]]   # 给副本，调用方改动不污染缓存
        data = json.loads(p.read_text(encoding="utf-8"))
        rows = [d for d in data if isinstance(d, dict) and d.get("id")]
    except Exception:
        return []
    _index_cache = (stamp, rows)
    return [dict(r) for r in rows]


def _save_index(rows: list[dict]) -> None:
    global _index_cache
    try:
        _index_path().write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        _index_cache = None     # 写盘后缓存作废（mtime 粒度可能不足一秒）
    except Exception:
        pass


def add_user_track(filename: str, data: bytes) -> dict:
    """上传背景音乐：校验 → 转码 → 标准化 → 落盘。

    id 取文件内容的 md5 前 8 位，所以**同一首歌重复上传是幂等的**
    （不会在列表里堆出多条同名项）。
    """
    size_mb = len(data) / 1024 / 1024
    if size_mb > MAX_UPLOAD_MB:
        raise ValueError(f"文件过大（{size_mb:.1f}MB），请控制在 {MAX_UPLOAD_MB}MB 以内")
    if not data:
        raise ValueError("文件内容为空")

    # ⚠️ 先按**容器头部**粗筛时长，再决定要不要解码：一个 40MB 的音频解成
    # float32 / 24kHz / 单声道要占数百 MB 内存，而它本来就会被「太长」拒掉。
    # 探针读不到可靠时长（无 Xing 信息的 VBR MP3 等）时返回 None，照旧走解码后
    # 的精确判断；1.25 的宽容系数是为了「探针略有偏差 → 合法文件被误拒」。
    est = mixer.probe_seconds(data)
    if est is not None and est > MAX_UPLOAD_SECONDS * 1.25:
        raise ValueError(
            f"音频太长（约 {est / 60:.0f} 分钟），请控制在 {MAX_UPLOAD_SECONDS // 60} 分钟以内")

    # 能解码就说明格式可用（mp3/wav/m4a/aac/flac/ogg 都支持）
    pcm, _ = mixer.decode(data)
    seconds = pcm.size / RATE
    if seconds < MIN_UPLOAD_SECONDS:
        raise ValueError(f"音频太短（{seconds:.0f} 秒），至少需要 {MIN_UPLOAD_SECONDS} 秒")
    if seconds > MAX_UPLOAD_SECONDS:
        raise ValueError(f"音频太长（{seconds / 60:.0f} 分钟），请控制在 {MAX_UPLOAD_SECONDS // 60} 分钟以内")

    pcm = mixer.trim_silence_edges(pcm)      # 用户音乐常有首尾静音
    pcm = _standardize(pcm)
    mp3 = mixer.encode_mp3(pcm)

    tid = "u-" + hashlib.md5(data).hexdigest()[:8]
    (user_dir() / f"{tid}.mp3").write_bytes(mp3)
    _clear_previews(tid)                             # 内容变了 → 试听缓存作废

    display = Path(filename or "未命名").stem.strip() or "未命名"
    rows = [r for r in _load_index() if r.get("id") != tid]
    entry = {"id": tid, "name": display, "seconds": round(seconds),
             "bytes": len(mp3), "added_at": datetime.now().isoformat(timespec="seconds")}
    rows.insert(0, entry)
    _save_index(rows)
    return entry


def delete_user_track(track_id: str) -> bool:
    """删除用户上传的素材（内置曲目不可删）。绝不抛错。

    返回值以**索引里原本是否存在**为准，而不是「文件是否被删掉」：
    曲目文件可能早已被用户手工删掉，此时仍应清掉索引记录并返回成功；
    反过来索引里没有它，就是真的不存在，调用方据此回 404。
    （早先只看文件删除结果，会出现「接口报 404、索引记录却已被删」——报错但操作生效。）
    """
    if not track_id.startswith("u-"):
        return False
    rows = _load_index()
    existed = any(r.get("id") == track_id for r in rows)
    for p in (user_dir() / f"{track_id}.mp3",):
        try:
            if p.exists():
                p.unlink()
        except BaseException:
            pass
    _clear_previews(track_id)
    if existed:
        _save_index([r for r in rows if r.get("id") != track_id])
    return existed


# ---------- 对外接口 ----------

def list_tracks() -> dict:
    ensure_builtin()
    builtin = []
    for t in BUILTIN_TRACKS:
        p = builtin_dir() / f"{t['id']}.mp3"
        if not p.exists():
            continue
        builtin.append({**t, "kind": "builtin",
                        "seconds": round(mixer.duration_ms(p.read_bytes()) / 1000)})
    mine = [{**r, "kind": "user"} for r in _load_index()
            if (user_dir() / f"{r['id']}.mp3").exists()]
    return {"builtin": builtin, "mine": mine}


def track_name(track_id: str) -> str:
    if not track_id:
        return ""
    for t in BUILTIN_TRACKS:
        if t["id"] == track_id:
            return t["name"]
    for r in _load_index():
        if r["id"] == track_id:
            return r.get("name") or track_id
    return ""


def resolve(track_id: str) -> Path:
    """取素材文件；不存在则抛错（调用方据此提示用户重新选择）。"""
    p = track_path(track_id)
    if p is None:
        raise ValueError(f"未知的背景音乐：{track_id}")
    if not p.exists():
        # 内置素材可能尚未生成
        if track_id.startswith("bi-"):
            ensure_builtin()
        if not p.exists():
            raise ValueError(f"背景音乐《{track_name(track_id) or track_id}》已不存在")
    return p


def _preview_cache_path(track_id: str) -> Path:
    """试听缓存路径：**带上素材文件的 mtime+size 指纹**。

    只按 track_id 命名的话，素材被重新生成 / 重新上传（内容变了、id 没变）之后
    试听仍然是旧片段 —— 用户改完参数点试听，听到的还是上一版，无法判断改动有没有生效。
    """
    src = track_path(track_id)
    stamp = "x"
    if src is not None:
        try:
            st = src.stat()
            stamp = hashlib.md5(
                f"{st.st_size}:{st.st_mtime_ns}".encode()).hexdigest()[:10]
        except OSError:
            stamp = "none"
    return previews_dir() / f"{track_id}-{stamp}.mp3"


def _clear_previews(track_id: str) -> None:
    """清掉某首曲目的全部试听缓存（含早期「不带指纹」的旧命名）。绝不抛错。"""
    try:
        for p in list(previews_dir().glob(f"{track_id}*.mp3")):
            try:
                p.unlink()
            except BaseException:
                pass
    except BaseException:
        pass


def preview_clip(track_id: str, seconds: float = 12.0) -> bytes:
    """试听片段：截前 N 秒并加淡出，避免每次试听都传整首歌。

    首次现场截取（约 0.2 秒），之后命中缓存；缓存 key 含素材指纹（见上）。
    """
    cache = _preview_cache_path(track_id)
    if cache.exists():
        try:
            return cache.read_bytes()
        except Exception:
            pass
    path = resolve(track_id)
    pcm, _ = mixer.decode(path)
    clip = pcm[:int(seconds * RATE)]
    clip = mixer.apply_fades(clip, fade_in_ms=30, fade_out_ms=1000)
    out = mixer.encode_mp3(clip)
    try:
        _clear_previews(track_id)      # 素材已变 → 旧指纹的缓存作废，避免堆积
        cache.write_bytes(out)
    except Exception:
        pass
    return out


def exists(track_id: str) -> bool:
    if not track_id:
        return False
    if track_id.startswith("bi-"):
        ensure_builtin()
    p = track_path(track_id)
    return bool(p and p.exists())
