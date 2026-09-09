"""文本去噪管道：夹在 parser（解析）与 chunk 入库之间

四条规则（全部本地零成本）：
1. 页眉页脚：PDF/PPTX 等按页块，首行/尾行在 ≥60% 页面重复 → 剔除；另剔页码模式行
2. 水印：短行（<30 字）在 ≥50% 页面重复 → 判定文字型水印剔除
3. 乱码：行内替换符 U+FFFD / 私有区 PUA / 控制字符占比 >2% → 剔除该行
4. 重复：块内容归一化（去空白标点）后 MD5 精确去重，同材料内只留首个

输出 (cleaned_chunks, report)；report 记录各类剔除数量，供日志与后续前端提示。
设计取舍：不引入 pdfplumber 版面分析 / simhash 近似去重（依赖体积与复杂度不值），
用跨页频次统计 + 精确去重覆盖中文规整语料的大头。
"""
import hashlib
import re
import unicodedata

# 页眉页脚/水印判定门槛
_MIN_PAGES = 4            # 页数太少时不做跨页统计（误伤风险高）
_HEADER_FOOTER_RATIO = 0.6
_WATERMARK_RATIO = 0.5
_WATERMARK_MAX_LEN = 30
_EDGE_LINE_MAX_LEN = 50   # 只有短行才可能是页眉页脚
_GARBLED_RATIO = 0.02

# 页码模式：纯数字、"- 12 -"、"第 12 页"、"第 12 页 共 30 页"、"12 / 30"
_PAGE_NO_RES = [
    re.compile(r"^\d{1,4}$"),
    re.compile(r"^[-—–·\s]*\d{1,4}[-—–·\s]*$"),
    re.compile(r"^第\s*\d+\s*页(\s*[/共]\s*\d+\s*页?)?$"),
    re.compile(r"^\d{1,4}\s*/\s*\d{1,4}$"),
]

# 归一化：去全部空白与标点，用于重复判定
_NORMALIZE_RE = re.compile(r"[\s　，。！？；：、,.!?;:·…—\-—'\"“”‘’（）()【】\[\]<>《》]+")

# 句末标点：水印/页眉页脚候选行不能带（否则正文重复句会被误判）
_SENT_END_RE = re.compile(r"[。！？!?…]$")


def _is_page_no_line(line: str) -> bool:
    return any(r.match(line) for r in _PAGE_NO_RES)


def _garbled_ratio(line: str) -> float:
    """乱码字符占比：替换符 / 私有区 / 未指派 / 控制字符（除 \t）"""
    if not line:
        return 0.0
    bad = 0
    for ch in line:
        cp = ord(ch)
        if cp == 0xFFFD or 0xE000 <= cp <= 0xF8FF:
            bad += 1
            continue
        cat = unicodedata.category(ch)
        if cat in ("Cc", "Cn") and ch != "\t":
            bad += 1
    return bad / len(line)


def _normalize(text: str) -> str:
    return _NORMALIZE_RE.sub("", text)


def _line_level_clean(text: str, report: dict, garbled: bool = True, page_no: bool = True) -> str:
    """行级清洗：乱码行 + 页码模式行（各自受开关控制）。返回清洗后文本。"""
    if not garbled and not page_no:
        return text
    kept = []
    for line in text.split("\n"):
        s = line.strip()
        if not s:
            kept.append("")
            continue
        if garbled and _garbled_ratio(s) > _GARBLED_RATIO and _garbled_ratio(s) * len(s) >= 2:
            report["garbled_lines"] += 1
            continue
        if page_no and len(s) <= 20 and _is_page_no_line(s):
            report["page_no_lines"] += 1
            continue
        kept.append(line)
    return "\n".join(kept)


def _load_opts() -> dict:
    """清洗开关：设置页配置优先，回落默认全开"""
    from . import settings_store
    conf = settings_store.load()
    return {
        "header_footer": bool(conf.get("clean_header_footer", True)),
        "watermark": bool(conf.get("clean_watermark", True)),
        "garbled": bool(conf.get("clean_garbled", True)),
        "dedup": bool(conf.get("clean_dedup", True)),
    }


def clean_chunks(chunks: list[dict], fmt: str = "", opts: dict | None = None) -> tuple[list[dict], dict]:
    """对 parser 输出的块列表做去噪。返回 (清洗后块列表, 清洗报告)。

    chunks: [{"content", "page_no", "section_path"}]，块级结构保持不变（只改 content）。
    opts: {"header_footer", "watermark", "garbled", "dedup"} 开关，缺省读设置页配置（默认全开）。
    顺序：先行级清洗（乱码/页码）→ 再跨页统计（页眉页脚/水印）→ 最后块级去重，
    保证频次统计基于干净文本，且乱码行不会被水印规则抢先吞掉。
    """
    o = {**_load_opts(), **(opts or {})} if opts else _load_opts()
    report = {"header_footer_lines": 0, "watermark_lines": 0,
              "garbled_lines": 0, "page_no_lines": 0, "dup_blocks": 0,
              "emptied_blocks": 0}
    if not chunks:
        return chunks, report

    # ---- 第零遍：换行符归一化（Windows 文档常见 \r\n，不归一会带入向量和阅读器）----
    chunks = [{**c, "content": c["content"].replace("\r\n", "\n").replace("\r", "\n")}
              for c in chunks]

    # ---- 第一遍：行级清洗（乱码行 + 页码模式行；页码归入页眉页脚开关）----
    chunks = [{**c, "content": _line_level_clean(c["content"], report,
                                                 garbled=o["garbled"],
                                                 page_no=o["header_footer"])}
              for c in chunks]

    # ---- 跨页频次统计（页眉页脚 & 水印），仅对按页组织的格式 ----
    # 同 page_no 可能多块（docx/md），跨页统计只对 pdf/pptx/ppt 有意义
    page_fmt = fmt.lower() in ("pdf", "pptx", "ppt")
    header_footer, watermarks = set(), set()
    if page_fmt and len(chunks) >= _MIN_PAGES and (o["header_footer"] or o["watermark"]):
        n = len(chunks)
        edge_count: dict[str, int] = {}   # 首行/尾行 → 出现块数
        short_count: dict[str, int] = {}  # 全部短行 → 出现块数（水印判定）
        for c in chunks:
            lines = [l.strip() for l in c["content"].split("\n") if l.strip()]
            if not lines:
                continue
            seen_edge, seen_short = set(), set()
            for line in (lines[0], lines[-1]):
                if (0 < len(line) <= _EDGE_LINE_MAX_LEN and not _SENT_END_RE.search(line)
                        and line not in seen_edge):
                    edge_count[line] = edge_count.get(line, 0) + 1
                    seen_edge.add(line)
            for line in lines:
                if (0 < len(line) <= _WATERMARK_MAX_LEN and not _SENT_END_RE.search(line)
                        and line not in seen_short):
                    short_count[line] = short_count.get(line, 0) + 1
                    seen_short.add(line)
        if o["header_footer"]:
            header_footer = {l for l, n_hit in edge_count.items() if n_hit >= n * _HEADER_FOOTER_RATIO}
        if o["watermark"]:
            watermarks = {l for l, n_hit in short_count.items()
                          if n_hit >= n * _WATERMARK_RATIO and l not in header_footer}

    # ---- 第二遍：剔除页眉页脚/水印行 + 块级精确去重 ----
    cleaned = []
    seen_hashes: set[str] = set()
    for c in chunks:
        text = c["content"]
        if header_footer or watermarks:
            kept = []
            for l in text.split("\n"):
                s = l.strip()
                if s in header_footer:
                    report["header_footer_lines"] += 1
                elif s in watermarks:
                    report["watermark_lines"] += 1
                else:
                    kept.append(l)
            text = "\n".join(kept)

        # 压缩多余空行
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if not text:
            report["emptied_blocks"] += 1  # 清洗后整页/整块为空（如纯水印页），单独计数
            continue

        # ---- 规则 4：块级精确去重（归一化后 MD5）----
        if o["dedup"]:
            digest = hashlib.md5(_normalize(text).encode()).hexdigest()
            if digest in seen_hashes:
                report["dup_blocks"] += 1
                continue
            seen_hashes.add(digest)

        cleaned.append({**c, "content": text})

    return cleaned, report
