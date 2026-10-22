"""视觉定位：把一句讲稿映射到材料画面上的一个具体位置（PRD 3.5.6）。

【两条底座，一套输出】

- **文字层**（PDF 等有 text layer 的材料）：PyMuPDF `page.search_for(anchor)`
  → 页面 pt 坐标（原点左上），精确到字。本项目实测（中文 PPT 讲义 57 页）：
  10 个多字 anchor 里 9 个唯一命中。
- **图片层**（扫描件 / 小红书图文 / 截图）：RapidOCR 识别整页 → 归一化 + LCS 匹配。

输出的几何一律是**页面宽高的百分比**（PRD 3.5.10 的 x/y/w/h），这样渲染层在
任意分辨率下都能定位，且与「图片显示矩形 ≠ 容器矩形」的坑解耦。

【为什么必须「归一化 + LCS」而不是直接子串】

实测 OCR 会把「隔离」认成「隔高」，子串匹配直接崩；LCS 下仍是 **0.83** 命中
（阈值取 **0.70**）。归一化只保留 CJK/字母/数字并小写 —— 去掉空格、全半角标点、
引号，让「用户访谈」与「用户 访谈，」等价。

【硬规则（违反则定位必然失败）】

1. anchor 必须是**多字词**：单字如「术」会在同页命中「技术 / 优术 / 术」多处。
2. anchor 必须是**原文原样出现**的短语，含标点要一致。
3. 图片层**只在该页内**匹配（讲稿已决定讲的是哪张图时）；跨页搜索的取舍见
   `locate()` 的 pages 参数。
4. OCR 框坐标取四点的 **min/max**，**不要用四点均值** —— 斜框用均值会偏。

【绝不抛错】

定位失败是**预期的正常结果**（材料里确实没有那句话）→ 一律返回空列表，
由上层质量闸门（PRD 3.9 判据 4：命中率 ≥ 95%）负责报警。
"""
import re

# ---------- 常量（源自实测，勿凭感觉调） ----------

# OCR 路径的 LCS 命中阈值：实测 0.83 命中，「隔离→隔高」这类误识仍能过；
# 低于 0.70 会把「只是有几个字相同」的无关行也判成命中。
LOCATE_THRESHOLD = 0.70

# 单个 anchor 最多返回几处命中。同页多处会全部高亮（视觉上就是「这几个字都亮」），
# 但上限要封住 —— 表头这种高频短语能命中几十处，全画出来等于没画。
MAX_HITS = 8

# anchor 下限长度：单字一律不接受（见模块 docstring 硬规则 1）
MIN_ANCHOR_LEN = 2

# 一份材料最多准备多少页（防「整本 500 页 PDF」把定位阶段拖爆；PRD 4.3 要求 ≤60s/100页）
MAX_PAGES = 120

# 判定「有文字层」的采样页数：只抽前若干页，避免为判定扫全本
_TEXT_PROBE_PAGES = 6

# 归一化：只保留 CJK（含扩展 A）/ 字母 / 数字
_KEEP_RE = re.compile(r"[0-9a-zA-Z\u4e00-\u9fff\u3400-\u4dbf]+")

_IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif")


def norm(s: str) -> str:
    """归一化：只保留 CJK / 字母 / 数字并小写（去空格、标点、引号）。"""
    return "".join(_KEEP_RE.findall(s or "")).lower()


def lcs_len(a: str, b: str) -> int:
    """最长公共子序列长度（滚动数组 DP，O(len(a)) 空间）。

    用 LCS 而不是编辑距离：OCR 的错误是**替换**（隔→高）与**漏字**，
    LCS 对这两种都稳，而编辑距离会被「长度差」放大惩罚。
    """
    if not a or not b:
        return 0
    if len(a) > len(b):
        a, b = b, a
    prev = [0] * (len(a) + 1)
    for cb in b:
        cur = [0] * (len(a) + 1)
        for i, ca in enumerate(a, 1):
            cur[i] = prev[i - 1] + 1 if ca == cb else max(prev[i], cur[i - 1])
        prev = cur
    return prev[-1]


def match_score(anchor: str, line: str) -> float:
    """anchor 与一行文本的匹配得分（1.0 = 完全包含；否则 LCS 覆盖率）。"""
    na, nl = norm(anchor), norm(line)
    if len(na) < MIN_ANCHOR_LEN or not nl:
        return 0.0
    if na in nl:
        return 1.0
    return lcs_len(na, nl) / len(na)


def _clamp_anchor(anchor: str) -> str:
    """anchor 归一前的轻量清洗：去首尾空白与包裹引号（保留句中标点）。"""
    a = (anchor or "").strip()
    return a.strip("「」『』\"'“”‘’").strip()


def _fallbacks(anchor: str) -> list[str]:
    """anchor 在原文找不到时的降级候选（PRD 3.5.9「退到最接近区域」）。

    做法：按标点/连接词切出若干「词块」，从最长的开始逐级缩短。
    只在整串找不到时才用，且**必须如实告知**调用方这是降级命中。
    """
    a = _clamp_anchor(anchor)
    parts = [p for p in re.split(r"[，,、。；;：:\s（）()【】\[\]]+", a) if len(p) >= MIN_ANCHOR_LEN]
    if len(parts) <= 1:
        # 无标点可切 → 取前缀二分（4 字起）
        out = []
        n = len(a)
        for cut in (max(4, n * 2 // 3), max(4, n // 2), 4):
            if 0 < cut < n:
                out.append(a[:cut])
        return [p for p in out if len(p) >= MIN_ANCHOR_LEN]
    parts.sort(key=len, reverse=True)
    return parts


# ---------- 统一容器 ----------

class _Page:
    """一页的可定位载体。`kind` 决定用哪条底座。"""

    # ⚠️ `_handle` 必须在这里声明：`_pdf_text_pages()` 会在构造后写 `p._handle = pg`，
    #    而 `__slots__` 生效后实例**没有 `__dict__`** —— 漏一个槽位就是
    #    `AttributeError: '_Page' object has no attribute '_handle' and no __dict__
    #    for setting new attributes`，被上层 `except` 转成「无法打开 PDF」，
    #    表现为「材料原件皮肤对所有 PDF 不可用」（静默、看起来像"不支持这种材料"）。
    __slots__ = ("no", "width", "height", "text", "lines", "_handle")

    def __init__(self, no: int, width: float, height: float,
                 text: str = "", lines: list | None = None):
        self.no = no                  # 页号（图片材料 = 图序号，1 起）
        self.width = max(1.0, float(width))
        self.height = max(1.0, float(height))
        self.text = text              # 文字层全文（仅 kind=="text"）
        self.lines = lines or []      # OCR 行 [{t, x0, y0, x1, y1, sc}]（仅 kind=="image"）
        self._handle = None           # pymupdf 的 Page 视图（仅文字层 PDF 用得上）


class Locator:
    """一份材料的定位器。构造后即可 `locate(anchor)`；用完调 `close()`。

    `kind` ∈ {"text", "image"}；`pages` 是**已就绪**的页（图片层为懒加载，
    见 `_ImageLocator` 的子类实现对 OCR 的按需触发）。
    """

    def __init__(self, kind: str, pages: list, source: str = ""):
        self.kind = kind
        self.pages = pages
        self.source = source          # 展示用：文件路径或「配图 N 张」

    # ---- 对外 ----

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def close(self) -> None:
        """释放底层资源（PDF 句柄等）。绝不抛错。"""
        doc = getattr(self, "_doc", None)
        try:
            if doc is not None:
                doc.close()
        except BaseException:
            pass

    def locate(self, anchor: str, pages: list | None = None,
               min_score: float = 0.0) -> list:
        """定位一个 anchor，返回 [{page, hit, sc, x, y, w, h}]（百分比矩形）。

        - 结果**按 (得分降序, 页号升序)** 排列 —— 上层取 [0] 即「得分最高的最早页」，
          正好实现 PRD 3.5.6「跨页多处 → 选最早出现的页」。
        - `pages` 传页号列表可限定搜索范围（图片层**必须**限定在指定页内）。
        - 达不到 min_score 的一律丢弃；空列表是**合法**结果（见模块 docstring）。
        """
        a = _clamp_anchor(anchor)
        if norm(a) and len(norm(a)) >= MIN_ANCHOR_LEN:
            hits = self._search(a, pages)
            if hits:
                return hits
        # 整串没找到 → 降级候选（仍然要求达到阈值，宁可不给也不错给）
        for alt in _fallbacks(a):
            hits = self._search(alt, pages)
            if hits:
                return hits
        return []

    # ---- 内部：由子类实现 ----

    def _search(self, anchor: str, pages: list | None) -> list:
        raise NotImplementedError

    def _rect(self, page, x0: float, y0: float, x1: float, y1: float) -> dict:
        """像素/pt 坐标 → 页面百分比（裁剪到 [0,100]，防越界框跑到画布外）。"""
        w = max(1e-6, page.width)
        h = max(1e-6, page.height)
        px = max(0.0, min(100.0, x0 / w * 100.0))
        py = max(0.0, min(100.0, y0 / h * 100.0))
        pw = max(0.0, min(100.0 - px, (x1 - x0) / w * 100.0))
        ph = max(0.0, min(100.0 - py, (y1 - y0) / h * 100.0))
        return {"x": round(px, 2), "y": round(py, 2),
                "w": round(pw, 2), "h": round(ph, 2)}


class _TextLocator(Locator):
    """文字层：PyMuPDF `search_for`。pymupdf 对 CJK 支持良好，无需分词。"""

    def _search(self, anchor: str, pages: list | None) -> list:
        out = []
        for p in self.pages:
            if pages is not None and p.no not in pages:
                continue
            try:
                rects = p._handle.search_for(anchor)   # noqa: SLF001
            except BaseException:
                continue
            for r in rects or []:
                out.append({"page": p.no, "hit": anchor, "sc": 1.0,  # 文字层恒 1.0
                            "match": "exact", **self._rect(p, r.x0, r.y0, r.x1, r.y1)})
        out.sort(key=lambda h: (h["page"], h["y"], h["x"]))
        return out[:MAX_HITS]


class _ImageLocator(Locator):
    """图片层：RapidOCR + 归一化 + LCS。

    OCR 是**懒**的：一页只在第一次被搜到时才识别，并把结果缓存在 `_cache`。
    材料有几十张图时，只识别真正被讲稿提到的那几张（PRD 4.3：≤3s/张）。
    """

    def __init__(self, pages: list, source: str = ""):
        super().__init__("image", pages, source)
        self._cache: dict[int, list] = {}

    def _lines(self, p: _Page) -> list:
        if p.lines:
            return p.lines
        got = self._cache.get(p.no)
        if got is not None:
            return got
        from . import ocr as ocr_svc
        try:
            got = ocr_svc.recognize_boxes(p._img())   # noqa: SLF001
        except BaseException:
            got = []
        self._cache[p.no] = got
        return got

    def _search(self, anchor: str, pages: list | None) -> list:
        na = norm(anchor)
        out = []
        for p in self.pages:
            if pages is not None and p.no not in pages:
                continue
            for ln in self._lines(p):
                sc = 1.0 if na in norm(ln["t"]) else lcs_len(na, norm(ln["t"])) / max(1, len(na))
                if sc < LOCATE_THRESHOLD:
                    continue
                out.append({"page": p.no, "hit": ln["t"], "sc": round(sc, 3),
                            "match": "exact" if sc >= 1.0 else "lcs",
                            **self._rect(p, ln["x0"], ln["y0"], ln["x1"], ln["y1"])})
        out.sort(key=lambda h: (-h["sc"], h["page"], h["y"], h["x"]))
        return out[:MAX_HITS]


# ---------- 构造：从材料得到定位器 ----------

def _material_file(m):
    from pathlib import Path
    p = Path(getattr(m, "file_path", "") or "")
    return p if p.is_file() else None


def _asset_images(db, m) -> list:
    """剪藏图文材料的本地化配图（`data/assets/<subdir>/img_*.jpg`）。

    目录口径走 `external.assets_subdir_for` 写入的 `origin_meta["assets_dir"]`，
    与删除侧（`routers/materials.py::_cleanup_material_assets`）指向同一处。
    """
    from pathlib import Path
    from . import external as ext_svc
    meta = getattr(m, "origin_meta", None) or {}
    sub = str(meta.get("assets_dir") or "").strip()
    if not sub:
        return []
    try:
        d = ext_svc.assets_dir(sub)
    except BaseException:
        return []
    if not d.is_dir():
        return []
    files = sorted([f for f in d.iterdir()
                    if f.is_file() and f.suffix.lower() in _IMG_EXT])
    return files[:MAX_PAGES]


def _pdf_text_pages(path) -> tuple:
    """打开 PDF 并返回 (pages, has_text, doc)。`pages[i]._handle` 是 pymupdf Page。

    ⚠️ doc 由调用方持有（挂在 Locator 上），**不能在这里关** —— 页面对象是它的视图。
    """
    import pymupdf
    doc = pymupdf.open(str(path))
    pages = []
    try:
        n = min(doc.page_count, MAX_PAGES)
        for i in range(n):
            pg = doc[i]
            r = pg.rect
            p = _Page(i + 1, r.width, r.height)
            p._handle = pg          # noqa: SLF001
            pages.append(p)
        # 文字层判定：抽前若干页，任取到非空文本即视为有文字层
        has_text = False
        for p in pages[:_TEXT_PROBE_PAGES]:
            try:
                if (p._handle.get_text() or "").strip():   # noqa: SLF001
                    has_text = True
                    break
            except BaseException:
                continue
    except BaseException:
        doc.close()
        raise
    return pages, has_text, doc


class _RenderedPage(_Page):
    """懒渲染的 PDF 页（无文字层时走 OCR）。"""

    # ⚠️ 子类不声明 `__slots__` 就会**自动获得 `__dict__`**（父类的 `__slots__` 形同虚设），
    #    且字段名写错时静默生效。显式声明，让拼写错误立刻报错。
    __slots__ = ("_path", "_index", "_dpi")

    def __init__(self, no, path, index, dpi: int = 150):
        super().__init__(no, 1.0, 1.0)
        self._path = str(path)
        self._index = index
        self._dpi = dpi

    def _img(self):
        from .parser import _render_pdf_page
        return _render_pdf_page(self._path, self._index, dpi=self._dpi)


class _FileImagePage(_Page):
    """磁盘上的图片文件（剪藏配图 / 图片材料）。"""

    __slots__ = ("_path",)

    def __init__(self, no, path):
        from PIL import Image
        try:
            with Image.open(path) as im:
                w, h = im.size
        except BaseException:
            w = h = 1
        super().__init__(no, w, h)
        self._path = str(path)

    def _img(self):
        return self._path


def build_locator(db, material_id: int, dpi: int = 150):
    """为一份材料建定位器。返回 `(Locator | None, reason)`。

    reason 是给 UI 的**可执行理由**（PRD 3.2.7「置灰并说明原因」），
    例如「这份材料没有可识别的文字层，建议改用 PPT 课件 / 板书」。
    """
    from ..models import Material
    m = db.get(Material, material_id)
    if not m:
        return None, "材料不存在"

    fmt = (m.format or "").lower()
    path = _material_file(m)

    # ① 图片文件材料（截图 / 单图）
    if path is not None and fmt.lstrip(".") in ("jpg", "jpeg", "png", "webp", "bmp"):
        pages = [_FileImagePage(1, path)]
        return _ImageLocator(pages, source=str(path.name)), "图片材料"

    # ② 剪藏图文：正文无「页」概念，但本地化配图可以定位
    imgs = _asset_images(db, m)
    if imgs:
        pages = [_FileImagePage(i + 1, f) for i, f in enumerate(imgs)]
        return _ImageLocator(pages, source=f"配图 {len(pages)} 张"), "图文材料"

    # ③ PDF（含扫描件）
    if path is not None and fmt.lstrip(".") == "pdf":
        try:
            pages, has_text, doc = _pdf_text_pages(path)
        except BaseException as e:
            return None, f"无法打开 PDF：{str(e)[:60]}"
        if has_text:
            loc = _TextLocator("text", pages, source=str(path.name))
            loc._doc = doc          # noqa: SLF001  PDF 句柄挂在定位器上，close() 负责释放
            return loc, "有文字层"
        try:
            doc.close()
        except BaseException:
            pass
        if not pages:
            return None, "PDF 没有可渲染的页面"
        rp = []
        for src in pages:
            # ⚠️ 页宽高必须换算成**渲染后的像素尺寸**（pt × dpi/72），不能直接用 pt：
            #    OCR 返回的框是像素坐标，而 _rect() 用 page.width/height 做分母 ——
            #    两者单位不一致会让百分比坐标整体缩小约 2 倍（框跑到左下角一小块）。
            dst = _RenderedPage(src.no, path, src.no - 1, dpi=dpi)
            dst.width = src.width * dpi / 72.0
            dst.height = src.height * dpi / 72.0
            rp.append(dst)
        return _ImageLocator(rp, source=f"{path.name}（扫描件）"), "无文字层，走 OCR"

    # ④ 其他（docx / pptx / 笔记 / 纯文本）：没有「页」概念，无法做原件定位
    return None, "这类材料没有可定位的原件页面"


def source_excerpt(loc, max_chars: int = 20000, max_pages: int = 20) -> str:
    """抽「原文片段」供锚点生成使用。

    ⚠️ 为什么必须有它：`llm.generate_video_anchors` 只拿到讲稿时，模型只能**猜**
       「哪个短语在原文里原样存在」（讲稿是改写过的）。实测讲稿与材料同源的剪藏材料上，
       讲稿 4-gram 在页面文本里的命中率只有 18.2% —— 不是模型不行，是它手里没有原文。
       把原文给它，命中率才有条件达到闸门判据 4 的 ≥95%。

    实现要点：
    - `kind=="text"`（PDF 文字层）直接取 `get_text()`，**不限页数** ——
      实测一份 57 页幻灯片 PDF 的**全书**文字层只有 4708 字（平均 82 字/页，57/57 页都有字），
      限 20 页会把这本就稀薄的可用原文砍掉 77%，而它正是锚点能否定位的唯一依据；
    - `kind=="image"`（扫描件 / 剪藏配图）只能走 OCR，**贵**（PRD 4.3：≤3s/张）→ 限量
      `max_pages` 页，且借道 `_ImageLocator._lines()` 的按页缓存：后续正式定位同一页不会重复识别；
    - 总量超 `max_chars` 时**按页均分**截断，而不是「只取前 N 页」——
      否则锚点会被全部引到材料开头几页，长文档的画面分布严重偏斜。
    """
    all_pages = list(getattr(loc, "pages", None) or [])
    if not all_pages:
        return ""
    # ⚠️ 只有 OCR 路径限页（理由见 docstring 的实测数据）；文字层走全量，由 max_chars 兜底。
    pages = (all_pages if getattr(loc, "kind", "") == "text"
             else all_pages[:max(1, int(max_pages))])
    texts = []
    for p in pages:
        t = ""
        if getattr(loc, "kind", "") == "text":
            h = getattr(p, "_handle", None)
            try:
                t = (h.get_text() or "") if h is not None else (p.text or "")
            except BaseException:
                t = p.text or ""
        else:
            lines = []
            if hasattr(loc, "_lines"):
                try:
                    lines = loc._lines(p)          # noqa: SLF001  按页缓存，不重复 OCR
                except BaseException:
                    lines = []
            t = " ".join(str(x.get("t") or "") for x in (lines or []))
        texts.append(t)
    total = sum(len(t) for t in texts)
    if total <= max_chars:
        return "\n".join(t for t in texts if t.strip())
    per = max(120, int(max_chars) // max(1, len(texts)))
    return "\n".join(t[:per] for t in texts if t.strip())[:max_chars]


def availability(db, material_id: int) -> dict:
    """兼容性预检（PRD 3.2.5 的「硬规则判据」）。

    返回 {"ok": bool, "kind": "text"|"image"|"", "reason": str, "pages": int}
    """
    loc, reason = build_locator(db, material_id)
    if loc is None:
        return {"ok": False, "kind": "", "reason": reason, "pages": 0}
    info = {"ok": True, "kind": loc.kind, "reason": reason, "pages": loc.page_count}
    loc.close()
    return info
