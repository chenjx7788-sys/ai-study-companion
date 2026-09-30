"""文件解析服务：PDF / Word / PPT / Markdown / EPUB / 图片 / 音视频 → 结构化文本块（含页码与章节路径）

输出统一为 [{"content": str, "page_no": int, "section_path": str}, ...]
扫描版 PDF（无文本层）与图片走 RapidOCR 本地识别；OCR 失败/依赖缺失时返回空，由上层标记"扫描件"。
音视频走 faster-whisper 本地 ASR 转写，page_no = 分钟序号（第 N 分钟）。
Markdown 按空行分段落，page_no = 段落序号，标题作为 section_path。
EPUB 按 spine 章节顺序解析，page_no = 章节序号，章节标题作为 section_path；
正文按结构块（段落/标题/列表/表格）逐块产出，块内不再预打包（切块交 RAG 层 split_text）。
"""
from pathlib import Path
from bisect import bisect_right
import re
import threading
import hashlib

from pypdf import PdfReader
from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from pptx import Presentation


def _pdf_section_marks(reader: PdfReader) -> tuple[list[int], list[str]]:
    """从 PDF 书签(outline)建立页码→章节路径映射。返回 (pages, paths)，按页码升序。

    无书签或读取失败时返回空——section_path 退化为 ""。
    """
    try:
        outline = reader.outline
    except Exception:
        return [], []
    marks: list[tuple[int, str]] = []

    def walk(items, prefix):
        last_title = None
        for it in items:
            if isinstance(it, list):   # 嵌套子书签跟随其父项
                walk(it, prefix + [last_title] if last_title else prefix)
            else:
                title = (getattr(it, "title", "") or "").strip()
                try:
                    page = reader.get_destination_page_number(it) + 1
                except Exception:
                    page = None
                if title and page:
                    marks.append((page, "/".join(prefix + [title])[:500]))
                    last_title = title

    walk(outline, [])
    marks.sort(key=lambda x: x[0])
    return [m[0] for m in marks], [m[1] for m in marks]


def parse_pdf(path: str, on_progress=None) -> list[dict]:
    """PDF 按页提取文本；无文本层的页（扫描件/图片页）自动转图走 OCR 兜底。

    此外，对「有文本层」的页，额外提取内嵌图片（图表/公式/流程图截图）单独 OCR，
    产出带 `[图片]` 前缀标记的独立块——文本层拿不到插图里的文字，需 OCR 补全。
    - 无文本层的页已由整页 OCR 覆盖，不再对内嵌图重复 OCR。
    - 内嵌图按 xref 全局去重（页眉 LOGO 跨页复用只认一次）。

    on_progress：0-100 进度回调（按已处理页数/总页数估算）。
    """
    from . import ocr as ocr_svc

    reader = PdfReader(path)
    mark_pages, mark_paths = _pdf_section_marks(reader)
    chunks = []
    total = len(reader.pages) or 1
    img_ocr_available = ocr_svc.is_available()
    seen_xrefs: set[int] = set()
    for i, page in enumerate(reader.pages):
        text = (page.extract_text() or "").strip()
        if not text:
            try:
                text = _ocr_pdf_page(path, i)   # 空文本页 → OCR 兜底
            except Exception:
                text = ""   # 依赖缺失/识别失败：降级为空，交由上层标记扫描件
            if text:
                # 章节 = 页码 ≤ 当前页的最后一个书签（无书签则为空）
                idx = bisect_right(mark_pages, i + 1) - 1
                section = mark_paths[idx] if idx >= 0 else ""
                chunks.append({"content": text, "page_no": i + 1, "section_path": section})
        else:
            # 有文本层：正文正常入库，另对内嵌图做 OCR 补漏
            idx = bisect_right(mark_pages, i + 1) - 1
            section = mark_paths[idx] if idx >= 0 else ""
            chunks.append({"content": text, "page_no": i + 1, "section_path": section})
            if img_ocr_available:
                try:
                    img_texts = _ocr_pdf_page_images(path, i, seen_xrefs)
                except Exception:
                    img_texts = []
                for t in img_texts:
                    chunks.append({
                        "content": f"[图片] {t}",
                        "page_no": i + 1,
                        "section_path": section,
                    })
        if on_progress:
            on_progress(int((i + 1) / total * 100))
    return chunks


def _render_pdf_page(pdf_path: str, page_index: int, dpi: int = 200):
    """用 PyMuPDF 把 PDF 单页渲染为 RGB numpy 数组（喂给 OCR）。

    pypdf 只能提取文本层、不能渲染成图；扫描件 OCR 必须有渲染器。
    选 PyMuPDF（自带二进制、纯 pip 包），避免 pdf2image+poppler 的系统依赖。
    """
    import pymupdf as fitz
    import numpy as np
    doc = fitz.open(pdf_path)
    try:
        page = doc[page_index]
        pix = page.get_pixmap(dpi=dpi)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        if pix.n == 4:
            img = img[:, :, :3]          # 丢弃 alpha 通道
        elif pix.n == 1:
            img = np.repeat(img, 3, axis=2)   # 灰度转 RGB
        return img
    finally:
        doc.close()


def _ocr_pdf_page(pdf_path: str, page_index: int, dpi: int = 200) -> str:
    """渲染 PDF 单页并 OCR 识别为文本"""
    from . import ocr as ocr_svc
    img = _render_pdf_page(pdf_path, page_index, dpi)
    return ocr_svc.ocr_image_to_text(img)


# 内嵌图 OCR 的最小/最大尺寸门槛（像素），用于过滤图标、装饰线与整页扫描件
_IMG_OCR_MIN_PX = 40          # 小于 40px 的图（图标/分隔线/装饰）跳过，OCR 无意义
_IMG_OCR_MAX_PX = 2000        # 大于 2000px 的图多为整页扫描件，整页 OCR 已覆盖，避免重复


def _extract_pdf_page_images(pdf_path: str, page_index: int) -> list:
    """提取 PDF 单页的内嵌图片（去重 + 尺寸过滤），返回 RGB numpy 数组列表。

    - 用 PyMuPDF `page.get_images()` 枚举该页引用的图片 xref（含跨页复用的页眉 LOGO）。
    - 按 xref 去重：同一张图在多页复用只 OCR 一次（由调用方维护全局 xref 集合）。
    - 过滤：过小的图标/装饰线、过大的整页扫描件（后者已由整页 OCR 覆盖）。
    - `extract_image` 返回的原始字节可能带 alpha / 灰度 / CMYK，统一归一为 RGB。
    """
    import pymupdf as fitz
    import numpy as np

    doc = fitz.open(pdf_path)
    try:
        page = doc[page_index]
        infos = page.get_images(full=True)
        out = []
        for info in infos:
            xref = info[0]                      # 图片对象 xref
            try:
                raw = doc.extract_image(xref)
            except Exception:
                continue
            img_bytes = raw.get("image")
            if not img_bytes:
                continue
            arr = np.frombuffer(img_bytes, dtype=np.uint8)
            img = None
            try:
                import cv2
                img = cv2.imdecode(arr, cv2.IMREAD_COLOR)   # BGR
                if img is not None:
                    img = img[:, :, ::-1]                    # BGR → RGB
            except Exception:
                img = None
            if img is None:
                continue
            h, w = img.shape[:2]
            if min(h, w) < _IMG_OCR_MIN_PX or max(h, w) > _IMG_OCR_MAX_PX:
                continue
            out.append((xref, img))
        return out
    finally:
        doc.close()


def _ocr_image_blob(blob: bytes) -> str:
    """图片字节 → OCR 文本（空/失败返回空串）。

    供 Word/EPUB 等「内嵌图以字节形式取出」的解析器复用；PDF 走 numpy 路径不经过这里。
    解码用 cv2.imdecode（依赖链已含 opencv-headless），与第一刀 PDF 内嵌图同款口径。
    """
    from . import ocr as ocr_svc
    import numpy as np
    arr = np.frombuffer(blob, dtype=np.uint8)
    img = None
    try:
        import cv2
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is not None:
            img = img[:, :, ::-1]   # BGR → RGB
    except Exception:
        img = None
    if img is None:
        return ""
    h, w = img.shape[:2]
    if min(h, w) < _IMG_OCR_MIN_PX or max(h, w) > _IMG_OCR_MAX_PX:
        return ""   # 图标/装饰/超大会话图，与 PDF 口经一致
    return ocr_svc.ocr_image_to_text(img).strip()


def _ocr_pdf_page_images(pdf_path: str, page_index: int, seen_xrefs: set) -> list[str]:
    """OCR 某页的内嵌图片，返回各图识别文本列表（空文本不计入），并登记已处理的 xref。

    用于「有文本层」的页——文本层能拿到正文，但页内插图（图表/公式/流程图截图）里的
    文字不会被 `extract_text` 提取，需单独 OCR 补全。
    """
    from . import ocr as ocr_svc
    texts = []
    for xref, img in _extract_pdf_page_images(pdf_path, page_index):
        if xref in seen_xrefs:
            continue
        seen_xrefs.add(xref)
        t = ocr_svc.ocr_image_to_text(img)
        if t.strip():
            texts.append(t)
    return texts


def parse_image(path: str, on_progress=None) -> list[dict]:
    """图片文件（截图/拍照书页/板书照片）→ OCR → 单块文本（page_no=1）"""
    from . import ocr as ocr_svc
    if on_progress:
        on_progress(10)
    text = ocr_svc.ocr_image_to_text(path)
    if on_progress:
        on_progress(100)
    if not text.strip():
        return []
    return [{"content": text, "page_no": 1, "section_path": ""}]


def _docx_page_breaks(para) -> int:
    """段落内的显式分页符数量（用户分页符 + Word 渲染分页标记）"""
    xml = para._element.xml
    return xml.count('w:type="page"') + xml.count("lastRenderedPageBreak")


def _docx_iter_blocks(doc):
    """按文档顺序产出段落与表格。

    python-docx 的 doc.paragraphs **不包含表格内的段落**，表格必须走 doc.tables；
    但若只分别遍历两者，会丢失「段落 ↔ 表格」的原始先后顺序（表格会被挪到最后）。
    因此这里直接遍历 body 子元素，保证顺序与原文一致。
    """
    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, doc)
        elif child.tag == qn("w:tbl"):
            yield Table(child, doc)


def _docx_heading_level(style_name: str) -> int:
    """标题级别：Heading 1/标题 1 → 1..6；非标题返回 0"""
    name = (style_name or "").strip()
    m = re.match(r"^(?:Heading|标题)\s*(\d+)$", name, re.I)
    if m:
        return max(1, min(int(m.group(1)), 6))
    if name in ("Title", "标题", "Subtitle", "副标题"):
        return 1 if name in ("Title", "标题") else 2
    return 0


def _docx_numbering_kinds(doc) -> dict:
    """numId → 'bullet' | 'number'（按 abstractNum 首个 level 的 numFmt 判定）。

    列表的项目符号/编号并不体现在 para.text 里，需要从 numbering.xml 反查，
    否则有序/无序列表会退化成无标记的普通段落。
    """
    kinds: dict[str, str] = {}
    try:
        numbering = doc.part.numbering_part.element
    except Exception:
        return kinds
    abstracts: dict[str, str] = {}
    for an in numbering.findall(qn("w:abstractNum")):
        aid = an.get(qn("w:abstractNumId"))
        fmt = None
        for lvl in an.findall(qn("w:lvl")):
            nf = lvl.find(qn("w:numFmt"))
            if nf is not None:
                fmt = nf.get(qn("w:val"))
                break
        abstracts[aid] = fmt or "bullet"
    for num in numbering.findall(qn("w:num")):
        nid = num.get(qn("w:numId"))
        ref = num.find(qn("w:abstractNumId"))
        aid = ref.get(qn("w:val")) if ref is not None else None
        fmt = abstracts.get(aid, "bullet")
        kinds[nid] = "bullet" if fmt in ("bullet", "none") else "number"
    return kinds


# 图片的 rId（r:embed）提取：段落 XML 里 a:blip 的 embed 属性。
_BLIP_TAG = qn("a:blip")
_EMBED_ATTR = qn("r:embed")


def _docx_image_rids(para) -> list[str]:
    """段落内嵌图片的 rId 列表（按出现顺序、去重）。无图片返回 []。

    图片可内联在 `w:drawing`（浮动/内联图）或 `w:pict`（VML 旧格式，含 Office 剪贴画）里，
    二者都落到 `a:blip` 的 `r:embed` 属性。用 `iter(_BLIP_TAG)` 遍历（qn 展开完整命名空间，
    避免 XPath 前缀/namespace 差异），兼容各种现实文档。
    """
    rids: list[str] = []
    for blip in para._p.iter(_BLIP_TAG):
        rid = blip.get(_EMBED_ATTR)
        if rid and rid not in rids:
            rids.append(rid)
    return rids


def _docx_image_blob(doc, rid: str):
    """按 rId 取图片字节（ImagePart.blob）；非图片关系返回 None。"""
    part = doc.part.related_parts.get(rid)
    if part is None:
        return None
    blob = getattr(part, "blob", None)
    if not blob:
        return None
    return blob


def _docx_list_marker(para, num_kinds: dict) -> tuple[str, int]:
    """返回 (marker, level)：('-'|'1.'|'', 缩进级别)。非列表返回 ('', 0)"""
    pPr = para._p.pPr
    numPr = pPr.numPr if pPr is not None else None
    if numPr is not None and numPr.numId is not None:
        ilvl = int(numPr.ilvl.val) if numPr.ilvl is not None else 0
        kind = num_kinds.get(str(numPr.numId.val), "bullet")
        return ("-" if kind == "bullet" else "1."), ilvl
    name = (para.style.name or "").lower()
    if "list bullet" in name:
        return "-", 0
    if "list number" in name:
        return "1.", 0
    return "", 0


def _md_cell(text: str) -> str:
    """单元格文本 → 单行 Markdown 安全文本。

    表格语法里 `|` 是列分隔符、换行会断行，都必须处理；由于前端 markdown-it 关闭了
    html（防注入），不能用 <br>（会被转义成字面量），改用 " / " 合并多行。
    """
    parts = [re.sub(r"[ \t]+", " ", p).strip() for p in (text or "").splitlines()]
    t = " / ".join([p for p in parts if p])
    # 段落边界自带 "/" 时（如「…微信公众号」+「/朋友圈…」）会拼出 " / /"，归一为单个分隔符
    t = re.sub(r"\s*/(?:\s*/)+\s*", " / ", t)
    t = re.sub(r"\s{2,}", " ", t).strip().strip(" /")
    return t.replace("|", "\\|")


_MAX_TABLE_CHARS = 1200      # 单块表格上限，超出则按行拆分并重复表头（便于检索与阅读）


def _docx_table_markdown(tbl, cell_text) -> list[str]:
    """表格 → Markdown 表格文本（可能拆成多段，每段都带表头）。

    cell_text(cell) -> str 由调用方提供，以兼容 python-docx（Word）与 python-pptx（PPT）。
    """
    rows: list[list[str]] = []
    for row in tbl.rows:
        cells, seen = [], set()
        for cell in row.cells:
            key = id(getattr(cell, "_tc", cell))
            if key in seen:
                cells.append("")            # 合并单元格：占位以保持列对齐
                continue
            seen.add(key)
            cells.append(cell_text(cell))
        rows.append(cells)
    return _rows_to_md_tables(rows)


def _rows_to_md_tables(rows: list[list[str]]) -> list[str]:
    """二维单元格数组 → Markdown 表格文本（超长时按行拆分并重复表头）。

    与来源解耦：Word / PPT（python-docx / python-pptx）与 EPUB（lxml）各自抽出行列，
    表格语法的拼接与拆分逻辑只有这一份实现。
    """
    rows = [r for r in rows if any(c for c in r)]
    if not rows:
        return []

    # 列数对齐：markdown-it 要求各行等列，否则整张表会退化成普通段落
    ncols = max(len(r) for r in rows)
    rows = [r + [""] * (ncols - len(r)) for r in rows]
    header, body = rows[0], rows[1:]
    if not any(header):
        header = [f"列{i + 1}" if ncols > 1 else "内容" for i in range(ncols)]

    sep = "| " + " | ".join(["---"] * ncols) + " |"
    head = "| " + " | ".join(header) + " |"
    out, buf = [], []
    for r in body:
        line = "| " + " | ".join(r) + " |"
        if buf and sum(len(x) + 1 for x in buf) + len(line) > _MAX_TABLE_CHARS:
            out.append("\n".join([head, sep, *buf]))
            buf = []
        buf.append(line)
    if buf:
        out.append("\n".join([head, sep, *buf]))
    # 表格只有表头行（无数据行）时也要输出，否则该表会整块丢失
    return out or ["\n".join([head, sep])]


def parse_docx(path: str) -> list[dict]:
    """DOCX → Markdown 文本块：段落 / 标题 / 列表 / **表格** 均按原文顺序产出。

    修复点：此前仅遍历 doc.paragraphs，表格内容被整段丢弃（正文写在表格里的文档几乎解析不出内容）。
    page_no 说明：优先按显式分页符翻页；无分页符时按 ~700 字/页估算（python-docx 拿不到真实页码）。

    内嵌图 OCR：段内 `a:blip` 图片单独 OCR，产出 `[图片]` 标记的独立块（挂所在段落位置）。
    - 按 rId 全局去重（同图多处引用只认一次）。
    - OCR 依赖缺失时整体跳过（`ocr.is_available()` 短路），不拖慢无 OCR 环境。
    """
    from . import ocr as ocr_svc

    doc = Document(path)
    blocks = list(_docx_iter_blocks(doc))
    has_breaks = any(_docx_page_breaks(b) for b in blocks if isinstance(b, Paragraph))
    num_kinds = _docx_numbering_kinds(doc)

    chunks, page, section, chars = [], 1, "", 0
    img_ocr_available = ocr_svc.is_available()
    seen_rids: set[str] = set()

    def _emit(text: str, bump: int = 0):
        """写入一块，并按估算方式推进页码"""
        nonlocal page, chars
        chunks.append({"content": text, "page_no": page, "section_path": section})
        chars += len(text)
        if not has_breaks:
            page = 1 + chars // 700
        page += bump

    for b in blocks:
        if isinstance(b, Paragraph):
            text = b.text.strip()
            n_breaks = _docx_page_breaks(b)
            para_page = page                      # 段落起始页（图片 chunk 挂这里，与段落同页）
            if text:
                level = _docx_heading_level(b.style.name if b.style else "")
                marker, indent = _docx_list_marker(b, num_kinds)
                if level:
                    section = text                       # 章节路径用纯文本
                    _emit(f"{'#' * level} {text}", n_breaks)
                elif marker:
                    _emit(f"{'  ' * indent}{marker} {text}", n_breaks)
                else:
                    _emit(text, n_breaks)
            else:
                page += n_breaks
            # 内嵌图 OCR：段内图片（含无文本、只装图的段落）→ [图片] 独立块
            if img_ocr_available:
                for rid in _docx_image_rids(b):
                    if rid in seen_rids:
                        continue
                    seen_rids.add(rid)
                    try:
                        blob = _docx_image_blob(doc, rid)
                    except Exception:
                        blob = None
                    if not blob:
                        continue
                    try:
                        t = _ocr_image_blob(blob)
                    except Exception:
                        t = ""
                    if t:
                        chunks.append({"content": f"[图片] {t}",
                                       "page_no": para_page,
                                       "section_path": section})
        else:                                            # Table
            for md_table in _docx_table_markdown(b, lambda c: _md_cell(c.text)):
                _emit(md_table)
    return chunks


def parse_pptx(path: str) -> list[dict]:
    """PPT → 文本块（每页一个正文块 + 表格块；图片单独 OCR 为 `[图片]` 块）。

    图片提取：`shape.shape_type == PICTURE` 时 `shape.image.blob` 直接给字节，
    无需解 zip（python-pptx 已封装）。按 blob 哈希去重（多页复用同一张图只 OCR 一次）。
    """
    from . import ocr as ocr_svc
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    prs = Presentation(path)
    chunks = []
    img_ocr_available = ocr_svc.is_available()
    seen_blobs: set[str] = set()
    for i, slide in enumerate(prs.slides):
        texts = [s.text_frame.text.strip() for s in slide.shapes
                 if s.has_text_frame and s.text_frame.text.strip()]
        if texts:
            chunks.append({"content": "\n".join(texts), "page_no": i + 1, "section_path": f"第{i+1}页"})
        # 幻灯片内表格：此前被完全忽略，与 Word 属同一类缺陷
        for s in slide.shapes:
            if getattr(s, "has_table", False):
                for md_table in _docx_table_markdown(s.table, lambda c: _md_cell(c.text)):
                    chunks.append({"content": md_table, "page_no": i + 1, "section_path": f"第{i+1}页"})
        # 幻灯片内图片 → OCR
        if img_ocr_available:
            for s in slide.shapes:
                if s.shape_type != MSO_SHAPE_TYPE.PICTURE:
                    continue
                try:
                    blob = s.image.blob
                except Exception:
                    blob = None
                if not blob:
                    continue
                key = hashlib.md5(blob).hexdigest()
                if key in seen_blobs:
                    continue
                seen_blobs.add(key)
                try:
                    t = _ocr_image_blob(blob)
                except Exception:
                    t = ""
                if t:
                    chunks.append({"content": f"[图片] {t}",
                                   "page_no": i + 1, "section_path": f"第{i+1}页"})
    return chunks


def split_markdown(text: str) -> list[dict]:
    """Markdown 纯文本 → 文本块：按空行分段落，标题行（# 开头）更新 section_path，page_no=段落序号

    ⚠️ 这里是 **唯一** 的 Markdown 切块实现，`parse_md`（落库路径）与「仅本次阅读」
    （不落库路径）必须共用它。两条路径若各自切块，「同一篇文章在两种模式下段落数不同」
    会被用户当成 bug 报上来。

    ⚠️ 换行必须先规范化：本函数的分段依据是 `"\\n\\n"`，而**网页抓取的正文与 Windows
    剪贴板内容普遍是 CRLF**。若不规范化，整篇会因找不到 `"\\n\\n"` 而退化成 1 个块
    （分块语义全丢，AI 只能看到一大坨）。读文件时 `Path.read_text` 会隐式做这个转换，
    所以此前只有文件来源、问题没暴露；一旦接入外部文本就是必现缺陷。
    """
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    chunks, section, page = [], "", 1
    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        first_line = block.splitlines()[0].strip()
        if first_line.startswith("#"):
            section = first_line.lstrip("#").strip()
        chunks.append({"content": block, "page_no": page, "section_path": section})
        page += 1
    return chunks


def parse_md(path: str) -> list[dict]:
    """Markdown 文件 → 文本块（薄封装，切块逻辑见 split_markdown）。

    内嵌图三种引用都单独 OCR 为 `[图片]` 块：
    1. 本地相对/绝对路径（`imgs/x.png`）——相对 md 所在目录解析；
    2. 站内配图 URL（`/api/assets/<sub>/<file>`）——映射到 `data/assets/<sub>/<file>`（图文笔记的主体图）；
    3. http(s) 外链——不在此处理（走配图本地化 + 防盗链代理体系）。

    若正文引用了「本地相对路径」图片、但文件都不可达（md 单独上传、图片未随迁），
    追加一条 `[图片]` 提示块引导改用「导入内容→引用原文件」。
    """
    from . import ocr as ocr_svc
    from ..core.config import settings

    try:
        text = Path(path).read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = Path(path).read_text(encoding="gbk", errors="ignore")

    chunks = split_markdown(text)

    img_ocr_available = ocr_svc.is_available()
    if not img_ocr_available:
        return chunks

    base_dir = Path(path).resolve().parent
    assets_root = (Path(settings.data_dir) / "assets").resolve()
    seen_paths: set[str] = set()
    local_refs = 0        # 正文里「本地相对路径图片引用」总数（站内 URL 不算）
    ocr_count = 0         # 成功 OCR 的图片数（含站内配图）
    # 只识别 `![alt](path)` 形式
    for target in re.findall(r"!\[[^\]]*\]\(([^)\s]+)\)", text):
        t = target.strip()
        if not t or t.lower().startswith(("http://", "https://", "data:")):
            continue
        # 去 query/fragment（图片文件路径不带它们）
        t = t.split("#", 1)[0].split("?", 1)[0]
        is_asset_url = t.startswith("/api/assets/")
        p: Path | None = None
        if is_asset_url:
            # 站内配图：/api/assets/<sub>/<file> → data/assets/<sub>/<file>
            rel = t[len("/api/assets/"):].lstrip("/")
            p = assets_root / rel
            try:
                p = p.resolve()
            except Exception:
                p = None
            # 防穿越：解析后必须仍在 assets 根内
            if p is None or assets_root not in p.parents and p != assets_root:
                p = None
        else:
            # 本地相对/绝对路径
            p = Path(t)
            if not p.is_absolute():
                p = base_dir / p
            try:
                p = p.resolve()
            except Exception:
                p = None
            if p is not None:
                local_refs += 1
        if p is None:
            continue
        key = str(p)
        if key in seen_paths:
            continue
        seen_paths.add(key)
        if not p.is_file():
            continue
        try:
            blob = p.read_bytes()
        except Exception:
            continue
        try:
            octext = _ocr_image_blob(blob)
        except Exception:
            octext = ""
        if octext:
            ocr_count += 1
            # 挂到「图片所在段落」的 page_no + 继承最近的标题章节
            ipage, isection = _md_img_loc(text, t)
            chunks.append({"content": f"[图片] {octext}",
                           "page_no": ipage,
                           "section_path": isection})

    # 引用了「本地相对路径」图片却一张都没识别出来 → 极可能是「md 单独上传、图片未随迁」
    if local_refs > 0 and ocr_count == 0:
        chunks.append({
            "content": f"[图片] ⚠️ 本文引用了 {local_refs} 张本地图片，但图片未随文件一起导入，"
                       f"未能识别。可改用「导入内容 → 引用原文件」方式导入，即可识别图片中的文字。",
            "page_no": 1,
            "section_path": "",
        })
    return chunks


def _md_img_loc(text: str, target: str) -> tuple[int, str]:
    """定位图片所属段落序号与章节路径（对齐 split_markdown 的切割口径）。

    在按空行切出的段落里找含该图引用的段落，返回其 1-based 序号 + 该处生效的最近标题；
    找不到返回 (1, "")。仅用于给 `[图片]` 块合理位置，不要求精确。
    """
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    page, section = 1, ""
    for block in text.split("\n\n"):
        b = block.strip()
        if not b:
            continue
        first_line = b.splitlines()[0].strip()
        if first_line.startswith("#"):
            section = first_line.lstrip("#").strip()
        if target in b:
            return page, section
        page += 1
    return 1, ""


# ---------- EPUB ----------

def parse_epub(path: str, on_progress=None) -> list[dict]:
    """EPUB → 按 spine 章节顺序的文本块（page_no = 章节序号，section_path = 章节标题）

    正文抽成 Markdown 风格块行（标题 / 列表 / 表格 / 引用），与 docx 同形态：
    **一个结构块行 = 一个 chunk**，不做二次预打包。
    理由：切块职责在 RAG 层（vector.split_text 已按段落/句边界贪婪打包到 chunk_size
    并做句级重叠），parser 层再打包会形成两层切分、重叠内容重复入库；
    且标题若与正文并进同一 chunk，前端 renderBlock 的多行判定会让 `#` 标记现出原形。
    含 DRM 或结构损坏时抛 ValueError（中文原因，上层标记 failed 直接展示给用户）。

    内嵌图 OCR：章节内 `<img>` 单独 OCR，产出 `[图片]` 标记的独立块（挂对应章节）。
    - 按 zip 内路径去重（同图多章引用只认一次）。
    - 这是解决「纯图片漫画 / 影印版 EPUB 提取不到文本」的关键补漏。
    """
    from . import epub as epub_svc
    from . import ocr as ocr_svc

    img_ocr_available = ocr_svc.is_available()
    seen_imgs: set[str] = set()

    # 纯「[图：alt]」占位块（图片单独成段、无文字）现在由 `[图片]` OCR 块承接，
    # 不再作为独立块入向量库（避免 alt 噪声向量）。行内图文混排的段仍保留 alt 占位。
    _SOLO_IMG_PLACEHOLDER = re.compile(r"^\[图：[^\]]*\]$")

    with epub_svc.EpubBook(path) as book:
        chapters = book.chapters()
        if on_progress:
            on_progress(5)
        total = len(chapters) or 1
        chunks: list[dict] = []
        for ch in chapters:
            lines = book.blocks(ch["href"])
            # 目录没给标题时：用正文首个标题兜底，再退化为「第 N 章」
            title = ch["title"]
            if not title:
                title = next((ln.lstrip("#").strip() for ln in lines if ln.startswith("#")), "")
            title = title or f"第 {ch['index']} 章"
            for ln in lines:
                t = ln.strip()
                if not t:
                    continue
                # 仅当 OCR 可用时才过滤纯占位块：`[图片]` 块会承接图内容；
                # OCR 缺失时保留占位，避免图片信息完全丢失。
                if img_ocr_available and _SOLO_IMG_PLACEHOLDER.match(t):
                    continue
                chunks.append({"content": ln, "page_no": ch["index"], "section_path": title})
            # 内嵌图 OCR（漫画/影印版补漏）
            if img_ocr_available:
                for img_path in book.images(ch["href"]):
                    if img_path in seen_imgs:
                        continue
                    seen_imgs.add(img_path)
                    try:
                        blob = book.read(img_path)
                    except Exception:
                        blob = None
                    if not blob:
                        continue
                    try:
                        t = _ocr_image_blob(blob)
                    except Exception:
                        t = ""
                    if t:
                        chunks.append({"content": f"[图片] {t}",
                                       "page_no": ch["index"],
                                       "section_path": title})
            if on_progress:
                on_progress(min(99, int(ch["index"] / total * 100)))
    return chunks


# ---------- 音视频转写（V2：faster-whisper 本地 ASR） ----------

# Whisper 转写并发策略（V3：多路并发 + 分核）：
# - V1 无锁：文件夹批量导入 N 路并发争抢 CPU + 无锁首次加载创建 N 个模型实例 → 每个都被拖慢
# - V2 互斥锁：一次只转一个（单文件独占全部核最快），但多文件完全串行、浪费多核并行能力
# - V3 固定并发路数：每路一个模型实例、独占 cpu_count//路数 线程（16 核 → 2 路 × 8 线程），
#   同时转 2 个文件，总吞吐提升 ~45%，又不至于并发失控。
_TRANSCRIBE_CONCURRENCY = 2
_whisper_slots = [None] * _TRANSCRIBE_CONCURRENCY      # 每路模型实例（懒加载）
_whisper_slots_size = [None] * _TRANSCRIBE_CONCURRENCY
_whisper_slots_busy = [False] * _TRANSCRIBE_CONCURRENCY
_whisper_slots_lock = threading.Lock()
_transcribe_sem = threading.BoundedSemaphore(_TRANSCRIBE_CONCURRENCY)


def reset_whisper_cache():
    """清空 whisper 模型缓存（下载完成 / 切换默认版本后调用，下次转写重新加载）"""
    with _whisper_slots_lock:
        for i in range(_TRANSCRIBE_CONCURRENCY):
            _whisper_slots[i] = None
            _whisper_slots_size[i] = None



def _load_whisper_slot(slot: int):
    """为指定槽加载模型实例（懒加载，每路独立实例、独占 cpu_count//路数 线程）。
    按 asr.preferred_size() 选择版本（用户配置优先，否则 small→base）；
    以 model.bin 体积>20MB 判定已安装，避免半截残留文件被误加载。"""
    from . import asr as asr_svc
    from ..core.config import settings
    import os

    # 同槽 double-check：借槽线程已在锁外串行初始化本槽，基本不会走到，防御即可
    with _whisper_slots_lock:
        if _whisper_slots[slot] is not None:
            return _whisper_slots[slot]

    wanted = asr_svc.preferred_size()
    cpu_threads = max(2, (os.cpu_count() or 4) // _TRANSCRIBE_CONCURRENCY)

    from faster_whisper import WhisperModel
    model_dir = settings.data_dir / "models"
    model_dir.mkdir(exist_ok=True)

    order = [wanted] if wanted else ["small", "base"]
    model = None
    for size in order:
        if asr_svc.is_installed(size):
            local = model_dir / f"faster-whisper-{size}"
            print(f"[asr] 加载转写路 {slot + 1}/{_TRANSCRIBE_CONCURRENCY}：faster-whisper-{size}（{cpu_threads} 线程）...")
            model = WhisperModel(str(local), device="cpu", compute_type="int8", cpu_threads=cpu_threads)
            _whisper_slots_size[slot] = size
            break

    if model is None:
        os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
        print("[asr] 本地无模型，从镜像下载 whisper base...")
        model = WhisperModel("base", device="cpu", compute_type="int8",
                             download_root=str(model_dir))
        _whisper_slots_size[slot] = "base"

    with _whisper_slots_lock:
        _whisper_slots[slot] = model
    return model


def _acquire_whisper():
    """借一个转写槽（信号量已保证同时借出 ≤ 并发路数）。返回 (slot, model)；
    用完必须 _release_whisper(slot)。首次借某槽时懒加载该路模型。"""
    slot = -1
    with _whisper_slots_lock:
        for i in range(_TRANSCRIBE_CONCURRENCY):
            if not _whisper_slots_busy[i]:
                _whisper_slots_busy[i] = True
                slot = i
                break
    if slot < 0:   # 理论上不会发生（信号量限流），防御
        raise RuntimeError("无可用转写槽")
    if _whisper_slots[slot] is None:
        _load_whisper_slot(slot)
    return slot, _whisper_slots[slot]


def _release_whisper(slot: int):
    with _whisper_slots_lock:
        _whisper_slots_busy[slot] = False


def parse_media(path: str, on_progress=None) -> list[dict]:
    """音视频转写：按 whisper 分段合并为 ~1 分钟一个块，page_no=分钟序号

    提速要点：beam_size=1 贪心解码（比默认 beam=5 快约 2 倍，质量损失可忽略）
    并发策略：信号量限流到 _TRANSCRIBE_CONCURRENCY 路（默认 2），每路独占一份模型实例
    （cpu_count//路数 线程）——多文件并行转写、总吞吐高于串行，又不互相抢死 CPU。
    on_progress：0-100 进度回调（按已转写时长/总时长估算）
    """
    with _transcribe_sem:
        slot, model = _acquire_whisper()
        try:
            segments, info = model.transcribe(
                path, language="zh", vad_filter=True, beam_size=1,
                initial_prompt="以下是简体中文普通话的语音内容，请用简体中文转写，注意标点。",  # 抑制繁体倾向
            )
            duration = getattr(info, "duration", 0) or 0

            chunks = []
            cur_texts, cur_start = [], 0.0
            minute = 1
            for seg in segments:
                if not cur_texts:
                    cur_start = seg.start
                cur_texts.append(seg.text.strip())
                # 每 ~60 秒或累积 ~500 字出一个块
                if on_progress and duration:
                    on_progress(min(99, int(seg.end / duration * 100)))
                if seg.end - cur_start >= 60 or sum(len(t) for t in cur_texts) >= 500:
                    chunks.append({
                        "content": "".join(cur_texts),
                        "page_no": minute,
                        "section_path": f"{minute} 分钟",
                    })
                    cur_texts = []
                    minute += 1
            if cur_texts:
                chunks.append({"content": "".join(cur_texts), "page_no": minute,
                               "section_path": f"{minute} 分钟"})
        finally:
            _release_whisper(slot)
    return chunks


PARSERS = {"pdf": parse_pdf, "docx": parse_docx, "doc": parse_docx, "pptx": parse_pptx, "ppt": parse_pptx,
           "md": parse_md, "markdown": parse_md, "epub": parse_epub,
           "mp3": parse_media, "wav": parse_media, "m4a": parse_media, "mp4": parse_media,
           "jpg": parse_image, "jpeg": parse_image, "png": parse_image, "webp": parse_image, "bmp": parse_image}

MEDIA_FORMATS = {"mp3", "wav", "m4a", "mp4"}

IMAGE_FORMATS = {"jpg", "jpeg", "png", "webp", "bmp"}

# 需要进度回调的格式（OCR / 转写 / 多章节解包慢，走 on_progress）
SLOW_FORMATS = {"pdf", "epub"} | IMAGE_FORMATS


def parse_file(path: str, fmt: str, on_progress=None) -> list[dict]:
    parser = PARSERS.get(fmt.lower())
    if not parser:
        raise ValueError(f"不支持的格式: {fmt}")
    if fmt.lower() in SLOW_FORMATS:
        return parser(path, on_progress=on_progress)
    return parser(path)
