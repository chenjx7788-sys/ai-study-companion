"""语音模型（Whisper）管理：状态查询 + 手动下载（small / base）

模型按需下载到 data/models/faster-whisper-{size}/（含 model.bin 等），
与 parser 多路并发模型槽（_load_whisper_slot）的本地加载路径对齐；下载采用标准库 urllib 流式 + 进度回调，
不引入新依赖。下载为单例后台线程（本地单用户场景）。
"""
import os
import threading
from pathlib import Path

from ..core.config import settings

# 版本定义：id -> 元信息
MODELS = {
    "small": {
        "name": "Small（推荐）",
        "size_mb": 461,
        "quality": "高",
        "repo": "Systran/faster-whisper-small",
        "pros": "中文识别准确率高、同音字错误少，适合正式课程 / 讲座",
        "cons": "体积较大（约 461MB），首次下载较慢",
    },
    "base": {
        "name": "Base",
        "size_mb": 145,
        "quality": "一般",
        "repo": "Systran/faster-whisper-base",
        "pros": "体积小、下载快（约 145MB）",
        "cons": "中文准确率一般，同音字易误识别，需人工校对",
    },
}

# faster-whisper（CTranslate2 格式）模型文件清单：model.bin 为大头，其余为小文件
_SMALL_FILES = ["config.json", "tokenizer.json", "vocabulary.txt"]

# 全局下载状态（单例后台线程）
_state = {
    "downloading": False,
    "size": "",
    "progress": 0,
    "downloaded_mb": 0,
    "total_mb": 0,
    "status": "idle",   # idle | downloading | done | error
    "error": "",
}
_lock = threading.Lock()


def _models_dir() -> Path:
    return settings.data_dir / "models"


def _model_path(size: str) -> Path:
    return _models_dir() / f"faster-whisper-{size}"


def is_installed(size: str) -> bool:
    """本地是否已有该版本（model.bin 存在且体积 > 20MB，防 .part 残留误判）"""
    p = _model_path(size) / "model.bin"
    return p.exists() and p.stat().st_size > 20 * 1024 * 1024


def installed_size() -> str | None:
    """返回已安装的最高版本（small 优先），都未装返回 None"""
    for size in ("small", "base"):
        if is_installed(size):
            return size
    return None


def preferred_size() -> str | None:
    """返回「应加载」的模型版本：用户显式配置（且已安装）优先，否则 small→base 自动"""
    from . import settings_store
    pref = (settings_store.load().get("asr_model_size") or "").strip()
    if pref in ("small", "base") and is_installed(pref):
        return pref
    return installed_size()


def get_models_status() -> dict:
    return {
        "models": [
            {**MODELS[k], "id": k, "installed": is_installed(k)}
            for k in ("small", "base")
        ],
        "installed": installed_size() is not None,
        "installed_size": installed_size() or "",
        "downloading": _state["downloading"],
        "downloading_size": _state["size"],
        "download_progress": _state["progress"],
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


def start_download(size: str) -> bool:
    """启动后台下载；已在下载中返回 False"""
    with _lock:
        if _state["downloading"]:
            return False
        if is_installed(size):
            _state.update(status="done", error="")
            return True
        _state.update(downloading=True, size=size, progress=0,
                      downloaded_mb=0, total_mb=0, status="downloading", error="")
    threading.Thread(target=_run_download, args=(size,), daemon=True).start()
    return True


def _run_download(size: str):
    def on_progress(pct, done, total):
        with _lock:
            _state["progress"] = pct
            _state["downloaded_mb"] = round(done / 1024 / 1024, 1)
            _state["total_mb"] = round(total / 1024 / 1024, 1) if total else MODELS[size]["size_mb"]

    try:
        _download_model(size, on_progress)
        _reset_parser_cache()   # 让下次转写按最新版本（small 优先）重新加载
        with _lock:
            _state.update(downloading=False, progress=100, status="done", error="")
    except Exception as e:
        _cleanup_partial(size)
        with _lock:
            _state.update(downloading=False, status="error", error=str(e)[:200])


def _reset_parser_cache():
    try:
        from . import parser
        parser.reset_whisper_cache()
    except Exception:
        pass


def _cleanup_partial(size: str):
    try:
        part = _model_path(size) / "model.bin.part"
        if part.exists():
            part.unlink()
    except Exception:
        pass


def _download_model(size: str, on_progress):
    import urllib.request
    endpoint = os.environ.get("HF_ENDPOINT", "https://hf-mirror.com").rstrip("/")
    repo = MODELS[size]["repo"]
    dst = _model_path(size)
    dst.mkdir(parents=True, exist_ok=True)

    def _get(name: str) -> bytes:
        url = f"{endpoint}/{repo}/resolve/main/{name}"
        req = urllib.request.Request(url, headers={"User-Agent": "ai-study-companion/0.1"})
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.read()

    # 小文件（tokenizer / config / vocab）
    for name in _SMALL_FILES:
        (dst / name).write_bytes(_get(name))

    # model.bin 流式下载到 .part，完成后原子替换
    url = f"{endpoint}/{repo}/resolve/main/model.bin"
    part = dst / "model.bin.part"
    req = urllib.request.Request(url, headers={"User-Agent": "ai-study-companion/0.1"})
    with urllib.request.urlopen(req, timeout=300) as r:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        with open(part, "wb") as f:
            while True:
                chunk = r.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if total and on_progress:
                    on_progress(int(done / total * 100), done, total)
    size = part.stat().st_size
    if size < 20 * 1024 * 1024:
        raise RuntimeError("模型下载不完整，请重试")
    part.replace(dst / "model.bin")
    if on_progress:
        on_progress(100, size, size)
