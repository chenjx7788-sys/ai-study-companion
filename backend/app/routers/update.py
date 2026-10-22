"""客户端更新：版本检查 / 安装包下载 / 定位（阶段 1：只下载，不自动替换）

设计取舍（2026-10-19）：

- **没有服务端推送**：桌面客户端没有常驻通道，所谓「推送」只能是启动时主动拉一次。
  所以本模块只负责「拉」，何时拉（启动延迟 / 6h 频控）由前端与缓存决定。
- **不用 SSE**：下载是后台线程 + 轮询进度。SSE 流式生成器要持有资源、断开后清理难，
  而下载本来就适合轮询（见项目「SSE 生成器分两类」约定）。
- **三源降级**：api.github.com → gh-proxy 代理同一 API → 官网下载区正则。
  2026-10-19 实测：本机直连 1.0s / 200；gh-proxy 代理同一接口 200；官网下载链接里
  写死了 `/releases/download/vX.Y.Z/`，所以第三源即便拿不到结构化数据，也够支撑
  「有新版本 + 去官网下载」这条降级路径。
- **只读不写程序目录**：本阶段绝不替换 exe / .app。下载只落 `data_dir/updates/`，
  由用户自己解压覆盖 —— 数据目录（`~/.ai-study-companion`）与安装目录本就分离，
  覆盖安装不会动用户数据。
"""

import hashlib
import json
import os
import platform
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app_version import __version__ as APP_VERSION
from ..core.config import settings

router = APIRouter(prefix="/update", tags=["update"])

_REPO = "chenjx7788-sys/ai-study-companion"
_API_URL = f"https://api.github.com/repos/{_REPO}/releases/latest"
_PROXY_PREFIX = "https://gh-proxy.com/"
# 官网：发版时会把下载区链接换成新 tag，故第三源只需正则抓 tag（无需额外维护 version.json）
_SITE_URL = "https://3dc49331b9e44344a0b080cab5aa2fee.app.workbuddy.link/"
_RELEASES_PAGE = f"https://github.com/{_REPO}/releases"
# 弹窗里「浏览器打开下载页」的落点：必须真的是**下载页**（官网 `#download` 区）。
# ⚠️ 此前它直接用 GitHub 的 `html_url`（releases/tag/vX.Y.Z）——那是发布说明页，
#    国内常打不开、页面上还得自己在 Assets 里找 zip，且没有官网的 gh-proxy 加速链接。
#    GitHub 那个地址不丢，改走 `release_url` 字段留给诊断/溯源用。
_DOWNLOAD_PAGE = _SITE_URL.rstrip("/") + "/#download"
_UA = "ai-study-companion-updater/1.0 (+https://github.com/%s)" % _REPO
_TIMEOUT = 8            # 检查用的单源超时（秒）：三源最坏 24s，仍小于前端 120s
# ⚠️ 下载必须单独放宽（检查只给 8s）：GitHub 会 302 到 objects.githubusercontent.com，
#    慢网下建连+重定向常超过 8s。它同时是「连续多久没数据算断线」的上限，
#    也是「链路彻底卡死时取消要等多久」的上限（没法更短：socket 超时一旦触发，
#    这个连接就废了 "cannot read from timed out object"，不能用它来轮询取消标志）。
_TIMEOUT_DL = 15
_CACHE_TTL = 6 * 3600   # 自动检查的缓存有效期
_FORCE_GAP = 10         # 手动检查的最小间隔（防止连点把 GitHub 匿名配额打满）
# ⚠️ 块不能太大：取消是在「两次 read 之间」检查的，块越大越晚才轮到检查。
#    实测本机 ~15KB/s 的链路：64KB 要 5s 才停下，32KB 约 2s；块再小则 Python 侧空转变多。
_CHUNK = 32 * 1024


# ---------------------------------------------------------------- 版本与平台

def _ver_tuple(s: str):
    """'v0.1.10' → (0, 1, 10)。⚠️ 必须按数字比较，不能用字符串（'0.1.10' < '0.1.9'）。"""
    nums = re.findall(r"\d+", s or "")
    return tuple(int(n) for n in nums[:4]) or (0,)


def _bare_ver(tag: str) -> str:
    """把 tag 归一化成纯版本号（去掉 v 前缀）。

    GitHub tag 是 `v0.1.6`，而前端所有文案都是 `v{{ latest }}` 自己拼 v，
    直接透传会出现 `vv0.1.6`。前端不加判断、后端只给纯版本号，避免两边各拼一次。
    """
    return (tag or "").strip().lstrip("vV").strip()


def _ver_gt(a: str, b: str) -> bool:
    ta, tb = _ver_tuple(a), _ver_tuple(b)
    n = max(len(ta), len(tb))
    return ta + (0,) * (n - len(ta)) > tb + (0,) * (n - len(tb))


def _platform_key() -> Optional[str]:
    """当前运行平台 → 安装包后缀标识（与 CI 产物命名对齐，见 build_windows.sh / build_macos.sh）"""
    if sys.platform == "win32":
        return "win"
    if sys.platform == "darwin":
        mach = (platform.machine() or "").lower()
        return "macos-arm64" if mach in ("arm64", "aarch64") else "macos-x86_64"
    return None


def _asset_for(assets, key: Optional[str]):
    """从 release assets 里挑出本平台对应的那个 zip"""
    if not key:
        return None
    suffix = "-win.zip" if key == "win" else f"-{key}.zip"
    for a in assets or []:
        if str(a.get("name", "")).lower().endswith(suffix):
            return a
    return None


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _install_dir() -> str:
    """安装根目录（打包态 = exe 所在目录；开发态 = 仓库 backend/）"""
    try:
        if _is_frozen():
            return str(Path(sys.executable).resolve().parent)
    except Exception:
        pass
    return str(Path(__file__).resolve().parent.parent.parent)


def _updates_dir() -> Path:
    p = Path(settings.data_dir) / "updates"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _cache_path() -> Path:
    return Path(settings.data_dir) / "update_state.json"


# ---------------------------------------------------------------- 网络（标准库）

def _urlopen(req, timeout: int = _TIMEOUT):
    """统一的 urlopen：**本地地址绕开系统代理**。

    ⚠️ 不能直接用 urllib.request.urlopen：它默认吃 `http_proxy` / `https_proxy` 环境变量
       （Windows 还会读注册表代理），而且环境变量形式**没有 bypass 语义** ——
       一旦用户开了代理，连 `127.0.0.1` 的请求也会被丢给代理，直接失败。
       外网（GitHub / 官网）仍然走用户代理：国内用户往往正是靠它才连得上。
    """
    host = (urlsplit(req.full_url).hostname or "").lower()
    if host in ("127.0.0.1", "localhost", "::1"):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        return opener.open(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout)


def _fetch_json(url: str):
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA,
        "Accept": "application/vnd.github+json",
    })
    with _urlopen(req) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with _urlopen(req) as r:
        return r.read().decode("utf-8", "replace")


def _from_github_api(url: str):
    """从（直连或代理的）GitHub API 取结构化信息。失败抛异常，由调用方降级。"""
    d = _fetch_json(url)
    tag = d.get("tag_name") or ""
    if not tag:
        raise ValueError("release 缺少 tag_name")
    assets = []
    for a in d.get("assets") or []:
        assets.append({
            "name": a.get("name"),
            "size": a.get("size") or 0,
            "url": a.get("browser_download_url"),
            "digest": a.get("digest") or "",
        })
    return {
        "latest": tag,
        "name": d.get("name") or tag,
        "notes": d.get("body") or "",
        "published_at": d.get("published_at") or "",
        "page_url": d.get("html_url") or f"{_RELEASES_PAGE}/tag/{tag}",
        "prerelease": bool(d.get("prerelease")),
        "assets": assets,
    }


def _from_site():
    """第三源：官网下载区里 `/releases/download/vX.Y.Z/` 的版本号（无 notes / assets）"""
    html = _fetch_text(_SITE_URL)
    tags = re.findall(r"/releases/download/(v\d+\.\d+\.\d+(?:[-\w.]*)?)/", html)
    if not tags:
        raise ValueError("官网未找到版本号")
    latest = max(tags, key=_ver_tuple)
    return {
        "latest": latest,
        "name": latest,
        "notes": "",
        "published_at": "",
        "page_url": f"{_RELEASES_PAGE}/tag/{latest}",
        "prerelease": False,
        "assets": [],
    }


def _read_cache():
    try:
        return json.loads(_cache_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_cache(obj) -> None:
    """缓存写失败绝不影响主流程（只读文件系统 / 权限异常都可能）"""
    try:
        _cache_path().write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _probe(force: bool):
    """按顺序试三个源，返回 (info, source, error)"""
    now = time.time()
    cache = _read_cache()
    last = cache.get("checked_at") or 0
    gap = _FORCE_GAP if force else _CACHE_TTL
    if cache.get("info") and now - last < gap:
        return cache["info"], cache.get("source") or "cache", ""

    chain = [
        ("github", lambda: _from_github_api(_API_URL)),
        ("gh-proxy", lambda: _from_github_api(_PROXY_PREFIX + _API_URL)),
        ("site", _from_site),
    ]
    errors = []
    for source, fn in chain:
        try:
            info = fn()
            _write_cache({"checked_at": time.time(), "source": source, "info": info})
            # ⚠️ 成功也要把前序失败带回去（调用方据此区分「降级成功」与「一切正常」）：
            #    丢掉它，线上就只剩一句「走了代理」，永远查不出直连为什么挂。
            return info, source, ",".join(errors)
        except Exception as e:
            # 只保留错误类型，不把 URL / 堆栈抛给前端
            errors.append("%s:%s" % (source, type(e).__name__))

    # 全部失败：有旧缓存就用旧的（标注 stale），否则明说失败
    if cache.get("info"):
        return cache["info"], (cache.get("source") or "cache") + "-stale", ",".join(errors)
    return None, "", ",".join(errors)


# ---------------------------------------------------------------- 下载（单任务）

_LOCK = threading.Lock()
_CANCEL = threading.Event()
# 当前正在读的响应对象：cancel() 直接 close 它 —— 否则 worker 卡在 r.read() 里，
# 要等到下一个数据块或读超时（30s）才会看到取消标志，表现成「点了取消没反应」。
_ACTIVE = {"resp": None, "sock": None}
_TASK = {
    "status": "idle",      # idle | downloading | verifying | done | error | canceled
    "version": "",
    "name": "",
    "downloaded": 0,
    "total": 0,
    "percent": 0.0,
    "speed": 0.0,          # 字节/秒（近 2s 滑动窗口）
    "file": "",
    "error": "",
    "started_at": 0,
}
_THREAD: Optional[threading.Thread] = None


def _set(**kw):
    with _LOCK:
        _TASK.update(kw)


def _snapshot():
    with _LOCK:
        return dict(_TASK)


def _sha256_of(path: Path, task_version: str):
    """流式校验，边算边报进度（300MB 全量读也要几秒，不能让人以为卡死）"""
    h = hashlib.sha256()
    done = 0
    total = path.stat().st_size
    with open(path, "rb") as f:
        while True:
            if _CANCEL.is_set():
                return None
            b = f.read(_CHUNK)
            if not b:
                break
            h.update(b)
            done += len(b)
            _set(status="verifying", percent=round(done * 100.0 / total, 1) if total else 0.0)
    return h.hexdigest()


def _stream(url: str, tmp: Path, total_hint: int, t0: float):
    """把 url 的剩余部分写进 tmp（支持 Range 续传）。返回写入的字节数。"""
    pos = tmp.stat().st_size if tmp.exists() else 0
    headers = {"User-Agent": _UA}
    if pos > 0:
        headers["Range"] = "bytes=%d-" % pos
    req = urllib.request.Request(url, headers=headers)
    with _urlopen(req, timeout=_TIMEOUT_DL) as r:
        _ACTIVE["resp"] = r
        _ACTIVE["sock"] = _sock_of(r)
        try:
            return _stream_body(r, tmp, pos, total_hint)
        finally:
            _ACTIVE["resp"] = None
            _ACTIVE["sock"] = None


def _sock_of(r):
    """从 urlopen 的响应里取出底层 socket（取消时要 shutdown 它）"""
    try:
        return r.fp.raw._sock
    except BaseException:
        return None


def _stream_body(r, tmp: Path, pos: int, total_hint: int):
    """把已建立的响应体写进 tmp（_stream 负责建连与句柄登记）。"""
    # 206 = 续传成功；200 = 服务端不认 Range，必须从头覆盖
    if pos > 0 and getattr(r, "status", 200) != 206:
        pos = 0
        mode = "wb"
    else:
        mode = "ab"
    clen = int(r.headers.get("Content-Length") or 0)
    total = (pos + clen) if clen else total_hint
    _set(status="downloading", total=total or total_hint, downloaded=pos,
         percent=round(pos * 100.0 / total, 1) if total else 0.0)

    last_t, last_b = time.time(), pos
    wrote = pos
    with open(tmp, mode) as f:
        while True:
            if _CANCEL.is_set():
                raise _Canceled()
            chunk = r.read(_CHUNK)
            if not chunk:
                break
            f.write(chunk)
            wrote += len(chunk)
            now = time.time()
            if now - last_t >= 0.5:
                sp = (wrote - last_b) / max(now - last_t, 1e-6)
                _set(downloaded=wrote, speed=round(sp, 1),
                     percent=round(wrote * 100.0 / total, 1) if total else 0.0)
                last_t, last_b = now, wrote
    _set(downloaded=wrote, speed=0.0,
         percent=round(wrote * 100.0 / total, 1) if total else 100.0)
    return wrote


class _Canceled(Exception):
    pass


class _ChecksumError(Exception):
    """安装包校验失败：与「网络断了」是两件事，必须给不同文案（前者要重下，后者可重试）"""
    pass


def _download_worker(version: str, name: str, urls, dest: Path, digest: str):
    tmp = dest.with_suffix(dest.suffix + ".part")
    _set(status="downloading", version=version, name=name, file=str(dest),
         downloaded=0, total=0, percent=0.0, speed=0.0, error="", started_at=time.time())
    try:
        # ⚠️ 必须自己建目录：_updates_dir() 只在接口层被调用过，后台线程不能依赖它
        dest.parent.mkdir(parents=True, exist_ok=True)
        if tmp.exists() and tmp.stat().st_size > 0:
            pass  # 断点续传：保留已有部分
        last_err = ""
        ok = False
        for i, url in enumerate(urls):
            try:
                _stream(url, tmp, 0, time.time())
                ok = True
                break
            except _Canceled:
                raise
            except Exception as e:
                last_err = type(e).__name__
                # 只在「几乎没下到东西」时才换源，避免把已下载的大半段丢掉
                size = tmp.stat().st_size if tmp.exists() else 0
                if size > 4 * 1024 * 1024 or i == len(urls) - 1:
                    raise
                _set(error="")
        if not ok:
            raise RuntimeError(last_err or "download failed")

        want = (digest or "").split(":", 1)[-1].strip().lower()
        if want:
            _set(status="verifying", speed=0.0, percent=0.0)
            got = _sha256_of(tmp, version)
            if got is None:
                raise _Canceled()
            if got != want:
                # 坏包必须删掉，否则下次续传会把脏数据拼进去
                try:
                    os.remove(str(tmp))
                except BaseException:
                    pass
                raise _ChecksumError("checksum mismatch")

        try:
            if dest.exists():
                os.remove(str(dest))
        except BaseException:
            pass
        os.replace(str(tmp), str(dest))
        _set(status="done", percent=100.0, downloaded=dest.stat().st_size,
             total=dest.stat().st_size, speed=0.0, file=str(dest))
    except _Canceled:
        _set(status="canceled", speed=0.0)
    except _ChecksumError:
        _set(status="error", speed=0.0,
             error="安装包校验失败（文件可能不完整），已删除，请重试")
    except Exception as e:
        # 取消时我们会 close 响应句柄 → read 抛 ValueError/OSError，
        # 它不等于「网络失败」，否则用户点了取消却看到一条红色错误
        if _CANCEL.is_set():
            _set(status="canceled", speed=0.0)
            return
        _set(status="error", speed=0.0,
             error="下载失败（%s），可重试或在浏览器打开 Release 页手动下载" % type(e).__name__)


def _safe_delete_downloads():
    """下载目录里的残留包（仅用于「下载坏了」的显式清理）。

    ⚠️ 删除一律 `except BaseException` 且绝不外抛：本机护栏在单轮删除配额耗尽时抛
       SystemExit，`except Exception` 接不住 —— 抛出去会变成「500 但文件已删」。
    """
    try:
        root = _updates_dir()
        for p in root.rglob("*"):
            if p.is_file():
                try:
                    os.remove(str(p))
                except BaseException:
                    pass
    except BaseException:
        pass


# ---------------------------------------------------------------- 接口

class DownloadReq(BaseModel):
    version: str = ""
    url: str = ""      # 可选：指定下载地址（高级用法 / 手动指定镜像）
    mirror: bool = True  # 是否允许在国内不可达时自动切 gh-proxy


@router.get("/check")
def check(force: int = 0):
    """检查更新。force=1 手动触发（仅 10s 内不重复打 GitHub）。

    返回字段刻意保持扁平，前端不需要二次推导：
      current / latest / has_update / notes / page_url / release_url / asset / source / error / stale

    ⚠️ page_url 恒为**官网下载区**（`_DOWNLOAD_PAGE`，按钮语义就是「下载页」）；
       本版本对应的 GitHub Release 页另放 `release_url`，前端当前不使用，只供诊断溯源。
    """
    info, source, error = _probe(bool(force))
    key = _platform_key()
    assets = (info or {}).get("assets") or []
    asset = _asset_for(assets, key)
    latest = _bare_ver((info or {}).get("latest"))
    # 三种结局要分清楚，前端提示完全不同：
    #   ① 一切正常      → error="" warn=""
    #   ② 降级成功      → error="" warn="github:URLError"（不打扰用户，只留诊断）
    #   ③ 全部失败/旧缓存 → error 非空（手动检查才提示，自动检查仍静默）
    all_failed = (not source) or source.endswith("-stale") or not info
    downloaded = None
    if asset:
        dest = _updates_dir() / str(asset.get("name"))
        if dest.exists() and dest.stat().st_size == (asset.get("size") or -1):
            downloaded = str(dest)

    return {
        "current": APP_VERSION,
        "latest": latest,
        "has_update": bool(latest) and _ver_gt(latest, APP_VERSION),
        "notes": (info or {}).get("notes") or "",
        "name": (info or {}).get("name") or "",
        "published_at": (info or {}).get("published_at") or "",
        "page_url": _DOWNLOAD_PAGE,
        "release_url": (info or {}).get("page_url") or _RELEASES_PAGE,
        "asset": asset,
        "downloaded_file": downloaded,
        "platform": key,
        "frozen": _is_frozen(),
        "install_dir": _install_dir(),
        "download_dir": str(_updates_dir()),
        "source": source,
        "stale": source.endswith("-stale"),
        "error": (error or "网络不可用") if all_failed else "",
        "warn": "" if all_failed else error,
        "checked_at": int(time.time()),
    }


@router.get("/progress")
def progress():
    return _snapshot()


@router.post("/download")
def start_download(req: DownloadReq):
    global _THREAD
    with _LOCK:
        busy = _TASK["status"] in ("downloading", "verifying")
    if busy:
        raise HTTPException(409, "已有下载任务在进行")

    info, _source, error = _probe(False)
    if not info:
        raise HTTPException(400, "无法获取版本信息（%s）" % (error or "网络不可用"))
    version = _bare_ver(info.get("latest"))
    # ⚠️ 必须两边都归一化：前端拿的是 check() 里去过 v 的版本号，
    #    而 info["latest"] 是原始 tag（v0.1.7）→ 直接比会永远不等，点下载就 400
    if req.version and _bare_ver(req.version) != version:
        raise HTTPException(400, "版本已变化，请重新检查更新")
    asset = _asset_for(info.get("assets"), _platform_key())
    if not asset or not asset.get("url"):
        raise HTTPException(400, "该平台暂无可下载的安装包，请前往 Release 页手动下载")

    dest = _updates_dir() / str(asset.get("name"))
    if dest.exists() and dest.stat().st_size == (asset.get("size") or -1):
        return _snapshot() | {"status": "done", "file": str(dest), "version": version,
                              "name": asset.get("name")}

    urls = [asset["url"]]
    if req.mirror:
        urls.append(_PROXY_PREFIX + asset["url"])

    _CANCEL.clear()
    _THREAD = threading.Thread(
        target=_download_worker,
        args=(version, str(asset.get("name")), urls, dest, asset.get("digest") or ""),
        daemon=True,
    )
    _THREAD.start()
    return {"status": "started", "version": version, "name": asset.get("name")}


@router.post("/cancel")
def cancel():
    _CANCEL.set()
    # ⚠️ 必须主动关掉响应句柄：worker 可能正卡在 r.read() 里等数据，
    #    只置标志要等下一块数据进来（慢网/卡流时可能几十秒）才生效。
    sk = _ACTIVE.get("sock")
    if sk is not None:
        try:
            # ⚠️ 只有 shutdown 能打断另一个线程阻塞中的 recv；
            #    close() 在 Windows 上不会唤醒它（实测 cancel 要等 19s）。
            sk.shutdown(socket.SHUT_RDWR)
        except BaseException:
            pass
    # ⚠️ 绝对不能在这里对响应对象 r 调 close()：BufferedReader.close() 会去抢
    #    读线程正持有的锁 → cancel 请求本身卡住十几秒（实测 19s，比不取消还糟）。
    #    这里只做「shutdown + 置标志」：数据在流动时，下一个 32KB 块读完就会停下；
    #    链路完全卡死时会拖到读超时（≤15s），前端点完立刻显示「正在取消…」。
    return {"status": "canceling"}


class RevealReq(BaseModel):
    path: str = ""


@router.post("/reveal")
def reveal(req: RevealReq):
    """在系统文件管理器里定位安装包（只允许下载目录内的路径）"""
    root = _updates_dir().resolve()
    target = Path(req.path) if req.path else root
    try:
        target = target.resolve()
        if root != target and root not in target.parents:
            raise HTTPException(400, "非法路径")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, "非法路径")

    if sys.platform == "win32":
        if target.is_file():
            subprocess.Popen(["explorer", "/select,", str(target)])
        else:
            subprocess.Popen(["explorer", str(target)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(target)] if target.is_file() else ["open", str(target)])
    else:
        raise HTTPException(400, "当前平台不支持")
    return {"ok": True}


@router.post("/clear")
def clear():
    """清空下载目录（用户主动，例如要腾空间）"""
    with _LOCK:
        if _TASK["status"] in ("downloading", "verifying"):
            raise HTTPException(409, "请先取消进行中的下载")
    _safe_delete_downloads()
    _set(status="idle", version="", name="", downloaded=0, total=0,
         percent=0.0, speed=0.0, file="", error="")
    return {"ok": True}
