"""EPUB（zip + XHTML）解析：章节正文提取 / 原文资源读取

两个使用方：
1. parser.parse_epub —— 抽章节正文写入 MaterialChunk（文本视图 + RAG 检索）
2. materials 路由的 epub-res 接口 —— 原文视图按 zip 内路径原样回吐资源（图片/CSS/字体）

设计取舍：
- 容器 container.xml、包文档 OPF、EPUB2 目录 NCX 都是严格 XML → 用 stdlib ElementTree，
  零依赖、解析快；
- 章节正文 XHTML 在现实文件里普遍不规范（未定义实体、标签未闭合、缺 DTD），
  严格 XML 解析经常直接报错 → 用 lxml.html 容错解析。lxml 已随包（依赖链带入，
  PyInstaller 产物 _internal/lxml 已存在），不新增分发体积。
- 章节正文按 Markdown 语义产出（标题/列表/表格/引用），与 docx 解析保持同一形态，
  前端文本视图可直接复用现成的渲染路径。
"""
import posixpath
import re
import zipfile
import xml.etree.ElementTree as ET
from urllib.parse import unquote

_CONTAINER = "META-INF/container.xml"
_ENCRYPTION = "META-INF/encryption.xml"
_NCX_MT = "application/x-dtbncx+xml"

# zip 内扩展名 → HTTP Content-Type（原文视图按原 MIME 回吐，浏览器才能正确内联渲染）
#
# 章节 XHTML 刻意按 text/html 回吐，而不是规范上的 application/xhtml+xml：
# 后者会让浏览器启用**严格 XML 解析器**，而现实 EPUB 大量使用 HTML 命名实体
# （&nbsp; &mdash; &ldquo; …）——这些实体由 XHTML DTD 定义，而 DTD 是外部资源、
# 浏览器一律不加载，于是 `&nbsp;` 直接触发 XML 解析错误，读者看到的是一整屏
# 红色 parsererror。text/html 走容错解析器，既能认全部 HTML 实体，
# 也能容忍标签未闭合等常见不规范写法——这正是「原文视图失败也要优雅降级」的落点。
_MEDIA_BY_EXT = {
    ".xhtml": "text/html", ".html": "text/html", ".htm": "text/html",
    ".css": "text/css",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
    ".ttf": "font/ttf", ".otf": "font/otf",
    ".woff": "font/woff", ".woff2": "font/woff2",
    ".mp3": "audio/mpeg", ".mp4": "video/mp4",
    ".xml": "application/xml", ".opf": "application/oebps-package+xml",
    ".ncx": _NCX_MT, ".txt": "text/plain", ".json": "application/json",
    # .js 一律按纯文本回吐：原文视图会剥掉 script，没有执行场景，
    # 降级成 text/plain 可彻底断掉「被当脚本加载」的可能
    ".js": "text/plain",
}

_HTML_MEDIA = ("text/html", "application/xhtml+xml")   # 兼容历史值，判断仍按内容语义


def media_type(name: str) -> str:
    """zip 内路径 → Content-Type（未知扩展名兜底 octet-stream）"""
    return _MEDIA_BY_EXT.get(posixpath.splitext((name or "").lower())[1],
                             "application/octet-stream")


def is_html(name: str) -> bool:
    return media_type(name) in _HTML_MEDIA


# ---------- XML / 路径工具 ----------

def _local(tag) -> str:
    """取标签本地名（去命名空间、转小写）。非字符串标签（注释/PI）返回空串。"""
    if not isinstance(tag, str):
        return ""
    return (tag.rsplit("}", 1)[-1] if "}" in tag else tag).strip().lower()


def _txt(el) -> str:
    """元素全部后代文本（含命名空间无关的 itertext）"""
    return "".join(el.itertext())


def _norm_zip_path(base: str, href: str) -> str:
    """OPF/NCX/nav 里的 href（可含 fragment、百分号编码、相对路径）→ zip 内路径

    例：base='OEBPS/text'、href='../Images/a%20b.jpg#x' → 'OEBPS/Images/a b.jpg'
    """
    href = (href or "").split("#", 1)[0].strip()
    if not href:
        return ""
    href = unquote(href)
    joined = posixpath.join(base, href) if base else href
    # normpath 会吃掉 '..'，再 lstrip('/') 兼容 zip 内绝对路径写法
    return posixpath.normpath(joined).lstrip("/")


_SPACE_RE = re.compile(r"[ \t\r\n\u00a0\u3000]+")


def _flat(s: str) -> str:
    """行内文本归一：空白折叠成单空格（标题/列表项/表格单元格用，不要换行）"""
    return _SPACE_RE.sub(" ", s or "").strip()


def _para(s: str) -> str:
    """段落文本归一：折叠横向空白，保留换行（<br> 的分行在这里活下来）"""
    s = re.sub(r"[ \t\u00a0\u3000]+", " ", s or "")
    s = re.sub(r"[ \t]*\n[ \t]*", "\n", s)
    return s.strip()


_XML_DECL_RE = re.compile(r"^\s*<\?xml[^>]*\?>", re.I)
_XML_ENC_RE = re.compile(r"<\?xml[^>]*encoding=[\"']([\w.-]+)[\"']", re.I)
_META_ENC_RE = re.compile(r"<meta[^>]+charset=[\"']?\s*([\w.-]+)", re.I)


def _detect_encoding(data: bytes) -> str:
    """从 XML 声明 / meta charset 取声明编码（取不到返回空串，交给下面的兜底顺序）"""
    m = _XML_ENC_RE.search(data[:2048].decode("ascii", "ignore"))
    if m:
        return m.group(1)
    m = _META_ENC_RE.search(data[:4096].decode("ascii", "ignore"))
    return m.group(1) if m else ""


def decode_text(data: bytes) -> str:
    """XHTML / HTML 字节 → 文本

    EPUB 规范要求正文为 UTF-8/UTF-16，但现实文件常既不写 <meta charset> 也不写
    XML 声明；此时交给 libxml2 或浏览器自行猜测会按 Latin-1 / windows-1252 处理，
    中文整篇乱码。所以统一自己解码：声明编码 → UTF-8（含 BOM）→ GB18030 兜底 → latin-1 保底。
    """
    declared = _detect_encoding(data)
    for enc in ([declared] if declared else []) + ["utf-8-sig", "gb18030", "latin-1"]:
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", "ignore")   # 理论上到不了：latin-1 能解码任意字节


def _load_html(data: bytes):
    """XHTML → lxml 文档树（先自行解码，再以字符串交给 lxml）

    现实文件常见未定义实体（&nbsp; 等）/未闭合标签，lxml.html 本身容错，
    失败再降级重试一次。
    """
    from lxml import html as lhtml
    text = decode_text(data)
    # 已解码为字符串，保留 XML 声明会触发「Unicode strings with encoding declaration
    # are not supported」，且编码信息已在上一步消费掉，直接剥掉
    text = _XML_DECL_RE.sub("", text, count=1)
    try:
        return lhtml.fromstring(text)
    except Exception:
        try:
            return lhtml.fromstring(text, parser=lhtml.HTMLParser(encoding="utf-8"))
        except Exception:
            return None


# ---------- 正文抽取（XHTML → Markdown 风格的块行） ----------

_SKIP_TAGS = {"script", "style", "head", "title", "meta", "link", "base", "noscript"}

# 标题级别固定映射；其余块级标签只用于「是否另起一块」的判断
_HEADING_TAGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}

_BLOCK_TAGS = {
    "p", "div", "section", "article", "aside", "header", "footer", "main", "nav",
    "blockquote", "pre", "figure", "figcaption", "dl", "dt", "dd", "center",
    "ul", "ol", "li", "table", "caption", "tr", "td", "th",
    "thead", "tbody", "tfoot", "hr", "ruby", "rt", "rp",
} | set(_HEADING_TAGS)


def _inline_child_text(c) -> str:
    """行内子元素 → 文本。<br> 转换行；图片转 alt 占位（正文视图不展示图片本身）"""
    name = _local(c.tag)
    if name == "br":
        return "\n"
    if name == "img":
        alt = (c.get("alt") or c.get("title") or "").strip()
        return f"[图：{alt}]" if alt else ""
    return _txt(c)


def _own_text(el) -> str:
    """元素自身的行内文本：跳过块级子元素（它们各自成块），保留其 tail 文本"""
    parts = [el.text or ""]
    for c in el:
        if _local(c.tag) not in _BLOCK_TAGS:
            parts.append(_inline_child_text(c))
        if c.tail:
            parts.append(c.tail)
    return "".join(parts)


def _table_md(tbl) -> list[str]:
    """XHTML 表格 → Markdown 表格（复用 parser 的拆分/表头重复逻辑，避免两套实现）"""
    from .parser import _md_cell, _rows_to_md_tables
    rows = []
    for tr in tbl.iter():
        if _local(tr.tag) not in ("tr",):
            continue
        cells = [_md_cell(_flat(_txt(c))) for c in tr
                 if _local(c.tag) in ("td", "th")]
        if cells:
            rows.append(cells)
    return _rows_to_md_tables(rows)


def _emit_list(el, out: list, indent: int):
    """有序/无序列表 → Markdown 列表行（子列表多两级缩进）"""
    ordered = _local(el.tag) == "ol"
    n = 0
    for li in el:
        if _local(li.tag) != "li":
            continue
        n += 1
        text = _flat(_own_text(li))
        if text:
            marker = f"{n}." if ordered else "-"
            out.append("  " * indent + f"{marker} {text}")
        for sub in li:
            if _local(sub.tag) in ("ul", "ol"):
                _emit_list(sub, out, indent + 1)


def _emit_block(el, out: list, indent: int):
    """块级元素 → 若干 Markdown 块行"""
    name = _local(el.tag)
    if name in _HEADING_TAGS:
        text = _flat(_txt(el))
        if text:
            out.append("#" * _HEADING_TAGS[name] + " " + text)
        return
    if name in ("ul", "ol"):
        _emit_list(el, out, indent)
        return
    if name == "table":
        out.extend(_table_md(el))
        return
    if name in ("hr", "caption", "rt", "rp"):
        return
    if name == "dl":
        for c in el:
            if _local(c.tag) == "dt":
                t = _flat(_txt(c))
                if t:
                    out.append(f"**{t}**")
            elif _local(c.tag) == "dd":
                t = _flat(_txt(c))
                if t:
                    out.append(t)
            elif _local(c.tag) in _BLOCK_TAGS:
                _emit_block(c, out, indent)
        return
    # 其余块（p / div / blockquote / figure …）：先出自身行内文本，再递归块级子元素。
    # 行内子元素不递归，避免 <span><p>x</p></span> 这类畸形结构把文本产出两遍。
    text = _para(_own_text(el))
    if text:
        out.append(text)
    for c in el:
        if _local(c.tag) in _BLOCK_TAGS:
            _emit_block(c, out, indent)


def _walk(el, out: list, indent: int):
    """遍历容器层级：行内文本累积成一段，遇到块级元素先落段落再单独处理"""
    buf: list = []

    def flush():
        text = _para("".join(buf))
        if text:
            out.append(text)
        buf.clear()

    if el.text:
        buf.append(el.text)
    for c in el:
        name = _local(c.tag)
        if name in _SKIP_TAGS:
            pass
        elif name in _BLOCK_TAGS:
            flush()
            _emit_block(c, out, indent)
        else:
            buf.append(_inline_child_text(c))
        if c.tail:
            buf.append(c.tail)
    flush()


def blocks_from_html(data: bytes) -> list[str]:
    """XHTML 字节 → Markdown 块行列表"""
    root = _load_html(data)
    if root is None:
        return []
    body = next((el for el in root.iter() if _local(el.tag) == "body"), root)
    out: list = []
    _walk(body, out, 0)
    return out


# ---------- EPUB 容器 ----------

class EpubBook:
    """EPUB 容器读取器（上下文管理器，用完 close）

    - 打开即校验：非 zip / 含 DRM → 抛 ValueError（中文原因，直接给用户看）
    - 只在需要时解析 OPF（懒加载），一次打开可反复读章节与资源
    """

    def __init__(self, path: str):
        self.path = str(path)
        try:
            self.zf = zipfile.ZipFile(self.path)
        except zipfile.BadZipFile as e:
            raise ValueError("文件不是有效的 EPUB（zip 结构损坏）") from e
        self.names = self.zf.namelist()
        self._lower = {n.lower(): n for n in self.names}
        if self._has(_ENCRYPTION):
            self.zf.close()
            raise ValueError("该 EPUB 含 DRM 加密，无法解析正文，请换用无 DRM 的版本")
        try:
            self.opf_path = self._find_opf()
        except ValueError:
            self.zf.close()
            raise
        self.opf_dir = posixpath.dirname(self.opf_path)
        self._opf = None

    # ---- 生命周期 ----
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        try:
            self.zf.close()
        except Exception:
            pass

    # ---- 基础读取 ----
    def _has(self, name: str) -> bool:
        return bool(name) and (name in self.names or (name or "").lower() in self._lower)

    def read(self, name: str) -> bytes:
        """按 zip 内路径读资源（大小写不敏感兜底）。不存在时抛 KeyError。"""
        real = name if name in self.names else self._lower.get((name or "").lower())
        if not real:
            raise KeyError(name)
        return self.zf.read(real)

    def _find_opf(self) -> str:
        try:
            root = ET.fromstring(self.read(_CONTAINER))
            for el in root.iter():
                if _local(el.tag) == "rootfile":
                    p = _norm_zip_path("", el.get("full-path") or "")
                    if self._has(p):
                        return p
        except Exception:
            pass
        for n in self.names:   # 兜底：container.xml 缺失/异常时扫 zip 内的 .opf
            if n.lower().endswith(".opf") and not n.lower().startswith("meta-inf/"):
                return n
        raise ValueError("EPUB 结构异常：未找到内容清单（OPF）")

    def _manifest(self) -> dict:
        """OPF → {id: {href, media, props}} + spine 顺序 + spine@toc 指向的 NCX id"""
        if self._opf is None:
            root = ET.fromstring(self.read(self.opf_path))
            items, spine, toc_id = {}, [], None
            for el in root.iter():
                t = _local(el.tag)
                if t == "item":
                    items[el.get("id") or ""] = {
                        "href": _norm_zip_path(self.opf_dir, el.get("href") or ""),
                        "media": (el.get("media-type") or "").strip().lower(),
                        "props": (el.get("properties") or "").strip(),
                    }
                elif t == "itemref":
                    ref = el.get("idref")
                    if ref:
                        spine.append(ref)
                elif t == "spine":
                    toc_id = el.get("toc") or toc_id
            self._opf = {"items": items, "spine": spine, "toc": toc_id}
        return self._opf

    # ---- 目录（EPUB3 nav / EPUB2 NCX）----
    def _nav_titles(self, href: str) -> dict:
        root = _load_html(self.read(href))
        if root is None:
            return {}
        base = posixpath.dirname(href)
        navs = [el for el in root.iter() if _local(el.tag) == "nav"]
        target = None
        for el in navs:
            # HTML 解析器不做命名空间，epub:type 会原样保留；部分文件用完整 URI 形式
            t = (el.get("epub:type")
                 or el.get("{http://www.idpf.org/2007/ops}type") or "").strip().lower()
            if t == "toc":
                target = el
                break
        if target is None and navs:
            target = navs[0]
        if target is None:
            return {}
        out = {}
        for a in target.iter():
            if _local(a.tag) != "a":
                continue
            path = _norm_zip_path(base, a.get("href") or "")
            title = _flat(_txt(a))
            if path and title and path not in out:
                out[path] = title[:200]
        return out

    def _ncx_titles(self, href: str) -> dict:
        try:
            root = ET.fromstring(self.read(href))
        except Exception:
            return {}
        base = posixpath.dirname(href)
        out = {}
        for np in root.iter():
            if _local(np.tag) != "navpoint":
                continue
            src, label = "", ""
            for el in np.iter():
                t = _local(el.tag)
                if t == "content" and not src:
                    src = el.get("src") or ""
                elif t == "text" and not label:
                    label = _flat(el.text or "")
            path = _norm_zip_path(base, src)
            if path and label and path not in out:
                out[path] = label[:200]
        return out

    def _toc_titles(self) -> dict:
        info = self._manifest()
        for it in info["items"].values():   # EPUB3：properties 含 nav 的清单项
            if "nav" in it["props"].split() and self._has(it["href"]):
                titles = self._nav_titles(it["href"])
                if titles:
                    return titles
        ncx = None
        for it in info["items"].values():   # EPUB2：先按 MIME，再按扩展名兜底
            if it["media"] == _NCX_MT or it["href"].lower().endswith(".ncx"):
                ncx = it["href"]
                break
        if not ncx and info["toc"]:
            ncx = info["items"].get(info["toc"], {}).get("href")
        return self._ncx_titles(ncx) if (ncx and self._has(ncx)) else {}

    # ---- 对外 ----
    def chapters(self) -> list[dict]:
        """按 spine 顺序返回可读章节：[{index, href, title}]

        index 从 1 起、连续编号，直接作为 MaterialChunk.page_no（文本视图按章分组）。
        非 XHTML 的 spine 项（封面图等）跳过；title 取不到时留空，
        由 parse_epub 用正文首个标题兜底。
        """
        info = self._manifest()
        toc = self._toc_titles()
        out, n = [], 0
        for idref in info["spine"]:
            it = info["items"].get(idref)
            if not it or not it["href"] or not self._has(it["href"]):
                continue
            media = it["media"]
            if media and not (media.startswith("application/xhtml") or media == "text/html"):
                continue
            n += 1
            out.append({"index": n, "href": it["href"], "title": toc.get(it["href"], "")})
        return out

    def blocks(self, href: str) -> list[str]:
        """章节正文 → Markdown 块行"""
        try:
            data = self.read(href)
        except KeyError:
            return []
        return blocks_from_html(data)
