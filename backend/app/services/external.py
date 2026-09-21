"""外部内容接入：URL 抓取 / 正文抽取 / 来源能力判定
分层（方案 §2）：
  ① 本地文件（已实现）② 官方 API（微信读书，独立方案）③ 公开网页 / 公众号（本模块）④ 付费课程（不做）
设计要点 —— 每条都是实测或踩坑才写下来的，不要"顺手改回去"：
1. **能力判定集中一张 `ORIGIN_RIGHTS` 表**。项目反复踩过「漏一处就不一致」
   （转笔记 8 入口、播客加风格成套改）。接新渠道时必然有人忘记加限制。
   **能力判定的唯一来源就在本模块**，渠道侧不许各写一套。
2. **SPA 用特征串判定，绝不用字数阈值**。实测少数派返回的"正文"就是一句
   `We're sorry but sspai doesn't work properly without JavaScript enabled.`（101 字）——
   它是**一段合法的短正文**，按「字数 < 200 判失败」会把它误判成「抓到了短正文」，
   于是功能在大量现代内容平台上"看起来能用但抓到的是废话"。
3. **失效的公众号链接返回 HTTP 200**。实测：伪造短链 → 200 + 75 字噪声（`参数错误`）；
   伪造临时链接 → 200 + 75 字噪声（`系统出错`）。**只看 HTTP 状态码会把它当成功**，
   必须同时查错误页标志串。
4. **分块复用 `parser.split_markdown`**，本模块只做转出（见文件末尾 import）。
   两条路径各切一套 → 同一篇文章在「仅本次阅读」与「入库」下块数不同 → 用户当成 bug。
5. **代理必须显式绕过**。本机系统代理会拒绝连直连目标（报 "目标计算机积极拒绝"）。
   用 `ProxyHandler({})` 建独立 opener，而**不去改 `os.environ`** —— 后者会污染同进程其他模块。
6. **必须显式开 `include_images=True`**。默认 False 会丢掉正文里的**全部**图片：
   对"正文以图片为主"的文章（截图/表格/流程图型，如人人都是产品经理）等于只剩几句说明文字，
   用户看到的就是「抓到的正文只有一点点」。实测 woshipm 三篇默认只有 970/1276/3308 字、
   0 张图；开图后 1713/1975/3896 字 + 8~10 张内容图。详见 `_extract` 与 `_collect_images`。
7. **公众号必须走专用抽取（`_wx_extract`），不能只靠 trafilatura**。三重实测原因：
   trafilatura 的 precision 模式删公众号特有的 `<section>` 排版块（A 4900→**8208** 字、
   C 789→**1732** 字）；正文图全是 `data-src` 懒加载，它只认 `src` → 0 张图；
   新版页面（`item_show_type=8` 图片消息 / SSR 壳）DOM 里**根本没有正文**，
   生产结果 `ok=False / TOO_SHORT`。正文字段与降级顺序见「微信公众号专用抽取」一节。
"""

from __future__ import annotations
import gzip
import hashlib
import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path
import trafilatura
from lxml import html as _lxml_html


# ---------- 来源能力判定（唯一来源） ----------
ORIGIN_RIGHTS: dict[str, dict] = {
    "upload": {"can_index": True, "label": "本地上传"},
    "local": {"can_index": True, "label": "本地导入"},
    "weread": {"can_index": True, "label": "微信读书"},
    # 网页 / 公众号：允许入库，但**必须先经用户逐条确认**（need_confirm）。
    # 这不是交互偏好，是合规边界的技术表达：用户主动单篇剪藏（对齐 Notion/印象笔记惯例）
    # 与"平台提供一键抓全量"性质不同（方案 §2.1）。
    "url": {"can_index": True, "label": "网页剪藏", "need_confirm": True},
    "wx": {"can_index": True, "label": "公众号文章", "need_confirm": True},
    # 小红书（P0-3h）：必须用带 xsec_token 的完整分享链接才能打开（裸 note id 不行），
    # 且配图是**短时签名直链** → 落库时下载到本地（见 `localize_images`）。
    "xhs": {"can_index": True, "label": "小红书笔记", "need_confirm": True},
    # "feed": 播客 / RSS 暂缓（方案 V1.3）。枚举值预留 —— 将来启用时不必改数据模型。
}
_DEFAULT_RIGHTS = {"can_index": False, "label": "未知来源", "need_confirm": True}


def origin_rights(origin: str | None) -> dict:
    """取某来源的能力配置。未知来源一律按「不可索引 + 需确认」兜底（安全默认）。"""
    return ORIGIN_RIGHTS.get((origin or "").strip(), _DEFAULT_RIGHTS)


# 以 URL 作为 origin_ref 的来源 —— "同一 URL 是否已入库"只在这几个来源之间成立。
# ⚠️ 这是枚举的单一来源：将来新增可剪藏来源（如 RSS 启用）只需改这里。
URL_ORIGINS = ("url", "wx", "xhs")


def find_saved_material(db, norm_url: str):
    """幂等查重：归一化 URL 是否已入库，命中返回 Material，否则 None。

    ⚠️ `origin_ref` **没有 DB 层唯一约束**（存量记录全为空串，加唯一约束会让迁移失败），
    所以幂等只能由业务层"先查后插"保证。见方案 §9.1。
    同时比对 `origin`：URL 归一化后同一篇文章的 kind 必然一致，多加一重防误判。

    剪藏落库（materials）与临时阅读的"已沉淀"标记共用本函数 ——
    两处若各写一份，新增来源时必有一处漏改，表现为"明明加过知识库却显示未加"。
    """
    if not norm_url:
        return None
    from ..models import Material        # 延迟导入：避免 services 在模块级耦合 models
    return (db.query(Material)
            .filter(Material.origin_ref == norm_url)
            .filter(Material.origin.in_(URL_ORIGINS))
            .first())


def find_saved_map(db, urls: list[str]) -> dict[str, int]:
    """批量查重（供列表页用）：{归一化 URL: material_id}。

    「最近阅读」列表一次要问 20 条是否已沉淀 —— 逐条查库是 20 次往返。
    本函数与 `find_saved_material` 共用 `URL_ORIGINS`，口径不会分叉。
    """
    clean = [u for u in {(u or "").strip() for u in (urls or [])} if u]
    if not clean:
        return {}
    from ..models import Material        # 延迟导入，同上
    rows = (db.query(Material.id, Material.origin_ref)
            .filter(Material.origin_ref.in_(clean))
            .filter(Material.origin.in_(URL_ORIGINS))
            .all())
    return {ref: mid for mid, ref in rows}


def origin_label(origin: str | None) -> str:
    return origin_rights(origin)["label"]


def can_index(origin: str | None) -> bool:
    return bool(origin_rights(origin)["can_index"])


def need_confirm(origin: str | None) -> bool:
    return bool(origin_rights(origin).get("need_confirm", False))


# ---------- 判定清单 ----------
WX_HOST = "mp.weixin.qq.com"

# 小红书（P0-3h）：笔记页域 + 短链域（App 分享给的是短链）+ 国际版
XHS_HOSTS = ("xiaohongshu.com", "xhslink.com", "rednote.com")


# SPA「没渲染出正文」的特征串。命中即判 SPA_EMPTY，**不看字数**。
# 新增站点时往这里加特征串，不要在别处另写判定。
SPA_MARKERS = (
    "doesn't work properly without JavaScript",
    "does not work properly without JavaScript",
    "Please enable JavaScript",
    "enable JavaScript and cookies to continue",
    "需要启用 JavaScript",
    "请开启 JavaScript",
    "You need to enable JavaScript to run this app",
    "This page requires JavaScript",
    "we're sorry but",
)


# 页面虽返回 200，但内容其实是错误页（**不是文章**）。
# ⚠️ 实测失效公众号链接走的是这条路径（200 + 短噪声），不是 HTTP 4xx。
WX_ERROR_MARKERS = (
    "参数错误",
    "系统出错",
    "环境异常",
    "请完成验证",
    "该内容已被发布者删除",
    "此内容因违规无法查看",
    "该公众号已迁移",
    "此内容发送失败无法查看",
    "链接已过期",
    "请在微信客户端打开链接",
)


# 通用错误页标志（非公众号）
GENERIC_ERROR_MARKERS = (
    "页面不存在",
    "404 not found",
    "the page you are looking for",
)


# 正文低于此长度视为抽取失败。仅作为**兜底**（主判据是 SPA 特征串与错误页标志）。
MIN_CHARS = 200

# 粘贴降级（用户主动粘正文）可入库的最短长度。与 clip_save 共用同一阈值（P3-3）：
# 误粘几个字（如 3 字）不该被当成可入库的文章；正常粘贴的一段内容（金句/要点）不能误伤。
MIN_CLIP_CHARS = 10


# 单次抓取上限。实测公众号文章页 3.5MB，留足余量同时防住超大页面吃内存。
MAX_HTML_BYTES = 8 * 1024 * 1024


# 通用跟踪参数（跨站点通行）
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
    "spm", "scm", "from", "from_source", "isappinstalled", "share_from",
    "share_medium", "share_plat", "share_session_id", "share_source", "share_tag",
    "ref", "referer", "referrer", "fbclid", "gclid", "yclid", "igshid", "wt_mc",
}


# 仅公众号域名的跟踪参数。**不要合并进上面那张表** ——
# 其中 `version` / `scene` / `devicetype` 在别的站点可能是业务参数，误删会改变语义。
# ⚠️⚠️ **绝对不要把 `src` / `timestamp` / `ver` / `signature` / `new` 加进这张表**（P2-1 实测）：
#   它们是搜狗「临时链接」（src=11）的全部身份 —— 真实 src=11 链接的 URL 里**没有** `__biz/mid/idx/sn`
#   （身份参数在页面 HTML 的 `msg_link` 变量里，不在 URL query）。删掉 `signature` 会让所有临时链接
#   塌成 `https://mp.weixin.qq.com/s` → **不同文章判成同一篇**（比「重复入库」糟得多）。
#   保留它们 → 同一篇两次搜狗搜索会重复入库，但临时链接 6 小时过期、用户极少两次搜同一篇，代价可忽略。
#   实测见 `_fetch_probe/_probe_p21_wx_temp_link.py`。
WX_TRACKING_PARAMS = {
    "scene", "clicktime", "enterid", "ascene", "devicetype", "version",
    "nettype", "abtest_cookie", "fontScale", "wx_header", "exportkey",
    "pass_ticket", "chksm", "xtrack", "nsukey", "key", "uin",
}
# 仅小红书域名的跟踪/凭证参数。
# ⚠️⚠️ 这里**必须**包含 `xsec_token`，这与公众号的处理**恰好相反**，理由：
#   微信的 `__biz/mid/idx/sn` 是**内容身份**（删掉会把不同文章归一成同一个 URL）；
#   而小红书的 `xsec_token` 是**访问凭证**（短时效），**内容身份由 path 里的
#   `/explore/{24位 note id}` 承担**。留着 token 的后果是：同一篇笔记换个分享链接
#   就归一成不同 URL → 幂等失效、重复入库。删掉它，同一笔记的所有分享链接归一为同一个。
XHS_TRACKING_PARAMS = {
    "xsec_token", "xsec_source", "xsec_share_type", "source", "type", "appuid",
    "apptime", "share_id", "ex_source", "xhsshare", "ignoreEngage", "ignoreFilter",
    "allow_red_type", "is_from_note_sqr", "share_from_user_hidden",
}

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


# SSL：默认开启校验；证书链异常的站点降级重试一次（见 _read_url），并在 meta 留痕
_ctx_verify = ssl.create_default_context()
_ctx_noverify = ssl.create_default_context()
_ctx_noverify.check_hostname = False
_ctx_noverify.verify_mode = ssl.CERT_NONE


def _build_opener(ctx):
    """显式绕过代理、并指定 SSL 上下文的 opener。
    ⚠️ 两个必须记住的 urllib 细节（写完第一版就踩了，联网全挂）：
      1. **`context=` 只有模块级 `urllib.request.urlopen` 才有**，
         `OpenerDirector.open()` 不收这个参数 —— 传了直接 `TypeError`。
         要给自定义 opener 控制 SSL，必须挂 `HTTPSHandler(context=ctx)`。
      2. **代理用 `ProxyHandler({})` 显式清空**，而不要去改 `os.environ`
         （后者会污染同进程的其他模块，且 reload 后才发现）。
    """
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),      # {} = 不走任何代理
        urllib.request.HTTPSHandler(context=ctx),
    )
_opener_no_proxy = _build_opener(_ctx_verify)
_opener_no_proxy_noverify = _build_opener(_ctx_noverify)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """不跟随 3xx —— 用于读短链的 Location（见 `resolve_xhs_short`）。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# 读短链跳转专用（不跟随重定向）
_opener_noredirect = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    urllib.request.HTTPSHandler(context=_ctx_verify),
    _NoRedirect(),
)


# ---------- URL 处理 ----------
def detect_kind(url: str) -> str:
    """按域名判定来源渠道：mp.weixin.qq.com → wx，其余 → url"""
    try:
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
    except ValueError:
        return "url"
    if host == WX_HOST or host.endswith("." + WX_HOST):
        return "wx"
    # 小红书（含短链域）。短链会在 `fetch_url` 里先展开成真实笔记链接再抓。
    if any(host == h or host.endswith("." + h) for h in XHS_HOSTS):
        return "xhs"
    return "url"


def is_wx_temp_link(url: str) -> bool:
    """是否公众号「临时链接」。
    实测：`s?src=11&timestamp=..&signature=..` 形态仅 6 小时有效。
    判据用查询参数 `src=11`（而不是"有没有 signature"）—— 后者也出现在正常链接里。
    """
    if detect_kind(url) != "wx":
        return False
    try:
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    except ValueError:
        return False
    return q.get("src", [""])[0] == "11"


def _ensure_scheme(u: str) -> str:
    """补上协议前缀。**`normalize_url` 与 `fetch_url` 的 target 共用这一份规则。**

    ⚠️ 存在的理由（P0-2，实测 `_fetch_probe/_probe_clip_scheme.py`）：
    这两处各写一遍必然漂移，而漂移的后果不是"不好看"，是**接口 500** ——
    补前缀只发生在归一化结果 `norm` 上、真正去抓的 `target` 没补时，
    `urllib.request.Request(target)` 直接抛 `ValueError: unknown url type`，
    而该异常**不在 `fetch_url` 的 except 列表里**（那里只捕获网络异常族，这是刻意的）。
    触发面很宽：用户手打/从别处抄来的裸域名、批量切分出的碎片都算。
    """
    u = (u or "").strip()
    if not u:
        return ""
    return u if u.lower().startswith(("http://", "https://")) else "https://" + u


def normalize_url(url: str) -> str:
    """归一化 URL，用于「同一篇内容不重复入库」的幂等判定。
    实测依据（`_fetch_probe/probe8_failure_modes.py`）：下面 4 个字符串指向同一篇文章，
    互不相等 → 不归一化就会入库 4 次：
        .../weekly-issue-288.html
        .../weekly-issue-288.html?utm_source=wechat&utm_medium=share
        .../weekly-issue-288.html#section-2
        https://WWW.Ruanyifeng.COM/blog/2024/01/weekly-issue-288.html
    保留策略：**只删白名单里的跟踪参数**，不做「只留某些参数」的反向裁剪 ——
    后者会误删 `__biz/mid/idx/sn` 这类公众号身份参数，把不同文章判成同一篇。
    """
    url = (url or "").strip()
    if not url:
        return ""
    # 用户常连中文标点一起复制进来
    # ⚠️ 补协议前缀**只在这一处实现**（`_ensure_scheme`），与 fetch_url 的 target 共用
    url = _ensure_scheme(url.strip("\u3000 \t\r\n'\"<>《》【】（）()，,。;；"))
    try:
        sp = urllib.parse.urlsplit(url)
    except ValueError:
        return url
    scheme = (sp.scheme or "https").lower()
    netloc = (sp.netloc or "").lower()
    # 去掉默认端口，避免 http://a.com:80/x 与 http://a.com/x 判成两篇
    if netloc.endswith(":443") and scheme == "https":
        netloc = netloc[:-4]
    elif netloc.endswith(":80") and scheme == "http":
        netloc = netloc[:-3]
    kind = detect_kind(url)
    drop = TRACKING_PARAMS | (WX_TRACKING_PARAMS if kind == "wx" else set())
    if kind == "xhs":
        drop = drop | XHS_TRACKING_PARAMS
    pairs = urllib.parse.parse_qsl(sp.query, keep_blank_values=True)
    kept = [(k, v) for k, v in pairs if k.lower() not in drop]
    query = urllib.parse.urlencode(kept)
    path = sp.path or "/"
    # 去掉尾部多余斜杠（保留根路径）—— /a/b/ 与 /a/b 通常是同一页
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/") or "/"
    # 丢弃 fragment（#section-2 是页内锚点，不影响内容身份）
    return urllib.parse.urlunsplit((scheme, netloc, path, query, ""))


# ---------- 抓取 ----------
def _decode_body(raw: bytes, content_type: str) -> str:
    """HTML 字节 → str。
    优先按 Content-Type 里声明的 charset；没声明或解出乱码时用 charset_normalizer 猜。
    实测 `charset_normalizer` 把 GBK 页面正确识别为 gb18030。
    """
    charset = ""
    if "charset=" in content_type.lower():
        charset = content_type.lower().split("charset=", 1)[1].split(";")[0].strip().strip('"\'')
    if charset and charset not in ("utf-8", "utf8"):
        try:
            return raw.decode(charset, errors="replace")
        except (LookupError, UnicodeDecodeError):
            pass
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        from charset_normalizer import from_bytes
        best = from_bytes(raw).best()
        if best is not None:
            return str(best)
    except Exception:
        pass
    return raw.decode("utf-8", errors="replace")


def _decompress_capped(raw: bytes, enc: str, cap: int) -> bytes:
    """按 Content-Encoding 解压，并**限制解压后的输出上限**。

    ⚠️ 为什么不能用 gzip.decompress / zlib.decompress：那两个是「一次全量解压」——
    8MB 的压缩流可以解出几百 MB，**在解压那一刻内存就已经吃满**，之后再截断已经晚了。
    这里用 decompressobj 分块喂入、单块限制输出长度，解压出的字节数**永远不超过 cap**。

    解压不成功（数据不是该编码 / 流被截断）时**原样返回入参**，由调用方按原逻辑解码。
    `gzip.decompress` 对截断流抛的是 `EOFError`（不是 `OSError`），原实现会漏网冒泡成 500；
    这里改成「解压期任何异常都退回原字节」。
    """
    if not enc or ("gzip" not in enc and "deflate" not in enc):
        return raw
    # wbits 47 = 自动识别 gzip / zlib 两种头；-15 = 裸 deflate（部分站点这么发）
    for wbits in (47, -15):
        try:
            out = zlib.decompressobj(wbits).decompress(raw, cap)
        except zlib.error:
            continue
        except Exception:                 # noqa: BLE001 解压器不该打断抓取
            return raw
        if out:
            return out
    return raw


def _read_url(url: str, timeout: int) -> tuple[str, str, bool]:
    """读一个 URL，返回 (html, content_type, ssl_降级过)。
    只把「网络层失败」转成异常；**代码缺陷必须原样抛出**（见下面对 TypeError 的处理）。
    """
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
    })
    degraded = False
    try:
        resp = _opener_no_proxy.open(req, timeout=timeout)
    except ssl.SSLError:
        # 部分站点证书链异常。降级重试一次并留痕，而不是静默 fail。
        degraded = True
        resp = _opener_no_proxy_noverify.open(req, timeout=timeout)
    with resp as r:
        ct = r.headers.get("Content-Type", "") or ""
        enc = (r.headers.get("Content-Encoding", "") or "").lower()
        raw = r.read(MAX_HTML_BYTES)
    raw = _decompress_capped(raw, enc, MAX_HTML_BYTES)
    return _decode_body(raw, ct), ct, degraded


def _looks_like_html(content_type: str, html: str) -> bool:
    """是否值得交给 trafilatura。
    纯文本文件（如 robots.txt）也能 200 且内容不大，但 trafilatura 返回 None ——
    不做这道闸，用户会看到一句含糊的"未提取到正文"。
    """
    ct = (content_type or "").lower()
    if "html" in ct or "xml" in ct or not ct:
        return True
    head = html[:2000].lower()
    return "<html" in head or "<!doctype html" in head or "<body" in head


# ---------- WP15：从「页面原始源码」抽取（带登录态的浏览器视图路径） ----------
#
# 为什么需要这一节（实测，见 `AI伴学助手_WP15内容抽取入库方案-20260921.md` §2）：
#   ① 服务端裸抓被反爬拒绝的站点（知乎 403 等），在**用户自己的浏览器视图**里不是问题；
#   ② `QWebEngineView.toHtml()` 给的是**渲染后 DOM 序列化**，`<script>` 已被剥离
#      → 公众号/小红书正文都在内联脚本变量里，实测正文 **−90.9%**、配图 0 张
#      （`_probe_wp15_extract_matrix.py`：1413 字/8 图 → 129 字/0 图）；
#   ③ Qt 的 `QWebEngineCookieStore` **只写不可读**（无 `cookiesForUrl`）→ 无法把会话
#      导出给服务端重抓（`_probe_wp15_p1_cookie_fetch.py`）。
#   ⇒ 唯一可行：在**页面内** `fetch(location.href,{credentials:'include'})`
#      取回**网络层原始响应体**（含 `<script>` 载荷、自动带 cookie / HttpOnly），
#      再交给**同一条抽取链**处理（`_probe_wp15_p2_page_fetch.py` 已实证）。

# 单页源码截断上限（字符）。依据：本机样本 2.47 MB / 1.45 MB；回调把字符串搬回 Python
# 要走 Qt IPC，实测 588K 字符拉取约 62 ms（`_probe_wp15_p3_bigstr.py`）—— 压测无问题，
# 但数 MB × 多标签会拖垮主线程。取 3,000,000 覆盖绝大多数真实页面（含内联脚本的 SSR 页）。
# ⚠️ 截断**必须保头**：`window.__INITIAL_STATE__` 等载荷都在文档头部（P3 已验「头在尾丢」）。
SOURCE_MAX_CHARS = 3_000_000


def source_fetch_js(cap: int = SOURCE_MAX_CHARS, token: str = "") -> str:
    """构造「页内取源」JS。结果写 `window.__asc_src`，状态写 `window.__asc_src_*`。

    ⚠️⚠️ 三个「违反就静默出错」的点，均有实测依据：
      ① **绝不能**在主线程同步等回调（WP13 血泪）：`runJavaScript` 的回调在事件循环里跑，
         主线程若被 `Event.wait()` 占住 → 事件循环停摆 → 回调永不触发 → **零结果且不报错**。
         故本 JS 只「发起 + 写全局」，值由调用方在**自己的线程**里轮询。
      ② `credentials:'include'` **必须有** —— 少了它请求不带会话，登录站点会返回登录墙。
         实测负样本：`credentials:'omit'` → 抓到「需要登录才能查看」。
      ③ 截断在 **JS 侧**做：让 IPC 只搬上限内的字节，同时把**截断前的真实长度**
         如实回报（`__asc_src_len`），供上层判「是否只拿到半篇」。

    ⚠️ `token` 会**原样嵌入 JS 字面量**。调用方只传本项目自生成的短标识
       （`browser_panel` 用 `t<seq>`），**绝不传页面内容 / URL** —— 否则引号会提前闭合
       JS 字符串（症状是「取源莫名失败」）。这里仍做一次清洗兜底。
    """
    tok = re.sub(r"[^A-Za-z0-9_\-]", "", str(token or ""))[:64]
    return (
        "(function(){var CAP=%d,TOK='%s';"
        "window.__asc_src=null;window.__asc_src_len=-1;window.__asc_trunc=false;"
        "window.__asc_tok=TOK;window.__asc_err=null;"
        "fetch(location.href,{credentials:'include'})"
        ".then(function(r){return r.text();})"
        ".then(function(t){window.__asc_trunc=t.length>CAP;"
        "window.__asc_src=window.__asc_trunc?t.slice(0,CAP):t;"
        "window.__asc_src_len=t.length;})"
        ".catch(function(e){window.__asc_err=String(e);window.__asc_src_len=-2;});"
        "return 'started';})()"
    ) % (int(cap), tok)


def extract_from_source(url: str, html: str) -> dict:
    """从页面**原始源码**抽取正文 —— 与 `fetch_url` 共用**同一条判定链**。

    ⚠️⚠️ 为什么必须要这个函数，而不是「把源码当 `text` 传给 `fetch_or_passthrough`」：
       `fetch_or_passthrough(url, text)` 的 `text` 语义是**已抽好的正文**（传了就 passthrough
       原样落库）。把页面源码当 `text` 传进去 → 落库的是**整篇 HTML**。
       这正是本项目最忌的「同一资源两个入口给出不同产物」。

    ⚠️ 判定顺序必须与 `fetch_url` **逐字一致**（错误页 → 专用抽取器 → xhs 判死 → SPA →
       图片消息/图文豁免 → 长度）。`fetch_url` 的网络段之后就是调本函数 →
       **两处只有一份实现**。守住这条不变量的探针：`_probe_wp15_parity.py`。
    """
    raw_input = (url or "").strip()
    picked = extract_first_url(raw_input)
    target = _ensure_scheme(picked or raw_input)
    norm = normalize_url(target)
    kind = detect_kind(norm)
    out = {
        "ok": False, "kind": kind, "url": norm, "input_url": raw_input,
        "title": "", "text": "", "meta": {}, "reason": "",
        "chars": 0, "elapsed_ms": 0,
    }

    def done(reason: str = "") -> dict:
        if out["ok"] and not out["title"]:
            try:
                out["title"] = urllib.parse.urlsplit(norm).hostname or "未命名网页"
            except ValueError:
                out["title"] = "未命名网页"
        out["reason"] = reason
        out["chars"] = len(out["text"])
        return out

    # ① 先查错误页（在抽正文之前）：公众号失效链接是 200 + 短噪声，靠抽正文长度判不出来
    if kind == "wx":
        hit = next((m for m in WX_ERROR_MARKERS if m in html), "")
        if hit:
            out["meta"]["wx_error_marker"] = hit
            return done("WX_EXPIRED")
    # 小红书同性质：**裸 note id / token 失效**时页面标题就是「小红书 - 你访问的页面不见了」，
    # 且同样是 HTTP 200。必须在抽取前判 —— 否则会把一个"页面不见了"壳页当笔记存下来。
    if kind == "xhs" and _XHS_GONE_MARKER in _xhs_page_title(html):
        out["meta"]["xhs_gone"] = True
        return done("XHS_EXPIRED")
    # 公众号走专用路径：trafilatura 在公众号页上系统性抽不全、拿不到 data-src 图，
    # 新版页面更是 DOM 里压根没有正文。详见上方「微信公众号专用抽取」一节。
    if kind == "wx":
        title, text, meta = _wx_extract(html, norm)
    elif kind == "xhs":
        # 小红书同理必须单开：正文与配图都在内联的 __INITIAL_STATE__ 里，DOM 抽取拿不到
        # （详见上方「小红书专用抽取」一节）
        title, text, meta = _xhs_extract(html, norm)
    else:
        title, text, meta = _extract(html, norm)
    out["title"] = title
    out["text"] = text
    out["meta"].update(meta)
    if is_wx_temp_link(norm):
        # 不拦截，只提示 —— 6 小时内仍然可用，用户可能就是想马上读完
        out["meta"]["wx_temp_link"] = True
    out["meta"]["template"] = "wx" if kind == "wx" else "article"
    # 小红书：<title> 正常但笔记对象为空（token 失效 / 笔记被删）→ 单独判死。
    # ⚠️ 必须放在 SPA 判定之前：否则空正文会被归因成 SPA_EMPTY，给用户的建议动作就错了
    #    （SPA 是"请粘贴正文"，而这里该做的是"重新复制分享链接"）。
    if kind == "xhs" and not text:
        return done("XHS_EXPIRED")
    # ② SPA 判定（在长度判定**之前**）：SPA 会返回"提示语正文"，先按长度判会被放过
    haystack = (text or "")[:3000].lower()
    if not text or any(m.lower() in haystack for m in SPA_MARKERS):
        return done("SPA_EMPTY")
    # ⚠️ 图片消息（`item_show_type=8`）例外：正文本来就只有一小段说明 + 若干张图，
    # 按「字数 < MIN_CHARS 判失败」会把它误判成「不是文章页」—— 用户实测看到的
    # 「只提取到很短的正文，可能不是文章页」正是这条路径。拿到了内容就算成功。
    if kind == "wx" and out["meta"].get("wx_image_post") and text:
        out["ok"] = True
        return done("")
    # 小红书图文笔记的文字天然极短（配图才是主体，实测正文 110 字 + 3 图）→ 同样豁免
    # 字数判失败，否则每篇图文笔记都会被误判成「不是网页正文」。纯文字笔记仍按阈值判。
    if kind == "xhs" and out["meta"].get("images") and text:
        out["ok"] = True
        return done("")
    if len(text) < MIN_CHARS:
        low_html = html[:4000].lower()
        if any(m in low_html for m in GENERIC_ERROR_MARKERS):
            return done("FETCH_FAILED")
        return done("TOO_SHORT")
    out["ok"] = True
    return done("")

def fetch_url(url: str, timeout: int = 20) -> dict:
    """抓取网页并抽取正文。
    返回：
        {
          ok: bool,            # 是否拿到可用正文
          kind: 'url'|'wx',    # 来源渠道（detect_kind）
          url: str,            # 归一化后的 URL（入库的 origin_ref 用它）
          input_url: str,      # 用户原始输入（排错用）
          title: str,          # 标题（取不到时回退域名）
          text: str,           # 正文（Markdown 风格纯文本；ok=False 时为空）
          meta: dict,          # {author, date, sitename, template, ssl_degraded, ...}
          reason: str,         # ok=False 时的原因码；ok=True 时为空串
          chars: int,          # 正文字数
          elapsed_ms: int,
        }
    原因码：
        BAD_URL         URL 为空或无法解析
        FETCH_FAILED    连接层 / HTTP 层失败（404、域名不存在等）
        NOT_HTML        目标不是网页（图片、纯文本文件）
        SPA_EMPTY       页面未渲染出正文（SPA）→ 走「粘贴正文」降级通道
        WX_EXPIRED      公众号临时链接失效 / 文章被删 / 需验证
        TOO_SHORT       抽到了内容但太短，不足以当一篇文章
    """
    t0 = time.time()
    raw_input = (url or "").strip()
    # App「分享 → 复制链接」给的是**整段文案**（标题 + emoji + 短链 + 说明），
    # 先抽出其中的 URL 再归一化。对纯 URL 输入本步恒等（正则匹配整串）。
    picked = extract_first_url(raw_input)
    # ⚠️⚠️ 补协议前缀必须作用在 **target** 上，而不是只作用在 `norm` 上（P0-2）。
    #    实测：同一主机只差一个前缀 —— `https://no-such-host.invalid/` 返回 FETCH_FAILED，
    #    不带前缀的 `no-such-host.invalid` 则抛 `ValueError: unknown url type`
    #    （`_fetch_probe/_probe_clip_scheme.py` 的对照实验）。该异常不在下面那几个
    #    except 里 → 冒泡成 500，前端只看到 `Request failed with status code 500`。
    # ⚠️⚠️ **抓取必须用 `target`（保留 xsec_token）**；归一化后的 `norm` 只作幂等身份。
    #    对小红书，`normalize_url` 会**故意删掉 xsec_token**（它是访问凭证、不是内容身份，
    #    见 `XHS_TRACKING_PARAMS` 的说明）。若拿 norm 去抓，必然只得到"页面不见了"。
    target = _ensure_scheme(picked or raw_input)
    norm = normalize_url(target)
    kind = detect_kind(norm)
    out = {
        "ok": False, "kind": kind, "url": norm, "input_url": raw_input,
        "title": "", "text": "", "meta": {}, "reason": "", "chars": 0,
        "elapsed_ms": 0,
    }
    def done(reason: str = "") -> dict:
        # ⚠️⚠️ 标题兜底必须收口在这里（N9）。
        #    原先只写在下方「长度判定通过」之后，于是**两条提前返回的成功路径** ——
        #    公众号图片消息（`item_show_type=8`）、小红书图文笔记（正文天然极短，
        #    走豁免分支直接 return）—— 带着空 title 出去，一路飘到材料库被兜成
        #    「未命名网页」，多条之间还会互相抢文件名（`未命名网页 (2).md`）。
        #    实测：同一条 xhs 笔记去掉配图（走不到早退）能拿到域名标题，加回配图就
        #    变成空串 —— 差别只来自语句顺序，是缺陷不是设计。
        #    ⚠️ 只在 ok=True 时兜：失败条目（BAD_URL / FETCH_FAILED / NOT_HTML…）保持
        #       空标题，否则前端「无法读取这个链接」的兜底文案会被域名顶掉。
        if out["ok"] and not out["title"]:
            try:
                out["title"] = urllib.parse.urlsplit(norm).hostname or "未命名网页"
            except ValueError:      # 畸形 netloc（理论不可达：host 校验已在前置拦截）
                out["title"] = "未命名网页"
        out["reason"] = reason
        out["chars"] = len(out["text"])
        out["elapsed_ms"] = int((time.time() - t0) * 1000)
        return out
    try:
        host = urllib.parse.urlsplit(norm).hostname or ""
    except ValueError:            # 畸形 netloc（例如未闭合的 IPv6 字面量）
        return done("BAD_URL")
    if not host:
        return done("BAD_URL")
    # ⚠️⚠️ host 必须能编码成 ASCII、且像个域名（含点号）—— 这是 P0-1 的前置拦截。
    #    实测（`_probe_clip_badhost.py` / `_probe_clip_fixscheme.py`）：粘贴
    #    「不含链接的说明文字」「批量切分出的碎片」「emoji」时，host 会是
    #    `刚看到一篇好文` / `——` / `😭` 这类非 ASCII 串，而 `opener.open()` 在发请求时抛
    #    `UnicodeEncodeError: 'latin-1' codec can't encode…` —— 同样不在 except 列表里 → 500，
    #    且**补协议前缀也救不了**（这是与 P0-2 独立的第二个 500 来源）。
    #    在这里前置拒绝有两个好处：
    #      ① 异常在进入网络层之前就变成正常的抓取结果（`reason=BAD_URL`，不再是 500）
    #      ② 碎片不再发起真实 DNS / 连接（批量粘贴时这是几十次无谓等待）
    #    ⚠️ 代价与取舍：中文域名（`中文.com`）会被拒。这类域名实际应使用 punycode（`xn--`）
    #       形式，而当前实现对中文域名本来就是抛异常 → 拒掉是改善而非退步。
    try:
        host.encode("ascii")
    except UnicodeEncodeError:
        return done("BAD_URL")
    # ⚠️ 含 `:` 的 host 也要放行：IPv6 字面量的 hostname 是 `::1` 这种形态（**不含点号**），
    #    只按「必须含点号」判会把它误拒 —— 这是第 1 批加 host 校验时自引入的回归（N2）。
    if "." not in host and ":" not in host and host != "localhost":
        return done("BAD_URL")
    # 小红书短链（xhslink.com）：先展开成真实笔记链接再抓。
    # ⚠️ 实测**短链失效的形态是「跳首页」**（302/307 → 不含 note id 的地址），不是报错 ——
    #    必须在这里判死，否则会把一个首页当正文抓回来存下去。
    if kind == "xhs" and "xhslink" in norm:
        real = resolve_xhs_short(norm)
        if not real:
            return done("XHS_EXPIRED")
        target = real
        norm = normalize_url(real)
        kind = detect_kind(norm)
        out["url"] = norm
        out["kind"] = kind
    try:
        html, ct, degraded = _read_url(target, timeout)
    except urllib.error.HTTPError as e:
        out["meta"]["http_status"] = e.code
        return done("FETCH_FAILED")
    except urllib.error.URLError:
        return done("FETCH_FAILED")
    except (TimeoutError, OSError):
        return done("FETCH_FAILED")
    # ⚠️ 这里**不要**写 `except Exception` 兜底。
    # 第一版就是宽兜 `except Exception: FETCH_FAILED`，结果 `_read_url` 里
    # 一个参数用错（给 `open()` 传了它不接受的 `context`）抛的 TypeError
    # 被吞成「网络失败」—— 联网用例全挂、离线用例全过，看起来像"网不通"，
    # 实际是代码 bug 被伪装了。非网络类异常必须向上抛，让测试直接看见。
    if not _looks_like_html(ct, html):
        out["meta"]["content_type"] = ct
        return done("NOT_HTML")
    # ⚠️ 网络段之后**只剩一次委派** —— 判定链的唯一实现在 `extract_from_source`。
    #    不要在这里内联任何判定（那会把「浏览器取源」与「服务端抓取」变成两套口径，
    #    而两者对同一个 href 必须给出同一个分类结果）。
    src = extract_from_source(target, html)
    out["title"] = src.get("title") or ""
    out["text"] = src.get("text") or ""
    out["meta"].update(src.get("meta") or {})
    if degraded:
        out["meta"]["ssl_degraded"] = True
    out["ok"] = bool(src.get("ok"))
    out["reason"] = src.get("reason") or ""
    out["chars"] = len(out["text"])
    out["elapsed_ms"] = int((time.time() - t0) * 1000)
    return out


def fetch_or_passthrough(url: str, text: str | None = None,
                         title: str | None = None) -> dict:
    """统一的"正文从哪来"入口：**前端传了正文就用它（粘贴降级），否则抓取**。
    ⚠️ 这是单一实现。剪藏落库（`routers/materials.py`）与临时阅读
    （`routers/ephemeral.py`）必须共用它 —— 两条路径若各自实现粘贴分支，
    "SPA 页面粘贴正文后能不能用"在两处会得出不同结论，用户当成 bug。
    本项目反复踩过"同一口径两处实现"的坑（BGM label、笔记状态口径）。
    传了 `text` 时**不做任何网络请求**，但仍走同一条 `normalize_url` / `detect_kind`，
    保证 `url`（即入库的 `origin_ref`）与抓取路径完全一致 —— 否则粘贴入库的
    同一篇文章会被当成另一份，幂等失效。
    ⚠️ **长度策略不在这里**：本层只管"正文从哪来"。超长是拒绝（剪藏：400）
    还是截断（临时阅读：截到 200KB），由调用方按各自的语义决定。
    """
    body = (text or "").strip()
    if body:
        # 与抓取路径同口径：先抽 URL 再归一化，否则粘贴入库的同一篇文章会被当成另一份
        norm = normalize_url(extract_first_url(url) or url)
        kind = detect_kind(norm)
        # ⚠️⚠️ 小红书短链也必须在这里展开（P1-3）。抓取路径会 `resolve_xhs_short` 展开成
        #    真实笔记链接再归一化；粘贴降级路径若不展开，**同一篇笔记就会有两个 `origin_ref`**
        #    （`https://xhslink.com/a/xxx` vs `https://www.xiaohongshu.com/explore/<id>`）
        #    → 判不出「已入库」→ 重复入库，「最近阅读」里同一篇出现两条。
        #    ⚠️ 与抓取路径的**有意差别**：这里展开失败**不判死** —— 用户已经把正文交给我们了，
        #       不该因为短链解析不出来就拒绝入库；失败时保持短链身份（尽力而为）。
        if kind == "xhs" and "xhslink" in norm:
            real = resolve_xhs_short(norm)
            if real:
                norm = normalize_url(real)
                kind = detect_kind(norm)
        if len(body) < MIN_CLIP_CHARS:
            # P3-3：粘贴降级也拦「只贴了几个字」的误操作。与 clip_save 同一阈值，
            # 在预览阶段就判死（而不是入库时才 400）→ 前端据此提示「正文太短」。
            return {
                "ok": False,
                "kind": kind,
                "url": norm,
                "input_url": (url or "").strip(),
                "title": "",
                "text": "",
                "meta": {},
                "reason": "TOO_SHORT",
                "chars": len(body),
                "elapsed_ms": 0,
            }
        return {
            "ok": True,
            "kind": kind,
            "url": norm,
            "input_url": (url or "").strip(),
            "title": (title or "").strip() or "未命名网页",
            "text": body,
            "meta": {"template": "wx" if kind == "wx" else "article", "passthrough": True},
            "reason": "",
            "chars": len(body),
            "elapsed_ms": 0,
        }
    return fetch_url(url)


# ---------- 「抓取结果 → 前端候选条目」口径（剪藏 / 临时阅读共用） ----------
#
# ⚠️ 必须单一来源。前端据此决定展示「粘贴正文」还是「重取链接」，
# 若剪藏与临时阅读各写一份映射，两处很快会不一致
# —— 本项目反复踩过这个坑（BGM 曲名、笔记状态口径都因此出过 bug）。
#
# ⚠️ 失败时**不要**只回 reason 码：要把原因翻译成人话与建议动作，
# 否则「后端新增了 reason，前端显示空白」。
ACTION_READY = "ready"       # 可直接使用
ACTION_PASTE = "paste"       # 抓取能力不足 → 降级到粘贴正文
ACTION_BLOCKED = "blocked"   # 不可用（链接失效 / 非网页 / 格式错）


def resolve_action(reason: str, ok: bool) -> str:
    """原因码 + 成败 → 前端动作：ready / paste / blocked"""
    if ok:
        return ACTION_READY
    if reason in ("SPA_EMPTY", "TOO_SHORT"):
        # 抓取能力不足 → 降级到粘贴，而不是报错终点（方案 §6.3）
        return ACTION_PASTE
    return ACTION_BLOCKED


def reason_hint(reason: str, kind: str) -> str:
    """原因码 → 用户可读提示 + 建议动作。单一来源，前端不再重复维护映射表。"""
    if not reason:
        return ""
    if reason == "SPA_EMPTY":
        return "这个页面是动态渲染的，无法自动提取正文。请把正文粘贴到下方，其余流程不变。"
    if reason == "TOO_SHORT":
        return "只提取到很短的正文，可能不是文章页。你可以粘贴正文后继续。"
    if reason == "WX_EXPIRED":
        return "公众号链接已失效或文章被删除（临时链接约 6 小时过期）。请在微信里重新打开文章、再分享链接。"
    if reason == "XHS_EXPIRED":
        return ("小红书链接已失效或不完整。小红书笔记必须带 xsec_token 才能打开"
                "（在小红书 App 里「分享 → 复制链接」，把整段一起粘进来即可），"
                "而且这类链接时效很短，请重新复制后马上粘贴。")
    if reason == "NOT_HTML":
        return "这个链接不是网页正文（可能是图片或纯文本文件）。"
    if reason == "BAD_URL":
        return "链接格式不正确，请检查是否粘贴完整。"
    if reason == "FETCH_FAILED":
        return "抓取失败（网络不通、页面不存在或站点拒绝访问）。可稍后重试，或粘贴正文。"
    return "抓取失败。"


def preview_payload(r: dict) -> dict:
    """`fetch_url` / `fetch_or_passthrough` 的结果 → 前端候选列表条目。
    含 SPA 降级所需信息（action/hint）与「已沉淀」所需的归一化 URL。
    """
    kind = r.get("kind") or "url"
    reason = r.get("reason") or ""
    return {
        "ok": bool(r.get("ok")),
        "kind": kind,
        "kind_label": origin_label(kind),
        "need_confirm": need_confirm(kind),
        "url": r.get("url") or "",
        "input_url": r.get("input_url") or "",
        "title": r.get("title") or "",
        "chars": r.get("chars") or 0,
        # 图片张数：正文以图片为主时字数少是正常的，不是抓取失败（见 _extract 说明）
        "images": int((r.get("meta") or {}).get("images") or 0),
        # 图片消息（公众号 item_show_type=8）：字数少是内容形态，不是抓取失败
        "wx_image_post": bool((r.get("meta") or {}).get("wx_image_post")),
        # 小红书视频笔记：抓到的正文只有配文 + 封面，视频内容没抓（诚实透出，别让用户以为抓漏了）
        "xhs_video_note": bool((r.get("meta") or {}).get("xhs_video_note")),
        "meta": r.get("meta") or {},
        "reason": reason,
        # 三态给前端：可以入库 / 需粘贴正文 / 不可用
        "action": resolve_action(reason, bool(r.get("ok"))),
        "hint": reason_hint(reason, kind),
        # 临时链接（src=11）：只提示不拦截 —— 6 小时内仍可用，用户可能就是想马上读完
        "wx_temp_link": bool((r.get("meta") or {}).get("wx_temp_link")),
    }


# ---------- 微信公众号专用抽取（新旧两版通吃） ----------
#
# 为什么必须单开一条路径（实测 `_fetch_probe/probe27~35`）：
# ① **trafilatura 在公众号页上系统性抽不全** —— 它的 precision 模式会删掉公众号
#    特有的 <section> 排版块。同一页面实测：
#      老版 A  4900 字 → 8208 字（+67%）   老版 B  1085 → 1754（+62%）
#      老版 C   789 字 → 1732 字（+120%）
#    用户看到的就是「抓到的正文只有一点点」。
# ② **正文图一律是 `data-src` 懒加载**，trafilatura 只认 `src` → 图片型文章 0 张图
#    （P0-3b 曾记为「救不回」，那只是 trafilatura 的边界，不是我们拿不到）。
# ③ **新版页面（`item_show_type=8` 图片消息 / SSR 壳）DOM 里没有正文**：
#    `rich_media_content` 出现 0 次、`#js_content` 只是 JS 里的字符串。
#    实测生产结果 `ok=False / TOO_SHORT / 129 字` —— 正是用户看到的提示。
#
# 解法的关键：微信前端把正文放在 `window.cgiDataNew` 的 **`content_noencode`**
# 字段里（老版是**转义后的正文 HTML**；新版图片消息是**纯文本**）。实测同一篇文章
# 解转义后的产出与 `#js_content` 子树**完全一致**（8208/1754/1732 字，段数图数全同），
# 但它是**数据源**而非 DOM —— 不依赖页面排版实现，改版也不会失效。
#
# ⚠️ 该字段用单引号 + `\x3c` 转义 + `'583' * 1` 混淆（微信**故意**让 json.loads 失败），
#    必须按 JS 字面量规则还原；**不能用 `codecs.decode(s, "unicode_escape")`** ——
#    它按 latin-1 解释非 ASCII，中文会全毁。
# ⚠️ 三条来源按可靠性降级：`content_noencode` → `#js_content` 子树 → trafilatura。
#    前两条产出实测一致，所以"换路径"不会让同一篇文章在不同情况下得到不同内容。
_WX_CND_RE = re.compile(r"content_noencode\s*:\s*'((?:[^'\\]|\\.)*)'")
_WX_JS_CONTENT_RE = re.compile(r'<div[^>]*id="js_content"[^>]*>', re.I)
_WX_SHOW_TYPE_RE = re.compile(r"item_show_type\s*:\s*'?(\d+)")
# ⚠️ 只匹配到开括号，数组内容用 `_slice_brackets` 配对切 ——
#    非贪婪正则会被元素对象内部的 `[]`（如 theme_color: []）提前截断：
#    实测 8 张正文图只拿到 1 张。
_WX_PIC_LIST_RE = re.compile(r"picture_page_info_list\s*:\s*\[")
_WX_PIC_URL_RE = re.compile(r"cdn_url\s*:\s*'((?:[^'\\]|\\.)*)'")
# `item_show_type=8` = 图片消息：正文以图为主，文字本来就少（不是抓取失败）
_WX_PIC_POST = "8"

# 结构转换用：块级标签（前后补空行）、整棵丢掉的标签
_MD_BLOCK_TAGS = frozenset((
    "p", "section", "div", "li", "blockquote", "tr", "figure", "figcaption",
    "h1", "h2", "h3", "h4", "h5", "h6", "table", "ul", "ol", "pre", "article",
))
_MD_SKIP_TAGS = frozenset((
    "script", "style", "svg", "noscript", "iframe", "canvas", "video", "audio",
))


def _js_unescape(s: str) -> str:
    """JS 单引号字符串字面量 → 真实文本。

    ⚠️ 顺序有讲究：先解 `\\xNN` / `\\uNNNN`，**最后**才处理 `\\\\`。
    反过来会把转义序列里的反斜杠先吃掉 —— `\\x3c` 会变成字面 "x3c"，正文全成乱码。
    实测同一篇文章：顺序正确 → 8208 字；顺序颠倒 → 一堆 "x3c/x22" 噪声。
    """
    s = re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), s)
    s = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), s)
    s = s.replace("\\/", "/").replace("\\'", "'").replace('\\"', '"')
    s = s.replace("\\n", "\n").replace("\\r", "")
    return s.replace("\\\\", "\\")


def _clean_ws(s: str) -> str:
    """清公众号排版噪声：零宽字符、不换行空格、连续空格/制表符。"""
    return re.sub(r"[ \t]+", " ", (s or "").replace("\u200b", "").replace("\xa0", " "))


def _walk_md(el, parts: list) -> None:
    """递归把 lxml 节点摊成 Markdown 风格纯文本（保留段落/换行，`<img>` 取 data-src）。"""
    tag = el.tag if isinstance(el.tag, str) else ""
    tag = tag.lower()
    if tag in _MD_SKIP_TAGS:
        if el.tail:
            parts.append(_clean_ws(el.tail))
        return
    if tag == "br":
        parts.append("\n")
    elif tag == "img":
        # 公众号正文图是懒加载：真实地址在 `data-src`，`src` 往往是占位符或空
        src = (el.get("data-src") or el.get("src") or "").strip()
        if src and not src.startswith("data:"):
            parts.append("\n\n![](%s)\n\n" % src)
    else:
        if tag in _MD_BLOCK_TAGS:
            parts.append("\n\n")
        if el.text:
            parts.append(_clean_ws(el.text))
        for child in el:
            _walk_md(child, parts)
        if tag in _MD_BLOCK_TAGS:
            parts.append("\n\n")
    if el.tail:
        parts.append(_clean_ws(el.tail))


def _html_to_text(frag: str) -> str:
    """HTML 片段 → Markdown 风格纯文本：保留段落与换行，`<img>` 取 `data-src`。

    与 trafilatura 的分工：它做"从整页里猜哪块是正文"；我们这里**已经知道**就是正文，
    所以只做结构转换，不做取舍 —— 这正是公众号不再丢段的原因。
    """
    if not frag:
        return ""
    src = frag if "<html" in frag[:300].lower() else "<html><body>%s</body></html>" % frag
    try:
        doc = _lxml_html.fromstring(src)
    except (ValueError, TypeError):
        try:
            doc = _lxml_html.fromstring(src.encode("utf-8", "ignore"))
        except Exception:
            return ""
    parts: list = []
    _walk_md(doc, parts)
    text = "".join(parts)
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _slice_brackets(text: str, start: int, open_ch: str, close_ch: str) -> str:
    """从 `start`（须指向开括号）起做括号配对，返回**内层**字符串。

    ⚠️ 不能用非贪婪正则：数组元素对象内部还有 `[]`，会提前截断。
    跳过字符串字面量内部（含转义），否则正文里的引号会让配对错位。
    """
    quotes = "\x27\x22"          # 单引号 / 双引号：这样写避免源码里出现嵌套引号
    if start < 0 or start >= len(text) or text[start] != open_ch:
        return ""
    depth, in_s, i, n = 0, None, start, len(text)
    while i < n:
        c = text[i]
        if in_s:
            if c == "\\":
                i += 2
                continue
            if c == in_s:
                in_s = None
        elif c in quotes:
            in_s = c
        elif c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return text[start + 1:i]
        i += 1
    return ""


def _wx_js_subtree(html: str) -> str:
    """取老版页 `#js_content` 容器的内层 HTML。
    ⚠️ 不能用非贪婪正则：正文里嵌套大量 `<div>`/`<section>`，正则会提前截断。
    改用 `<div>` 配对计数。
    """
    m = _WX_JS_CONTENT_RE.search(html)
    if not m:
        return ""
    i = m.end()
    depth = 1
    for t in re.finditer(r"<(/?)div\b[^>]*>", html[i:], re.I):
        depth += -1 if t.group(1) else 1
        if depth == 0:
            return html[i:i + t.start()]
    return html[i:]


def _wx_raw_content(html: str) -> tuple:
    """取出公众号正文。返回 (正文, 正文图 URL 列表, 是否图片消息)。

    正文来源按可靠性降级：`content_noencode` → `#js_content` 子树 → 空串
    （空串时由 `_wx_extract` 交 trafilatura 兜底）。
    """
    m = _WX_CND_RE.search(html)
    content = _js_unescape(m.group(1)) if m else ""
    if not content:
        content = _wx_js_subtree(html)
    show = _WX_SHOW_TYPE_RE.search(html)
    is_pic = bool(show and show.group(1) == _WX_PIC_POST)
    pics: list = []
    if is_pic:
        lm = _WX_PIC_LIST_RE.search(html)
        if lm:
            inner = _slice_brackets(html, lm.end() - 1, "[", "]")
            seen = set()
            for u in _WX_PIC_URL_RE.findall(inner):
                u = _js_unescape(u).replace("&amp;", "&").strip()
                if u and u not in seen:
                    seen.add(u)
                    pics.append(u)
    return content, pics, is_pic


def _extract_meta(html: str, url: str) -> tuple:
    """标题 + 元信息：trafilatura metadata，取不到再从 `<title>` 兜底。

    ⚠️ 两条抽取路径（通用 / 公众号）**共用本函数** —— 本模块反复踩过
    「同一口径两处实现」（BGM 曲名、笔记状态口径都因此出过 bug）。
    """
    title, meta = "", {}
    try:
        m = trafilatura.extract_metadata(html, default_url=url)
        if m is not None:
            title = (getattr(m, "title", "") or "").strip()
            meta = {
                "author": (getattr(m, "author", "") or "").strip(),
                "date": (getattr(m, "date", "") or "").strip(),
                "sitename": (getattr(m, "sitename", "") or "").strip(),
            }
            meta = {k: v for k, v in meta.items() if v}
    except Exception:
        pass
    # 标题兜底：从 HTML 的 <title> 取（trafilatura 对错误页常抽不到 title）
    if not title:
        title = _title_from_html(html)
    return title, meta


def _wx_extract(html: str, url: str) -> tuple:
    """微信公众号正文抽取。返回 (title, text, meta)，与 `_extract` 同构。

    meta 里额外的键：
        images         正文图张数（含图片消息的图列表）
        wx_image_post  是否「图片消息」（正文以图为主，文字天然少 → 不按字数判失败）
    """
    text, pics, is_pic = "", [], False
    try:
        content, pics, is_pic = _wx_raw_content(html)
        text = _html_to_text(content) if "<" in content else content.strip()
    except Exception:
        text = ""
    if pics:
        # 图片消息的图**就是正文**。用单 `\n` 连接成一块 —— 若用空行分段，
        # `_collect_images` 会把 N 张图逐个并进上一段文字，读起来反而乱。
        block = "\n".join("![](%s)" % u for u in pics)
        text = ("%s\n\n%s" % (text, block)).strip() if text else block
    if not text:
        # 兜底：整页交给 trafilatura —— 页面结构再变也不至于完全失效
        try:
            text = trafilatura.extract(
                html, url=url, include_comments=False, include_tables=True,
                output_format="markdown", include_images=True) or ""
        except Exception:
            text = ""
    title, meta = _extract_meta(html, url)
    text, img_count = _collect_images(text.strip())
    if img_count:
        meta["images"] = img_count
    if is_pic:
        meta["wx_image_post"] = True
    return title, text.strip(), meta


# ---------- 小红书专用抽取（P0-3h） ----------
#
# 为什么必须单开一条路径（实测 `_fetch_probe/_probe_xhs_*.py`）：
# ① **笔记页是 SSR**（`window.__SSR__=true`），正文与配图**都在内联的
#    `window.__INITIAL_STATE__` 里** —— DOM 抽取（trafilatura）拿不到笔记内容。
# ② **必须带 `xsec_token` 才能打开**。实测同一 note id 的对照：
#      带 token → <title>笔记写不出来？4个万能公式帮你搞定 - 小红书</title>，
#                 noteDetailMap 有该笔记、desc 是完整正文、imageList 含配图；
#      裸 id    → <title>小红书 - 你访问的页面不见了</title>，noteDetailMap 为空 {}。
#    ⚠️ **两种情况都是 HTTP 200** → 失败判定必须查内容特征，绝不能只看状态码。
# ③ **配图是短时强签名直链**：形如
#      http://sns-webpic-qc.xhscdn.com/{时间戳}/{hash}/spectrum/{fileId}!{变换}
#    实测篡改时间戳（±1h/±1d/置 0）、去 hash、换 `!jpg_3`、去掉 `!` 后缀**全部 403**，
#    只有 http→https 仍 200（协议不参与签名）。→ **不能外链引用（会裂图），必须本地化**。
#    ⚠️ 这与公众号 `mmbiz.qpic.cn`（长期直链、可外链）**性质不同**，别照抄 P0-3b。
#
# 取值路径（实测）：
#   window.__INITIAL_STATE__.note.noteDetailMap[{noteId}].note
#       .title / .desc / .type("normal" 图文 | "video" 视频) / .tagList / .imageList / .user
#   imageList[i] = {urlDefault, urlPre, url, infoList[{imageScene, url}], fileId, width, height}
#   ⚠️ **顶层 `.url` 实测是空串** —— 主取值必须是 `urlDefault`（退回 `urlPre`）。
XHS_STATE_KEY = "window.__INITIAL_STATE__="
_XHS_NOTE_ID_RE = re.compile(r"/explore/([0-9a-f]{24})", re.I)
_XHS_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
_XHS_GONE_MARKER = "你访问的页面不见了"
# App「分享 → 复制链接」给的是**整段文案**（标题 + emoji + 短链 + 说明），需要从中抽 URL。
# 对纯 URL 输入本正则匹配整串 → 恒等，现有调用方行为不变。
_URL_IN_TEXT_RE = re.compile(r"https?://[^\s\u4e00-\u9fff\u3000-\u303f\uff00-\uffef\"'<>]+")
# 抽出后要剥掉的**尾部句末标点**（中文标点已被上面的正则排除，ASCII 的会跟着进来）
_URL_TAIL_PUNCT = ".,;:!?"
_XHS_IMG_MAX_BYTES = 12 * 1024 * 1024
_XHS_EXT_BY_TYPE = (("webp", ".webp"), ("jpeg", ".jpg"), ("jpg", ".jpg"),
                    ("png", ".png"), ("gif", ".gif"), ("avif", ".avif"))


def xhs_note_id(url: str) -> str:
    """从 URL 里取 24 位 note id（取不到返回空串）。"""
    m = _XHS_NOTE_ID_RE.search(url or "")
    return m.group(1) if m else ""


def strip_url_tail(u: str) -> str:
    """剥掉 URL 尾部的标点（N1）。

    ⚠️ 两类**必须分开处理**，否则会剥坏合法 URL：
      · 句末标点 `.,;:!?` —— 无条件剥（URL 以它们结尾几乎总是句读）；
      · 闭括号 `)]}` —— **只在「闭比开多」时才剥**。反例（真实存在）：
        维基百科 `.../wiki/Foo_(bar)` 以 `)` 结尾且**成对**，剥掉它等于打不开这个页面。
    """
    u = u or ""
    while u and u[-1] in _URL_TAIL_PUNCT:
        u = u[:-1]
    for close, open_ in ((")", "("), ("]", "["), ("}", "{")):
        while u.endswith(close) and u.count(close) > u.count(open_):
            u = u[:-1]
    return u


def extract_first_url(text: str) -> str:
    """从一段文本里抽出第一个 http(s) URL（App 分享文案的降噪入口）。
    对纯 URL 输入**恒等** —— 现有调用方行为不变。

    ⚠️ 抽出后必须剥**尾部标点**（N1）：正则排除了中文标点，但 ASCII 的没有 ——
    `看看这篇(https://a.com/x)很有意思` 会抽出 `https://a.com/x)`，带着右括号去抓取
    必然失败，而给用户的提示是「网络不通」，**用户永远猜不到问题出在一个括号上**。
    实测 8 个用例里 4 个命中（`()`、`[]`、句末 `.` / `,`）。
    """
    m = _URL_IN_TEXT_RE.search(text or "")
    return strip_url_tail(m.group(0)) if m else ""


def _xhs_page_title(html: str) -> str:
    """页面 <title>（只在前 20KB 里找，避免正文里出现的 "title" 字样干扰）。"""
    m = _XHS_TITLE_RE.search((html or "")[:20000])
    return " ".join(m.group(1).split()) if m else ""


def resolve_xhs_short(url: str, timeout: int = 15) -> str:
    """xhslink.com 短链 → 真实笔记链接（含 xsec_token）。取不到返回空串。

    ⚠️ 实测：**失效短链的形态是「跳首页」而不是报错** ——
    不存在的短码返回 `302/307` + `Location: https://www.xiaohongshu.com`（**不含 note id**）。
    所以判定必须校验 Location 里有没有 `/explore/{24 位 id}`，不能只看"有跳转"。
    """
    if not url:
        return ""
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA, "Accept": "text/html,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    try:
        resp = _opener_noredirect.open(req, timeout=timeout)
        with resp as r:
            loc = (r.headers.get("Location") or "")
    except urllib.error.HTTPError as e:
        # 不跟随重定向时 3xx 会走这里（`_NoRedirect.redirect_request` 返回 None）
        loc = (e.headers.get("Location") if e.headers else "") or ""
    except (urllib.error.URLError, TimeoutError, OSError):
        return ""
    if not loc:
        return ""
    return loc if _XHS_NOTE_ID_RE.search(loc) else ""


def _loads_js_object(src: str):
    """JS 对象字面量 → dict。失败返回 None。

    ⚠️ `__INITIAL_STATE__` **不是合法 JSON**：含 `undefined` 与尾部逗号，直接 json.loads
    必失败。清理必须**按字符串边界**做 —— 全文正则替换会被正文里的 `,}` 误伤。
    """
    out, i, n, in_s = [], 0, len(src), None
    while i < n:
        c = src[i]
        if in_s:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(src[i + 1])
                i += 2
                continue
            if c == in_s:
                in_s = None
            i += 1
            continue
        if c == '"' or c == "'":
            in_s = c
            out.append(c)
            i += 1
            continue
        if src.startswith("undefined", i):
            out.append("null")
            i += 9
            continue
        if c == ",":
            j = i + 1
            while j < n and src[j] in " \t\r\n":
                j += 1
            if j < n and src[j] in "}]":
                i += 1            # 尾部逗号：丢掉
                continue
        out.append(c)
        i += 1
    try:
        return json.loads("".join(out))
    except (ValueError, TypeError):
        return None


def _parse_xhs_state(html: str):
    """取 `window.__INITIAL_STATE__` → dict；失败返回 None。"""
    i = (html or "").find(XHS_STATE_KEY)
    if i < 0:
        return None
    raw = _slice_brackets(html, i + len(XHS_STATE_KEY), "{", "}")
    if not raw:
        return None
    return _loads_js_object("{" + raw + "}")


def _xhs_note_from_state(data, note_id: str) -> dict:
    """从 state 里取出笔记对象（取不到返回 {}）。"""
    if not isinstance(data, dict):
        return {}
    ndm = (data.get("note") or {}).get("noteDetailMap") or {}
    if not isinstance(ndm, dict):
        return {}
    if note_id and isinstance(ndm.get(note_id), dict):
        return ndm[note_id].get("note") or {}
    # note id 对不上时：只有唯一候选才敢用（多候选无法判断是哪一篇，宁可不猜）
    if len(ndm) == 1:
        only = list(ndm.values())[0]
        if isinstance(only, dict):
            return only.get("note") or {}
    return {}


def _xhs_image_urls(note: dict) -> list:
    """笔记配图 → 可下载的 URL 列表（统一为 https、去重）。

    ⚠️ 取 `urlDefault`（清晰版，实测约 100KB/张）；`urlPre` 是压缩预览（约 22KB）。
    ⚠️ 顶层 `.url` 字段实测为**空串**，不能当主取值。
    ⚠️ http → https：实测两者都 200，但 http 在 https 页面会被浏览器按混合内容拦截。
    """
    urls, seen = [], set()
    for im in (note.get("imageList") or []):
        if not isinstance(im, dict):
            continue
        u = (im.get("urlDefault") or im.get("url") or im.get("urlPre") or "").strip()
        if not u:
            continue
        if u.startswith("http://"):
            u = "https://" + u[7:]
        if u not in seen:
            seen.add(u)
            urls.append(u)
    return urls


def _xhs_extract(html: str, url: str) -> tuple:
    """小红书笔记抽取。返回 (title, text, meta)，与 `_extract` / `_wx_extract` 同构。

    meta 额外键：
        images         配图张数（前端显示「N 张图」）
        image_urls     配图原始签名 URL —— **仅供落库时本地化**，会过期，别当稳定地址存
        xhs_note_id    笔记 id
        xhs_note_type  "normal"（图文）/ "video"（视频）
        xhs_video_note 是否视频笔记（True 时正文只有配文 + 封面，需明确提示用户）
    """
    meta: dict = {}
    data = _parse_xhs_state(html)
    nid = xhs_note_id(url)
    note = _xhs_note_from_state(data, nid)
    if not note:
        return "", "", meta
    title = (note.get("title") or "").strip()
    desc = (note.get("desc") or "").strip()
    ntype = (note.get("type") or "").strip()
    urls = _xhs_image_urls(note)
    text = desc
    if urls:
        # 配图**就是正文主体**（图文笔记的文字常只有一句，实测 110 字 + 3 图）。
        # 用单 `\n` 连接成一块，避免 `_collect_images` 把每张图逐个并进上一段
        # （与公众号图片消息同理），也避免切出「只含图片 URL」的块。
        block = "\n".join("![](%s)" % u for u in urls)
        text = ("%s\n\n%s" % (desc, block)).strip() if desc else block
    if urls:
        meta["images"] = len(urls)
        meta["image_urls"] = urls
    if nid:
        meta["xhs_note_id"] = nid
    if ntype:
        meta["xhs_note_type"] = ntype
        if ntype == "video":
            meta["xhs_video_note"] = True
    user = note.get("user") or {}
    if isinstance(user, dict):
        nick = (user.get("nickname") or user.get("nickName") or "").strip()
        if nick:
            meta["author"] = nick
    tags = [t.get("name") for t in (note.get("tagList") or [])
            if isinstance(t, dict) and t.get("name")]
    if tags:
        meta["xhs_tags"] = [str(t) for t in tags[:20]]
    return title, text, meta


def _download_image(url: str, timeout: int = 20) -> tuple:
    """下载一张图 → (bytes, 扩展名)。非图片响应抛 ValueError（由调用方跳过）。"""
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA,
        "Accept": "image/avif,image/webp,image/png,image/*,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    resp = _opener_no_proxy.open(req, timeout=timeout)
    with resp as r:
        ct = (r.headers.get("Content-Type") or "").lower()
        raw = r.read(_XHS_IMG_MAX_BYTES + 1)
    if "image" not in ct:
        raise ValueError("非图片响应：%s" % ct)
    if len(raw) > _XHS_IMG_MAX_BYTES:
        raise ValueError("图片超过上限")
    ext = next((e for k, e in _XHS_EXT_BY_TYPE if k in ct), ".img")
    return raw, ext


# 正文里的图片：`![alt](https://…)`。URL 里出现 `)` 极罕见，不做括号配对。
_MD_IMAGE_URL_RE = re.compile(r"!\[[^\]]*\]\((https?://[^)\s]+)\)")


def image_urls_in_markdown(text: str) -> list:
    """从 Markdown 正文里抽出图片 URL（按出现顺序、去重）。

    ⚠️ 存在的理由（P1-2）：落库时**不能只依赖 `meta.image_urls`** ——
    「仅本次阅读 → 加入知识库」走的是粘贴降级分支，那条路的 meta 只有
    `{template, passthrough}`，没有 `image_urls`；旧写法据此**跳过**图片本地化，
    于是落库正文里留着**短时签名外链**（小红书实测改时间戳/hash/后缀任一处即 403）
    → 过几天整篇破图。而正文里本来就是这个形态：`![](https://sns-webpic-qc.xhscdn.com/…)`。
    """
    out, seen = [], set()
    for u in _MD_IMAGE_URL_RE.findall(text or ""):
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def assets_subdir_for(norm_url: str, meta: dict | None = None) -> str:
    """这篇材料该用哪个「配图子目录」。**落库与删除共用这一处口径。**

    优先 note id（可读、可调试）；抽不到时用归一化 URL 的短哈希兜底。

    ⚠️ **绝不能退化成固定串**：旧写法是 `subdir = meta.get("xhs_note_id") or kind`，
    在 note id 抽不到时退化成 `"xhs"` → 多篇笔记落进同一个 `data/assets/xhs/`，
    而文件名是 `img_01.webp` 这种**序号名** → **互相覆盖**：A 的正文会指向 B 的图。
    """
    nid = str(((meta or {}).get("xhs_note_id")) or "").strip() or xhs_note_id(norm_url or "")
    if nid:
        return nid
    return "u-" + hashlib.md5((norm_url or "").encode("utf-8")).hexdigest()[:12]


def assets_dir(subdir: str) -> Path:
    """本地化配图的落盘目录：`data/assets/<safe_stem(subdir)>`。

    ⚠️ **全项目只此一处**定义这个口径：落库（`localize_images`）与删除
    （`routers/materials.py::_cleanup_material_assets`）必须指向同一个目录 ——
    两边各拼一遍路径，迟早漂移，然后就变成"删了材料但图片删不掉"的孤儿。
    ⚠️ `safe_stem` 是必需的（不是装饰）：subdir 是**远端可控**的 note id，
    直接拼路径等于把目录名交给对方（P0-3f 已实测 `<title>` 含 `../` 能穿越）。
    """
    return Path(settings.data_dir) / "assets" / safe_stem(subdir or "xhs", fallback="xhs")


def localize_images(text: str, urls: list, subdir: str, timeout: int = 20) -> tuple:
    """把正文里的签名外链配图下载到 `data/assets/<subdir>/`，并替换为站内地址。

    返回 `(新正文, 统计)`；统计键：images_saved / images_failed /
    image_paths / image_failed_urls。

    ⚠️ **只在落库时调**（`materials.clip_save`），不在抓取时调：「仅本次阅读」的契约是
    **不落盘**（方案 §2.3）。临时阅读直接用原始签名 URL 外链即可 —— 当次阅读必然还在有效期内。
    ⚠️ **逐张容错**：任何一张失败只跳过它、不中断整篇（正文里那条 `![]()` 保留原 URL，
    短时间内仍能显示），失败张数进 meta 让用户知道，而不是静默变少。
    """
    stat = {"images_saved": 0, "images_failed": 0,
            "image_paths": [], "image_failed_urls": []}
    text = text or ""
    urls = [u for u in (urls or []) if u]
    if not urls:
        return text, stat
    # 目录口径来自 assets_dir()（删除侧调的就是它）——本函数不再自己拼路径
    dest = assets_dir(subdir)
    sub = dest.name                      # 同时用于站内地址 /api/assets/<sub>/<name>
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError:
        stat["images_failed"] = len(urls)
        stat["image_failed_urls"] = list(urls)
        return text, stat
    for idx, u in enumerate(urls, 1):
        try:
            raw, ext = _download_image(u, timeout)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError,
                OSError, ValueError):
            stat["images_failed"] += 1
            stat["image_failed_urls"].append(u)
            continue
        name = "img_%02d%s" % (idx, ext)
        rel = "/api/assets/%s/%s" % (sub, name)
        try:
            (dest / name).write_bytes(raw)
        except OSError:
            stat["images_failed"] += 1
            stat["image_failed_urls"].append(u)
            continue
        text = text.replace(u, rel)
        stat["images_saved"] += 1
        stat["image_paths"].append(rel)
    return text, stat


# trafilatura 的 markdown 把图片输出成独立段落（前后都是空行）。
_MD_IMAGE_ONLY = re.compile(r"^\s*!\[[^\]]*\]\([^)]*\)\s*$")
_MD_IMAGE_ANY = re.compile(r"!\[[^\]]*\]\([^)]*\)")


def _collect_images(text: str) -> tuple[str, int]:
    """把「独立成段」的 markdown 图片并回上一段，返回 (新文本, 图片总数)。

    ⚠️ 为什么需要它（实测 `_fetch_probe/probe26_image_chunks.py`）：
    trafilatura 把图片输出为**独立段落**，而下游 `parser.split_markdown` 按 `"\\n\\n"`
    切块 → 一篇带 54 张图的文章会多出 54 个"只含图片 URL"的块。对阅读无害，
    但这些块的向量无意义却可能被检索命中，也会白白挤占 AI 上下文。
    合并后用单个 `\\n` 连接：图片留在它所属段落的块里（语义正确），
    markdown 渲染时仍照常显示为图片。

    不合并的两种情形（刻意）：
      · 该图前面没有段落（首图）→ 保持独立
      · 前一段是标题（`#` 开头）→ 保持独立，避免把标题块撑成大块

    负向保证（实测）：纯文字文章的合并数为 0、块数与之前**完全一致**
    —— 腾讯新闻 / 公众号两篇对照的合并数均为 0。
    """
    blocks = (text or "").split("\n\n")
    out, merged = [], 0
    for b in blocks:
        s = b.strip()
        if (s and out and _MD_IMAGE_ONLY.match(s)
                and not out[-1].lstrip().startswith("#")):
            out[-1] = out[-1].rstrip() + "\n" + s
            merged += 1
        else:
            out.append(b)
    return "\n\n".join(out), len(_MD_IMAGE_ANY.findall(text or ""))


def _extract(html: str, url: str) -> tuple[str, str, dict]:
    """trafilatura 抽正文 + 元信息。
    ⚠️ 不要照抄网上示例：`extract(..., favorite_languages=[...])` 的参数在 2.x **已被移除**，
    直接 TypeError（本项目实测撞过）。2.2.0 的真实签名见
    `_fetch_probe/probe7_trafilatura_api.py` 的输出。
    ⚠️ `extract()` 抽不到正文时返回 **`None`**（不是空串）；`extract_metadata` 返回
    `Document` 对象（`.title/.author/.date/.sitename`，另有 `.as_dict()`）。
    ⚠️⚠️ **`output_format="markdown"` 不是美化选项，是正确性要求**（实测见
    `_fetch_probe/probe9_output_format.py`）：
    | output_format | `split_markdown` 切块数 | 带 section_path |
    |---|---|---|
    | `"txt"`（原本用的默认值） | **1** | 0 |
    | `"markdown"` | **136** | 134 |
    `txt` 输出的段落之间**只有单个 `\\n`、没有空行**，而 `split_markdown` 的分段依据是
    `"\\n\\n"` → 整篇 4842 字退化成 1 个 chunk。后果不是"排版难看"而是：
    AI 摘要/解读只能看到一大坨、检索粒度崩塌、前端目录为空。
    这与 P0-2 的 CRLF 是**同一类缺陷的另一个版本**（换行形式不对 → 分块失效）：
    上次是"分隔符被吃"，这次是"根本没有分隔符"。
    ⚠️⚠️ **`include_images=True` 同样是正确性要求**（实测
    `_fetch_probe/probe23_images_missing.py`、`probe24_confirm_and_lazyimg.py`）：
    默认 False 会把正文里的图片**全部丢弃**，对"正文以图片为主"的文章
    （人人都是产品经理这类截图/表格/流程图型文章、周刊类的配图文章）
    后果就是用户只看到几句说明文字 —— 表现为「抓到的正文只有一点点」。
    实测：woshipm 三篇分别只剩 970 / 1276 / 3308 字且 0 张图，开图后
    1713 / 1975 / 3896 字 + 8~10 张内容图（那些图**就是**文章的实质内容）；
    阮一峰周刊更是丢了 54 张图。产品经理的日常阅读源大量属于此类。
    配套 `_collect_images` 把独立成段的图并回上一段，避免切出"只含 URL 的块"。
    已知边界：`data-src` 懒加载图（腾讯新闻等）**救不回** ——
    实测把 `data-src` 改写成 `src` 后 trafilatura 仍输出 0 张，属上游能力边界，
    不要为此写预处理。**公众号已不走本函数**（见 `_wx_extract`：那边直接读 `data-src`）。
    """
    try:
        text = trafilatura.extract(
            html, url=url, include_comments=False, include_tables=True,
            # 必须显式指定：默认值 "txt" 会让下游切块退化（见上方说明）
            output_format="markdown",
            # ⚠️ 默认 False 会把图片型文章的图全部丢掉（见上方说明）
            include_images=True,
        ) or ""
    except Exception:
        text = ""
    title, meta = _extract_meta(html, url)
    # 图片：独立成段的图并回上一段（防下游切出"只含图片 URL 的块"），并记下张数。
    # 张数会进 meta → preview_payload → 前端显示"N 张图"，
    # 让"正文以图片为主时字数本来就少"变成用户看得懂的信息，而不是像抓取失败。
    text, img_count = _collect_images(text.strip())
    if img_count:
        meta["images"] = img_count
    return title, text.strip(), meta


def _title_from_html(html: str) -> str:
    """极简 <title> 提取（只在 trafilatura 抽不到时兜底，不追求严谨）"""
    low = html.lower()
    i = low.find("<title")
    if i < 0:
        return ""
    j = low.find(">", i)
    k = low.find("</title>", j)
    if j < 0 or k < 0:
        return ""
    t = html[j + 1:k]
    # 公众号标题常带前后空白与换行
    return " ".join(t.split())[:200]


# ---------- 分块（单一来源，勿在本模块另写一份） ----------
# ⚠️ 方案 §4.4 明确要求「分块逻辑只允许一处实现」。本模块**只做转出**，
# 真正的实现在 services/parser.py 的 split_markdown（落库路径 parse_md 也用它）。
# 若在本模块另写一份，同一篇文章在「仅本次阅读」与「入库」下块数会不同 → 用户当成 bug。
from ..core.config import settings     # noqa: E402
from ..core.filename import safe_stem  # noqa: E402

from .parser import split_markdown  # noqa: E402,F401
