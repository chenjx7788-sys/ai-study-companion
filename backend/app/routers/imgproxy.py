"""只读图片代理：让浏览器能显示被防盗链拦掉的外链图。

## 为什么需要它（2026-09-23 实测，不是推测）

微信 `mmbiz.qpic.cn` 的判定规则（逐条实测）：

| 请求头特征 | 结果 |
|---|---|
| 无 Referer / 空 Referer / Referer 是微信域 | **真图**（如 1080x666） |
| Referer 是其它任何域（含 `http://127.0.0.1:8000/`） | **HTTP 200 + 140x140 占位图**（图上印着「此图片来自微信公众平台未经允许不可引用」） |
| 浏览器特征头（`Sec-Fetch-*` 等）**且**无 Referer | **HTTP 400** |

⇒ **浏览器里无解**：`<img referrerpolicy="no-referrer">` 确实能去掉 Referer
（实测请求头里 `referer` 已为空），但随即撞上第三行那条 → 400。
用 {自带 Chromium / 覆盖 Edge UA / 真实 msedge} 三组矩阵验证，结论完全一致
—— **不是 headless UA 的问题**。
⇒ 而后端 `urllib` 不带 `Sec-Fetch-*` → 被当作普通抓取工具放行 → **拿到真图**。

所以：让 `<img>` 指向本端点，由后端取图再转发。前端改写见 `utils/md.js`。

## 与「配图本地化」的分工（不是二选一）

  · **本地化**（`external.localize_images`）是**主路径**：入库时把图下到 `data/assets/`，
    离线可用、不受对方站点变化影响。**新剪藏走这条**。
  · **本代理**是**显示兜底**，只服务两类场景：
      1. 契约上「不落盘」的路径 —— 仅本次阅读（临时阅读）、剪藏预览；
      2. **历史材料**（R6 之前入库的，正文里还是外链）。
    它**不写任何文件**，纯粹转发。

## 安全（本端点对外提供的是「按 URL 取回内容」的能力，必须防 SSRF）

  1. 协议白名单 http/https；拒绝 `file:` / `data:` / 带 userinfo 的 URL
  2. **端口白名单 {80, 443}**
  3. 域名解析后，**每一个 IP 都必须是公网**（`is_global`）——
     loopback / 私网 / 链路本地 / 保留 / CGNAT 一律拒绝
  4. 重定向**手动逐跳**处理，每跳重新做 1~3 的校验（最多 3 跳）
  5. 只接受 `image/*` 响应，且**不代理 SVG**（`image/svg+xml` 直接打开会执行脚本，
     而同源脚本能调本应用的 API）
  6. 大小上限、超时沿用 `external._download_image` 的既有口径（12 MB / 15s）
  7. **任何失败都 302 回原 URL** —— 见 `_fallback()`。这不是"放弃"，而是
     「代理拿不到时，让浏览器照原样去取」，结果**不会比没有本代理时更差**。

⚠️ 已知残余风险（写清楚，不假装没有）：DNS rebinding —— 我们校验的是解析结果，
   但真正建连时 `urllib` 会**再解析一次**。本应用是**本地单机**使用、且被代理的内容
   只可能来自用户自己剪藏的正文，风险面很窄；要彻底封死需自行接管 `socket.create_connection`
   做「解析即连接」，对本地单机应用不值得那套复杂度。
"""
import ipaddress
import socket
import urllib.error
import urllib.parse

from fastapi import APIRouter, Query
from fastapi.responses import RedirectResponse, Response

from ..services.external import fetch_image

router = APIRouter(prefix="/imgproxy", tags=["imgproxy"])

_ALLOWED_SCHEMES = ("http", "https")
_ALLOWED_PORTS = (80, 443)
_MAX_REDIRECTS = 3
_TIMEOUT = 15
_MAX_URL_LEN = 2048

# 与 external._XHS_EXT_BY_TYPE 的取值一一对应（那边只收这几种）
_EXT2MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".avif": "image/avif",
}
# 明确不代理的扩展名（见模块 docstring 第 5 条）
_BLOCKED_EXT = (".svg",)

_REDIRECT_CODES = (301, 302, 303, 307, 308)


def _host_is_public(host: str) -> tuple:
    """host 的所有解析结果是否都是公网地址 → (ok, reason)。"""
    try:
        ips = [ipaddress.ip_address(host)]          # 直接就是 IP
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        except OSError:
            return False, "dns-fail"
        ips = []
        for info in infos:
            try:
                ips.append(ipaddress.ip_address(info[4][0]))
            except ValueError:
                continue
        if not ips:
            return False, "dns-empty"
    for ip in ips:
        mapped = getattr(ip, "ipv4_mapped", None)   # ::ffff:192.168.1.1 要按 v4 判
        if mapped is not None:
            ip = mapped
        if not ip.is_global:
            return False, "non-public-ip"
    return True, ""


def _check_url(u: str) -> tuple:
    """校验并归一化 → (ok, 归一化 URL, reason)。"""
    try:
        sp = urllib.parse.urlsplit(u)
    except ValueError:
        return False, "", "unparsable"
    scheme = (sp.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        return False, "", "scheme"
    if sp.username or sp.password:
        return False, "", "userinfo"
    host = sp.hostname or ""
    if not host:
        return False, "", "no-host"
    try:
        port = sp.port if sp.port is not None else (443 if scheme == "https" else 80)
    except ValueError:
        return False, "", "bad-port"
    if port not in _ALLOWED_PORTS:
        return False, "", "port"
    ok, why = _host_is_public(host)
    if not ok:
        return False, "", why
    # 归一化：去掉 fragment（对图片无意义）；**保留 query**（微信靠 wx_fmt 等参数选格式）
    norm = urllib.parse.urlunsplit((scheme, sp.netloc, sp.path, sp.query, ""))
    return True, norm, ""


def _fallback(target: str, reason: str) -> RedirectResponse:
    """取不到就 302 回原 URL —— 等价于「没有本代理」时的行为。

    ⚠️ 为什么敢这么兜：`target` 已经过 `_check_url`（协议/端口/公网 IP 全过），
       且 302 之后是**浏览器自己**去请求，与本进程无关。
       于是「代理失败」的最终效果 == 现状，用户不会比现在更差。
    """
    return RedirectResponse(target, status_code=302, headers={
        "X-ASC-Proxy": "fallback-" + reason,
        "Cache-Control": "no-store",
    })


@router.get("")
def proxy_image(url: str = Query(..., max_length=_MAX_URL_LEN)):
    """把外链图片取回并原样转发。失败则 302 回原 URL。"""
    raw_url = (url or "").strip()
    ok, target, why = _check_url(raw_url)
    if not ok:
        # ⚠️ 校验失败**不做 302**：把非 http(s)/私网目标交回浏览器等于放任。
        return Response(status_code=400, media_type="text/plain",
                        headers={"X-ASC-Proxy": "reject-" + why,
                                 "Cache-Control": "no-store"})

    cur = target
    for hop in range(_MAX_REDIRECTS + 1):
        try:
            raw, ext = fetch_image(cur, timeout=_TIMEOUT, follow_redirects=False)
        except urllib.error.HTTPError as e:
            code = getattr(e, "code", 0)
            if code in _REDIRECT_CODES and hop < _MAX_REDIRECTS:
                loc = (e.headers.get("Location") or "").strip() if e.headers else ""
                if loc:
                    ok2, nxt, why2 = _check_url(urllib.parse.urljoin(cur, loc))
                    if not ok2:
                        return _fallback(target, "redirect-" + why2)
                    cur = nxt
                    continue
            return _fallback(target, "http-%s" % code)
        except BaseException as e:                  # noqa: BLE001
            # 逐类容错：任何网络/超限/非图片异常都不该把图片变成 500
            return _fallback(target, "fetch-%s" % type(e).__name__)

        if ext in _BLOCKED_EXT:
            return _fallback(target, "blocked-ext")
        mime = _EXT2MIME.get(ext)
        if not mime:
            return _fallback(target, "unknown-type")
        return Response(content=raw, media_type=mime, headers={
            # 同 URL 的图片内容基本不变；长缓存可以显著减少重复转发
            # （浏览器缓存的是本端点 URL，因此不会再去打对方站点）
            "Cache-Control": "public, max-age=86400",
            "X-Content-Type-Options": "nosniff",
            "X-ASC-Proxy": "hit",
        })

    return _fallback(target, "too-many-redirects")
