"""把材料分块渲染成「自己看舒服 + 能交给别人」的 Markdown。

## 为什么单独成模块

导出不是「把 content 拼起来」。原始 content 是**按页的 OCR/抽取流水**，直接拼会有四个问题
（均为实测，见 `_probe_md_export.py`）：

| 问题 | 实测 |
|---|---|
| 没有标题层级 | 4 页产物 **0 个 `#`**，章节层级明明已经在 `section_path` 里 |
| 硬换行没合并 | 98 行里 **53% 短于 20 字**，仅 18% 以句末标点结尾（「实质生 / 产的高原期」被切断） |
| 目录页混进正文 | 目录页 OCR 出 20+ 行标题清单，原样落在正文开头 |
| 图内文字丢一半 | 剪藏材料 `kind=img_ocr` 的 85 块被前端过滤，导出时直接消失 |

## 产物只需一套（不为「自己看 / 外发」做两版）

三处冲突都用「渲染时不可见、解析时可读」的写法化解：
- YAML front-matter：Typora / Obsidian 不渲染，其他系统能读来源与页数
- 页码锚 `<!-- P12 -->`：HTML 注释，阅读时看不见，机器能定位
- 图内文字：书籍内嵌图**原位**转引用块（位置有意义），剪藏图文字收进文末附录（不丢）

## 段落合并为什么不能只看标点

只看「行尾不是句号就拼下一行」，会把图里的标签粘成一坨（实测那页的
「关注度 / 过高期望的峰值期 / 时间」会被连成一个长句）。所以要求**同时满足**
「上一行接近满行宽」——被排版折断的行必然排满整栏，而标签/短标题不会。

满行宽怎么判：该页有 `line_boxes` 且**行数对得上**时用 `x1 >= 0.88`（右侧贴到边距）；
否则退化成「显示宽度 ≥ 该页行长 P75 与全书 P85×0.9 的较大者」。
⚠️ `line_boxes` 与 content 的行数**可能不一致**（`cleaner` 会按行删乱码/页码），
所以必须先比行数、再抽检前 3 行文本，任一不符就放弃精确判据 —— 否则会整体错位一行，
把「满行」判到隔壁行上，产物看起来正常但段落全错。
"""
import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path

from . import outline

# 句末标点：这些行尾是自然断句，不该与下一行合并。
# ⚠️ **中文逗号「，」刻意不在内** —— 排版折行最常发生在逗号后（「…技术脉络，/ 才不会迷失」），
#    把它算作句末会让超过一半的折行漏合并。
_SENT_END = ("。", "！", "？", "；", "：", "…", "!", "?", ";", ":")

# 这些开头的行是结构性内容，绝不能被上一行「吸走」
_NO_JOIN_START = ("|", "-", "*", ">", "#", "·", "•", "【", "［", "[")

# 列表项开头（`1）` / `（2）` / `一、` / `- `）。**连续多条都命中时整段不按图表标签保护**，
# 否则同一个编号列表会被切成「第一条是正文、后几条是引用块」（实测 249 页那本出现 6 处）。
_LIST_START = re.compile(
    r"^(?:\d{1,2}\s*[）)]|[（(]\s*\d{1,2}\s*[）)]"
    r"|[（(]\s*[一二三四五六七八九十]{1,3}\s*[）)]"
    r"|[一二三四五六七八九十]{1,3}\s*[、．.]"
    r"|[-*·•]\s)")

_IMAGE_PREFIX = re.compile(r"^\[图片\]\s*")
_WS = re.compile(r"\s+")

# 行首的 Markdown 标题标记（`## 一、产品定价`）。剪藏 md / docx / epub 的正文里**自带**
# 这种行，而同一块的 `section_path` 是剥掉 `#` 的纯文本 —— 不剥就永远比不上，
# 标题既由 `_heading_blocks` 输出一次、又当正文行输出一次（实测 20 份材料命中）。
#
# ⚠️ `#` 后**必须有空格**：小红书的 `#AI大模型[话题]# #智能体[话题]#` 是话题标签，
#    用 `\s*` 会把整行当标题（实测 40 号）。docx/epub 产出的标题都带空格，不受影响。
_HEAD_MARK = re.compile(r"^#{1,6}\s+")


def _is_table_row(s: str) -> bool:
    """`| a | b |` 形式的表格行。Markdown 表格要求行与行**紧邻**，中间插空行即失效。"""
    t = (s or "").strip()
    return len(t) > 2 and t.startswith("|") and t.endswith("|")

# 纯页码行（目录清单里的 «002»），目录页判据用
_PAGE_NO = re.compile(r"^\d{1,4}$")

# line_boxes 的 x1 达到这个值视为「排满整行」（右侧到版心边距）
_FULL_LINE_X1 = 0.88


def _norm(s: str) -> str:
    return _WS.sub("", s or "")


def _disp_width(s: str) -> int:
    """显示宽度：全角算 2、半角算 1。中文书的行长必须按这个算，否则中英混排的页阈值会歪。"""
    w = 0
    for ch in s:
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return w


def _percentile(vals: list[int], q: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    k = (len(s) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def _page_lines(content: str) -> list[str]:
    return [ln.strip() for ln in (content or "").split("\n") if ln.strip()]


def _yaml_str(s: str) -> str:
    return '"' + (s or "").replace("\\", "\\\\").replace('"', '\\"') + '"'


def _front_matter(m, chunks) -> str:
    # 页数以**实际块**为准：材料重解析时 page_count 可能滞后一版，写进 front-matter
    # 会让下游系统按错误页数切分（比「少写一个字段」严重得多）。
    pages = max((c.page_no or 0 for c in chunks), default=0) or (
        getattr(m, "page_count", 0) or 0)
    rows = [
        "---",
        f"title: {_yaml_str(getattr(m, 'title', '') or '')}",
        f"format: {getattr(m, 'format', '') or ''}",
        f"pages: {pages}",
        f"exported_at: {datetime.now().strftime('%Y-%m-%dT%H:%M')}",
        "generator: ai-study-companion",
    ]
    fp = getattr(m, "file_path", "") or ""
    if fp:
        rows.append(f"source: {_yaml_str(Path(fp).name)}")
    rows.append("---")
    return "\n".join(rows)


def _heading_blocks(prev_segs: list[str], segs: list[str]) -> list[str]:
    """只输出「与上一节相比发生变化的那几级」标题。

    跨章时（`第1章/A` → `第2章/B`）必须同时输出 `#` 与 `##` 两级，
    否则新章会挂到旧章下面 —— 只输出最末段是最常见的写法，也是错得最隐蔽的那个。
    """
    i = 0
    while i < min(len(prev_segs), len(segs)) and prev_segs[i] == segs[i]:
        i += 1
    return [("#" * min(j + 1, 6)) + " " + segs[j].replace("／", "/")
             for j in range(i, len(segs))]


def _page_geometry(c, lines: list[str], global_fw: float,
                   boxes_raw: str = "") -> tuple[float, list[bool] | None]:
    """算这一页的满行阈值；能拿到可信行框就返回逐行「是否满行」的布尔表。

    ⚠️ 满行宽必须用 **P90 × 0.9**，不能用 P75。实测 P3：正文行宽稳定在 74~80，
       但页眉/标题等短行把 P75 抬到 77.8，于是 74、76、77 全被判成「没排满」→
       整页几乎不合并（正是第一版 24% 句末占比的来源）。
       正文的排版右边界是恒定的，P90 才代表那条边界，×0.9 留出容差。
    """
    widths = [_disp_width(x) for x in lines]
    fw = max(_percentile(widths, 0.9) * 0.9, global_fw * 0.9)
    raw = boxes_raw
    if not raw:
        return fw, None
    try:
        arr = json.loads(raw)
    except Exception:
        return fw, None
    if not isinstance(arr, list) or len(arr) != len(lines):
        # 行数不一致 = cleaner 删过行，按索引对齐会整体错位 → 放弃精确判据
        return fw, None
    probe = min(3, len(lines))
    for i in range(probe):
        if _norm(str(arr[i].get("t", ""))) != _norm(lines[i]):
            return fw, None
    # ⚠️ 文字层 PDF 的行框（src=="text"）**一律不参与排版判断**，只用来做句子级定位。
    # 两个理由（实测 47 号 249 页那本书）：
    #   ① 它按 y0 排序，与 extract_text 的行序不一致（页眉被 extract_text 排到首行）；
    #   ② 就算逐行全等，拿 x1 判满行也会把「连字符断词」的段尾（`…正是这样-` / `本提炼…`）
    #      判成没排满 → 句子被拆开还插一条空行。这类改动该在排版链路上单独验证，
    #      不能夹在「补定位」这个改动里（A/B 实测：2/7 号不受影响，47 号 5 处变化，2 处变差）。
    #      （OCR 行框的同类问题已由下面的「右边界校准检查」兜住，文字层因行序问题仍整体拒绝。）
    if any(isinstance(x, dict) and x.get("src") == "text" for x in arr):
        return fw, None
    # ⚠️⚠️ 行框判据只对「文字块右边界顶到页宽 0.88 以上」的版式成立。课件/横幅排版是这样，
    #    **书籍版面通常 13%~87%**（实测 47 号 p1 封面 max=0.872）→ 整页没有一行够得着阈值
    #    → 每行都判「没排满」，这一页等于彻底关闭段落合并（2 句话被拆成 4 段还插空行）。
    #    整页够不到阈值 = 该判据对本版式未校准，退回字符宽度口径（与无行框时一致）。
    #    ⚠️ 别改成「按 max(x1) 做相对阈值」：p1 的署名行与书评行 x1 同为 0.849，
    #    但字号不同（署名仅 28 字、书评 44 字），相对阈值会把署名粘进下一段书评。
    try:
        if arr and max(float(x.get("x1") or 0) for x in arr) < _FULL_LINE_X1:
            return fw, None
    except Exception:
        return fw, None
    try:
        return fw, [float(x.get("x1") or 0) >= _FULL_LINE_X1 for x in arr]
    except Exception:
        return fw, None


def _joinable(prev: str, nxt: str) -> bool:
    if not prev or not nxt:
        return False
    if prev.endswith(_SENT_END):
        return False
    if prev.startswith(_NO_JOIN_START) or nxt.startswith(_NO_JOIN_START):
        return False
    if "|" in prev or prev.endswith(("：", ":")):
        return False
    # 极短的下一行（≤4 个汉字）视为独立元素：图表标签（「关注度」「时间」）就是这个长度。
    # ⚠️ 但**带句末标点的短行必须放行** —— 那是正文折行的末行（实测「的应用。」「机等)。」
    #    只有 3~4 个字），一刀切会把这些句子永久截断。
    if _disp_width(nxt) <= 8 and not any(ch in nxt for ch in _SENT_END):
        return False
    # 下一行本身是标题（编号行 / 章节名）时不能吸进上一段
    if outline.detect_level(nxt) > 0:
        return False
    return True


def _join(prev: str, nxt: str) -> str:
    """中文直拼；两侧都是 ASCII 词时补一个空格，避免 `wordanother`。"""
    if prev and nxt and ord(prev[-1]) < 128 and ord(nxt[0]) < 128:
        return prev + " " + nxt
    return prev + nxt


def _short_runs(lines: list[str], widths: list[int], fw: float) -> list[bool]:
    """标出「连续 ≥2 行都很短」的区间，整段保护、不参与合并。

    为什么需要它：正文折行的最后一行也短，单看长度与图表标签**无法区分**。
    区别在于**连续性** —— 折行末尾的短行是孤立的，后面紧跟新的满行段落；
    而图表标签（`关注度` / `过高期望的峰值期` / `时间`）、目录清单、列表项
    一定是连着好几行都短。实测那页正是连续 11 行短标签。

    ⚠️ **段首若是正文末行要单独放行**：实测 P2 的短行段是
       `产的高原期。`（12 宽，以句号结尾）/ `关注度` / `过高期望的峰值期` / …
       第一行是上一段正文的结尾，必须能接回去合并；从第二行起才是图表标签。
       一刀切保护整段，会让「实质生 / 产的高原期」这句永远合不上。

    阈值 0.55 倍满行宽：中文一行 16~20 字，0.55 约等于 9~11 字，标签/清单都落在这之下，
    而正文折行末行常还剩半句（>11 字）。
    """
    if not widths:
        return []
    # ⚠️ 表格行**不算短行**：`| --- | --- |` 只有十来个字符，必然落在 0.55×fw 之下，
    #    一旦被判成图表标签就进引用块 → 整张表被劈成「前两行引用、后面正文」
    #    （实测材料 44 号 21 处、51 号 8 处）。
    short = [w < fw * 0.55 and not _is_table_row(ln)
             for ln, w in zip(lines, widths)]
    # 整页几乎都是短行 = 这本来就是短行排版（PPT 课件实测每页 5~8 行、行行都短）。
    # 此时「短行 = 图表标签」的前提不成立，继续保护会把整页变成引用块
    # （存量课件实测 172 行被误判）。这类页返回全 False，内容按原段落保留。
    if sum(short) / len(widths) > 0.7:
        return [False] * len(widths)
    prot = [False] * len(widths)
    i = 0
    while i < len(widths):
        if short[i]:
            j = i
            while j < len(widths) and short[j]:
                j += 1
            if j - i >= 2:
                # 编号列表（`1）…` `2）…` `（3）…`）不是图表标签，是正文结构。
                # 把它整段当引用块，会和同一列表里「以句读结尾、被段首规则放行」的第一条
                # 割裂成两种版式（实测 P60：`1）定义业务需求；` 是段落，`2）3）` 变成引用）。
                # 所以过半是列表项时整段不保护，逐条各成一段，版式一致。
                if sum(1 for k in range(i, j)
                       if _LIST_START.match(lines[k].strip())) * 2 >= (j - i):
                    i = j
                    continue
                start = i
                if lines[i].strip().endswith(_SENT_END):
                    start = i + 1     # 段首是正文末行：放行
                for k in range(start, j):
                    prot[k] = True
            i = j
        else:
            i += 1
    return prot


def _is_toc_like(lines: list[str]) -> bool:
    """导出时的目录页判定（比 `outline.is_toc_page` 宽一点，但**不删内容**）。

    为什么不能只用 `outline.is_toc_page`：它要求「编号行 + 纯页码行 > 50%」，
    而章首页的分节清单实测是这样的 ——

        第1章 / AI产品经理 一不是简单的为… / 三大浪潮看AI技术发展 / AI产业结构 / … / 002

    OCR 只认出 2 条编号（`第1章`、`1.1 …`），占比 10%，直接漏判，
    于是 20 行清单原样进了正文，把「句末标点占比」从 90% 拉到 26%。

    判据（全部满足）：
      1. 行数 ≥ 8
      2. **一行都没有句末标点**（正文页几乎不可能满足）
      3. 行长**中位数** ≤ 26 显示宽（≈13 个汉字；用中位数不用平均数，
         因为清单里常有 1~2 条长标题，平均数会被它们拉过阈值）
      4. 至少 1 行匹配编号模式（章首页清单实测只有 `第1章` 一条编号 ——
         要求 ≥2 条会漏判，实测就是漏在这里）

    ⚠️ 判中后**不删除**，而是整页降级为引用块：判据再稳也可能在课件类材料上误判，
       删掉就是内容永久丢失；降级只影响排版，信息还在。这是刻意选的「错也不丢」。
    """
    if len(lines) < 8:
        return False
    # 表格行不是目录：docx/epub 的表格逐行成块，宽度短、无句末标点、还带编号，
    # 上面四条判据全中 → 整张表被打成引用块（实测 44 号 23 处、51 号 8 处）。
    if sum(1 for x in lines if _is_table_row(x)) >= 2:
        return False
    if outline.is_toc_page([{"t": x} for x in lines]):
        return True
    if any(x.strip().endswith(_SENT_END) for x in lines):
        return False
    widths = [_disp_width(x) for x in lines]
    if _percentile(widths, 0.5) > 26:
        return False
    return any(outline.detect_level(x) > 0 or _PAGE_NO.match(x.strip())
               for x in lines)


def _reflow(lines: list[str], fw: float, full_flags: list[bool] | None,
            sec_norm: set[str]) -> tuple[list[str], bool]:
    """返回 (段落列表, 最后一段是否「满行未说完」)。

    第二个返回值专供**跨页合并**：正文折行会正好断在页边界（P3 末「…实现不同场景」/
    P4 首「场景。包括…」），只在本页内 reflow 永远合不上 —— 实测 9 个停在半句的长段落里
    有 4 个是跨页造成的。
    """
    out: list[str] = []
    widths = [_disp_width(x) for x in lines]
    prot = _short_runs(lines, widths, fw)
    prev_full = False
    quote_buf: list[str] = []      # 连续受保护行（图内标签 / 框线文字）攒成一个引用块

    def flush_quote():
        if quote_buf:
            out.append(_quote("\n".join(quote_buf)))
            quote_buf.clear()

    for i, ln in enumerate(lines):
        s = ln.strip()
        if not s:
            continue
        if _norm(_HEAD_MARK.sub("", s)) in sec_norm:
            # 标题行已由 heading 输出，正文里再出现一次就是重复。
            # ⚠️ 必须剥掉行首 `#{1,6}` 再比：源材料的标题行自带 `## `，section_path 是纯文本，
            #    直接 `_norm` 比不相等 → 同一标题输出两次（实测 40 号 27 个标题里 17 个重复）。
            # ⚠️ 同时把 prev_full 置 False：这一行是**节边界**，跨过它继续合并会把
            #    上一节的段落和下一节的正文粘成一段（标题本身已经不在 out 里，看不出来）。
            prev_full = False
            continue
        if _is_table_row(s) and out and _is_table_row(out[-1].split("\n")[-1]):
            # 表格行紧跟表格行：用**单换行**拼（不能走 _join 的直拼，也不该各自成段），
            # 否则空行会把 Markdown 表格打断，整张表退化成普通文本行。
            out[-1] = out[-1] + "\n" + s
            prev_full = False
            continue
        if prot[i]:
            # 图里的标签（实测一页能连续出现 12 条）散成 12 个段落会把正文切碎，
            # 攒成一个引用块：视觉上是一整块，内容一条不少。
            quote_buf.append(s)
            prev_full = False
            continue
        flush_quote()
        full = full_flags[i] if (full_flags and i < len(full_flags)) else (
            widths[i] >= fw)
        if out and prev_full and _joinable(out[-1], s):
            out[-1] = _join(out[-1], s)
            prev_full = full
        else:
            out.append(s)
            prev_full = full
    flush_quote()
    return out, (prev_full and bool(out))


def _quote(text: str) -> str:
    return "\n".join("> " + x if x else ">" for x in text.split("\n"))


# 行尾页码：「AI 技术与能力升级018」/「AI 产品经理—… 019」
_PAGE_TAIL = re.compile(r"\s*\d{1,3}$")

# 只由章号构成的页眉行（`第 1 章`），允许短于 8 字
_CHAPTER_ONLY = re.compile(r"^第\s*[0-9一二三四五六七八九十百零〇]{1,4}\s*[章篇部]$")

# Markdown 结构行（表格 / 列表 / 标题 / 引用）：这些**不允许**按「重复」判成页眉。
# 反例（实测 docx 转出的表格）：`|---|---|` 在 3 页的表头各出现一次，若不加这条会被整行删掉 → 表格错位。
_STRUCT_HEAD = ("|", "-", "*", "#", ">", "+")

# 制表符画框线（`┌───┐` `│ │` `└───┘`）：PRD / 流程图的视觉结构，重复出现极正常。
_BOX_CHARS = re.compile(r"[\u2500-\u257F\u25A0-\u25FF]")


def _is_struct_line(s: str) -> bool:
    """Markdown 结构行 / 画框线：**绝不**按「跨页重复」当页眉删。

    ⚠️ 两处实测误删（都是内容丢失，不是排版问题）：
      - 材料 44 号（PRD）里 `| ID | 需求描述 | 优先级 |` 出现 7 次（每个模块一张表），
        被「任意位置重复 ≥ need」判成页眉，7 张表的表头全没了；
      - 同一份材料用 `┌────┐` 画的流程图框线不以 `|`/`-` 开头，绕过 `_STRUCT_HEAD`，
        删掉后图直接残缺。
    """
    t = (s or "").strip()
    return t.startswith(_STRUCT_HEAD) or bool(_BOX_CHARS.search(t))


def _repeat_key(s: str) -> str:
    """页眉比较用的归一化：去空白 + 剥掉行尾 1~3 位页码。

    ⚠️ 剥页码是必须的：实测页眉是「AI 产品经理—不是简单的… 019」，**每页页码不同**，
       不剥就永远凑不出重复，`outline.collect_repeated` 也因此完全失效。
    """
    return _WS.sub("", _PAGE_TAIL.sub("", s or ""))


def _repeated_lines(pages: list[list[str]]) -> set[str]:
    """导出侧的页眉/页脚统计。

    为什么不复用 `outline.collect_repeated`：那个函数要求「归一化后 ≥6 字」，
    而页眉常常就三个字（`第 1 章`），实测一条都抓不到。也不能去改它 ——
    它同时承担「该行不能判为标题」的职责，放宽阈值会让真标题被判成页眉。

    ⚠️ 入口门槛必须收到 **8 字**（或恰好是「第N章」这种章名行）。
       第一版放到 3 字，目录页里的短条目「AI产品经理」因为跨页出现被当成页眉删掉了 ——
       目录内容属于正文，删了就是信息丢失。8 字以上才能排除「短标题同名」的巧合。

    ⚠️ **两条判据取并集，缺一条就漏**（都是实测）：
       ① 任意位置重复 ≥ need（need 随页数放大到 5）—— 抓「人工智能产品经理技能图谱」这种
          每页都挂的书名页眉（249 页那本重复 118 次）。
       ② 「每次出现都在页首页尾（前/后 2 行）且至少 3 页」—— 抓**短章节**的页眉。
          249 页那本的「第 8 章」「职业进阶—当 AI 产品经理遇上未来」只出现 4 次，
          卡在 need=5 之下漏网，导出后章首页顶着两行垃圾。
          这条不会误伤正文：正文行不可能「每次出现都恰好排在页首页尾」。
          也不会误伤 Markdown 表格分隔行（`|---|`）—— 那是排在页中的。
    """
    n = len(pages)
    if n < 8:
        # 页数太少时「跨页重复」不是可靠信号：4 页的材料里，某个条目出现 3 次就是 75%，
        # 实测因此把目录条目「AI产品经理的岗位布局」当成页眉删掉了（内容丢失）。
        return set()
    need = max(3, min(5, n // 4))
    seen: dict[str, int] = {}
    edge: dict[str, int] = {}
    for lines in pages:
        for k in {_repeat_key(x) for x in lines}:
            if (len(k) >= 8 or _CHAPTER_ONLY.match(k)) and not _is_struct_line(k):
                seen[k] = seen.get(k, 0) + 1
        for k in {_repeat_key(x) for x in (lines[:2] + lines[-2:])}:
            if (len(k) >= 8 or _CHAPTER_ONLY.match(k)) and not _is_struct_line(k):
                edge[k] = edge.get(k, 0) + 1
    return {k for k, v in seen.items()
            if v >= need or (v >= 3 and edge.get(k, 0) >= v)}



def _drop_repeated(c, lines: list[str], repeated: set[str]) -> tuple[list[str], str]:
    """剔除跨页重复行（页眉/页脚/书名），并**同步裁掉对应的行框**。

    为什么导出还要做一遍：`cleaner` 只清「每块的首末行」与短行，实测 249 页那本书的
    页眉「第 1 章」「AI 产品经理—不是简单的…019」落在页内第 2~3 行，清洗没碰到，
    导出后每页开头都挂着两行垃圾（实测仍有 12 + 13 处）。

    ⚠️ 行框必须按同一批下标裁：不裁的话行数对不上，`_page_geometry` 会放弃精确判据
       （退化还能用，但扫描件的 x1 判据白白丢掉）。
    """
    if not repeated:
        return lines, getattr(c, "line_boxes", "") or ""
    keep = [i for i, l in enumerate(lines) if _repeat_key(l) not in repeated]
    if len(keep) == len(lines):
        return lines, getattr(c, "line_boxes", "") or ""
    new_lines = [lines[i] for i in keep]
    raw = getattr(c, "line_boxes", "") or ""
    if raw:
        try:
            arr = json.loads(raw)
            if (isinstance(arr, list) and len(arr) == len(lines)
                    and not any(isinstance(x, dict) and x.get("src") == "text" for x in arr)):
                raw = json.dumps([arr[i] for i in keep], ensure_ascii=False)
        except Exception:
            pass
    return new_lines, raw


# 页级小节的标题前缀（`P12 需求管理`），只出现在 section_path 里，正文行不带它
_PREFIX_P = re.compile(r"^P\d+\s*")


def _slice_boxes(raw: str, n: int) -> str:
    """从行框 JSON 里去掉前 n 项（与删掉的前 n 行对齐）。

    ⚠️ 必须同步裁：`_page_geometry` 用「框数 == 行数」判断能否走精确判据，
       对不上就退化成按宽度猜 —— 扫描件会白白丢掉 x1 判据。
    """
    if not raw or n <= 0:
        return raw
    try:
        arr = json.loads(raw)
    except Exception:
        return raw
    if not isinstance(arr, list) or len(arr) <= n:
        return raw
    if any(isinstance(x, dict) and x.get("src") == "text" for x in arr):
        # 文字层行框与正文行序不一致，按下标裁会错位；宁可整批交给 `_page_geometry` 拒绝
        return raw
    return json.dumps(arr[n:], ensure_ascii=False)


def build_markdown(m, chunks) -> str:
    """chunks 需已按 (page_no, id) 排序，元素具备 content/page_no/section_path/kind/line_boxes。"""
    body, appendix = [], []
    for c in chunks:
        if not (c.content or "").strip():
            continue
        if (getattr(c, "kind", "") or "body") == "img_ocr":
            appendix.append(c)
        else:
            body.append(c)

    all_w = [_disp_width(ln) for c in body for ln in _page_lines(c.content)]
    global_fw = max(_percentile(all_w, 0.85) * 0.9, 12.0)
    repeated = _repeated_lines([_page_lines(c.content) for c in body])

    out: list[str] = [_front_matter(m, chunks)]
    prev_segs: list[str] = []
    cur_page = None
    # 跨页续写状态：last_para 指向 out 里「还停在半句」的那一段，-1 表示当前不可续写。
    # 换节、遇图块、遇目录页都必须置 -1 —— 那些边界本来就该断段，续写会把两节粘在一起。
    last_para = -1
    last_full = False
    for c in body:
        lines = _page_lines(c.content)
        if not lines:
            continue
        page = c.page_no or 0
        sec = (c.section_path or "").strip()
        segs = [x.strip() for x in sec.split("/") if x.strip()]
        if segs != prev_segs:
            blocks = _heading_blocks(prev_segs, segs)
            if blocks:
                out.append("\n\n".join(blocks))
            prev_segs = segs
            last_para, last_full = -1, False

        lines, boxes_raw = _drop_repeated(c, lines, repeated)

        # 页级小节（`P12 需求管理`，幻灯片式文字层 PDF 的形态）的标题**就是本页首行** ——
        # 正文里再出现一次就是重复（实测 2 号 57 页每页都顶着两遍标题）。
        # 只比首行、只比「去掉 `P<页号> ` 前缀」的那一级：正文中间恰好等于标题的短行不动。
        if lines and segs:
            loose = _WS.sub("", _PREFIX_P.sub("", segs[-1]))
            if loose and _norm(lines[0]) == loose:
                lines = lines[1:]
                boxes_raw = _slice_boxes(boxes_raw, 1)

        anchor = f"<!-- P{page} -->" if (page and page != cur_page) else None
        if anchor:
            cur_page = page

        if not lines:
            # 整页都是页眉（实测第 235 页的文本层只有「第 8 章 + 副标题」两行）：
            # 内容剔干净了，但**页码锚必须留下** —— 否则该页在产物里凭空消失，
            # 下游按锚点做页码映射时会整体偏移一页。同时置回 -1，
            # 免得上一页那段「未说完」的正文跨过这一页去吸下一页（跨了空页，本来就不该连）。
            if anchor:
                out.append(anchor)
            last_para, last_full = -1, False
            continue

        if _IMAGE_PREFIX.match(lines[0]):
            # 书籍内嵌图 OCR：位置有意义，原位保留为引用块（不参与段落合并）
            if anchor:
                out.append(anchor)
            out.append(_quote(_IMAGE_PREFIX.sub("", "\n".join(lines))))
            last_para, last_full = -1, False
            continue

        if _is_toc_like(lines):
            # 目录/分节清单：不打断正文流，但**保留内容**（降级为引用块）。
            # 它的另一个贡献是上面的 section 层级，已经在 heading 里了。
            if anchor:
                out.append(anchor)
            out.append(_quote("\n".join(lines)))
            last_para, last_full = -1, False
            continue

        fw, flags = _page_geometry(c, lines, global_fw, boxes_raw)
        sec_norm = {_norm(x) for x in segs}
        # 标题内斜杠转义成了全角 `／`，正文行还是半角 `/`：加一份半角版本，
        # 否则「小红书/公众号」正文行匹配不上转义后的 section，标题去重失效 → 又重复。
        sec_norm |= {_norm(x.replace("／", "/")) for x in segs}
        paras, page_full = _reflow(lines, fw, flags, sec_norm)

        # 表格**跨块**续接：同一张表被拆到多个 chunk（docx/epub 逐行成块、剪藏 md 亦然），
        # 不接回去的话 `\n\n` 拼接会把表格打断（实测 44 号 84 处、52 号 60 处、51 号 27 处）。
        # 锚点在这里刻意不插：锚是页级定位，插在表格中间会把整张表劈成两半。
        if (paras and all(_is_table_row(x) for x in paras)
                and out and _is_table_row(out[-1].split("\n")[-1])):
            out[-1] = out[-1] + "\n" + "\n".join(paras)
            last_para, last_full = -1, False
            continue

        merged = False
        if (paras and last_para >= 0 and last_full
                and _joinable(out[last_para], paras[0])):
            out[last_para] = _join(out[last_para], paras[0])
            paras = paras[1:]
            merged = True

        if anchor:
            if merged:
                # 锚插到被续写段落**之前**：该段起于上一页，这一页从这里接着写
                out.insert(last_para, anchor)
                last_para += 1
            else:
                out.append(anchor)
                last_para = -1

        if paras:
            out.extend(paras)
            last_para = len(out) - 1
            last_full = page_full
        elif merged:
            last_full = page_full

    if appendix:
        parts = ["## 附录：图内文字"]
        # 按页分组：同一页常有多张内嵌图（材料 40 的第 5 页有 17 个 OCR 块），
        # 逐块吐 `### P5` 会得到 17 个同名小节 —— 目录里看起来像重复标题。
        groups: dict[int, list[str]] = {}
        order: list[int] = []
        for c in appendix:
            txt = _IMAGE_PREFIX.sub("", (c.content or "").strip())
            if not txt:
                continue
            key = int(c.page_no or 0)
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(txt)
        for i, key in enumerate(order, 1):
            label = f"### P{key}" if key else f"### 片段 {i}"
            parts.append(label + "\n\n" + "\n\n".join(groups[key]))
        if len(parts) > 1:
            out.append("\n\n".join(parts))

    return "\n\n".join(x.strip() for x in out if x and x.strip()) + "\n"


def export_filename(m) -> str:
    from ..core.filename import safe_stem
    return f"{safe_stem(getattr(m, 'title', '') or '材料')}.md"
