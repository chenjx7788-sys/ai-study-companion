"""混音服务：人声 + 背景音乐 → 合成一条 MP3。

【为什么不需要 ffmpeg】
PyAV(av) 随 faster-whisper 进入依赖，它**自带 ffmpeg 的编解码库**：
解码 / 重采样 / 编码都能做，无需外挂 ffmpeg 可执行文件
→ 绿色版分发不必额外打包二进制，也不新增任何依赖。

【⚠️ 输出规格必须与 edge-tts 完全一致】
项目全局用「字节数 ÷ 6 = 毫秒」算时长（见 tts.BYTES_PER_MS），所以产物必须是
MPEG-2 Layer III / 24kHz / 48kbps / mono 且 **无 ID3 头**。
→ 编码时必须传 options={"id3v2_version": "0", "write_xing": "0"}。
  不关掉 ID3，头部会多出标签字节，时长与进度条全线错位（实测产物帧头会从
  FF F3 64 C4 变成 00 00 00 00）。

【⚠️ 编码器固有延迟 45ms —— 调用方必须补偿】
libmp3lame 会在流开头插入固定静音，实测 **45.0ms**：
1.8s / 135s / 150.5s / 160.1s 四种长度下极差 0.0ms（常量，与内容长度无关）。
所以混音产物中所有内容都比原始 PCM 晚 45ms。
→ 调用方必须把逐句时间戳整体 +ENCODER_DELAY_MS，否则「点句子跳转 / 逐句高亮」
  会整体滞后。人耳虽难分辨 45ms，但补偿成本为零，没有理由不补。
"""
from __future__ import annotations

import io
import math
from pathlib import Path

import av
import numpy as np

# ---------- 输出规格（与 tts.py 保持一致，勿随意改动） ----------
SAMPLE_RATE = 24000
BIT_RATE = 48000
BYTES_PER_MS = 6.0

# 关闭 ID3v2 与 Xing：产物才能是「纯 MP3 帧流」，与 edge-tts 同规格
_ENCODE_OPTIONS = {"id3v2_version": "0", "write_xing": "0"}

# 编码器固有延迟（实测常量，四种长度极差 0ms）
ENCODER_DELAY_MS = 45.0

# ducking 默认参数：人声出现时 BGM 再降 8dB，让位给人声
# ducking 参数经实测调优（脚本句间停顿仅 280ms，参数必须与之匹配）
DUCK_DB = -8.0
DUCK_THRESHOLD_DB = -42.0    # 人声能量阈值：低于此值视为停顿（TTS 句间是纯零静音，区分很干净）
DUCK_ATTACK_MS = 60.0        # 人声出现 → 快速压下，避免句首几个字被盖住
DUCK_RELEASE_MS = 260.0      # 人声结束 → 浮回。⚠️ 必须短于句间停顿(280ms)，
                             # 否则停顿段也会被压住，BGM 永远浮不出来（实测 420ms 时如此）


# ---------- 解码 / 编码 ----------

class AudioError(RuntimeError):
    """音频处理失败（格式不支持 / 文件损坏等）"""


def decode(source: bytes | bytearray | str | Path) -> tuple[np.ndarray, int]:
    """解码任意常见音频 → (mono float32 一维数组, 24000)。

    支持 mp3 / wav / m4a / aac / flac / ogg —— 全部由 PyAV 内部处理，无需转码工具。
    统一重采样为 24kHz 单声道：混音只在同一规格下做，避免逐次判断源格式。
    """
    if isinstance(source, (bytes, bytearray)):
        if not source:
            raise AudioError("音频数据为空")
        container = av.open(io.BytesIO(bytes(source)), mode="r")
    else:
        p = Path(source)
        if not p.exists():
            raise AudioError(f"音频文件不存在：{p.name}")
        container = av.open(str(p), mode="r")

    try:
        streams = container.streams.audio
        if not streams:
            raise AudioError("文件里没有音频轨道")
        resampler = av.AudioResampler(format="fltp", layout="mono",
                                      rate=SAMPLE_RATE)
        chunks: list[np.ndarray] = []
        for frame in container.decode(audio=0):
            for rf in resampler.resample(frame):
                if rf.samples:
                    chunks.append(rf.to_ndarray())
        for rf in resampler.resample(None):      # flush，漏了会丢尾部
            if rf.samples:
                chunks.append(rf.to_ndarray())
    except AudioError:
        raise
    except Exception as e:
        raise AudioError(f"无法解码音频：{str(e)[:120]}")
    finally:
        try:
            container.close()
        except Exception:
            pass

    if not chunks:
        raise AudioError("音频解码后没有数据")
    pcm = np.concatenate(chunks, axis=1)[0].astype(np.float32)
    return pcm, SAMPLE_RATE


def probe_seconds(source: bytes | bytearray | str | Path) -> float | None:
    """只读容器头部的时长探测，**不解码任何音频帧**。

    用途：上传前粗筛明显超长的文件，避免把几十 MB 音频整份解码
    （float32 / 24kHz / 单声道解码后约为源文件的 4~6 倍）再拒绝。
    拿不到可靠时长时返回 None，由调用方回退到「解码后精确判断」。
    """
    try:
        target = (io.BytesIO(bytes(source))
                  if isinstance(source, (bytes, bytearray)) else str(source))
        with av.open(target, mode="r") as c:
            if not c.streams.audio:
                return None
            dur = c.duration                       # AV_TIME_BASE（微秒）单位
            if dur:
                return float(dur) / float(getattr(av, "time_base", 1_000_000))
            st = c.streams.audio[0]
            if st.duration is not None and st.time_base:
                return float(st.duration * st.time_base)
            return None
    except Exception:
        return None


def encode_mp3(pcm: np.ndarray, bit_rate: int = BIT_RATE) -> bytes:
    """把 PCM 编码成与 edge-tts 同规格的 MP3（24kHz / mono / 无 ID3 / CBR）。"""
    arr = np.ascontiguousarray(np.asarray(pcm, np.float32).reshape(1, -1))
    out = io.BytesIO()
    with av.open(out, mode="w", format="mp3", options=dict(_ENCODE_OPTIONS)) as c:
        stream = c.add_stream("mp3", rate=SAMPLE_RATE)
        stream.layout = "mono"
        stream.bit_rate = bit_rate
        stream.options = dict(_ENCODE_OPTIONS)
        frame = av.AudioFrame.from_ndarray(arr, format="fltp", layout="mono")
        frame.sample_rate = SAMPLE_RATE
        frame.pts = 0
        for pkt in stream.encode(frame):
            c.mux(pkt)
        for pkt in stream.encode(None):
            c.mux(pkt)
    return out.getvalue()


def duration_ms(mp3_bytes: bytes) -> int:
    """与 tts.duration_ms 同口径（字节数 ÷ 6），保证混音产物时长算法不变。"""
    return round(len(mp3_bytes) / BYTES_PER_MS)


# ---------- 素材标准化 ----------

def rms_db(pcm: np.ndarray) -> float:
    if pcm.size == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(pcm.astype(np.float64) ** 2)))
    return 20 * math.log10(rms) if rms > 1e-12 else -120.0


def peak_db(pcm: np.ndarray) -> float:
    if pcm.size == 0:
        return -120.0
    peak = float(np.max(np.abs(pcm)))
    return 20 * math.log10(peak) if peak > 1e-12 else -120.0


def normalize_rms(pcm: np.ndarray, target_db: float = -20.0,
                  peak_ceiling: float = 0.9) -> np.ndarray:
    """统一响度到目标 RMS。

    不同 BGM 素材原始电平差异很大（雨声 vs 钢琴），不统一的话用户换一首曲子
    就会忽大忽小 —— 归一化在**入库时**做一次，混音时直接用。
    """
    if pcm.size == 0:
        return pcm
    cur = rms_db(pcm)
    if cur <= -120:
        return pcm
    out = pcm * (10 ** ((target_db - cur) / 20))
    peak = float(np.max(np.abs(out)))
    if peak > peak_ceiling:                  # 归一化后若触顶，整体回退避免削波
        out = out * (peak_ceiling / peak)
    return out.astype(np.float32)


def make_seamless(pcm: np.ndarray, xfade_ms: int = 600) -> np.ndarray:
    """把素材做成可无缝循环：尾部交叉淡入到头部。

    做法：取头部 xf 段与尾部 xf 段按权重混合，得到新的开头，末尾 xf 段丢弃。
    播放时「新开头」正好承接「丢弃段之前」的最后一个采样，衔接连续，听不出接缝。
    内置素材与用户上传的完整歌曲都走这一步 —— 播客比素材长时才能自然循环。
    """
    xf = int(SAMPLE_RATE * xfade_ms / 1000)
    if pcm.size <= xf * 3:
        return pcm
    head = pcm[:xf].copy()
    tail = pcm[-xf:].copy()
    w = np.linspace(0.0, 1.0, xf, dtype=np.float32)
    body = pcm[:-xf].copy()
    body[:xf] = head * w + tail * (1.0 - w)
    return body


def trim_silence_edges(pcm: np.ndarray, thresh_db: float = -50.0) -> np.ndarray:
    """裁掉首尾的静音（用户上传的曲子常有几秒空白，留着会浪费循环长度）。"""
    if pcm.size == 0:
        return pcm
    win = int(SAMPLE_RATE * 0.02)
    n = pcm.size // win
    if n < 4:
        return pcm
    frames = pcm[:n * win].reshape(n, win)
    rms = np.sqrt((frames.astype(np.float64) ** 2).mean(axis=1)) + 1e-12
    loud = np.where(20 * np.log10(rms) > thresh_db)[0]
    if loud.size == 0:
        return pcm
    start = max(0, int(loud[0]) * win - win)
    end = min(pcm.size, (int(loud[-1]) + 1) * win + win)
    return pcm[start:end]


# ---------- 混音 ----------

def duck_curve(vocal: np.ndarray, duck_db: float = DUCK_DB,
               threshold_db: float = DUCK_THRESHOLD_DB,
               attack_ms: float = DUCK_ATTACK_MS,
               release_ms: float = DUCK_RELEASE_MS,
               frame_ms: float = 20.0) -> np.ndarray:
    """由人声能量包络生成 BGM 的增益曲线（样本级）。

    为什么必须做：BGM 恒定音量铺着，人说话时两者互相打架，比不加背景音乐更糟。
    正确做法是「侧链闪避」——人声出现就把 BGM 压下去，说完再缓缓浮回来，
    BGM 只在停顿处被听见，既保留氛围又不抢话。

    平滑是**非对称**的：压下要快（否则开头几个字会被盖住），浮回要慢
    （增益突然跳上去会听到「喘气」般的音量起伏）。
    """
    n = vocal.size
    win = max(1, int(SAMPLE_RATE * frame_ms / 1000))
    frames = max(1, n // win)
    if frames < 2:
        return np.ones(n, dtype=np.float32)

    blocks = vocal[:frames * win].reshape(frames, win).astype(np.float64)
    rms = np.sqrt((blocks ** 2).mean(axis=1)) + 1e-12
    db = 20 * np.log10(rms)
    target = np.where(db > threshold_db, duck_db, 0.0)

    a_att = 1.0 - math.exp(-frame_ms / max(1.0, attack_ms))
    a_rel = 1.0 - math.exp(-frame_ms / max(1.0, release_ms))
    smooth = np.empty(frames, dtype=np.float64)
    cur = float(target[0])
    for i in range(frames):                  # 帧数在 10^3 量级，纯 Python 循环足够快
        v = float(target[i])
        cur += (a_att if v < cur else a_rel) * (v - cur)
        smooth[i] = cur

    gain = np.repeat(10.0 ** (smooth / 20.0), win)
    if gain.size < n:
        pad = gain[-1] if gain.size else 1.0
        gain = np.concatenate([gain, np.full(n - gain.size, pad)])
    return gain[:n].astype(np.float32)


def _fade_curve(n: int, fade_in_ms: int, fade_out_ms: int) -> np.ndarray:
    env = np.ones(n, dtype=np.float32)
    fi = min(n, int(SAMPLE_RATE * fade_in_ms / 1000))
    fo = min(n, int(SAMPLE_RATE * fade_out_ms / 1000))
    if fi > 1:
        env[:fi] = np.linspace(0.0, 1.0, fi, dtype=np.float32)
    if fo > 1:
        env[n - fo:] = np.minimum(env[n - fo:],
                                  np.linspace(1.0, 0.0, fo, dtype=np.float32))
    return env


def apply_fades(pcm: np.ndarray, fade_in_ms: int = 0,
                fade_out_ms: int = 0) -> np.ndarray:
    """给音频加淡入/淡出（试听截断、片段收尾都要用，否则会有「啪」的爆音）。"""
    if pcm.size == 0:
        return pcm
    return (pcm * _fade_curve(pcm.size, fade_in_ms, fade_out_ms)).astype(np.float32)


def shape_spectrum(pcm: np.ndarray, lo_hz: float | None = None,
                   hi_hz: float | None = None, order: float = 1.0) -> np.ndarray:
    """频域整形的低通 / 高通（Butterworth 幅频响应，平滑无数字味）。

    用 FFT 而非逐样本递推：素材本就是**为循环而生**的周期信号，
    频域处理不产生边界效应，正好；同时比 Python 循环快两个数量级。

    为什么需要它：做播客背景音要把「齿音频段（>6kHz）」压下去、
    「隆隆低频（<60Hz）」清掉 —— 否则会和人声打架，或在小喇叭上糊成一团。
    """
    if pcm.size < 64:
        return pcm
    n = pcm.size
    spec = np.fft.rfft(pcm.astype(np.float64))
    freq = np.fft.rfftfreq(n, 1.0 / SAMPLE_RATE)
    safe = np.maximum(freq, 1e-9)
    shape = np.ones_like(freq)
    if lo_hz:
        shape *= 1.0 / (1.0 + (lo_hz / safe) ** (2 * order))
    if hi_hz:
        shape *= 1.0 / (1.0 + (safe / hi_hz) ** (2 * order))
    return np.fft.irfft(spec * shape, n).astype(np.float32)


def loop_to(pcm: np.ndarray, n: int) -> np.ndarray:
    """把素材铺满到 n 个采样（不够则整体平铺，够了则直接截取）。"""
    if pcm.size == 0:
        return np.zeros(n, dtype=np.float32)
    if pcm.size >= n:
        return pcm[:n].astype(np.float32)
    reps = math.ceil(n / pcm.size)
    return np.tile(pcm, reps)[:n].astype(np.float32)


def mix(vocal: np.ndarray, bgm: np.ndarray, volume_db: float = -20.0,
        ducking: bool = True, duck_db: float = DUCK_DB,
        fade_in_ms: int = 1500, fade_out_ms: int = 2500,
        vocal_gain: float = 1.0) -> np.ndarray:
    """人声与 BGM 混合（返回等长 PCM，调用方负责编码落盘）。

    volume_db 是 BGM 的**基准**音量（相对人声），ducking 打开时它只是「停顿处」的
    音量，说话时会再降 duck_db。
    vocal_gain 保持 1.0 —— BGM 已被压到 -20dB 量级，实测混完峰值只到 -3.5dB，
    余量充足；再对人声做衰减只会让用户觉得「加了背景音乐人声就变轻了」。
    真正的保护是下面的峰值检测：触顶才整体回退，避免削波失真。
    """
    n = vocal.size
    if n == 0:
        return vocal
    bed = loop_to(bgm, n)

    gain = 10.0 ** (volume_db / 20.0)
    if ducking:
        gain = gain * duck_curve(vocal, duck_db=duck_db)

    bed = bed * gain * _fade_curve(n, fade_in_ms, fade_out_ms)
    mixed = vocal * vocal_gain + bed

    peak = float(np.max(np.abs(mixed)))
    if peak > 0.99:
        mixed = mixed * (0.99 / peak)
    return mixed.astype(np.float32)


def mix_audio(vocal_mp3: bytes, bgm_path: str | Path,
              volume_db: float = -20.0, ducking: bool = True) -> bytes:
    """一站式：人声 MP3 + BGM 文件 → 混音后的 MP3（同规格、无 ID3）。"""
    vocal, _ = decode(vocal_mp3)
    bgm, _ = decode(bgm_path)
    return encode_mp3(mix(vocal, bgm, volume_db=volume_db, ducking=ducking))


# ---------- 诊断 ----------

def inspect(pcm: np.ndarray) -> dict:
    return {"samples": int(pcm.size), "rms_db": round(rms_db(pcm), 2),
            "peak_db": round(peak_db(pcm), 2)}


def inspect_mp3(mp3_bytes: bytes) -> dict:
    pcm, _ = decode(mp3_bytes)
    has_id3 = mp3_bytes[:3] == b"ID3"
    head = mp3_bytes[10:14] if has_id3 else mp3_bytes[:4]
    return {**inspect(pcm), "bytes": len(mp3_bytes),
            "duration_ms_by_bytes": duration_ms(mp3_bytes),
            "duration_ms_decoded": round(pcm.size / SAMPLE_RATE * 1000),
            "id3": has_id3, "frame_header": head.hex(" ").upper()}
