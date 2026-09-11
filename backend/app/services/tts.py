"""TTS 服务：默认 edge-tts（免费、无需 API Key），provider 可扩展

【为什么默认 edge-tts】
项目走「免费分发 + 用户自带 Key」路线，不能要求用户为了听播客再配第二个 Key。
edge-tts 零成本、零配置、中文音色齐全（含中国香港 / 中国台湾），完全契合该约束。
synthesize() 是唯一入口，未来接入云端 TTS 只需在此加分支，不改调用方。

【音频规格（2026-09-11 实测确认）】
edge-tts 输出 MPEG-2 Layer III / 24000Hz / 48kbps / mono / CBR，无 ID3 头。
- 每帧 144 字节、24ms  →  时长(ms) = 字节数 ÷ 6（精确，非估算）
- 同参数 MP3 帧流可直接字节拼接，拼接产物经 mutagen 校验时长一致
- 帧头固定为 FF F3 64 C4

【句间留白】
纯标点/空格合成会抛 NoAudioReceived，无法用 TTS 生成静音，
故手工构造静音帧：帧头 + 全零数据。Layer III 的 side info 全零 →
part2_3_length=0 → 无主数据 → 解码为静音。

【⚠️ 必须重试】
edge-tts 存在间歇性 NoAudioReceived（连接冷启动 / 网络抖动），失败位置随机、
与文本长度和字符无关（已实测排除），重试后可 100% 恢复。因此每句默认重试 3 次。
"""
import asyncio
import hashlib
import logging
import time
from pathlib import Path

logger = logging.getLogger("uvicorn.error")

# ---------- 音频常量（源自实测，勿随意改动） ----------
BYTES_PER_MS = 6.0              # 48kbps → 6000 B/s → 6 B/ms
FRAME_BYTES = 144               # 每帧字节数
FRAME_MS = 24                   # 每帧时长
FRAME_HEADER = bytes([0xFF, 0xF3, 0x64, 0xC4])

# ---------- 音色目录 ----------
# 只列内置常用音色，避免每次生成都联网拉全量清单（离线也能给出可选列表）。
VOICE_CATALOG = [
    {"id": "zh-CN-XiaoxiaoNeural", "name": "晓晓", "gender": "female", "locale": "zh-CN", "desc": "温柔亲和，适合主持人"},
    {"id": "zh-CN-XiaoyiNeural",   "name": "晓伊", "gender": "female", "locale": "zh-CN", "desc": "活泼明快"},
    {"id": "zh-CN-YunxiNeural",    "name": "云希", "gender": "male",   "locale": "zh-CN", "desc": "阳光青年，适合讲解"},
    {"id": "zh-CN-YunjianNeural",  "name": "云健", "gender": "male",   "locale": "zh-CN", "desc": "沉稳有力"},
    {"id": "zh-CN-YunyangNeural",  "name": "云扬", "gender": "male",   "locale": "zh-CN", "desc": "新闻播报感"},
    {"id": "zh-CN-YunxiaNeural",   "name": "云夏", "gender": "male",   "locale": "zh-CN", "desc": "少年音"},
    {"id": "zh-CN-liaoning-XiaobeiNeural", "name": "晓北", "gender": "female", "locale": "zh-CN", "desc": "东北口音"},
    {"id": "zh-CN-shaanxi-XiaoniNeural",   "name": "晓妮", "gender": "female", "locale": "zh-CN", "desc": "陕西口音"},
    {"id": "zh-HK-HiuMaanNeural",  "name": "曉曼", "gender": "female", "locale": "zh-HK", "desc": "粤语（中国香港）"},
    {"id": "zh-HK-HiuGaaiNeural",  "name": "曉佳", "gender": "female", "locale": "zh-HK", "desc": "粤语（中国香港）"},
    {"id": "zh-HK-WanLungNeural",  "name": "雲龍", "gender": "male",   "locale": "zh-HK", "desc": "粤语（中国香港）"},
    {"id": "zh-TW-HsiaoChenNeural", "name": "曉臻", "gender": "female", "locale": "zh-TW", "desc": "国语（中国台湾）"},
    {"id": "zh-TW-HsiaoYuNeural",   "name": "曉雨", "gender": "female", "locale": "zh-TW", "desc": "国语（中国台湾）"},
    {"id": "zh-TW-YunJheNeural",    "name": "雲哲", "gender": "male",   "locale": "zh-TW", "desc": "国语（中国台湾）"},
]

VOICE_IDS = {v["id"] for v in VOICE_CATALOG}

# 默认语速与句间停顿：既决定成品音频的节奏与时长，也参与音频指纹
# （见 services/podcast.py:script_signature）—— 改了它们，旧音频就不再对应当前配置。
DEFAULT_RATE = "+0%"
DEFAULT_GAP_MS = 280

# 角色默认音色：主持人偏清亮、专家偏沉稳
DEFAULT_VOICES = {
    "host": "zh-CN-XiaoxiaoNeural",
    "expert": "zh-CN-YunxiNeural",
}

SUPPORTED_PROVIDERS = ("edge",)
DEFAULT_PROVIDER = "edge"

RETRY_TIMES = 5          # edge-tts 失败是间歇性的，且可能连续数句都失败，重试次数要留够
RETRY_BACKOFF = 1.0      # 秒，退避按 1/2/3/4 递增（累计约 10s）
CONCURRENCY = 2          # 并发合成数：实测并发 3 更容易被限流，降到 2 更稳


class TTSError(RuntimeError):
    """语音合成失败，携带成功/失败句数，便于上层给出可执行的提示。

    成功部分会写入 cache_dir，再次合成时命中缓存、只补失败句，不必整段重做。
    """

    def __init__(self, message: str, failed: int = 0, succeeded: int = 0, total: int = 0):
        super().__init__(message)
        self.failed = failed
        self.succeeded = succeeded
        self.total = total

    @property
    def friendly(self) -> str:
        if self.succeeded and self.failed:
            return (f"语音服务临时不可用：{self.total} 句中有 {self.failed} 句未合成"
                    f"（已完成 {self.succeeded} 句，已缓存）。稍后点「重新合成」"
                    f"会只补失败的部分，不会重做全部。")
        return f"语音服务暂时不可用（{self.total} 句均未合成成功），请稍后重试。"


def list_voices() -> list[dict]:
    """内置音色清单（不联网，离线可用）"""
    return VOICE_CATALOG


def resolve_voice(voice_id: str | None, role: str = "host") -> str:
    """校验音色 id，非法则回退角色默认音色"""
    if voice_id and voice_id in VOICE_IDS:
        return voice_id
    return DEFAULT_VOICES.get(role, DEFAULT_VOICES["host"])


def voice_name(voice_id: str) -> str:
    for v in VOICE_CATALOG:
        if v["id"] == voice_id:
            return v["name"]
    return voice_id.split("-")[-1].replace("Neural", "")


# ---------- 静音帧 ----------

def silence(ms: int) -> bytes:
    """构造静音 MP3 帧序列（帧头 + 全零数据）"""
    if ms <= 0:
        return b""
    n_frames = max(1, round(ms / FRAME_MS))
    return (FRAME_HEADER + bytes(FRAME_BYTES - 4)) * n_frames


# ---------- 合成 ----------

async def _synth_one(text: str, voice: str, rate: str, provider: str) -> bytes:
    """合成单句，返回 mp3 字节。抛异常由上层重试。"""
    if provider != "edge":
        raise ValueError(f"暂不支持的 TTS 提供方：{provider}")

    import edge_tts

    comm = edge_tts.Communicate(text, voice, rate=rate, receive_timeout=60)
    audio = bytearray()
    async for chunk in comm.stream():
        if chunk["type"] == "audio":
            audio.extend(chunk["data"])
    if not audio:
        raise RuntimeError("TTS 未返回音频数据")
    return bytes(audio)


def _part_key(text: str, voice: str, rate: str) -> str:
    """分句缓存文件名：**只由内容决定，不含序号**。

    ⚠️ 旧写法是 `{index:03d}-{hash}.mp3`：删掉或插入一句，其后每一句的序号都会
    位移 → key 全变 → 缓存整体 miss、整段重合成（用户只改一句，也要等一遍完整合成）。
    序号对缓存没有任何价值（内容相同就该命中），去掉之后「改一句只重合成一句」
    才真正成立。旧命名的文件由 podcast.drop_legacy_parts() 清理。
    """
    h = hashlib.md5(f"{text}|{voice}|{rate}".encode("utf-8")).hexdigest()[:10]
    return f"{h}.mp3"


async def _synth_one_cached(text: str, voice: str, rate: str, provider: str,
                            sem: asyncio.Semaphore,
                            cache_dir: Path | None, key: str) -> bytes:
    """合成单句：优先读缓存，未命中则带重试合成并写回缓存。

    缓存是「失败续传」的基础 —— 17 句里失败 2 句时，重试只需补这 2 句。
    """
    if cache_dir is not None:
        f = cache_dir / key
        try:
            if f.exists() and f.stat().st_size > 0:
                return f.read_bytes()
        except Exception:
            pass

    async with sem:
        last_err: Exception | None = None
        for attempt in range(RETRY_TIMES):
            try:
                data = await _synth_one(text, voice, rate, provider)
                if cache_dir is not None:
                    try:
                        (cache_dir / key).write_bytes(data)
                    except Exception:
                        pass
                return data
            except Exception as e:
                last_err = e
                if attempt < RETRY_TIMES - 1:
                    await asyncio.sleep(RETRY_BACKOFF * (attempt + 1))
        raise TTSError(str(last_err))


async def _synth_all(segments: list[dict], voice_map: dict, rate: str, provider: str,
                     cache_dir: Path | None, on_progress=None) -> list:
    """并发合成全部句子，返回与 segments 等长的结果列表（保持原顺序，失败项为异常）

    on_progress(done, total) 每完成一句回调一次（命中缓存、失败的句子也算）——
    上层据此回传进度，避免「合成中…」一挂就是几十秒、用户不知道是不是卡死。
    回调异常一律吞掉：进度展示不该影响合成本身。
    """
    sem = asyncio.Semaphore(CONCURRENCY)
    total = len(segments)
    state = {"done": 0}

    async def one(seg: dict):
        voice = resolve_voice(
            (voice_map or {}).get(seg.get("speaker")), seg.get("speaker") or "host")
        key = _part_key(seg["text"], voice, rate)
        try:
            return await _synth_one_cached(
                seg["text"], voice, rate, provider, sem, cache_dir, key)
        finally:
            state["done"] += 1
            if on_progress is not None:
                try:
                    on_progress(state["done"], total)
                except Exception:
                    pass

    return await asyncio.gather(*[one(seg) for seg in segments], return_exceptions=True)


def synthesize(segments: list[dict], voice_map: dict | None = None,
               rate: str = DEFAULT_RATE, gap_ms: int = DEFAULT_GAP_MS,
               provider: str = DEFAULT_PROVIDER,
               cache_dir: str | Path | None = None,
               on_progress=None) -> tuple[bytes, list[dict]]:
    """合成整段播客。

    segments: [{"speaker": "host"|"expert", "text": "..."}]
    cache_dir: 分句缓存目录。传入后可实现「失败续传」——重试只补未成功的句子。
    on_progress(done, total): 每合成完一句回调一次（供 SSE 回传进度）。

    返回 (mp3_bytes, timings)，timings 与 segments 等长，每项 {"start_ms", "end_ms", "voice"}。
    时间戳由拼接位置直接算出（字节数 ÷ 6），不依赖 TTS 返回的事件 —— 精确且不会错位。
    """
    items = [s for s in segments if (s.get("text") or "").strip()]
    if not items:
        raise ValueError("没有可合成的脚本内容")

    cache_path: Path | None = None
    if cache_dir is not None:
        cache_path = Path(cache_dir)
        cache_path.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    results = asyncio.run(_synth_all(items, voice_map or {}, rate, provider, cache_path,
                                     on_progress=on_progress))
    logger.info("TTS 合成完成：%d 句，耗时 %.1fs", len(items), time.time() - t0)

    failed = [i for i, r in enumerate(results) if isinstance(r, BaseException)]
    if failed:
        raise TTSError(f"{len(failed)} 句合成失败", failed=len(failed),
                       succeeded=len(items) - len(failed), total=len(items))

    out = bytearray()
    timings = []
    for i, (seg, audio) in enumerate(zip(items, results)):
        if i > 0:
            out.extend(silence(gap_ms))      # 句间留白，避免两个音色抢话
        start_ms = round(len(out) / BYTES_PER_MS)
        out.extend(audio)
        end_ms = round(len(out) / BYTES_PER_MS)
        voice = resolve_voice((voice_map or {}).get(seg.get("speaker")),
                              seg.get("speaker") or "host")
        timings.append({
            "start_ms": start_ms,
            "end_ms": end_ms,
            "voice": voice,
            "voice_name": voice_name(voice),
        })

    return bytes(out), timings


def duration_ms(mp3_bytes: bytes) -> int:
    """按 CBR 字节数换算时长（48kbps → 6 字节/毫秒）"""
    return round(len(mp3_bytes) / BYTES_PER_MS)


def probe() -> dict:
    """连通性自检（设置页「测试语音服务」用）"""
    try:
        t0 = time.time()
        audio = asyncio.run(_synth_one("语音服务连接正常。", DEFAULT_VOICES["host"], "+0%", "edge"))
        ms = round((time.time() - t0) * 1000)
        return {
            "ok": True,
            "detail": f"edge-tts 可用（{ms}ms，试听音频 {duration_ms(audio)}ms）",
            "sample_bytes": len(audio),
        }
    except Exception as e:
        return {"ok": False, "detail": f"语音服务不可用：{type(e).__name__} {str(e)[:120]}"}


# ---------- 音色试听 ----------

def preview_text(voice_id: str) -> str:
    """试听样本文案。带上音色自己的名字，用户才能确认「现在听到的是谁」。"""
    return f"你好，我是{voice_name(voice_id)}。很高兴陪你一起学习。"


def preview(voice_id: str, rate: str = "+0%", provider: str = DEFAULT_PROVIDER,
            cache_dir: str | Path | None = None) -> bytes:
    """合成一段音色试听音频。

    样本文案固定 → 按 (音色, 语速, 文本) 落盘缓存：首次要几秒，之后秒开。
    复用 _synth_one_cached 的多重重试 —— edge-tts 会间歇性抽风，试听同样需要。
    """
    if voice_id not in VOICE_IDS:
        raise ValueError(f"未知音色：{voice_id}")

    text = preview_text(voice_id)
    key = hashlib.md5(f"{voice_id}|{rate}|{text}".encode("utf-8")).hexdigest()[:10]
    cache_path = Path(cache_dir) if cache_dir else None
    if cache_path is not None:
        cache_path.mkdir(parents=True, exist_ok=True)

    sem = asyncio.Semaphore(1)
    return asyncio.run(_synth_one_cached(
        text, voice_id, rate, provider, sem, cache_path, f"{voice_id}-{key}.mp3"))
