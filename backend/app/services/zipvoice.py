"""ZipVoice 语音合成模型（sherpa-onnx 版）：状态查询 + 按需下载 + 本地推理。

与 asr.py（Whisper 语音转写）**同构**：单例后台线程 + urllib 流式 + 进度回调 +
.part 原子替换，不引入新依赖（仅标准库 + 已随包的 sherpa-onnx）。

【为什么按需下载而不随包】
模型本体约 195MB（decoder 118.9 + vocoder 51.7 + encoder 5.3 + 文本前端 18.9），
随包会让安装包从约 257MB 涨到 450MB+。语音合成是可选能力，用不到的用户不该为
体积买单 —— 与 Whisper 模型同一策略。

【下载源为什么是 ModelScope（实测 2026-10-13）】
  · GitHub release 直链 000（不可达，与项目既有「release 502/不通」结论一致）
  · hf-mirror 的 k2-fsa/ZipVoice 只有 encoder/decoder/tokens，
    **缺 vocoder 与 espeak-ng-data** —— 少了这两个，合成必然失败
  · ModelScope 镜像 roarson/* 是完整版（含 test_wavs 示例音色），直连 200
故以 ModelScope 为唯一完整源；不设 fallback（残缺 fallback 比没有更危险：
会下到一半才失败，用户看到的是「下载完成但合成报错」）。

【安装判据】decoder.int8.onnx > 100MB **且** 其余必需文件齐备（见 _REQUIRED_FILES）。
⚠️ 不能只看 decoder 单文件（2026-10-13 自查坐实）：decoder 是下载计划里**第一个**
   大文件，中断几乎必然停在「decoder 在、其余不在」→ 只看 decoder 会谎报「已安装」，
   且 start_download() 提前早退 → 用户再也无法通过界面补全（唯一出路是手删模型目录）。
"""
import json
import os
import shutil
import threading
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from ..core.config import settings

# ---------- 版本定义（与 asr.MODELS 同构，供设置页卡片展示） ----------
MODELS = {
    "zipvoice": {
        "name": "ZipVoice 蒸馏版（INT8）",
        "size_mb": 195,
        "quality": "高",
        "pros": "纯本地推理，不依赖网络；零样本音色克隆；中英混合；中文清晰度高",
        "cons": "首次需下载约 195MB；CPU 合成（比在线稍慢，但不受网络波动影响）",
    },
}

_MODEL_KEY = "zipvoice"

# ---------- 远端仓库（ModelScope） ----------
_ZV_REPO = "roarson/sherpa-onnx-zipvoice-distill-int8-zh-en-emilia"
_VOCODER_REPO = "roarson/vocos_24khz.onnx"

# 固定文件：本地相对路径 -> (远端仓库, 远端路径)
_ZV_FILES = {
    "decoder.int8.onnx": (_ZV_REPO, "decoder.int8.onnx"),
    "encoder.int8.onnx": (_ZV_REPO, "encoder.int8.onnx"),
    "lexicon.txt": (_ZV_REPO, "lexicon.txt"),
    "tokens.txt": (_ZV_REPO, "tokens.txt"),
}
_VOCODER_LOCAL = "vocos_24khz.onnx"

# 目录型文件（远端需递归列举）：本地目录前缀 -> 远端仓库
_ZV_DIRS = {
    "espeak-ng-data": _ZV_REPO,   # 391 个小文件，总约 17.2MB（音素化必需）
    "test_wavs": _ZV_REPO,        # 3 个内置示例音色 + prompt.txt
}

_MS_API = "https://modelscope.cn/api/v1/models/{repo}/repo/files?Revision=master&Recursive=true"
_MS_FILE = "https://modelscope.cn/api/v1/models/{repo}/repo?Revision=master&FilePath={path}"

# 下载并发：espeak-ng-data 全是几十 KB 的小文件，串行会被 RTT 拖到几分钟
_DL_WORKERS = 8

# 全局下载状态（单例后台线程，与 asr.py 同款）
_state = {
    "downloading": False,
    "size": "",
    "progress": 0,
    "downloaded_mb": 0,
    "total_mb": 0,
    "status": "idle",     # idle | downloading | done | error
    "error": "",
}
_lock = threading.Lock()

# 推理引擎单例（加载 decoder 118MB 有明显耗时，必须缓存）
_engine = None
_engine_lock = threading.Lock()   # 只保护「构建/释放引擎」这一件事
_gen_lock = threading.Lock()      # 串行化 generate：引擎非并发安全（⚠️ 不可与上一把共用：Lock 不可重入）


# ---------- 路径 ----------

def _models_dir() -> Path:
    return settings.data_dir / "models"


def model_dir() -> Path:
    """模型根目录：data/models/zipvoice-distill-int8/"""
    return _models_dir() / "zipvoice-distill-int8"


_DECODER_MIN_BYTES = 100 * 1024 * 1024     # decoder.int8.onnx 正常约 118.9MB
_VOCODER_MIN_BYTES = 1 * 1024 * 1024       # vocos_24khz.onnx 正常约 51.7MB

# 合成必需文件（相对模型根目录）：任一缺失都不算「已安装」。
# ⚠️ 判据必须覆盖**全部**必需件，不能只看 decoder —— 见模块头注释。
_REQUIRED_FILES = (
    "decoder.int8.onnx",
    "encoder.int8.onnx",
    _VOCODER_LOCAL,
    "tokens.txt",
    "lexicon.txt",
    "espeak-ng-data/phontab",
    "test_wavs/prompt.txt",
)


def is_installed() -> bool:
    """模型是否**完整可用**：decoder > 100MB 且全部必需文件齐备。

    ⚠️ 不能只看 decoder 单文件（原因见 _REQUIRED_FILES 注释）。
    """
    d = model_dir()
    try:
        dec = d / "decoder.int8.onnx"
        if not (dec.exists() and dec.stat().st_size > _DECODER_MIN_BYTES):
            return False
        for rel in _REQUIRED_FILES:
            p = d / rel
            if not p.exists() or p.stat().st_size <= 0:
                return False
        return True
    except Exception:
        return False


def installed_size() -> str:
    return _MODEL_KEY if is_installed() else ""


def get_models_status() -> dict:
    return {
        "models": [
            {**MODELS[k], "id": k, "installed": is_installed()}
            for k in (_MODEL_KEY,)
        ],
        "installed": is_installed(),
        "installed_size": installed_size(),
        "downloading": _state["downloading"],
        "downloading_size": _state["size"],
        "download_progress": _state["progress"],
        "error": _state["error"],
        "voices": list_voices() if is_installed() else [],
    }


def download_status() -> dict:
    return {
        "downloading": _state["downloading"],
        "size": _state["size"],
        "progress": _state["progress"],
        "downloaded_mb": _state["downloaded_mb"],
        "total_mb": _state["total_mb"],
        "status": _state["status"],
        "error": _state["error"],
    }


# ---------- 下载 ----------

def _http_json(url: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "ai-study-companion/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _list_repo_files(repo: str) -> list[dict]:
    """递归列举 ModelScope 仓库文件：[{Path, Size}]（目录项 Size=0，需过滤）"""
    data = _http_json(_MS_API.format(repo=repo))
    files = (data.get("Data") or {}).get("Files") or []
    return [f for f in files if int(f.get("Size") or 0) > 0]


def _build_plan() -> list[tuple[str, str, str, int]]:
    """构建下载计划：[(repo, remote_path, local_relpath, size_bytes)]

    ⚠️ 数量与体积以**远端列举**为准（写死清单一旦与远端漂移，就会下成残包）。
    """
    plan: list[tuple[str, str, str, int]] = []

    # 固定文件：拿远端 Size 做进度分母
    listing_cache: dict[str, dict[str, int]] = {}

    def _listing(repo: str) -> dict[str, int]:
        # ⚠️⚠️ 列举失败**必须炸**，绝不能静默返回空表：
        #    空表 → 计划残缺（目录型文件 espeak-ng-data / test_wavs 会整个消失）
        #    → 下载「完成」但模型不可用，用户看到「下载完成却合成报错」，最难排查。
        if repo not in listing_cache:
            files = _list_repo_files(repo)
            if not files:
                raise RuntimeError(f"远端文件清单为空（{repo}），请检查网络后重试")
            listing_cache[repo] = {f["Path"]: int(f.get("Size") or 0) for f in files}
        return listing_cache[repo]

    for repo in (_ZV_REPO, _VOCODER_REPO):
        files = (_ZV_FILES if repo == _ZV_REPO else {_VOCODER_LOCAL: (_VOCODER_REPO, _VOCODER_LOCAL)})
        lst = _listing(repo)
        for local_name, (r, remote) in files.items():
            if r != repo:
                continue
            plan.append((repo, remote, local_name, lst.get(remote, 0)))

    # 目录型文件（递归列举后按前缀挑选）
    lst = _listing(_ZV_REPO)
    for dirname, repo in _ZV_DIRS.items():
        for path, size in lst.items():
            if path.startswith(dirname + "/"):
                plan.append((repo, path, path, size))
    return plan


def start_download() -> bool:
    """启动后台下载；已在下载中返回 False"""
    with _lock:
        if _state["downloading"]:
            return False
        if is_installed():
            _state.update(status="done", error="")
            return True
        _state.update(downloading=True, size=_MODEL_KEY, progress=0,
                      downloaded_mb=0, total_mb=0, status="downloading", error="")
    threading.Thread(target=_run_download, daemon=True).start()
    return True


def _run_download():
    try:
        _download_all()
        with _lock:
            _state.update(downloading=False, progress=100, status="done", error="")
    except Exception as e:
        with _lock:
            _state.update(downloading=False, status="error", error=str(e)[:200])


def _download_all():
    # 先清上一轮失败留下的 .part（否则体积统计与目录会一直留垃圾）
    cleanup_partial()
    plan = _build_plan()
    if not plan:
        raise RuntimeError("无法获取模型文件清单，请检查网络后重试")

    total = sum(p[3] for p in plan) or 1
    done = {"bytes": 0}
    root = model_dir()
    root.mkdir(parents=True, exist_ok=True)

    def _bump(n: int):
        done["bytes"] += n
        with _lock:
            _state["progress"] = min(99, int(done["bytes"] / total * 100))
            _state["downloaded_mb"] = round(done["bytes"] / 1024 / 1024, 1)
            _state["total_mb"] = round(total / 1024 / 1024, 1)

    def _fetch_one(item):
        repo, remote, local_rel, size = item
        dst = root / local_rel
        _fetch_file(repo, remote, dst)
        _bump(size or dst.stat().st_size)

    # 大文件先下（用户能更早看到进度推进），小文件（espeak）并发
    big = [p for p in plan if p[3] >= 1024 * 1024]
    small = [p for p in plan if p[3] < 1024 * 1024]
    big.sort(key=lambda p: -p[3])

    for item in big:
        _fetch_one(item)

    if small:
        with ThreadPoolExecutor(max_workers=_DL_WORKERS) as pool:
            futs = [pool.submit(_fetch_one, it) for it in small]
            for f in as_completed(futs):
                f.result()   # 任何一个小文件失败 → 整体失败，不静默吞掉


def _fetch_file(repo: str, remote: str, dst: Path):
    """流式下载单个文件到 dst（先 .part 再原子替换）。

    ⚠️ ModelScope 的 file 端点返回 302 → OSS 签名 URL，urllib 默认跟随重定向。
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and dst.stat().st_size > 0:
        return   # 断点续传：已完整的文件跳过（按文件粒度，不做字节级续传）
    url = _MS_FILE.format(repo=repo, path=urllib.parse.quote(remote))
    part = dst.with_suffix(dst.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "ai-study-companion/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r, open(part, "wb") as f:
            while True:
                chunk = r.read(1024 * 256)
                if not chunk:
                    break
                f.write(chunk)
        if part.stat().st_size <= 0:
            raise RuntimeError(f"下载为空：{remote}")
        part.replace(dst)
    except Exception:
        try:
            if part.exists():
                part.unlink()
        except Exception:
            pass
        raise


def cleanup_partial():
    """清理下载残留（.part），供失败重试时调用"""
    try:
        for p in model_dir().rglob("*.part"):
            try:
                p.unlink()
            except Exception:
                pass
    except Exception:
        pass


# ---------- 内置音色（来自随下载的 test_wavs） ----------

def _prompt_map() -> dict[str, str]:
    """读 test_wavs/prompt.txt：`<文件名> <参考文本>` 每行一条。"""
    p = model_dir() / "test_wavs" / "prompt.txt"
    out: dict[str, str] = {}
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(None, 1)
            if len(parts) == 2:
                out[parts[0].strip()] = parts[1].strip()
    except Exception:
        pass
    return out


# 音色命名空间：zv:<wav 名>（与 edge 的 "zh-CN-XxxNeural" 不冲突）
ZV_PREFIX = "zv:"

_FALLBACK_LABELS = {
    "news-female": "女声 · 播报",
    "news-female-2": "女声 · 新闻",
    "leijun-1": "男声 · 沉稳",
}


def list_voices() -> list[dict]:
    """ZipVoice 可用音色：随模型下载的示例音色（未下载时返回空表）。"""
    if not is_installed():
        return []
    prompts = _prompt_map()
    out = []
    for name in sorted(prompts.keys()):
        if not name.lower().endswith(".wav"):
            continue
        stem = Path(name).stem     # 比 name[:-4] 稳（不依赖「后缀恰好 4 字符」）
        if not (model_dir() / "test_wavs" / name).exists():
            continue
        out.append({
            "id": ZV_PREFIX + stem,
            "name": _FALLBACK_LABELS.get(stem, stem),
            "gender": "female" if "female" in stem or "news" in stem else "male",
            "locale": "zh-CN",
            "desc": "ZipVoice 本地音色（示例素材）",
            "prompt_text": prompts[name],
        })
    return out


ZV_VOICE_IDS = None   # 惰性：下载完成前后会变，不做模块级缓存


def is_zv_voice(voice_id: str | None) -> bool:
    return bool(voice_id) and str(voice_id).startswith(ZV_PREFIX)


def voice_spec(voice_id: str) -> tuple[Path, str]:
    """把 zv:<name> 解析为 (参考音频路径, 参考文本)。

    ⚠️ 参考文本必须与音频**逐字一致**，否则音色相似度明显下降（官方警告）。
    """
    stem = str(voice_id)[len(ZV_PREFIX):]
    wav = model_dir() / "test_wavs" / f"{stem}.wav"
    text = _prompt_map().get(f"{stem}.wav", "")
    if not wav.exists():
        raise ValueError(f"音色文件缺失：{stem}.wav（请重新下载模型）")
    if not text:
        raise ValueError(f"音色缺少参考文本：{stem}.wav（test_wavs/prompt.txt 不完整）")
    return wav, text


# ---------- 推理 ----------

def _espeak_fingerprint(src: Path) -> str:
    """espeak-ng-data 的轻量指纹（文件数 + 总字节数）：判断 ProgramData 副本是否过期。"""
    n = 0
    total = 0
    try:
        for p in src.rglob("*"):
            if p.is_file():
                n += 1
                try:
                    total += p.stat().st_size
                except OSError:
                    pass
    except Exception:
        pass
    return f"{n}:{total}"


def _rm_tree_safe(root: Path) -> None:
    """逐文件 / 逐目录删除（**不用** shutil.rmtree）。

    ⚠️ 删除配额护栏会抛 `SystemExit`（`except Exception` 接不住）→ 必须
       `except BaseException` 且**绝不外抛**：清不掉最多留个旧副本，不该炸掉合成。
    """
    try:
        entries = sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True)
    except Exception:
        return
    for p in entries:
        try:
            if p.is_dir():
                p.rmdir()
            else:
                p.unlink()
        except BaseException:      # noqa: BLE001
            pass
    try:
        root.rmdir()
    except BaseException:          # noqa: BLE001
        pass


def _ascii_espeak_dir() -> str:
    """给引擎一个**纯 ASCII** 的 espeak-ng-data 路径（必要时复制一份）。

    ⚠️⚠️ espeak-ng 是 C 库，**打不开非 ASCII 路径**（2026-10-13 实测）：
      模型目录在 `C:\\Users\\陈锦祥\\...` 时，合成报
      `Error processing file '/usr/share/espeak-ng-data\\phontab': No such file...`
      —— 它把 data_dir 丢了、回落到编译默认路径；同一模型放到纯 ASCII 路径
      （`/tmp/zv_ascii/...`）立刻成功。注意它**只影响 espeak 的数据文件**：
      onnx 模型 / tokens / lexicon 走 C++ 与 onnxruntime，中文路径没问题。

    ⚠️ 不能靠 Windows 8.3 短路径兜底：实测 `GetShortPathNameW` 对 CJK **不做
      转写**（仍返回 `...\\陈锦祥\\AI伴学~1\\...`，非 ASCII）。

    ⚠️ 为什么必须处理：中文 Windows 用户名极其常见（本机开发路径就是），
      不处理等于「一部分用户装完模型也用不了」，且报错信息完全指不到病根。

    策略：路径本身是 ASCII → 原样用（零复制）；否则复制到 ProgramData / Public
    （两者都是 ASCII 且默认可写）。复制是 355 个小文件约 17MB，仅首次。
    """
    src = model_dir() / "espeak-ng-data"
    if str(src).isascii():
        return str(src)

    for base in (os.environ.get("ProgramData"), os.environ.get("PUBLIC")):
        if not base or not str(base).isascii():
            continue
        root = Path(base) / "ai-study-companion"
        dst = root / "espeak-ng-data"
        stamp = root / "espeak-ng-data.stamp"
        try:
            # ⚠️ 副本必须**随模型更新**：指纹（文件数 + 总字节数）不一致就重做。
            #    否则「重下模型后 espeak 数据仍是旧的」→ 报错完全指不到病根。
            want = _espeak_fingerprint(src)
            have = ""
            try:
                have = stamp.read_text(encoding="utf-8").strip()
            except Exception:
                have = ""
            if not (dst / "phontab").exists() or have != want:
                _rm_tree_safe(dst)     # 逐文件删（不用 rmtree）：护栏抛 BaseException 也不外抛
                shutil.copytree(src, dst, dirs_exist_ok=True)
                root.mkdir(parents=True, exist_ok=True)
                stamp.write_text(want, encoding="utf-8")
            if (dst / "phontab").exists():
                return str(dst)
        except Exception:
            continue
    # 兜底：交给引擎（会报错，但错误原样透出，不做静默降级）
    return str(src)


def engine():
    """惰性构建 OfflineTts 单例（模型未安装时抛错，由调用方转成友好提示）。"""
    global _engine
    with _engine_lock:
        if _engine is not None:
            return _engine
        if not is_installed():
            raise RuntimeError("ZipVoice 语音模型未下载")
        import sherpa_onnx
        d = model_dir()
        conf = sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(
                # ⚠️ num_threads 属于 **ModelConfig**（不是 ZipvoiceModelConfig）：
                #    zipvoice 配置只接受 tokens/encoder/decoder/vocoder/data_dir/lexicon
                #    + feat_scale/t_shift/target_rms/guidance_scale（实测 1.13.8 签名）。
                num_threads=max(2, min(4, (os.cpu_count() or 4) // 2)),
                zipvoice=sherpa_onnx.OfflineTtsZipvoiceModelConfig(
                    tokens=str(d / "tokens.txt"),
                    encoder=str(d / "encoder.int8.onnx"),
                    decoder=str(d / "decoder.int8.onnx"),
                    vocoder=str(d / _VOCODER_LOCAL),
                    # ⚠️ espeak 数据必须给 ASCII 路径，见 _ascii_espeak_dir()
                    data_dir=_ascii_espeak_dir(),
                    lexicon=str(d / "lexicon.txt"),
                ),
            ),
        )
        _engine = sherpa_onnx.OfflineTts(conf)
        return _engine


def unload_engine():
    """释放引擎（模型删除 / 测试收尾用）"""
    global _engine
    with _engine_lock:
        _engine = None


def _load_prompt_samples(wav: Path):
    """读参考音频 → (float32 单声道数组, 采样率)。复用 mixer 的解码（PyAV）。"""
    import numpy as np
    from . import mixer
    pcm, sr = mixer.decode(wav)      # 已统一 24kHz mono float32
    return np.asarray(pcm, dtype=np.float32), sr


def synthesize_pcm(text: str, voice_id: str, speed: float = 1.0):
    """合成单句 → (float32 PCM 数组, 采样率)。串行化（引擎非并发安全）。"""
    wav, prompt_text = voice_spec(voice_id)
    prompt_samples, prompt_sr = _load_prompt_samples(wav)
    eng = engine()                        # 内部自带 _engine_lock，此处不可再持锁
    import numpy as np
    with _gen_lock:                       # generate 串行：同实例并发调用不安全
        audio = eng.generate(text, prompt_text, prompt_samples, prompt_sr,
                             speed=speed, num_steps=4)
    pcm = np.asarray(audio.samples, dtype=np.float32)
    sr = int(audio.sample_rate)
    if sr != 24000:
        # 规格兜底：项目全局按 24kHz 口径算时长，采样率不对必须重采样
        pcm, sr = _resample_to_24k(pcm, sr)
    return pcm, sr


def _resample_to_24k(pcm, sr: int):
    """把任意采样率的 float32 mono PCM 重采样到 24kHz（PyAV，已在依赖内）。"""
    import numpy as np
    import av
    layout = "mono"
    frame = av.AudioFrame.from_ndarray(
        np.ascontiguousarray(pcm.reshape(1, -1)), format="fltp", layout=layout)
    frame.sample_rate = sr
    resampler = av.AudioResampler(format="fltp", layout=layout, rate=24000)
    out = []
    for rf in resampler.resample(frame):
        out.append(rf.to_ndarray())
    for rf in resampler.resample(None):
        out.append(rf.to_ndarray())
    if not out:
        return pcm, sr
    return np.concatenate(out, axis=1)[0].astype(np.float32), 24000


def probe() -> dict:
    """连通性自检（设置页「测试语音服务」用）"""
    import time
    if not is_installed():
        return {"ok": False, "detail": "ZipVoice 语音模型未下载，请先在设置页下载"}
    voices = list_voices()
    if not voices:
        return {"ok": False, "detail": "ZipVoice 模型缺少示例音色素材（test_wavs），建议重新下载"}
    try:
        t0 = time.time()
        pcm, sr = synthesize_pcm("语音服务连接正常。", voices[0]["id"], 1.0)
        ms = round((time.time() - t0) * 1000)
        dur = round(len(pcm) / sr * 1000)
        return {"ok": True,
                "detail": f"ZipVoice 本地合成可用（{ms}ms，试听音频 {dur}ms）",
                "sample_bytes": int(len(pcm) * 4)}
    except Exception as e:
        return {"ok": False, "detail": f"ZipVoice 合成失败：{type(e).__name__} {str(e)[:120]}"}
