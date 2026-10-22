"""标题层级识别：给「没有书签」的材料（扫描件 / 拍照页）兜底 section_path。

## 为什么需要它

`parser._pdf_section_marks()` 只能读 PDF 书签。扫描书基本没有书签 → section_path 全空，
入库后的章节树是平的，学习页目录、讲义、视频锚点都拿不到「这一句属于哪一节」。

## 判据优先级（实测得出，别凭感觉调）

对 249 页技术书（173 条书签做 ground truth）实测：

| 判据 | 召回 | 层级一致 |
|---|---|---|
| 仅字号（> 正文×1.15） | 28.1% | — |
| 编号正则 + 行长 + 句末约束 | **100%** | **100%** |

该书三级标题（`1.3.1 第一步：找到自己的糖山`）字号与正文同为 10.0 号 ——
**只靠字号必然漏掉大半**。所以：编号是第一判据，字号只做「无编号」时的兜底。

## 三个必须排除的误判来源

1. **页眉**：扫描页 OCR 的第一行常是「第2章 051 不懂AI技术做不好AI产品经理」，
   长得就像章标题，但它其实是左章节名 + 页码 + 书名。靠两条判据剔除：
   位于页面顶部（y0 < TOP_MARGIN）或**跨页高频重复**（同一行出现在 ≥5 页）。
2. **目录页**：整页都是编号行，还带 `......` 引导点 → 引导点直接判非标题；
   同时目录页本身没有「章节归属」，不影响后续正文。
3. **正文列举**：`一、xxx` 这种可能是正文里的列表项。用「行长 ≤ 34 字且不以句号结尾」
   压住大部分；仍有残留，见 RISK 说明。

RISK：无编号、字号又与正文相同的真标题（如散文集的分节留白）识别不出来 ——
这类材料需要版面模型或视觉模型，不在本模块职责内。
"""
import re

# 标题行的最大字数。中文书标题很少超过这个长度，再长基本是正文句子。
MAX_TITLE_CHARS = 34

# 页面上下安全区：落在里面的行不参与标题判定（页眉/页脚/页码）
TOP_MARGIN = 0.06
BOTTOM_MARGIN = 0.94

# 同一行文本出现在多少页以上，判定为页眉/页脚（与位置判据互补）
REPEAT_PAGES = 5

_DASH_DOTS = ("……", "....", "····", "－－", "——")

# 顺序即优先级：先长后短，避免 `1.3.1` 被 `1.3` 的规则吃掉
_PATTERNS = (
    (re.compile(r"^第\s*[0-9一二三四五六七八九十百零〇]{1,4}\s*[章篇部]"), 1),
    (re.compile(r"^\d{1,2}\s*\.\s*\d{1,2}\s*\.\s*\d{1,2}"), 3),
    (re.compile(r"^\d{1,2}\s*\.\s*\d{1,2}(?!\s*\.\s*\d)"), 2),
    (re.compile(r"^[（(][一二三四五六七八九十]{1,3}[）)]"), 3),
    (re.compile(r"^[一二三四五六七八九十]{1,3}\s*[、．]"), 2),
)

# 标题里不该保留的标点：OCR 会把「1.3.1 第一步」识别成「1.3.1  第一步」，不影响判据但脏
_WS = re.compile(r"[ \t\u3000]+")

# 纯页码行（目录页判据用）：`001` / `25`
_PAGE_NO = re.compile(r"^\d{1,4}$")

# 编号紧贴正文时补空格：`1.2AI产品` / `第1章AI产品`  （回顾断言：后面不是空格/数字/点）
_NUMBER_THEN_TEXT = re.compile(
    r"^((?:第\s*[0-9一二三四五六七八九十百零〇]{1,4}\s*[章篇部])|(?:\d{1,2}(?:\.\d{1,2})*))(?=[^\s\d.])"
)


def clean_title(s: str) -> str:
    """规范化标题：压掉 OCR 多余空格，并在编号与文字之间补一个空格。

    `1.2AI产品和AI产品经理` → `1.2 AI产品和AI产品经理`（只为 section_path 可读，
    匹配时仍会去掉全部空格，所以不影响判据）。
    """
    t = _WS.sub(" ", s or "").strip()
    return _NUMBER_THEN_TEXT.sub(r"\1 ", t)


def detect_level(text: str, height: float | None = None,
                 body_height: float | None = None,
                 y0: float | None = None, repeated: bool = False,
                 size_fallback: bool = False) -> int:
    """判断一行是几级标题。0 = 不是标题（正文）。

    height / body_height：行高（扫描件用 OCR 行框高度，文字层用字号），无编号时兜底。
    y0：行顶在页内的归一化位置（0~1），用于排除页眉页脚。
    repeated：该行是否跨页高频重复（页眉判据，由 collect_repeated() 统一算）。
    size_fallback：**默认关闭**。实测该书字号兜底会把目录页码「001」、扉页「张俊林 王斌 编著」
        判成二级标题（467 条判定里只有 173 条是书签标题）→ 误报率 63%。
        只有确认材料「整本无编号」时才打开。
    """
    t = clean_title(text)
    if not t:
        return 0
    if y0 is not None and (y0 < TOP_MARGIN or y0 > BOTTOM_MARGIN):
        return 0
    if repeated:
        return 0
    if len(t) > MAX_TITLE_CHARS:
        return 0
    if t.endswith(("。", "！", "？", "，", "；", ".", "!", "?", ",")):
        return 0
    if any(d in t for d in _DASH_DOTS):
        return 0
    for pat, level in _PATTERNS:
        if pat.match(t):
            return level
    if size_fallback and height and body_height:
        if height > body_height * 1.35:
            return 1
        if height > body_height * 1.18:
            return 2
    return 0


def is_toc_page(lines: list[dict]) -> bool:
    """整页判为目录页（目录页不能参与标题判定，否则会把全书章节提前「预演」一遍）。

    判据：行数够多，且「编号行 + 纯页码行」过半。
    ⚠️ 必须把**纯页码行**算进来：目录页的页码常被排版成独立一行（实测该书目录页
       43 行 = 21 条编号 + 21 个页码 + 页头），只看编号行只有 49%，刚好卡在 0.5 之下漏判。
    """
    if len(lines) < 10:
        return False
    hits = 0
    for ln in lines:
        t = clean_title(ln.get("t", ""))
        if _PAGE_NO.match(t) or any(p.match(t) for p, _ in _PATTERNS):
            hits += 1
    return hits / len(lines) > 0.5


def merge_wrapped_title(lines: list[dict]) -> list[dict]:
    """把被排版拆成两行的标题拼回去（`第1 章` + `AI 产品经理——不是简单的…`）。

    实测该书每章首页只有两行：`第1 章`（3 字）与副标题（17 字）。不合并的话
    section_path 会变成「第1 章」，读者根本不知道是哪一章；扫描件 OCR 同样会拆行。

    合并条件（全部满足才合并，宁可少合不错合）：
      1. 当前行是编号行且极短（≤6 字，例如 `第1章`/`1.3`）
      2. 下一行不以句读结尾、合并后不超长
      3. 两行紧邻（纵向间距 < 2 倍行高）
    只返回**新列表**，不改动传入的行（行框仍按原始行保存，定位不受影响）。
    """
    out: list[dict] = []
    i = 0
    while i < len(lines):
        cur = dict(lines[i])
        t = clean_title(cur.get("t", ""))
        short_numbered = len(t) <= 6 and any(p.match(t) for p, _ in _PATTERNS)
        if short_numbered and i + 1 < len(lines):
            nxt = lines[i + 1]
            nt = clean_title(nxt.get("t", ""))
            gap = float(nxt.get("y0", 0)) - float(cur.get("y0", 0))
            line_h = float(cur.get("h") or 0.02)
            if (nt and not nt.endswith(("。", "！", "？", "，", "；", ".", "!", "?"))
                    and len(t) + len(nt) <= MAX_TITLE_CHARS
                    and 0 <= gap < max(line_h * 2, 0.06)):
                cur["t"] = f"{t} {nt}"
                out.append(cur)
                i += 2
                continue
        out.append(cur)
        i += 1
    return out


def collect_repeated(pages: list[list[dict]], min_pages: int = REPEAT_PAGES) -> set[str]:
    """扫全书，返回「跨页高频重复」的行文本集合（页眉/页脚/书名）。

    pages: 每页的行列表，元素形如 {"t": str, ...}
    ⚠️ 只比长度 ≥6 的归一化文本：页码「051」这种短行会被位置判据处理掉，
       在这里比较反而容易误伤正常正文。
    """
    seen: dict[str, set[int]] = {}
    for pi, lines in enumerate(pages):
        for ln in lines:
            key = _WS.sub("", ln.get("t", ""))
            if len(key) < 6:
                continue
            seen.setdefault(key, set()).add(pi)
    return {k for k, v in seen.items() if len(v) >= min_pages}


class OutlineBuilder:
    """章节栈：喂标题行，吐 `第1章/1.3/1.3.1` 形式的路径（与书签路径同构）。"""

    def __init__(self, depth: int = 3):
        self.depth = depth
        self.stack = [""] * depth

    def path(self) -> str:
        return "/".join(s for s in self.stack if s)

    def apply(self, level: int, text: str) -> None:
        if level <= 0:
            return
        level = min(level, self.depth)
        self.stack[level - 1] = clean_title(text)
        for i in range(level, self.depth):
            self.stack[i] = ""

    def feed(self, text: str, **kw) -> int:
        """吃一行，若是标题则更新栈并返回层级；否则返回 0 且不改状态。"""
        level = detect_level(text, **kw)
        if level:
            self.apply(level, text)
        return level
