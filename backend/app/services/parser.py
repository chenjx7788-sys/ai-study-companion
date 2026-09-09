"""文件解析服务：PDF / Word / PPT / Markdown / 图片 / 音视频 → 结构化文本块（含页码与章节路径）

输出统一为 [{"content": str, "page_no": int, "section_path": str}, ...]
扫描版 PDF（无文本层）与图片走 RapidOCR 本地识别；OCR 失败/依赖缺失时返回空，由上层标记"扫描件"。
音视频走 faster-whisper 本地 ASR 转写，page_no = 分钟序号（第 N 分钟）。
Markdown 按空行分段落，page_no = 段落序号，标题作为 section_path。
"""
from pathlib import Path
from bisect import bisect_right
import threading

from pypdf import PdfReader
from docx import Document
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

    on_progress：0-100 进度回调（按已处理页数/总页数估算）。
    """
    reader = PdfReader(path)
    mark_pages, mark_paths = _pdf_section_marks(reader)
    chunks = []
    total = len(reader.pages) or 1
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


def parse_docx(path: str) -> list[dict]:
    """DOCX 按段落出块。page_no 修复：优先按显式分页符翻页；
    文档无分页符时按 ~700 字/页估算（python-docx 拿不到真实页码，估算供定位参考）"""
    doc = Document(path)
    has_breaks = any(_docx_page_breaks(p) for p in doc.paragraphs)
    chunks, page, section, chars = [], 1, "", 0
    for para in doc.paragraphs:
        text = para.text.strip()
        n_breaks = _docx_page_breaks(para)
        if text:
            if para.style.name.startswith("Heading"):
                section = text
            chunks.append({"content": text, "page_no": page, "section_path": section})
            chars += len(text)
            if not has_breaks:
                page = 1 + chars // 700   # 估算页码
        page += n_breaks   # 分页符通常在段落末尾/空段落，文本归属当前页后翻页
    return chunks


def parse_pptx(path: str) -> list[dict]:
    prs = Presentation(path)
    chunks = []
    for i, slide in enumerate(prs.slides):
        texts = [s.text_frame.text.strip() for s in slide.shapes if s.has_text_frame and s.text_frame.text.strip()]
        if texts:
            chunks.append({"content": "\n".join(texts), "page_no": i + 1, "section_path": f"第{i+1}页"})
    return chunks


def parse_md(path: str) -> list[dict]:
    """Markdown 纯文本：按空行分段落，标题行（# 开头）更新 section_path，page_no=段落序号"""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = Path(path).read_text(encoding="gbk", errors="ignore")

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
           "md": parse_md, "markdown": parse_md,
           "mp3": parse_media, "wav": parse_media, "m4a": parse_media, "mp4": parse_media,
           "jpg": parse_image, "jpeg": parse_image, "png": parse_image, "webp": parse_image, "bmp": parse_image}

MEDIA_FORMATS = {"mp3", "wav", "m4a", "mp4"}

IMAGE_FORMATS = {"jpg", "jpeg", "png", "webp", "bmp"}

# 需要进度回调的格式（OCR / 转写慢，走 on_progress）
SLOW_FORMATS = {"pdf"} | IMAGE_FORMATS


def parse_file(path: str, fmt: str, on_progress=None) -> list[dict]:
    parser = PARSERS.get(fmt.lower())
    if not parser:
        raise ValueError(f"不支持的格式: {fmt}")
    if fmt.lower() in SLOW_FORMATS:
        return parser(path, on_progress=on_progress)
    return parser(path)
