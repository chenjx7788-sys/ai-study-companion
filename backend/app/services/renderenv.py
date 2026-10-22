"""AI 视频渲染组件（Chromium 浏览器内核）：状态查询 + 按需下载。

与 asr.py / zipvoice.py **同构**：单例后台线程 + urllib 流式 + 进度回调 + .part 原子替换，
不引入新依赖。

【为什么不随包】
chromium-win64.zip ≈ 170MB（解压后 ≈ 500MB）。AI 视频是可选能力，随包会让所有用户
为用不到的功能买单 —— 与 Whisper / ZipVoice 模型同一策略（按需下载到 data/ 下）。

【版本锁定：chromium-1200 == playwright 1.57.0 自带 build】
⚠️ 实测 2026-10-20：微软新下载网关（playwright.download.prss.microsoft.com）与
   npmmirror 镜像对 **build ≥ 1217 的 chromium-win64.zip 双双缺席**（网关 400
   GatewayException / 镜像 404），≤1200 的 build 两个源都正常（206 实测）。
   渲染链路通过 `executable_path` 显式启动浏览器、不依赖 playwright 版本与浏览器
   build 严格对齐（本机 playwright 1.62 + chromium-1223 一直在用），故锁 **1200**：
   双源可下，且与随包的 playwright==1.57.0 恰好同代。将来 CDN 修复新 build 后可
   整体上调（须重新实测两源）。

【下载源（顺序即优先级）】
1. npmmirror（阿里镜像，国内速度稳；zipvoice 选 ModelScope 是同一取舍）
2. playwright.azureedge.net（官方 CDN，307 跳到微软网关）

【落盘布局】复用 ms-playwright 目录结构：
   data/browsers/chromium-1200/chrome-win64/chrome.exe（+ INSTALLATION_COMPLETE 标记）
→ renderer.find_chromium 的既有 glob（chromium-*/chrome-win64/chrome.exe）直接命中，
  渲染器不需要第二套发现逻辑。
"""
import os
import platform
import shutil
import stat
import threading
import urllib.request
import zipfile
from pathlib import Path

from ..core.config import settings

# ---------- 组件定义 ----------
CHROMIUM_BUILD = "1200"

# 供前端展示的下载体积（实测 npmmirror 2026-10-20）
SIZE_MB = {"win32": 170, "darwin": 168, "linux": 165}

_SOURCES = [
    "https://registry.npmmirror.com/-/binary/playwright/builds/chromium/{rev}/{fname}",
    "https://playwright.azureedge.net/builds/chromium/{rev}/{fname}",
]

_UA = {"User-Agent": "ai-study-companion/0.1"}

# 全局下载状态（单例后台线程，与 asr.py 同款）
_state = {
    "downloading": False,
    "progress": 0,
    "downloaded_mb": 0,
    "total_mb": 0,
    "status": "idle",     # idle | downloading | extracting | done | error
    "error": "",
}
_lock = threading.Lock()


# ---------- 路径 ----------

def browsers_dir() -> Path:
    """应用自管的浏览器根目录：data/browsers/（renderer.find_chromium 的探测点之一）"""
    return settings.data_dir / "browsers"


def component_dir() -> Path:
    return browsers_dir() / f"chromium-{CHROMIUM_BUILD}"


def _platform_asset() -> tuple[str, str]:
    """返回 (zip 文件名, 解压后 exe 相对路径)。不支持的平台抛 RuntimeError。"""
    if os.name == "nt":
        return "chromium-win64.zip", "chrome-win64/chrome.exe"
    if platform.system() == "Darwin":
        rel = "chrome-mac/Chromium.app/Contents/MacOS/Chromium"
        return ("chromium-mac-arm64.zip" if platform.machine() == "arm64"
                else "chromium-mac.zip"), rel
    if platform.system() == "Linux":
        return "chromium-linux.zip", "chrome-linux/chrome"
    raise RuntimeError(f"不支持的平台：{platform.system()}")


def component_exe() -> Path:
    return component_dir() / _platform_asset()[1]


def is_component_installed() -> bool:
    """应用自管组件是否完整可用：exe 存在 + 安装完成标记。

    ⚠️ 判据同 zipvoice：不能只看 exe（中断可能停在「exe 已解压、依赖 dll 还没到」），
       标记文件在全部解压+校验之后才写（见 _run_download）。
    """
    try:
        return (component_dir() / "INSTALLATION_COMPLETE").exists() and component_exe().exists()
    except Exception:
        return False


def size_mb() -> int:
    key = "win32" if os.name == "nt" else ("darwin" if platform.system() == "Darwin" else "linux")
    return SIZE_MB.get(key, 170)


# ---------- 状态查询 ----------

def download_status() -> dict:
    with _lock:
        return dict(_state)


def get_status() -> dict:
    """渲染环境总状态：组件安装情况 + 生效中的内核来源 + 下载状态。"""
    from . import renderer
    exe, source = renderer.find_chromium_ex()
    return {
        "installed": is_component_installed(),
        "component_build": CHROMIUM_BUILD,
        "exe": exe or "",
        # env=ASC_CHROME_PATH / app=本组件 / user=用户 playwright 缓存 / edge / chrome
        "source": source,
        "render_available": bool(exe),
        "size_mb": size_mb(),
        "download": download_status(),
    }


# ---------- 下载 ----------

def start_download(force: bool = False) -> str:
    """启动后台下载。返回 "started" / "busy" / "installed"。"""
    with _lock:
        if _state["downloading"]:
            return "busy"
        if is_component_installed() and not force:
            _state.update(status="done", error="")
            return "installed"
        _state.update(downloading=True, progress=0, downloaded_mb=0,
                      total_mb=size_mb(), status="downloading", error="")
    threading.Thread(target=_run_download, daemon=True).start()
    return "started"


def _set(**kw):
    with _lock:
        _state.update(kw)


def _run_download():
    part = browsers_dir() / f"chromium-{CHROMIUM_BUILD}.zip.part"
    # ⚠️ staging 目录名绝不能匹配 renderer 的 `chromium-*/` glob —— 否则解压到一半
    #    chrome.exe 已落地，渲染器会拿到**半成品**内核直接起浏览器。用下划线前缀隔离。
    staging = browsers_dir() / f"_extracting-{CHROMIUM_BUILD}"
    try:
        browsers_dir().mkdir(parents=True, exist_ok=True)
        fname, _ = _platform_asset()
        _download_zip(fname, part)
        _set(status="extracting", progress=100)
        _extract_zip(part, staging)
        _finalize(staging)
        try:
            part.unlink()
        except BaseException:
            pass
        _set(downloading=False, progress=100, status="done", error="")
    except Exception as e:
        # ⚠️ 清理绝不能外抛（本机删除护栏超阈值抛 SystemExit，except Exception 接不住）
        try:
            if part.exists():
                part.unlink()
        except BaseException:
            pass
        try:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
        except BaseException:
            pass
        _set(downloading=False, status="error", error=str(e)[:200])


def _download_zip(fname: str, part: Path):
    """按源顺序流式下载；全部源失败才抛错（错误里带各源原因）。"""
    errs = []
    for tpl in _SOURCES:
        url = tpl.format(rev=CHROMIUM_BUILD, fname=fname)
        try:
            _stream_to(url, part)
            return
        except Exception as e:
            errs.append(f"{url.split('/')[2]}: {type(e).__name__} {str(e)[:80]}")
            try:
                if part.exists():
                    part.unlink()
            except BaseException:
                pass
    raise RuntimeError("全部下载源失败（" + "；".join(errs) + "）")


def _stream_to(url: str, part: Path):
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=120) as r:
        if getattr(r, "status", 200) >= 400:
            raise RuntimeError(f"HTTP {r.status}")
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        with open(part, "wb") as f:
            while True:
                chunk = r.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                _set(progress=int(done / total * 100) if total else 0,
                     downloaded_mb=round(done / 1048576, 1),
                     total_mb=round(total / 1048576, 1) if total else size_mb())
    # ⚠️ 完整性判据不能只看「字节数>0」：zip 的中央目录在**末尾**，截断的 zip
    #    前 99% 都能正常解出文件、最后才炸。testzip() 全量 CRC 校验兜底。
    if part.stat().st_size < 50 * 1024 * 1024:
        raise RuntimeError("下载不完整（体积过小）")
    if not zipfile.is_zipfile(part):
        raise RuntimeError("下载结果不是有效 zip")


def _extract_zip(part: Path, staging: Path):
    try:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(part) as zf:
            # 路径穿越防护：extractall 不过滤绝对路径与 ..（py3.13 默认 fully_trusted）
            for nm in zf.namelist():
                if nm.startswith(("/", "\\")) or ".." in nm.replace("\\", "/").split("/"):
                    raise RuntimeError(f"zip 含非法路径（{nm[:60]}）")
            bad = zf.testzip()          # 全量 CRC 校验（读完整包，170MB 约十几秒）
            if bad:
                raise RuntimeError(f"zip 校验失败（{bad}），请重试")
            zf.extractall(staging)
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"解压失败：{type(e).__name__} {str(e)[:120]}")


def _finalize(staging: Path):
    """staging → 正式目录（原子 rename）；posix 平台补执行位；最后写完成标记。"""
    final = component_dir()
    exe_rel = _platform_asset()[1]
    if not (staging / exe_rel).exists():
        raise RuntimeError(f"解压结果缺 {exe_rel}（包结构不符）")
    if os.name != "nt":
        # zipfile.extractall 不恢复 unix 执行位 → 浏览器本体与 helper 都起不来
        for root, _dirs, files in os.walk(staging):
            for name in files:
                try:
                    p = Path(root) / name
                    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                except BaseException:
                    pass
    try:
        if final.exists():
            shutil.rmtree(final, ignore_errors=True)
    except BaseException:
        pass
    os.replace(staging, final)
    (final / "INSTALLATION_COMPLETE").write_text("ok", encoding="utf-8")
