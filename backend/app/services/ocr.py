"""OCR 引擎管理：RapidOCR（ONNX 版 PaddleOCR）懒加载 + 图片识别

模型随 rapidocr_onnxruntime wheel 打包（PP-OCRv3 det/rec/cls + config.yaml），
无需下载、无需 launcher 预置（区别于 Whisper/BGE 的外部模型文件）。
识别结果按阅读顺序（自上而下、自左而右）排序后返回 [(text, score)]。
"""
import threading

_engine = None
_lock = threading.Lock()


def is_available() -> bool:
    """OCR 依赖是否已安装。未安装时上层降级为「标记扫描件」而非报错。"""
    try:
        import rapidocr_onnxruntime  # noqa: F401
        return True
    except Exception:
        return False


def _get_engine():
    """懒加载 RapidOCR 引擎（首次识别才初始化，约 0.4s，节省常驻内存）"""
    global _engine
    if _engine is None:
        with _lock:
            if _engine is None:
                from rapidocr_onnxruntime import RapidOCR
                _engine = RapidOCR()
    return _engine


def reset_ocr_cache():
    """清空引擎缓存（模型路径/版本变更后调用）"""
    global _engine
    with _lock:
        _engine = None


def recognize(img) -> list[tuple[str, float]]:
    """识别图片（路径 str / numpy 数组 / bytes），返回按阅读顺序排列的 [(text, score)]

    RapidOCR 返回 [box(4点), text, score_str]，检测顺序大体即阅读顺序，
    这里按「行中心 y → 行中心 x」再排一次，保证多栏/乱序场景也稳定。
    """
    engine = _get_engine()
    result, _ = engine(img)
    if not result:
        return []
    items = []
    for box, text, score in result:
        try:
            s = float(score)
        except (TypeError, ValueError):
            s = 0.0
        cy = sum(p[1] for p in box) / len(box)
        cx = sum(p[0] for p in box) / len(box)
        items.append((cy, cx, text, s))
    items.sort(key=lambda it: (it[0], it[1]))
    return [(t, s) for _, _, t, s in items]


def ocr_image_to_text(img) -> str:
    """识别并拼接为文本（逐行换行）；空白/模糊图片返回空串"""
    return "\n".join(t for t, _ in recognize(img) if t.strip())
