"""舞台页（阶段 2）：把作品数据注入模板，产出无头渲染器加载的自包含 HTML。

设计边界（与 PRD 3.8 一致）：
- **原型零改动。** `_video_proto_v6_20261004/index.html` 是交互演示页（带播放器 / 分镜条 /
  全屏），不能直接当舞台。这里用**同源的模板** `stage_tpl.html`（CSS 与渲染逻辑搬自原型），
  由本模块注入数据后落到 `videos_root/{id}/stage.html`。
- 三种皮肤共用一条时间轴、一份讲稿、一条音轨（PRD 3.6/3.7 硬约束）：
  换皮肤**不得**改变任何一句的起止时间。
- 结构图皮肤是**程序化生成**的：原型那 8 个手绘 SVG 镜头只对应它自己那份 300 秒讲稿，
  真实内容的 `points` 只有 `{t,k,group,seg,at}`，必须由本模块布局成卡片。

⚠️ 两条容易静默出错的映射约定：
  1. `points.group`（LLM 给的镜头号）与 `shots.n`（`build_shots` 按停顿切出的镜头号）
     **不是同一个划分**。绝不能让两者直接对齐 —— 一律用**时间归属**（`at` 落在哪个镜头区间）
     把要点归到场景，这样画面切镜与要点栏分组天然一致。
  2. `SRC.aspect` 的口径是 **宽/高**（原型 0.75 对应 1080×1440 的竖版图）；
     舞台页用它把 `.pagewrap` 定量成「恰好等于图片显示区」。
"""

from __future__ import annotations

import html as _html
import re
import threading
from pathlib import Path

from . import artworks as artwork_svc
from . import backgrounds as bg_svc
from . import locate as locate_svc
from . import video as svc
from . import waveform as waveform_svc

# 材料页导出分辨率（与 locate 的默认 dpi 一致：定位框坐标就是按这个 dpi 算的）
PAGE_DPI = 150
_TEMPLATE = Path(__file__).with_name("stage_tpl.html")
# 解压型皮肤（radio / night / ambient）的独立模板（DOM 结构与信息型完全不同 → 单独模板，
# 不往 stage_tpl.html 里塞，避免两套逻辑互相污染）。
_TEMPLATE_RELAX = {
    "radio": Path(__file__).with_name("stage_tpl_radio.html"),
    "night": Path(__file__).with_name("stage_tpl_night.html"),
    "ambient": Path(__file__).with_name("stage_tpl_ambient.html"),
}
# PPT 课件（2026-10-20，方案 A）也是独立模板：画面语法是「课件页 + 常驻节导航」，
# 与信息型（同一份 graph_svg 铺在右栏）完全不同，且**不需要** graph_svg
# —— 那是结构图的画法，课件页上的内容是条目列表，不是节点图。
_TEMPLATE_PPT = Path(__file__).with_name("stage_tpl_ppt.html")
_TEMPLATE_BOARD = Path(__file__).with_name("stage_tpl_board.html")
_TEMPLATE_MOTION = Path(__file__).with_name("stage_tpl_motion.html")

# ambient 的光晕参数（探针验证值；六个周期互质 23/31/9/29/17/14，避免长片可察觉的拍频循环）。
AMBIENT_GLOW = [
    {"color": "167,139,250", "r0": 620, "x0": 0.30, "y0": 0.42, "ax": 0.16, "ay": 0.12,
     "tx": 23.0, "ty": 31.0, "px": 0.0, "py": 1.7, "tb": 9.0, "pb": 0.0, "ab": 0.12,
     "stops": [[0.00, 0.320], [0.30, 0.200], [0.55, 0.110], [0.78, 0.045], [1.00, 0.0]]},
    {"color": "93,202,165", "r0": 480, "x0": 0.70, "y0": 0.62, "ax": 0.13, "ay": 0.10,
     "tx": 29.0, "ty": 17.0, "px": 2.1, "py": 0.4, "tb": 14.0, "pb": 1.1, "ab": 0.10,
     "stops": [[0.00, 0.260], [0.35, 0.155], [0.65, 0.070], [1.00, 0.0]]},
]

# 结构图卡片布局区（viewBox 1280×720 坐标系；右侧 29.6% 原为要点栏 / 章节栏所在）
# ⚠️ 2026-10-14：右侧栏已按产品要求整体移除，画布右侧腾出的空间**还给内容** ——
#    `graph_svg(wide=True)` 把右边界从 846 推到 1224（三档非口播结构图）；
#    数字人口播·结构图那一档不铺满（`wide=False`）：右侧站的是数字人形象
#    （`.th-fig` 宽 33.6%），布局区一放宽就会压到人身上。
# ⚠️ 两档值都是**显式常量**，不要在别处再写一遍数字：卡片越界判据（探针）按 1280 画布算，
#    1224 + 56 的对称边距 = 原左边界，视觉与 `_BX0` 同宽。
_BX0, _BX1 = 56.0, 846.0
_BX1_NARROW = 846.0
_BX1_WIDE = 1224.0
# 两档靠**模块级 `_BX1`** 切换（布局函数全都直接读它，逐个加参数会把签名污染 20 处）。
# 代价是这一小段状态不线程安全 —— 用锁把「设置跨度 → 生成完全部 SVG」整段串起来。
# ⚠️ 别把 `with` 缩到只包设置那一行：出了作用域 `_BX1` 仍是改过的值，别的线程会读到中间态。
_LAYOUT_LOCK = threading.Lock()
_BY0, _BY1 = 190.0, 648.0
_CARD_GAP = 14.0
_CARD_RX = 10.0
_TX_SIZE = 19.0
_MAX_LINES = 2


def _esc(s) -> str:
    return _html.escape(str(s if s is not None else ""), quote=True)


def _j(obj) -> str:
    """JSON 字面量，供内联进 <script>。

    ⚠️ 必须转义 `</`：内容里出现 `</script>` 会**提前关掉脚本块**（讲稿里出现这种串
       完全可能，比如讲 HTML 转义时）。`\\u003c` 在 JS 里等价于 `<`，语义不变。
    """
    import json
    txt = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    return txt.replace("</", "<\\/")


# ---------- 时间轴归位 ----------

def _scene_index(at_ms: int, shots: list) -> int:
    """绝对毫秒 → 场景（镜头）下标。落不到任何区间时归到最近的镜头。"""
    if not shots:
        return 0
    k = 0
    for i, sh in enumerate(shots):
        if at_ms >= int(sh.get("a") or 0):
            k = i
    return k


def _scene_of_script(script: list, shots: list) -> list:
    """句 → 场景下标。用每句的**起始毫秒**归属（与画面切镜同一判据）。"""
    out = []
    for s in script or []:
        out.append(_scene_index(int(s.get("start_ms") or 0), shots))
    if not out:
        return [0]
    return out


def _at_by_scene(shots: list, script: list, scene_of: list) -> list:
    """每个场景的起始毫秒 —— 取「落在该场景的第一句」的起点。

    ⚠️ 不能直接用 `shots[k].a`：那是分镜切点，可能与首句起点差几十毫秒（句间留白的分配），
       用它会让标题比第一句早亮，出现「画面已换、字幕还没换」的错觉。
    """
    at = [None] * max(1, len(shots))
    for s, si in zip(script or [], scene_of):
        if si < len(at) and at[si] is None:
            at[si] = int(s.get("start_ms") or 0)
    if shots:
        if at[0] is None:
            at[0] = int(shots[0].get("a") or 0)
        for i in range(1, len(at)):
            if at[i] is None:
                at[i] = at[i - 1]
    return at


# ---------- 讲稿 ----------

def _segs(script: list) -> list:
    """讲稿 → 原型 SEGS 形状 `[{s,t,a,b}]`。

    ⚠️ `b` 缺省时**不能**用 `start + 1000` 顶替：那会让句尾停在半句上，
       而 `idxAt()` 与 `paint()` 都按 `b` 推进。缺时间戳的讲稿（未合成的播客）
       根本不该走到渲染 —— 这里只在有值时生成，缺值由调用方拦下。
    """
    out = []
    for s in script or []:
        text = (s.get("text") or "").strip()
        if not text:
            continue
        a = int(s.get("start_ms") or 0)
        b = int(s.get("end_ms") or (a + 1000))
        who = "expert" if (s.get("speaker") == "expert") else "host"
        out.append({"s": who, "t": text, "a": a, "b": max(a, b)})
    return out


# ---------- 结构图：程序化 SVG ----------

def _text_units(s: str) -> float:
    """按「显示宽度」估字符数：中日韩全角算 1，其余算 0.55。"""
    u = 0.0
    for c in s or "":
        u += 1.0 if ord(c) > 0x2E80 else 0.55
    return u


def _wrap_cjk(text: str, max_units: float, max_lines: int = _MAX_LINES) -> list:
    """按显示宽度折行（最多 max_lines 行，超出加省略号）。"""
    text = (text or "").strip()
    if not text:
        return [""]
    lines, cur, cu = [], "", 0.0
    for c in text:
        w = 1.0 if ord(c) > 0x2E80 else 0.55
        if cu + w > max_units and cur:
            lines.append(cur)
            if len(lines) >= max_lines:
                break
            cur, cu = "", 0.0
        cur += c
        cu += w
    if len(lines) < max_lines and cur:
        lines.append(cur)
    if len(lines) >= max_lines:
        used = "".join(lines)
        if len(used) < len(text):
            lines[-1] = lines[-1][:max(1, len(lines[-1]) - 1)] + "…"
    return lines or [""]


def _card_boxes(n: int) -> list:
    """n 张卡的 (x, y, w, h)。n ≤ 4 单列，n ≥ 5 双列。n=0 返回空。

    ⚠️ n=0 必须早退：`rows = (0+cols-1)//cols = 0` → 除零。
       这条路径**真实存在** —— 有些镜头就是没有要点（PRD 3.6.8：元素过少是允许的），
       实测 5 个镜头的要点分布可以是 [4,4,0,0,0]。
    ⚠️ 列数必须**跟着跨度走**（2026-10-14 铺满改造）：铺满档若还沿用「n≥5 双列」，
       每列会宽到 577px —— 卡片里一行要点只占左侧一条，整屏看着是「一排空框」
       （取图实证）。铺满档改成最多三列后每列 ≈380px，与窄档双列同宽，字号 / 折行 /
       视觉密度都不变，只是横向铺满。窄档（口播·结构图）**逐像素保持原样**。
    """
    if n <= 0:
        return []
    W = _BX1 - _BX0
    H = _BY1 - _BY0
    if _BX1 > _BX1_NARROW + 1.0:
        cols = 1 if n <= 1 else (2 if n <= 4 else 3)
    else:
        cols = 1 if n <= 4 else 2
    rows = (n + cols - 1) // cols
    cw = (W - _CARD_GAP * (cols - 1)) / cols
    ch = (H - _CARD_GAP * (rows - 1)) / rows
    # ⚠️ 行高封顶按行数分档：3 行以上还要给标题区留呼吸，1-2 行则**撑满**
    #    —— 一律封 112 会在少要点镜头里留下一大片空黑（实测海报就是这么空的）。
    ch = min(ch, 130.0 if rows >= 3 else 200.0)
    # 行高封顶后整体垂直居中
    block = ch * rows + _CARD_GAP * (rows - 1)
    y0 = _BY0 + max(0.0, (H - block) / 2.0)
    boxes = []
    for i in range(n):
        r, c = divmod(i, cols)
        boxes.append((_BX0 + c * (cw + _CARD_GAP),
                      y0 + r * (ch + _CARD_GAP), cw, ch))
    return boxes


# ---------- 结构图：五种版式的绘制原语 ----------
# ⚠️ 版式白名单与元素数范围在 `video.GRAPH_LAYOUTS / GRAPH_LAYOUT_LIMITS`（**唯一权威**），
#    这里只做「分发 + 双保险兜底」—— 不在本文件另写一份名单，避免两处漂移。

def _kind(p) -> str:
    k = str((p or {}).get("k") or "concept")
    return k if k in ("concept", "good", "cost") else "concept"


def _el(at, body: str, cls: str = "") -> str:
    """把一个绘制片段包成「按 data-at（毫秒）点亮」的元素组。

    `cls` 只用于**加类**（纸感主题要区分「外层容器」与「内层元素」的位移策略）；
    不传时输出与改造前逐字节一致。
    """
    c = ("el " + cls).strip()
    return f'<g class="{c}" data-at="{int(at or 0)}">{body}</g>'


def _card(x: float, y: float, w: float, h: float, kind: str, text, at,
          size: float | None = None, num: int | None = None,
          center: bool = False) -> str:
    """一张结构图卡片 = 底 + 左侧类别条 + 折行文字（可带序号），返回 el 组。

    ⚠️ 折行宽度按**该卡实际字号**算（`(w-44)/size`）：flow 的小卡片字号更小、
       每行能放更多字 —— 用全局字号算会把文字折得太碎。
    ⚠️ 只有 grid 走「按宽度自适应字号」这条（它不给 `size`）：铺满档里列数少的组合
       （n≤4 → 2 列）仍会得到 577px 宽的卡，19px 字压不住版面 → 提到 22/26。
       **窄档一律 19**（`_wide` 为假时短路），保证口播·结构图逐像素不变。
    """
    _wide = _BX1 > _BX1_NARROW + 1.0
    if size is None:
        size = _TX_SIZE if (not _wide or w < 500.0) else (22.0 if w < 900.0 else 26.0)
    sz = float(size)
    lines = _wrap_cjk(str(text or ""), max(5.0, (w - 44.0) / sz))
    lh = sz * 1.35
    y0 = y + h / 2.0 - (len(lines) - 1) * lh / 2.0 + sz * 0.35
    tx_x = x + w / 2.0 if center else x + 38.0
    anchor = ' text-anchor="middle"' if center else ""
    tx = "".join(
        f'<text class="gtx k-{kind}" x="{tx_x:.1f}" y="{y0 + i * lh:.1f}"'
        f'{anchor} font-size="{sz:.0f}">{_esc(ln)}</text>'
        for i, ln in enumerate(lines))
    badge = ""
    if num:
        badge = (f'<circle class="gnum-bg" cx="{x + w - 19:.1f}" cy="{y + 18:.1f}" r="11"/>'
                 f'<text class="gnum" x="{x + w - 19:.1f}" y="{y + 22.5:.1f}" '
                 f'text-anchor="middle">{int(num)}</text>')
    body = (f'<rect class="gcard k-{kind}" x="{x:.1f}" y="{y:.1f}" '
            f'width="{w:.1f}" height="{h:.1f}" rx="{_CARD_RX}"/>'
            f'<rect class="gmark k-{kind}" x="{x + 15:.1f}" y="{y + 13:.1f}" '
            f'width="5" height="{max(6.0, h - 26):.1f}" rx="2.5"/>'
            + tx + badge)
    return _el(at, body)


def _lay_grid(pts: list) -> str:
    """grid：要点平铺（原版式 + 序号徽标）。"""
    out = []
    for i, (p, (x, y, w, h)) in enumerate(zip(pts, _card_boxes(len(pts))), 1):
        out.append(_card(x, y, w, h, _kind(p), p.get("t"), p.get("at"), num=i))
    return "".join(out)


def _lay_flow(pts: list) -> str:
    """flow：横向链 —— 卡片依次排开，卡片之间用箭头连接。

    ⚠️ 箭头挂**下一张卡**的 `data-at`（指向谁就跟谁一起亮）；
       挂在上一张会出现「画面已经指向下一处、箭头还没长出来」。
    """
    n = len(pts)
    if n < 2:
        return _lay_grid(pts)
    gap = 30.0
    w = (_BX1 - _BX0 - gap * (n - 1)) / n
    h = min(240.0, 0.55 * (_BY1 - _BY0))
    y = _BY0 + ((_BY1 - _BY0) - h) / 2.0
    size = 16.0 if w < 150 else 18.0
    out = []
    for i, p in enumerate(pts):
        x = _BX0 + i * (w + gap)
        out.append(_card(x, y, w, h, _kind(p), p.get("t"), p.get("at"),
                         num=i + 1, size=size))
        if i + 1 < n:
            yc = y + h / 2.0
            out.append(_el(pts[i + 1].get("at"),
                           f'<path class="gflow" d="M{x + w + 6:.1f} {yc:.1f} '
                           f'L{x + w + gap - 6:.1f} {yc:.1f}" marker-end="url(#garr)"/>'))
    return "".join(out)


def _lay_hub(pts: list) -> str:
    """hub：中心辐射 —— 第 1 个元素是中心主题，其余分列两侧并用曲线连回中心。"""
    n = len(pts)
    if n < 3:
        return _lay_grid(pts)
    cx = (_BX0 + _BX1) / 2.0
    cy = (_BY0 + _BY1) / 2.0
    cw, ch = 280.0, 156.0
    center, branches = pts[0], pts[1:]
    # ⚠️ 分支卡宽度必须留出**可见的连线长度**：卡片贴到中心卡边缘时，
    #    连线会被后画的卡片整段盖住（实测第一版列宽 232 + 中心 300 → 只剩 7px，
    #    画面上完全看不到辐射关系，「hub」退化成两个孤立卡片堆）。
    bw = 205.0
    half = (len(branches) + 1) // 2
    cols = [(branches[:half], _BX0 + 6.0), (branches[half:], _BX1 - 6.0 - bw)]

    boxes = []
    for items, x in cols:
        cnt = len(items)
        if not cnt:
            continue
        gap = 14.0
        h = min(150.0, ((_BY1 - _BY0) - gap * (cnt - 1)) / cnt)
        block = h * cnt + gap * (cnt - 1)
        y0 = _BY0 + ((_BY1 - _BY0) - block) / 2.0
        for i, p in enumerate(items):
            boxes.append((p, x, y0 + i * (h + gap), bw, h))

    out = []
    # 先画连线：后画的卡片会盖住线头，观感更干净
    for (p, x, y, w, h) in boxes:
        is_left = x < cx
        sx = cx - cw / 2.0 if is_left else cx + cw / 2.0
        ex = x + w if is_left else x
        ey = y + h / 2.0
        mx = (sx + ex) / 2.0
        out.append(_el(p.get("at"),
                       f'<path class="gflow" d="M{sx:.1f} {cy:.1f} '
                       f'C{mx:.1f} {cy:.1f}, {mx:.1f} {ey:.1f}, {ex:.1f} {ey:.1f}"/>'))
    out.append(_card(cx - cw / 2.0, cy - ch / 2.0, cw, ch, _kind(center),
                     center.get("t"), center.get("at"), center=True, size=22.0))
    for (p, x, y, w, h) in boxes:
        out.append(_card(x, y, w, h, _kind(p), p.get("t"), p.get("at"), size=17.0))
    return "".join(out)


def _lay_compare(pts: list) -> str:
    """compare：左右对比 —— 前一半一列、后一半一列，中缝带「对比」标记。"""
    n = len(pts)
    if n < 2:
        return _lay_grid(pts)
    half = (n + 1) // 2
    # ⚠️ 铺满档（2026-10-14）：两列按可用宽度对分，中缝固定 96 —— 留给虚线分隔与「对比」
    #    标记。若沿用窄档的固定 330，两列会各自贴到左右边缘、中间空出 500px 一片黑
    #    （取图实证）。窄档一行不改，保持口播·结构图逐像素原样。
    if _BX1 > _BX1_NARROW + 1.0:
        colw = ((_BX1 - _BX0) - 96.0) / 2.0
        xs = [_BX0, _BX1 - colw]
    else:
        colw = 330.0
        xs = [_BX0 + 16.0, _BX1 - 16.0 - colw]
    cx = (_BX0 + _BX1) / 2.0
    cy = (_BY0 + _BY1) / 2.0
    out = [_el(pts[0].get("at"),
               f'<path class="gdiv" d="M{cx:.1f} {_BY0 + 12:.1f} L{cx:.1f} {_BY1 - 12:.1f}"/>'
               f'<circle class="gvs" cx="{cx:.1f}" cy="{cy:.1f}" r="23"/>'
               f'<text class="gvstx" x="{cx:.1f}" y="{cy + 6.5:.1f}" '
               f'text-anchor="middle">对比</text>')]
    for ci, items in enumerate((pts[:half], pts[half:])):
        cnt = len(items)
        if not cnt:
            continue
        gap = 14.0
        h = min(200.0, ((_BY1 - _BY0) - gap * (cnt - 1)) / cnt)
        block = h * cnt + gap * (cnt - 1)
        y0 = _BY0 + ((_BY1 - _BY0) - block) / 2.0
        for i, p in enumerate(items):
            out.append(_card(xs[ci], y0 + i * (h + gap), colw, h, _kind(p),
                             p.get("t"), p.get("at"), size=18.0))
    return "".join(out)


def _lay_pyramid(pts: list) -> str:
    """pyramid：分层递进 —— 顶层最窄（抽象），越往下越宽（具体）。"""
    n = len(pts)
    if n < 2:
        return _lay_grid(pts)
    gap = 14.0
    h = min(160.0, ((_BY1 - _BY0) - gap * (n - 1)) / n)
    block = h * n + gap * (n - 1)
    y0 = _BY0 + ((_BY1 - _BY0) - block) / 2.0
    cx = (_BX0 + _BX1) / 2.0
    full = _BX1 - _BX0
    out = []
    for i, p in enumerate(pts):
        w = full * (0.38 + 0.62 * (i / max(1, n - 1)))
        out.append(_card(cx - w / 2.0, y0 + i * (h + gap), w, h, _kind(p),
                         p.get("t"), p.get("at"), center=True, size=18.0))
    return "".join(out)


_LAYOUT_FN = {"grid": _lay_grid, "flow": _lay_flow, "hub": _lay_hub,
              "compare": _lay_compare, "pyramid": _lay_pyramid}


# ---------- 结构图 · 纸感主题（graph_paper） ----------
#
# 设计边界（《结构图纸感皮肤改造方案 V0.1》）：
#   · **不改知识结构**：points / shots / 五种版式语义全部复用 graph，本节只负责「怎么画」。
#   · **逐条显现仍走 `data-at`**：一条 = 一个外层 `.el`（条目到达）+ 三个内层 `.el`
#     （序号/图标 → 手绘强调 → 说明）。渲染层只做 `ms >= data-at` 的开关 → 仍可 seek、
#     与成片同果。**绝不用 CSS keyframes 驱动核心时序**（合成器时钟不可 seek，已吃过亏）。
#   · **颜色必须显式**：本项目「颜色类」未实现，写类名会掉成黑色。文字色与装饰色**分开**：
#     青绿装饰 `#4B9B8B` 在纸底只有 **3.00:1** → 只能画线/描边；正文用 `#2F7A6B`(4.64:1) /
#     `#5E6865`(5.24:1)。荧光笔 `#F4D35E` 上压墨色 `#263238` 是 **8.98:1**，安全。
#   · **顶部不再有人物插画**（2026-10-22 移除）：原先按 (材料键 + 镜头号) 取模选姿势的
#     程序化线稿小人被判「无信息价值」，且第三套姿势里有一条悬空折线与一条横跨躯干的弧。

_P_INK = "#263238"        # 标题 / 正文（纸底 ≈12.6:1）
_P_INK2 = "#5E6865"       # 说明文字（卡片底 5.66:1，过 AA）
_P_TEAL = "#4B9B8B"       # **装饰**青绿（线 / 强调条 / 描边）
_P_TEAL_TX = "#2F7A6B"    # **文字**青绿（纸底 4.64:1 / 卡片底 5.02:1）
_P_CARD = "#FFFDF7"       # 条目卡底
_P_LINE = "#E8E1D0"       # 卡片描边 / 分隔线
_P_YELLOW = "#F4D35E"     # 荧光笔
_P_GOOD = "#3E9C7A"
_P_COST = "#D98B4A"
_P_ACCENT = {"concept": _P_TEAL, "good": _P_GOOD, "cost": _P_COST}
_P_ITEM_GAP = 14.0
# 单条最高（rows<=2 时放宽 —— 少条目镜头里一律封 150 会留下一条空带）
_P_ROW_CAP_WIDE = 210.0
_P_ROW_CAP = 150.0
# 宽档分列的卡宽下限：再窄标题一行只剩 7–8 字，折两行就顶到正文。
_P_MIN_CARD_W = 360.0
# flow 卡片的限宽：全宽 1168px 时短标题右侧 89% 是空白（见 `_pp_flow`）。
_P_FLOW_MAX_W = 820.0


def _paper_glyph(kind: str, color: str) -> str:
    """24×24 线性图标的 glyph body（调用方给 stroke 与尺寸）。

    ⚠️ 只按 `kind` 选，**不按位置轮换**：图标是语义提示，按位置轮换会让「同一类型」
       在不同屏显示不同图形，读者会当成两种含义。
    ⚠️ 只有 concept / good / cost 三种 —— 与 `video.py` 的 kind 口径一致，不另造枚举。
    """
    if kind == "good":
        return ('<circle cx="12" cy="12" r="8.4"/>'
                '<path d="M7.9 12.3l2.9 2.9 5.2-6.1"/>')
    if kind == "cost":
        return ('<path d="M12 4.3 20.3 18.7H3.7z"/>'
                '<path d="M12 9.5v4.1"/>'
                f'<circle cx="12" cy="16.3" r="1.05" fill="{color}" stroke="none"/>')
    return ('<circle cx="12" cy="9.4" r="4.3"/>'
            '<path d="M9.7 15.1h4.6M10.6 18h2.8"/>'
            '<path d="M12 2.7v1.3M4.7 9.4H3.4M20.6 9.4h-1.3"/>')


def _paper_stroke_hl(x: float, y: float, w: float, h: float) -> str:
    """手绘荧光笔：上下沿各一条二次曲线。

    ⚠️ 不能用圆角矩形 —— 矩形是「色块」，曲线才是「笔迹」。波动幅度刻意压到 ±3px：
       再大就会盖住字的上缘/下缘，标题可读性会被自己画的花吃掉。
    """
    w = max(12.0, w)
    yt, yb = y, y + h
    return (f'<path class="phl" d="M{x:.1f} {yt + 1.6:.1f} '
            f'Q{x + w * 0.42:.1f} {yt - 2.6:.1f} {x + w:.1f} {yt + 1.0:.1f} '
            f'L{x + w:.1f} {yb - 0.6:.1f} '
            f'Q{x + w * 0.56:.1f} {yb + 2.8:.1f} {x:.1f} {yb - 1.8:.1f} Z"/>')


def _split_title_body(text: str) -> tuple:
    """要点文本 → (标题, 说明)。

    ⚠️ **只用标点切**，不做语义理解：第一个句读之前作标题，其余作说明。
       切不出来就整句当标题、说明留空 —— 绝不把一句话硬切成「半句标题 + 半句说明」，
       那读起来像被打断（也比凭空编一句说明诚实）。
    """
    t = (text or "").strip()
    if not t:
        return "", ""
    for i, ch in enumerate(t):
        if ch in "，。：；、,.:;!?！？" and 2 <= i <= 16:
            return t[:i], t[i + 1:].strip()
    return t, ""


def _paper_boxes_in(n: int, x0: float | None = None, x1: float | None = None,
                    y0: float | None = None, y1: float | None = None,
                    cols: int = 1, cap: float | None = None,
                    gap: float = _P_ITEM_GAP) -> list:
    """矩形分格（x, y, w, h）。带行高封顶 + 整体垂直居中。n<=0 早退（否则除零）。

    ⚠️ 边界默认值**不能用 `= _BX0` 这种写法**：默认参数在**定义时**求值，会把 846 永久
       钉死，`graph_svg(wide=True)` 改的 `_BX1` 就再也进不来（2026-10-14 铺满改造踩到）。
       一律 `None` + 调用时兜底到模块全局，才能跟随当前跨度。
    """
    x0 = _BX0 if x0 is None else x0
    x1 = _BX1 if x1 is None else x1
    y0 = _BY0 if y0 is None else y0
    y1 = _BY1 if y1 is None else y1
    if n <= 0 or x1 <= x0 or y1 <= y0:
        return []
    cols = max(1, min(int(cols), n))
    rows = (n + cols - 1) // cols
    cw = (x1 - x0 - gap * (cols - 1)) / cols
    ch = (y1 - y0 - gap * (rows - 1)) / rows
    if cap:
        ch = min(ch, float(cap))
    block = ch * rows + gap * (rows - 1)
    y_start = y0 + max(0.0, ((y1 - y0) - block) / 2.0)
    out = []
    for i in range(n):
        r, c = divmod(i, cols)
        out.append((x0 + c * (cw + gap), y_start + r * (ch + gap), cw, ch))
    return out


def _paper_boxes(n: int) -> list:
    """默认版式分格：n ≤ 3 单列（真正的纵向列表），n ≥ 4 转双列。

    ⚠️ 为什么 n≥4 才转多列：纸感条目比卡片多了 **序号 + 图标行** 与 **说明行**，
       单列 4 条只剩 102px/条，标题折两行就挤到正文。
    ⚠️ 列数不是 `min(n, 3)`（2026-10-22 改）：那个写法把 n=4 排成 3+1（右下空 2 格）、
       n=7 排成 3+3+1（末行孤一条），与本文档「n≤3 单列」也自相矛盾。
       现在按「空位最少 → 行数最少 → 卡更宽」挑：n=4→2 列(2+2)、n=5/6→3 列、
       n=7/8→2 列(577px，长标题不折爆)。卡宽下限 `_P_MIN_CARD_W` 保证一行 ≥9 字。
    """
    if n <= 0:
        return []
    if _BX1 > _BX1_NARROW + 1.0:
        cols = 1
        if n >= 4:
            best = None
            for c in range(2, min(n, 3) + 1):
                cw = (_BX1 - _BX0 - _P_ITEM_GAP * (c - 1)) / c
                if cw < _P_MIN_CARD_W:
                    continue
                r = (n + c - 1) // c
                key = (r * c - n, r, -cw)
                if best is None or key < best[0]:
                    best = (key, c)
            cols = best[1] if best else 2
    else:
        cols = 1 if n <= 3 else 2
    rows = (n + cols - 1) // cols
    cap = _P_ROW_CAP_WIDE if rows <= 2 else _P_ROW_CAP
    return _paper_boxes_in(n, _BX0, _BX1, _BY0, _BY1, cols=cols, cap=cap)


def _paper_metrics(h: float, w: float) -> dict:
    """按条目高度分档的字号 / 行数 / 图标尺寸（空间不足时先减行数、再减字号）。"""
    if h >= 185:
        ts, bs, tl, bl, isz = 26.0, 15.5, 2, 3, 42.0
    elif h >= 128:
        ts, bs, tl, bl, isz = 23.0, 14.5, 2, 2, 36.0
    elif h >= 98:
        ts, bs, tl, bl, isz = 20.5, 13.5, 2, 1, 30.0
    else:
        ts, bs, tl, bl, isz = 18.0, 13.0, 1, 1, 26.0
    if w < 340 and tl > 1:
        tl = 1                      # 窄列（双列 380 以下）标题只留一行
    return {"title": ts, "body": bs, "tlines": tl, "blines": bl, "icon": isz}


def _wrap_title(text: str, max_units: float, max_lines: int) -> list:
    """标题折行 + **孤字消歧**。

    ⚠️ 为什么不能直接用 `_wrap_cjk`：它按宽度硬切，末行常常只剩 1 个字 ——
       实测双列里「把目标拆成可验证的小步子」被切成 `…小步` + `子`，
       「每周复盘一次偏差原因」切成 `…偏差原` + `因`。中文排版里这叫孤字，
       比多占一行的代价更难看（而荧光笔只画在第一行，孤字那行会显得像溢出）。
    ⚠️ 只在**没有被截断**时重排：`_wrap_cjk` 截断时会给末行加 `…`，
       此时按宽度重新均分会把省略号丢掉、凭空变出字来。
    """
    t = (text or "").strip()
    lines = _wrap_cjk(t, max_units, max_lines)
    if len(lines) != 2 or not t or "".join(lines) != t or _text_units(lines[1]) > 2.0:
        return lines
    half = _text_units(t) / 2.0
    cut, acc = 0, 0.0
    for i, ch in enumerate(t):
        w = 1.0 if ord(ch) > 0x2E80 else 0.55
        if acc + w > half:
            cut = i
            break
        acc += w
    if cut <= 0 or cut >= len(t):
        return lines
    return [t[:cut], t[cut:]]


def _paper_item(x: float, y: float, w: float, h: float, kind: str, text,
                at, num: int, m: dict) -> str:
    """一条纵向知识条目 = 纸卡 + 左侧类别条 + 序号 + 图标 + 荧光笔标题 + 说明。"""
    ttl, body = _split_title_body(str(text or ""))
    pad = 17.0
    icon_x = x + pad + 24.0
    tx = icon_x + m["icon"] + 12.0
    tw = max(64.0, x + w - pad - tx)
    lines = _wrap_title(ttl, max(3.0, tw / m["title"]), m["tlines"]) if ttl else []
    blines = _wrap_title(body, max(3.0, tw / m["body"]), m["blines"]) if body else []
    if blines == [""]:
        blines = []
    lh_t = m["title"] * 1.34
    lh_b = m["body"] * 1.5
    block = lh_t * len(lines) + ((5.0 + lh_b * len(blines)) if blines else 0.0)
    top = y + max(12.0, (h - block) / 2.0)
    a0 = int(at or 0)
    accent = _P_ACCENT.get(kind, _P_TEAL)

    out = [
        f'<rect class="pitem pitem-{kind}" x="{x:.1f}" y="{y:.1f}" '
        f'width="{w:.1f}" height="{h:.1f}" rx="12"/>',
        f'<rect class="paccent" x="{x:.1f}" y="{y + 14:.1f}" width="4.5" '
        f'height="{max(8.0, h - 28):.1f}" rx="2.2" fill="{accent}"/>',
    ]
    # 序号 + 图标（与标题同一行）
    cy1 = top + lh_t * 0.52
    isz = m["icon"]
    out.append(
        f'<g class="el" data-at="{a0}">'
        f'<text class="pnum" x="{x + pad:.1f}" y="{cy1 + 5.5:.1f}">{int(num):02d}</text>'
        f'<rect class="ptile" x="{icon_x:.1f}" y="{cy1 - isz / 2:.1f}" '
        f'width="{isz:.1f}" height="{isz:.1f}" rx="9"/>'
        f'<g transform="translate({icon_x + isz * 0.1875:.1f},{cy1 - isz * 0.3125:.1f}) '
        f'scale({isz * 0.625 / 24.0:.4f})" fill="none" stroke="{_P_TEAL_TX}" '
        f'stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">'
        f'{_paper_glyph(kind, _P_TEAL_TX)}</g></g>')
    # 荧光笔：**画在文字之前**（DOM 顺序在后画的上层）→ 逐条显现时它是「笔迹滑过」，
    # 独立 `.el` 且只做淡入（`transform:none`，见模板 CSS）—— 位移会让它相对标题错位。
    # ⚠️ 标题折成两行时**每行各画一笔**：只画第一行会让第二行看起来像「没被标到的尾巴」
    #    （实测双列下 `每周复盘一` 被标、`次偏差原因` 空着，读起来像截断）。
    if lines:
        hl = "".join(
            _paper_stroke_hl(tx - 5.0, top + lh_t * (i + 0.06), 
                             min(tw + 10.0, _text_units(ln) * m["title"] + 11.0),
                             lh_t * 0.92)
            for i, ln in enumerate(lines))
        out.append(f'<g class="el p-hl p-hl-static" data-at="{a0 + 260}">{hl}</g>')
        out.append('<g class="el" data-at="%d">%s</g>' % (a0 + 130, "".join(
            f'<text class="ptitle" x="{tx:.1f}" y="{top + lh_t * (i + 0.76):.1f}" '
            f'font-size="{m["title"]:.0f}">{_esc(ln)}</text>'
            for i, ln in enumerate(lines))))
    if blines:
        out.append('<g class="el" data-at="%d">%s</g>' % (a0 + 400, "".join(
            f'<text class="pbody" x="{tx:.1f}" '
            f'y="{top + lh_t * len(lines) + 5.0 + lh_b * (j + 0.76):.1f}" '
            f'font-size="{m["body"]:.0f}">{_esc(ln)}</text>'
            for j, ln in enumerate(blines))))
    # 外层用 pwrap：模板给 `.pwrap` 置 `transform:none`，让位移只发生在**内层**，
    # 否则外层与内层的 9px 位移会叠加成 18px，条目像「跳」上来。
    return _el(at, "".join(out), cls="pwrap")


def _paper_head(sh: dict, k: int, at) -> str:
    """纸感镜头条：CHAPTER 标记 + 标题 + 手绘下划线。

    ⚠️ 右上角的程序化线稿人物已于 2026-10-22 按用户决定**整体移除**（判定为无信息
       价值；且三套姿势里 pose2 的手臂本就是悬空折线 + 横跨躯干，画坏了）。顶部右侧
       留白改由「左对齐标题 + 下方内容区」的对角关系承担，不再设第二视觉锚点。
    """
    ttl = _wrap_cjk(str(sh.get("l") or ""), 17.0, 1)[0]
    tw = min(560.0, max(70.0, _text_units(ttl) * 36.0 + 10.0))
    a0 = int(at or 0)
    head = _el(a0, f'<text class="pchap" x="72" y="72">CHAPTER {k + 1:02d}</text>'
                   f'<text class="pttl" x="72" y="124">{_esc(ttl)}</text>',
               cls="phead")
    ul = _el(a0 + 200, f'<path class="pul" d="M72 {143:.1f} '
                       f'C{72 + tw * 0.32:.1f} 139.6, {72 + tw * 0.66:.1f} 146.0, '
                       f'{72 + tw:.1f} 142.2"/>', cls="p-hl-static")
    return head + ul


def _pp_grid(pts: list) -> str:
    out = []
    for i, (p, (x, y, w, h)) in enumerate(zip(pts, _paper_boxes(len(pts))), 1):
        out.append(_paper_item(x, y, w, h, _kind(p), p.get("t"), p.get("at"), i,
                               _paper_metrics(h, w)))
    return "".join(out)


def _pp_flow(pts: list) -> str:
    """flow → 纵向步进：单列条目 + 条目之间的青绿连接线与箭头。

    ⚠️ 箭头挂**下一条**的 `data-at`（指向谁就跟谁一起亮）—— 与 `_lay_flow` 同一约定。
    """
    n = len(pts)
    if n < 2:
        return _pp_grid(pts)
    # 卡片限宽并居中：全宽 1168px 时 7 字标题只占 11%，右侧 89% 是空的。
    # 820px ≈ 一行 30 个全角字，仍明显宽于双列卡（577），不破坏「步进」观感。
    fw = min(_BX1 - _BX0, _P_FLOW_MAX_W)
    fx0 = _BX0 + (_BX1 - _BX0 - fw) / 2.0
    boxes = _paper_boxes_in(n, fx0, fx0 + fw, _BY0, _BY1, cols=1, cap=_P_ROW_CAP)
    out = []
    for i, (p, (x, y, w, h)) in enumerate(zip(pts, boxes), 1):
        out.append(_paper_item(x, y, w, h, _kind(p), p.get("t"), p.get("at"), i,
                               _paper_metrics(h, w)))
        if i < n:
            cx = x + 33.0
            out.append(_el(pts[i].get("at"),
                           f'<path class="pflow" d="M{cx:.1f} {y + h + 1.5:.1f} '
                           f'L{cx:.1f} {y + h + _P_ITEM_GAP - 4.0:.1f}" '
                           f'marker-end="url(#parr)"/>'))
    return "".join(out)


def _pp_pyramid(pts: list) -> str:
    """pyramid → 分层递进：顶层最窄（抽象），越往下越宽（具体），整体**居中**。

    ⚠️ 宽度必须往两侧**对称**收：只改左边界会让每条都右对齐（首条最左、末条最右），
       观感从「金字塔」变成「阶梯」，与 `_lay_pyramid` 的原意相反。
    """
    n = len(pts)
    if n < 2:
        return _pp_grid(pts)
    full_w = _BX1 - _BX0
    cx = (_BX0 + _BX1) / 2.0
    step = min(26.0, 78.0 / max(1, n - 1))
    boxes = _paper_boxes_in(n, _BX0, _BX1, _BY0, _BY1, cols=1, cap=_P_ROW_CAP)
    out = []
    for i, (p, (_, y, _, h)) in enumerate(zip(pts, boxes), 1):
        w = full_w - 2.0 * (n - i) * step
        out.append(_paper_item(cx - w / 2.0, y, w, h, _kind(p), p.get("t"),
                               p.get("at"), i, _paper_metrics(h, w)))
    return "".join(out)


def _pp_compare(pts: list) -> str:
    """compare → 左右两栏条目 + 中缝手绘分隔线与「对比」标记。"""
    n = len(pts)
    if n < 2:
        return _pp_grid(pts)
    half = (n + 1) // 2
    mid_gap = 62.0
    cx = (_BX0 + _BX1) / 2.0
    cw = ((_BX1 - _BX0) - mid_gap) / 2.0
    cols = ((pts[:half], _BX0), (pts[half:], _BX0 + cw + mid_gap))
    first_at = pts[0].get("at")
    out = [_el(first_at,
               f'<path class="pdiv" d="M{cx:.1f} {_BY0 + 6:.1f} '
               f'C{cx - 3.2:.1f} {_BY0 + 120:.1f}, {cx + 3.2:.1f} {_BY1 - 120:.1f}, '
               f'{cx:.1f} {_BY1 - 6:.1f}"/>'
               f'<g transform="translate({cx:.1f},{(_BY0 + _BY1) / 2:.1f}) rotate(-90)">'
               f'<text class="pvstx" text-anchor="middle">对比</text></g>')]
    # ⚠️ 两栏必须用**同一个行数**算格：各自按自己的条数算，5 条时左 3 行 143px、
    #    右 2 行 210px（差 47%），7 条时 104 vs 143.3（差 38%）—— 一眼就是「没对齐」。
    rows = max(half, n - half)
    for items, x in cols:
        if not items:
            continue
        boxes = _paper_boxes_in(rows, x, x + cw, _BY0, _BY1, cols=1,
                                cap=_P_ROW_CAP_WIDE)[:len(items)]
        for i, (p, (bx, by, bw, bh)) in enumerate(zip(items, boxes), 1):
            num = i if x == _BX0 else i + half
            out.append(_paper_item(bx, by, bw, bh, _kind(p), p.get("t"), p.get("at"),
                                   num, _paper_metrics(bh, bw)))
    return "".join(out)


def _pp_hub(pts: list) -> str:
    """hub → 顶部主条目（整幅纸卡）+ 余下条目两列 + 青绿「母线」辐射。"""
    n = len(pts)
    if n < 3:
        return _pp_grid(pts)
    cx = (_BX0 + _BX1) / 2.0
    head_h = 104.0
    head_w = min(600.0, _BX1 - _BX0)
    bus_y = _BY0 + head_h + 20.0
    boxes = _paper_boxes_in(n - 1, _BX0, _BX1, bus_y + 12.0, _BY1, cols=2,
                            cap=_P_ROW_CAP_WIDE)
    out = []
    # 母线：中心卡下沿 → 水平干线 → 每条分支的竖直支线（后画的分支会盖住线头，观感干净）
    out.append(_el(pts[0].get("at"),
                   f'<path class="pflow" d="M{cx:.1f} {_BY0 + head_h:.1f} '
                   f'L{cx:.1f} {bus_y:.1f}"/>'))
    if boxes:
        xs = [b[0] + b[2] / 2.0 for b in boxes]
        out.append(_el(pts[0].get("at"),
                       f'<path class="pflow" d="M{min(xs):.1f} {bus_y:.1f} '
                       f'L{max(xs):.1f} {bus_y:.1f}"/>'))
        for j, b in enumerate(boxes):
            bx = b[0] + b[2] / 2.0
            # 同列第 2 行起不再从母线出发（那条线会横穿上一行的卡片），
            # 改为「接上一张卡的下沿」→ 每列一条连续脊线，6 个分支都有线。
            # ⚠️ 旧实现统一画 `bus_y → bus_y+8`：同列 3 行支线 x 完全相同（重叠成 1 条），
            #    且 8px 够不到第 2 行卡（距母线 124px）→ 4 个分支悬空。
            y0 = bus_y if j < 2 else (boxes[j - 2][1] + boxes[j - 2][3])
            out.append(_el(pts[0].get("at"),
                           f'<path class="pflow" d="M{bx:.1f} {y0:.1f} '
                           f'L{bx:.1f} {b[1] - 3.0:.1f}" marker-end="url(#parr)"/>'))
    out.append(_paper_item(cx - head_w / 2.0, _BY0, head_w, head_h, _kind(pts[0]),
                           pts[0].get("t"), pts[0].get("at"), 1,
                           _paper_metrics(head_h, head_w)))
    for i, (p, (x, y, w, h)) in enumerate(zip(pts[1:], boxes), 2):
        out.append(_paper_item(x, y, w, h, _kind(p), p.get("t"), p.get("at"), i,
                               _paper_metrics(h, w)))
    return "".join(out)


def _paper_layout(layout: str, pts: list) -> str:
    """纸感版式分发。语义版式仍生效，只是换成纸感构图。"""
    lay = str(layout or "grid").strip().lower()
    if lay == "hub":
        return _pp_hub(pts)
    if lay == "compare":
        return _pp_compare(pts)
    if lay == "flow":
        return _pp_flow(pts)
    if lay == "pyramid":
        return _pp_pyramid(pts)
    return _pp_grid(pts)


# ---------- 结构图 · 清透主题（graph_clean） ----------
#
# 参照知识类课件信息图风格（2026-10-13 用户给的参考图）：
#   · 浅灰白底 + 紫主色 + 白卡片（图标 + 黑标题 + 灰紫副标题），hub 是「左侧紫色
#     根节点 + 右侧分支卡片 + 弯头连线」的树状图，compare 是「紫表头 + 白行」的表格。
#   · 与 graph / graph_paper **同一份知识结构**（points / shots / 分屏 / data-at），
#     本节只负责「怎么画」。逐条显现分三拍：容器（卡 + 图标）→ 标题 → 副标题。
#   · **色值显式**：本项目「颜色类」未实现，写类名会掉成黑色。正文/副标题分开：
#     `#1B1B2A`（白底 14.8:1）/ `#6E6E86`（白底 5.2:1，且 ≥ 项目次要文字下限 #6e6e6e）。
#   · 类别（kind）只走**图标色 + 图标底 + 左侧细条**表达（语义），副标题恒用灰紫 ——
#     绿/橙直接写字在 14px 下过不了 AA（#268A66 白底仅 4.0:1），不拿可读性换花哨。
#   · **不编造内容**：表头用「该栏第一个要点」充当（内容仍是 LLM 给的原文），
#     表头文字不够时不补「方案 A / 方面一」这类凭空标签。

_C_PRI = "#6B5CE7"        # 紫主色（根节点 / 表头 / 步骤圆点 / 强调条）
_C_PRI_D = "#5747C9"      # 紫深色（小字强调：右栏标题等，白底 6.3:1）
_C_INK = "#1B1B2A"        # 标题 / 正文（白底 14.8:1）
_C_SUB = "#6E6E86"        # 副标题 / 次要（白底 5.2:1）
_C_CARD = "#FFFFFF"       # 卡片底
_C_LINE = "#E6E6F2"       # 卡片描边 / 分隔线
_C_SEP = "#ECECF4"        # 表格行分隔（比描边更浅一档）
_C_LINK = "#C8C3EF"       # 连线（hub 弯头 / flow 时间轴）
_C_TILE = {"concept": "#EFEDFC", "good": "#E6F5EE", "cost": "#FBEFE2"}
_C_KIND = {"concept": "#6B5CE7", "good": "#2FA97C", "cost": "#E08A3C"}
_C_GAP = 16.0


def _clean_metrics(h: float, w: float) -> dict:
    """按条目高度分档的字号 / 行数 / 图标尺寸（空间不足时先减行数、再减字号）。"""
    if h >= 170:
        ts, ss, tl, sl, isz = 25.0, 15.0, 2, 2, 40.0
    elif h >= 120:
        ts, ss, tl, sl, isz = 22.0, 14.5, 2, 2, 34.0
    elif h >= 88:
        ts, ss, tl, sl, isz = 20.0, 13.5, 2, 1, 28.0
    else:
        ts, ss, tl, sl, isz = 18.0, 13.0, 1, 1, 24.0
    if w < 340 and tl > 1:
        tl = 1                      # 窄列标题只留一行（与 _paper_metrics 同一纪律）
    return {"title": ts, "sub": ss, "tlines": tl, "slines": sl, "icon": isz}


def _clean_boxes(n: int, cols: int | None = None, cap: float | None = None,
                 x0: float | None = None, x1: float | None = None,
                 y0: float | None = None, y1: float | None = None) -> list:
    """清透版式分格：默认 n ≤ 3 单列、≥4 双列（与纸感同口径），行高封顶 + 垂直居中。

    ⚠️ 边界走 `None` 兜底（同 `_paper_boxes_in`）：写死默认值会把铺满档的 `_BX1` 挡在门外。
    """
    if n <= 0:
        return []
    if cols is None:
        # ⚠️ 铺满档最多三列（同 `_card_boxes` / `_paper_boxes`：双列在 1168px 跨度上
        #    每列 577px，卡片内容只占左侧一条）。窄档口径原样保留。
        # ⚠️⚠️ 但 **n == 4 必须用两列**（2026-10-22）：三列会排成 3+1，第二行只剩一张卡
        #    贴在左边、右侧空出 2/3 —— 而「每屏 4 条」是提示词（每屏 3-6 个元素）里最
        #    常见的档位之一。2×2 的卡宽 577 反而更舒展，字号档位不受影响（按高度分档）。
        if _BX1 > _BX1_NARROW + 1.0:
            cols = 2 if n == 4 else min(n, 3)
        else:
            cols = 1 if n <= 3 else 2
    rows = (n + cols - 1) // cols
    if cap is None:
        cap = 200.0 if rows <= 2 else 150.0
    return _paper_boxes_in(n, x0, x1, y0, y1, cols=cols, cap=cap, gap=_C_GAP)


def _clean_item(x: float, y: float, w: float, h: float, kind: str, text,
                at, m: dict) -> str:
    """一张清透卡片 = 白卡 + 左侧类别细条 + 图标（浅色方块底）+ 黑标题 + 灰紫副标题。

    显现三拍（与渲染层 `ms >= data-at` 契约一致）：
      容器（卡 + 细条 + 图标）@a0 → 标题 @a0+130 → 副标题 @a0+300。
    """
    ttl, body = _split_title_body(str(text or ""))
    pad = 18.0
    accent = _C_KIND.get(kind, _C_PRI)
    tile_bg = _C_TILE.get(kind, _C_TILE["concept"])
    isz = m["icon"]
    tile_x = x + pad
    tile_y = y + h / 2.0 - isz / 2.0
    tx = tile_x + isz + 14.0
    tw = max(64.0, x + w - pad - tx)
    lines = _wrap_title(ttl, max(3.0, tw / m["title"]), m["tlines"]) if ttl else []
    slines = _wrap_title(body, max(3.0, tw / m["sub"]), m["slines"]) if body else []
    if slines == [""]:
        slines = []
    lh_t = m["title"] * 1.32
    lh_s = m["sub"] * 1.45
    block = lh_t * len(lines) + ((4.0 + lh_s * len(slines)) if slines else 0.0)
    top = y + max(10.0, (h - block) / 2.0)
    a0 = int(at or 0)

    container = (
        f'<rect class="citem citem-{kind}" x="{x:.1f}" y="{y:.1f}" '
        f'width="{w:.1f}" height="{h:.1f}" rx="14"/>'
        f'<rect x="{x:.1f}" y="{y + 14:.1f}" width="4" '
        f'height="{max(8.0, h - 28):.1f}" rx="2" fill="{accent}"/>'
        f'<rect x="{tile_x:.1f}" y="{tile_y:.1f}" width="{isz:.1f}" '
        f'height="{isz:.1f}" rx="10" fill="{tile_bg}"/>'
        f'<g transform="translate({tile_x + isz * 0.1875:.1f},'
        f'{tile_y + isz * 0.1875:.1f}) scale({isz * 0.625 / 24.0:.4f})" '
        f'fill="none" stroke="{accent}" stroke-width="1.9" '
        f'stroke-linecap="round" stroke-linejoin="round">'
        f'{_paper_glyph(kind, accent)}</g>')
    out = [container]
    if lines:
        out.append('<g class="el" data-at="%d">%s</g>' % (a0 + 130, "".join(
            f'<text class="ctitle" x="{tx:.1f}" y="{top + lh_t * (i + 0.74):.1f}" '
            f'font-size="{m["title"]:.0f}">{_esc(ln)}</text>'
            for i, ln in enumerate(lines))))
    if slines:
        out.append('<g class="el" data-at="%d">%s</g>' % (a0 + 300, "".join(
            f'<text class="csub" x="{tx:.1f}" '
            f'y="{top + lh_t * len(lines) + 4.0 + lh_s * (j + 0.72):.1f}" '
            f'font-size="{m["sub"]:.0f}">{_esc(ln)}</text>'
            for j, ln in enumerate(slines))))
    # 外层用 cwrap：模板给它 transform:none，位移只发生在内层（与纸感 .pwrap 同一原因）。
    return _el(at, "".join(out), cls="cwrap")


def _clean_root(x: float, y: float, w: float, h: float, text, at) -> str:
    """hub 的根节点：紫色填充卡 + 白色标题/副标题（参考图左侧的根卡片）。"""
    ttl, body = _split_title_body(str(text or ""))
    pad = 22.0
    tw = max(60.0, w - 2 * pad)
    lines = _wrap_title(ttl, max(3.0, tw / 22.0), 3) if ttl else []
    slines = _wrap_title(body, max(3.0, tw / 14.0), 2) if body else []
    if slines == [""]:
        slines = []
    lh_t, lh_s = 22.0 * 1.32, 14.0 * 1.5
    block = lh_t * len(lines) + ((6.0 + lh_s * len(slines)) if slines else 0.0)
    top = y + max(12.0, (h - block) / 2.0)
    a0 = int(at or 0)
    out = [f'<rect class="croot" x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" '
           f'height="{h:.1f}" rx="16"/>']
    if lines:
        out.append('<g class="el" data-at="%d">%s</g>' % (a0 + 130, "".join(
            f'<text class="croott" x="{x + pad:.1f}" y="{top + lh_t * (i + 0.74):.1f}" '
            f'font-size="22">{_esc(ln)}</text>' for i, ln in enumerate(lines))))
    if slines:
        out.append('<g class="el" data-at="%d">%s</g>' % (a0 + 300, "".join(
            f'<text class="croots" x="{x + pad:.1f}" '
            f'y="{top + lh_t * len(lines) + 6.0 + lh_s * (j + 0.72):.1f}" '
            f'font-size="14">{_esc(ln)}</text>' for j, ln in enumerate(slines))))
    return _el(at, "".join(out), cls="cwrap")


def _clean_elbow(x0: float, y0: float, x1: float, y1: float, bx: float,
                 r: float = 12.0) -> str:
    """圆角弯头连线：x0,y0 →（经竖直母线 bx）→ x1,y1（参考图 hub 的连线样式）。"""
    if abs(y1 - y0) < 1.0:
        return f"M{x0:.1f} {y0:.1f} L{x1:.1f} {y1:.1f}"
    r = min(r, abs(y1 - y0) / 2.0, max(2.0, (bx - x0) / 2.0),
            max(2.0, (x1 - bx) / 2.0))
    s = 1.0 if y1 > y0 else -1.0
    return (f"M{x0:.1f} {y0:.1f} L{bx - r:.1f} {y0:.1f} "
            f"Q{bx:.1f} {y0:.1f} {bx:.1f} {y0 + s * r:.1f} "
            f"L{bx:.1f} {y1 - s * r:.1f} "
            f"Q{bx:.1f} {y1:.1f} {bx + r:.1f} {y1:.1f} L{x1:.1f} {y1:.1f}")


def _clean_head(sh: dict, k: int, at, n_pts: int) -> str:
    """清透镜头条：紫色序号 + 灰色引导 + 大标题 + 短紫色强调条（参考图的页眉）。"""
    ttl = _wrap_cjk(str(sh.get("l") or ""), 24.0, 1)[0]
    a0 = int(at or 0)
    # ⚠️ 引导小字原文是「镜头 · N 个要点」（2026-10-14 二次移除）：
    #    和结构图皮肤标题下那行「镜头 N」同性质，都是**制作态**信息，按产品要求去掉
    #    「镜头」字样；「N 个要点」保留 —— 它对听众是内容信息（本段要讲几条），
    #    不是制作信息。若日后也要去掉，把整条 `<text class="ckick2">` 删掉即可
    #    （紫色序号 `ckick` 独立成节点，不受影响）。
    # ⚠️⚠️ `n_pts <= 0` = **不显示**这行（2026-10-22）：多屏镜头由调用方传 0。
    #    页眉在屏循环之外只生成一次，拿的是整个镜头的条数；分屏后每屏只显示其中
    #    一部分 → 三屏都写「9 个要点」而每屏实有 3 条，等于画面在说假话。
    #    单屏时数字 == 屏内条数，照常显示。
    kick2 = (f'<text class="ckick2" x="112" y="62">{int(n_pts)} 个要点</text>'
             if int(n_pts) > 0 else "")
    head = _el(a0, f'<text class="ckick" x="72" y="64">{k + 1:02d}</text>'
                   f'{kick2}'
                   f'<text class="cttl2" x="72" y="118">{_esc(ttl)}</text>',
               cls="cwrap")
    bar = _el(a0 + 200, '<rect class="cbar" x="72" y="134" width="46" '
                        'height="5" rx="2.5"/>', cls="cwrap")
    return head + bar


def _cl_grid(pts: list) -> str:
    """grid：清透卡片平铺（图标 + 标题 + 副标题）。

    ⚠️ 末行不满时**整行水平居中**（2026-10-22）：5 条 = 3+2、7 条 = 3+3+1 时，
       左对齐会让最后一行贴左、右边空出整格，像「少画了一张卡」。
       居中只改 x 偏移，不动列宽 / 字号 / 行高 —— 卡片尺寸契约不变。
       `cols=1` 的档位（n ≤ 3 窄档）算出来的 shift 恒为 0，天然无副作用。
    """
    boxes = _clean_boxes(len(pts))
    if boxes:
        last_y = boxes[-1][1]
        last = [b for b in boxes if abs(b[1] - last_y) < 1.0]
        if last and len(last) < len(boxes):
            span = (last[-1][0] + last[-1][2]) - last[0][0]
            shift = ((_BX1 - _BX0) - span) / 2.0 - (last[0][0] - _BX0)
            if abs(shift) > 0.5:
                boxes = [(x + (shift if abs(y - last_y) < 1.0 else 0.0), y, w, h)
                         for (x, y, w, h) in boxes]
    out = []
    for p, (x, y, w, h) in zip(pts, boxes):
        out.append(_clean_item(x, y, w, h, _kind(p), p.get("t"), p.get("at"),
                               _clean_metrics(h, w)))
    return "".join(out)


def _cl_flow(pts: list) -> str:
    """flow → 纵向时间轴：左侧紫色步骤圆点 + 竖直轴线 + 右侧整宽卡片。

    ⚠️ 轴线分段挂在**下一条**的 `data-at`（指向谁跟谁一起亮）—— 与 `_lay_flow` 同一约定。
    """
    n = len(pts)
    if n < 2:
        return _cl_grid(pts)
    rail_x = _BX0 + 30.0
    boxes = _clean_boxes(n, cols=1, cap=128.0, x0=_BX0 + 64.0)
    out = []
    for i, (p, (x, y, w, h)) in enumerate(zip(pts, boxes), 1):
        cy = y + h / 2.0
        if i > 1:
            py_cy = boxes[i - 2][1] + boxes[i - 2][3] / 2.0
            # ⚠️ 线段必须止于圆点边缘（±13 = 圆半径）：连到圆心的话，后画的线段会盖住
            #    上一个圆点的下半截与数字（实测截图里 2/3 被竖线穿脸）。
            out.append(_el(p.get("at"),
                           f'<path class="clink" d="M{rail_x:.1f} {py_cy + 13.0:.1f} '
                           f'L{rail_x:.1f} {cy - 13.0:.1f}"/>'))
        out.append(_el(p.get("at"),
                       f'<circle class="cstep" cx="{rail_x:.1f}" cy="{cy:.1f}" r="13"/>'
                       f'<text class="cstepn" x="{rail_x:.1f}" y="{cy + 4.4:.1f}" '
                       f'text-anchor="middle">{i}</text>'))
        out.append(_clean_item(x, y, w, h, _kind(p), p.get("t"), p.get("at"),
                               _clean_metrics(h, w)))
    return "".join(out)


def _cl_hub(pts: list) -> str:
    """hub → 参考图的招牌构图：左侧紫色根节点 + 右侧单列分支卡 + 圆角弯头连线。

    ⚠️ 连线先画（后画的卡片盖住线头更干净）；连线挂**分支**的 `data-at`。
    """
    n = len(pts)
    if n < 3:
        return _cl_grid(pts)
    root, branches = pts[0], pts[1:]
    rx, rw, rh = _BX0, 232.0, 170.0
    ry = (_BY0 + _BY1 - rh) / 2.0
    bus_x = rx + rw + 40.0
    bx0 = bus_x + 58.0
    boxes = _clean_boxes(len(branches), cols=1, cap=132.0, x0=bx0)
    out = []
    for p, (x, y, w, h) in zip(branches, boxes):
        out.append(_el(p.get("at"),
                       f'<path class="clink" d="'
                       f'{_clean_elbow(rx + rw, ry + rh / 2.0, x, y + h / 2.0, bus_x)}"/>'))
    out.append(_clean_root(rx, ry, rw, rh, root.get("t"), root.get("at")))
    for p, (x, y, w, h) in zip(branches, boxes):
        out.append(_clean_item(x, y, w, h, _kind(p), p.get("t"), p.get("at"),
                               _clean_metrics(h, w)))
    return "".join(out)


def _cl_compare(pts: list) -> str:
    """compare → 紫表头表格（参考图 2）：每栏第一个要点作紫色表头卡，其余作白行。

    ⚠️ 表头不是编造的标签：它仍是 LLM 给的要点原文（对比屏里每栏第一项通常就是
       被对比的那一方）；行与行之间用发线分隔，表框随表头一起亮、行随各自时间亮。
    """
    n = len(pts)
    if n < 2:
        return _cl_grid(pts)
    half = (n + 1) // 2
    mid_gap = 64.0
    cw = ((_BX1 - _BX0) - mid_gap) / 2.0
    cols = ((pts[:half], _BX0), (pts[half:], _BX0 + cw + mid_gap))
    cx = (_BX0 + _BX1) / 2.0
    head_h = 64.0
    # ⚠️⚠️ 两栏**共用行高**（2026-10-22）：各栏按自己的行数算 `row_h` 时，n=5（3+2）
    #    会让左栏表框底 484、右栏 374 —— 差 110px，肉眼就是「一栏长一栏短」。
    #    改成按两栏最大行数统一算，短栏底部留白、两栏底边对齐。
    _y_top = _BY0 + head_h + 10.0
    _max_rows = max([len(it) - 1 for it, _x in cols] + [0])
    _row_h = min(110.0, (_BY1 - _y_top) / max(1, _max_rows))
    out = [_el(pts[0].get("at"),
               f'<circle class="cvs" cx="{cx:.1f}" cy="{_BY0 + 150:.1f}" r="20"/>'
               f'<text class="cvst" x="{cx:.1f}" y="{_BY0 + 155.5:.1f}" '
               f'text-anchor="middle">VS</text>')]
    for items, x in cols:
        if not items:
            continue
        head, rows = items[0], items[1:]
        a0 = int(head.get("at") or 0)
        ttl = _wrap_title(_split_title_body(str(head.get("t") or ""))[0],
                          max(4.0, (cw - 36.0) / 20.0), 2)
        lh = 20.0 * 1.3
        ty = _BY0 + head_h / 2.0 - (len(ttl) - 1) * lh / 2.0 + 20.0 * 0.36
        out.append(_el(a0,
                       f'<rect class="ctab" x="{x:.1f}" y="{_BY0:.1f}" '
                       f'width="{cw:.1f}" height="{head_h:.1f}" rx="12"/>',
                       cls="cwrap") +
                   '<g class="el" data-at="%d">%s</g>' % (a0 + 130, "".join(
                       f'<text class="ctabt" x="{x + cw / 2.0:.1f}" '
                       f'y="{ty + i * lh:.1f}" text-anchor="middle" '
                       f'font-size="20">{_esc(ln)}</text>'
                       for i, ln in enumerate(ttl))))
        if not rows:
            continue
        y_top = _y_top
        row_h = _row_h
        tab_h = row_h * max(1, _max_rows)
        # 表框随表头一起亮（空表框 = 待填充的表格，行再逐条长出来）
        out.append(_el(a0, f'<rect class="crows" x="{x:.1f}" y="{y_top:.1f}" '
                           f'width="{cw:.1f}" height="{tab_h:.1f}" rx="12"/>',
                       cls="cwrap"))
        for ri, p in enumerate(rows):
            ry = y_top + ri * row_h
            pa0 = int(p.get("at") or 0)
            kind = _kind(p)
            frag = []
            if ri > 0:
                frag.append(f'<path class="csep" d="M{x + 14:.1f} {ry:.1f} '
                            f'L{x + cw - 14:.1f} {ry:.1f}"/>')
            frag.append(f'<circle cx="{x + 26:.1f}" cy="{ry + row_h / 2.0:.1f}" '
                        f'r="4" fill="{_C_KIND.get(kind, _C_PRI)}"/>')
            out.append(_el(pa0, "".join(frag), cls="cwrap"))
            ttl2, body2 = _split_title_body(str(p.get("t") or ""))
            tx = x + 44.0
            tw = max(60.0, x + cw - 16.0 - tx)
            lines = _wrap_title(ttl2, max(3.0, tw / 18.0), 2)
            slines = _wrap_title(body2, max(3.0, tw / 13.5), 1) if body2 else []
            if slines == [""]:
                slines = []
            lh_t, lh_s = 18.0 * 1.32, 13.5 * 1.4
            block = lh_t * len(lines) + ((3.0 + lh_s) if slines else 0.0)
            top = ry + max(8.0, (row_h - block) / 2.0)
            txt = "".join(
                f'<text class="ctitle" x="{tx:.1f}" y="{top + lh_t * (i + 0.74):.1f}" '
                f'font-size="18">{_esc(ln)}</text>' for i, ln in enumerate(lines))
            if slines:
                txt += (f'<text class="csub" x="{tx:.1f}" '
                        f'y="{top + lh_t * len(lines) + 3.0 + lh_s * 0.72:.1f}" '
                        f'font-size="13.5">{_esc(slines[0])}</text>')
            out.append('<g class="el" data-at="%d">%s</g>' % (pa0 + 130, txt))
    return "".join(out)


def _cl_pyramid(pts: list) -> str:
    """pyramid → 紫色渐变层叠：顶层最深（紫主色白字），逐层变浅（浅紫底深色字）。"""
    n = len(pts)
    if n < 2:
        return _cl_grid(pts)
    gap = 16.0
    h = min(120.0, ((_BY1 - _BY0) - gap * (n - 1)) / n)
    block = h * n + gap * (n - 1)
    y0 = _BY0 + ((_BY1 - _BY0) - block) / 2.0
    cx = (_BX0 + _BX1) / 2.0
    full = _BX1 - _BX0

    def _hx(c: str) -> tuple:
        return int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)

    c0, c1 = _hx(_C_PRI), _hx("#EFEDFC")
    out = []
    for i, p in enumerate(pts):
        t = i / max(1, n - 1)
        r, g, b = (round(c0[j] + (c1[j] - c0[j]) * t) for j in range(3))
        fill = f"#{r:02X}{g:02X}{b:02X}"
        # ⚠️⚠️ 白字**只给最顶层**（t == 0，底色 #6B5CE7 白字 4.87:1，过 AA）。
        #    原判据 `t < 0.5` 是按**层序号比例**选字色，不是按背景实际亮度：渐变走到
        #    t=0.4 时底色已是 #A096EF，白字只剩 2.59:1（6 层时第 3 层）—— 画面主体读不清。
        #    切深字后最差的一层是 t=0.2（底色 #8579EB）深字 4.80:1，仍过 AA。
        ink = "#FFFFFF" if t <= 0.0 else _C_INK
        w = full * (0.40 + 0.60 * t)
        y = y0 + i * (h + gap)
        a0 = int(p.get("at") or 0)
        lines = _wrap_title(_split_title_body(str(p.get("t") or ""))[0],
                            max(4.0, (w - 48.0) / 18.0), 2)
        lh = 18.0 * 1.32
        ty = y + h / 2.0 - (len(lines) - 1) * lh / 2.0 + 18.0 * 0.36
        out.append(_el(a0, f'<rect class="cpyr" x="{cx - w / 2.0:.1f}" y="{y:.1f}" '
                           f'width="{w:.1f}" height="{h:.1f}" rx="12" '
                           f'fill="{fill}"/>', cls="cwrap") +
                   '<g class="el" data-at="%d">%s</g>' % (a0 + 130, "".join(
                       f'<text x="{cx:.1f}" y="{ty + li * lh:.1f}" '
                       f'text-anchor="middle" font-size="18" font-weight="600" '
                       f'fill="{ink}">{_esc(ln)}</text>'
                       for li, ln in enumerate(lines))))
    return "".join(out)


def _clean_layout(layout: str, pts: list) -> str:
    """清透版式分发。语义版式仍生效，只是换成清透构图。"""
    lay = str(layout or "grid").strip().lower()
    if lay == "hub":
        return _cl_hub(pts)
    if lay == "compare":
        return _cl_compare(pts)
    if lay == "flow":
        return _cl_flow(pts)
    if lay == "pyramid":
        return _cl_pyramid(pts)
    return _cl_grid(pts)


def graph_svg(shots: list, pts_by_scene: list, at_by_scene: list,
              skin: str = "graph", wide: bool = False) -> str:
    """铺满开关的持有者：把布局区跨度切到对应档，跑完**必定**复位。

    ⚠️ 为什么是「模块级切换 + 锁」而不是给布局函数加参数：`_BX1` 被二十多个绘制原语
       直接读（`_card_boxes` / `_lay_*` / `_pp_*` / `_cl_*` 全在内），逐个加参数会把
       签名污染一遍、且极易漏 —— 漏掉的表现是「那一种版式没铺满」，肉眼很难发现。
    ⚠️ 锁必须包住**整段 SVG 生成**（含全部布局计算），不能只包赋值那一行：
       赋值完就解锁的话，另一个线程会读到「跨已改、图还没画」的中间态。
    ⚠️ `finally` 里一律复位到**窄档**（而不是 prev）：只有两档，复位成确定值
       比「记住别人设过什么」更难出错。
    """
    global _BX1
    with _LAYOUT_LOCK:
        _BX1 = _BX1_WIDE if wide else _BX1_NARROW
        try:
            return _graph_svg_body(shots, pts_by_scene, at_by_scene, skin)
        finally:
            _BX1 = _BX1_NARROW


def _graph_svg_body(shots: list, pts_by_scene: list, at_by_scene: list,
                    skin: str = "graph") -> str:
    """按 points 与**每个镜头的分屏**生成结构图皮肤的全部 SVG 场景。

    每个镜头一个 `<svg class="scene" data-scene="k">`（**场景数 == 镜头数** ——
    右侧章节栏、时间轴导航、`SCENE_OF` 都按镜头走，这一层不动）；镜头内部再按「屏」切层：
    每屏一个 `<g class="sub" data-a data-b>`，前端按毫秒**整屏切换**（换屏 = 换一版式）。
    卡片仍在各屏内按 `data-at` 逐个点亮。
    ⚠️ viewBox 固定 `0 0 1280 720`（与 `.screen` 的 1920×1080 同为 16:9，
       缩放因子恰好 1.5，不会出现 letterbox —— 这点必须保持，改 viewBox 会让
       卡片坐标与右侧要点栏的百分比位置错位）。
    ⚠️ 版式取自**每一屏**（`shots[k].screens[j].layout`，LLM 生成时给出、后端钳制后落库）；
       存量作品没有 `screens` → 整镜一屏、沿用 `shots[k].layout`，**零回归**。
    ⚠️ `sc` 越界的点一律归到**最后一屏**，绝不丢元素（宁可位置不精确，也不能凭空少一个点亮）。
    ⚠️ `skin="graph_paper"` / `skin="graph_clean"` 时只换**画法**（纸感 / 清透），
       场景/分屏/`data-at` 与 `graph` 完全同构 —— 三者共用 `_norm_screens`、`points`
       与分屏时间区间，所以几种皮肤可以逐屏对齐比较。
    """
    theme = {"graph_paper": "paper", "graph_clean": "clean"}.get(
        str(skin or "").strip().lower(), "dark")
    out = []
    for k, sh in enumerate(shots or []):
        pts = (pts_by_scene[k] if k < len(pts_by_scene) else []) or []
        ttl_at = int((at_by_scene[k] if k < len(at_by_scene) else 0) or 0)
        screens = (sh or {}).get("screens") or []
        if not screens:
            lay0 = str((sh or {}).get("layout") or "grid").strip().lower()
            screens = [{"layout": lay0 if lay0 in svc.GRAPH_LAYOUTS else "grid",
                        "a": int((sh or {}).get("a") or 0),
                        "b": int((sh or {}).get("b") or 0)}]
        n_scr = len(screens)
        groups = [[] for _ in range(n_scr)]
        for p in pts:
            j = int(p.get("sc") or 0)
            if not (0 <= j < n_scr):
                j = n_scr - 1
            groups[j].append(p)
        out.append(f'<svg class="scene" data-scene="{k}" viewBox="0 0 1280 720">')
        # 箭头/曲线共用的 marker（每个 svg 独立定义，避免跨 svg 引用失效）
        # ⚠️ 纸感主题要另起一个青色 marker：深色皮肤的 `#575b6e` 落在纸底上几乎看不见。
        #    清透主题的连线是弯头/时间轴（无箭头端）→ 不需要 marker。
        if theme == "paper":
            out.append('<defs><marker id="parr" viewBox="0 0 10 10" refX="8" refY="5" '
                       'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
                       f'<path d="M2 1L8 5L2 9" fill="none" stroke="{_P_TEAL}" '
                       'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
                       '</marker></defs>')
        elif theme == "dark":
            out.append('<defs><marker id="garr" viewBox="0 0 10 10" refX="8" refY="5" '
                       'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
                       '<path d="M2 1L8 5L2 9" fill="none" stroke="#575b6e" '
                       'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>'
                       '</marker></defs>')
        if theme == "paper":
            out.append(_paper_head(sh, k, ttl_at))
        elif theme == "clean":
            # ⚠️ 多屏镜头传 0（= 不显示「N 个要点」）：页眉在屏循环外只生成一次，
            #    整镜条数写到每一屏上就是假信息（2026-10-22 实测 3 屏各写「9 个要点」）。
            out.append(_clean_head(sh, k, ttl_at, len(pts) if n_scr == 1 else 0))
        else:
            # ⚠️ 标题必须截断：`.ttl` 是 34px 居中，可用宽度约 24 个全角字符；
            #    LLM 现在给的镜头标题 ≤16 字（生成侧已钳制），这里只做双保险。
            ttl = _wrap_cjk(str(sh.get("l") or ""), 24.0, 1)[0]
            # ⚠️ 居中横坐标必须**现算**：451 是窄档 (56+846)/2，铺满档中点是 640 ——
            #    写死会让标题在铺满档整体偏左（2026-10-14 铺满改造）。
            mid = (_BX0 + _BX1) / 2.0
            # ⚠️⚠️ 标题下方那行「镜头 N」**已整体移除、不再生成**（2026-10-14 二次移除）：
            #    它是「制作态」信息（镜头序号），出现在给学生看的成片里是纯噪音 ——
            #    与上一轮移除的「左上角镜头标签 / 右侧章节栏」同一性质。
            #    口播·结构图（talking_head + inner=graph）与结构图共用本分支，一并生效。
            #    回退办法：把下面注释掉的那一行 text 加回 `</g>` 之前（备份见
            #    `_fetch_probe/_pre_kicklabel_removal_20261014b/stage.py`）。
            #      f'<text class="stl" x="{mid:.0f}" y="152" text-anchor="middle">'
            #      f'镜头 {_esc(sh.get("n") or (k + 1))}</text>'
            #    `.ttl` 仍留在 `.el[data-at]` 组里 —— 标题的入场动画不受影响。
            out.append(
                f'<g class="el" data-at="{ttl_at}">'
                f'<text class="ttl" x="{mid:.0f}" y="112" text-anchor="middle">{_esc(ttl)}</text>'
                f'</g>')
        for j, scr in enumerate(screens):
            layout = str(scr.get("layout") or "grid").strip().lower()
            if layout not in svc.GRAPH_LAYOUTS:
                layout = "grid"
            if theme == "paper":
                body = _paper_layout(layout, groups[j])
            elif theme == "clean":
                body = _clean_layout(layout, groups[j])
            else:
                body = _LAYOUT_FN.get(layout, _lay_grid)(groups[j])
            # ⚠️ data-a / data-b 必须原样带出去：前端只靠这两个数决定「现在该显示哪一屏」，
            #    不给就只能一屏到底（改造前的行为），子屏等于没做。
            out.append(f'<g class="sub" data-sub="{j}" '
                       f'data-a="{int(scr.get("a") or 0)}" '
                       f'data-b="{int(scr.get("b") or 0)}">{body}</g>')
        out.append('</svg>')
    return "\n".join(out)


# ---------- 材料页导出 ----------

def _placeholder_page(page):
    """出图失败页的占位图（深灰底 + 页码），保住 `pages/img_NN` 编号稠密。

    ⚠️ 为什么必须占位而不是跳过：舞台页按 `SRC_PAGES[p-1]` **下标**直取文件名，
       跳过一个编号会让这页之后**整片错页**且全程静默（2026-10-22 探针证实）。
    """
    from PIL import Image, ImageDraw
    w = max(2, int(float(getattr(page, "width", 595) or 595) * PAGE_DPI / 72.0))
    h = max(2, int(float(getattr(page, "height", 842) or 842) * PAGE_DPI / 72.0))
    im = Image.new("RGB", (w, h), (60, 60, 68))
    d = ImageDraw.Draw(im)
    d.text((w // 2, h // 2), f"Page {getattr(page, 'no', '?')} render failed",
           fill=(200, 200, 210), anchor="mm")
    return im


def _page_image(loc, page):
    """把一页转成 PIL.Image（统一入口，兼容三种页面对象）。

    - `_TextLocator` 的页：`_handle` 是 pymupdf Page，按 `PAGE_DPI` 渲染成位图；
    - `_RenderedPage` / `_FileImagePage`：自带 `_img()`（前者返回 pymupdf Pixmap，
      后者返回磁盘路径）。
    """
    from PIL import Image

    handle = getattr(page, "_handle", None)
    if handle is not None and loc.kind == "text":
        pix = handle.get_pixmap(dpi=PAGE_DPI, alpha=False)
        return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)

    fn = getattr(page, "_img", None)
    if callable(fn):
        src = fn()
        if isinstance(src, Image.Image):
            return src.convert("RGB")
        if isinstance(src, (str, Path)):
            with Image.open(str(src)) as im:
                return im.convert("RGB")
        # pymupdf Pixmap
        if hasattr(src, "samples"):
            return Image.frombytes("RGB", (src.width, src.height), src.samples)
    return None


def export_pages(db, v) -> tuple:
    """导出材料页到 `videos_root/{id}/pages/img_NN.jpg`，返回 `(文件名列表, aspect)`。

    aspect = **宽/高**（舞台页用它定量 `.pagewrap`）。
    ⚠️ 文件名从 `img_01` 起两位补零 —— 舞台页按 `SRC_PAGES[p-1]` 直取，
       命名规则与序号必须严格对应，否则第 10 页之后会因排序错位拿到隔壁页。
    """
    mid = svc.first_material_id(v)
    if not mid:
        return [], 0.0
    loc, _reason = locate_svc.build_locator(db, mid, dpi=PAGE_DPI)
    if loc is None:
        return [], 0.0
    outdir = svc.video_dir(v.id) / "pages"
    outdir.mkdir(parents=True, exist_ok=True)
    names, aspect = [], 0.0
    try:
        for i, page in enumerate(loc.pages, 1):
            try:
                im = _page_image(loc, page)
            except BaseException:
                im = None
            if im is None:
                # ⚠️ 绝不 continue（编号空洞 = 整片错页，见 _placeholder_page）
                im = _placeholder_page(page)
                print(f"  [材料原件] 第 {getattr(page, 'no', '?')} 页出图失败 → 占位图代替")
            w, h = im.size
            # ⚠️ 只取**首页**比例（2026-10-22 修复）：循环覆写会让 aspect=末页比例，
            #    混合方向材料里首页被拉变形（探针证实横竖混排变形 2 倍）。
            #    单一 aspect 只是首帧兜底；舞台页换页后按当前图 natural 比例重算。
            if h and not aspect:
                aspect = float(w) / float(h)
            name = f"img_{i:02d}.jpg"
            im.save(str(outdir / name), "JPEG", quality=86)
            im.close()
            names.append(name)
    finally:
        try:
            loc.close()
        except BaseException:
            pass
    return names, aspect


# ---------- 组装 ----------

# 口腔浮现层几何（2026-10-13 双层嘴部 v2）：{形象id: (left%, top%, width%)}，
# 比例坐标相对主图显示尺寸；top 是「全开时」层顶边（唇缝上方 0.40×层高），
# 前端按主图 getBoundingClientRect 换算 px，open 驱动 scaleY 从唇缝向下展开。
# ⚠️ v1 jaw 平移方案已废弃（矩形下移 → 下巴与脸颊错位缝），jaw 素材退役至
#    _fetch_probe/_bak_matting_20261013/。新增形象 = 生成 `{aid}_inner.png` + 登记几何。
INNER_GEOM = {
    "preset_01": (0.420, 0.416, 0.132),
}

def _inner_of(v) -> str:
    """口播叠加层之下真正做画面的内容层；非口播皮肤返回空串。

    ⚠️ 必须与生成路径走**同一个出口**（`svc.resolve_inner`）：两条路径对
       「口播层选了、内容层没选」给出不同答案时，会出现「渲染按结构图做、
       生成却报错」的自相矛盾（实测踩到：生成 400「未知的视觉方案：talking_head」，
       而这里本来就已经在按 graph 兜底）。
    """
    return svc.resolve_inner(v)


def _sub_html(text: str, k: int) -> str:
    """字幕 karaoke 分片：前 k 字算「已说」（`said`），其余 `todo`。

    ⚠️ 舞台页原实现走 `textContent`（天然免疫注入）；口播层改 `innerHTML` 后**必须转义**
       —— 讲稿里一个 `<` 就能把整页 DOM 结构打坏。
    ⚠️ 本函数是**渲染期 JS 的 Python 侧真值**：舞台页的 `thCaption()` 必须与它逐字同果
       （探针据此做真值比对）。渲染本身不走这里。
    """
    k = max(0, min(int(k), len(text)))
    return (f'<span class="said">{_esc(text[:k])}</span>'
            f'<span class="todo">{_esc(text[k:])}</span>')


def _th_dom(v, avatar: dict | None) -> str:
    """口播层 DOM。

    ⚠️ C1 是**示意性**说话状态（呼吸条），不是唇形同步 —— PRD 3.7.3 明确要求
       不得暗示具备唇形同步，所以文案写「说话状态示意」而不是「AI 数字人」。
    ⚠️ 合规标识（PRD 3.7.6 硬性）：成片必须带「AI 生成」显著标识，且**烧录进画面**。
       这里做成画面内的固定元素（不是元数据），导出后无法移除。
    ⚠️ 形象自 2026-10-12 起**一律是图片**（预置真人照 / 用户上传），原 SVG 矢量色块人已下线。
       未选择形象时 `build_stage` 已按预置 01 兜底；这里的 else 只是防御性占位
       （预置图被删/打包漏收时才会出现，属可见的错误状态，好过破图）。
    """
    if (getattr(v, "skin", "") or "").lower() != "talking_head":
        return ""
    src = (avatar or {}).get("src") or ""
    mot = (avatar or {}).get("motion") or ""
    if src and mot:
        # 有动作视频：形象自己会动（AI 生成的表情与姿态），不再靠整体位移伪装。
        # ⚠️ `poster` 指向静态照片 —— 视频未就绪 / 解不出来时显示的是原照片，
        #    而不是黑块。打包漏收素材时这层能兜住：画面只是「不动」，不会「变黑」。
        # ⚠️ **不给 autoplay**：起播必须由 `__stage.start()` 统一发起，与页钟同一起点，
        #    否则视频自己先跑起来，与渲染时钟错开一个不确定的偏移。
        # ⚠️ 动效视频的 class 由 `bg` 口径决定（不是「所有 video 都免 mask」）：
        #    · matte（抠像版，人物已分离）→ th-alpha 免 mask，否则发丝被羽化又削一层；
        #    · keep（带背景）→ **必须保留 mask**，靠羽化把矩形边缘化开融进舞台。
        _bg = str((avatar or {}).get("bg") or "keep")
        vcls = "th-photo th-alpha" if _bg == "matte" else "th-photo"
        fig = (f'<video id="thFig" class="{vcls}" src="{_esc(mot)}" '
               f'poster="{_esc(src)}" muted loop playsinline '
               f'preload="auto"></video>')
    elif src:
        # 透明 PNG 自带 alpha → 加 th-alpha 免 mask；上传 .img/jpg 带背景 → 保留 mask 羽化
        cls = "th-photo th-alpha" if src.lower().endswith(".png") else "th-photo"
        # 口腔浮现层（双层嘴部 v2，2026-10-13）：作品目录有 avatar_inner.png 时输出
        # 双层结构，几何按形象查表（比例坐标，相对主图显示尺寸）。thPaint 按
        # thJawOpen 驱动 scaleY + opacity 从唇缝展开。主图完全不动 → 零接缝。
        inner_style = None
        aid0 = str(getattr(v, "avatar_id", "") or svc.BUILTIN_AVATARS[0]["id"])
        try:
            if (svc.video_dir(v.id) / "avatar_inner.png").exists():
                inner_style = INNER_GEOM.get(aid0)
        except BaseException:
            inner_style = None
        if inner_style:
            il, it, iw = inner_style
            # inner 作为 .th-fig 直接子元素：left/top/width 由前端按主图实际显示矩形
            # 换算（object-fit:contain 的真实矩形只有 JS 拿得到），比例走 data-*。
            fig = (
                f'<img id="thFig" class="{cls}" src="{_esc(src)}" alt="形象示意">'
                f'<img class="th-inner" src="avatar_inner.png" aria-hidden="true" '
                f'data-l="{il}" data-t="{it}" data-w="{iw}">')
        else:
            fig = f'<img id="thFig" class="{cls}" src="{_esc(src)}" alt="形象示意">'
    else:
        fig = ('<div class="th-avatar-ph">未选择形象<br>（当前为纯口播示意）</div>')
    h = int((avatar or {}).get("tint") or 214)
    glow = (f"background:radial-gradient(ellipse 60% 44% at 50% 44%,"
            f"hsla({h},62%,66%,.20),transparent 64%)")
    return (
        f'<div class="th-fig">{fig}</div>'
        f'<div class="th-glow" style="{glow}" aria-hidden="true"></div>'
        f'<div class="th-pulse" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i></div>'
        f'<div class="th-mark">本视频由 AI 合成</div>'
        f'<div class="th-tip">形象为示意 · 口型为规则驱动，非唇形同步</div>'
    )


def _avatar_src(v, avatar: dict | None) -> str:
    """舞台页里的形象路径：上传形象拷进作品目录（相对路径自包含）。

    ⚠️⚠️ 上传接口（routers/videos.py::upload_avatar）存的是 `avatars_dir()/{aid}.img`，
        而这里原先只找 `{aid}.jpg` —— **后缀对不上**，导致上传的真人照片永远进不了画面，
        被静默忽略后落到预置矢量兜底（2026-10-11 实测坐实：元素命中测试与成片画面双证）。
        所以必须接受 `.img`。
    ⚠️ 缓存必须按**来源指纹**失效，不能只看「目标文件在不在」：同一作品换形象后，
        残留的 avatar.jpg 会让新形象不生效。这里用**字节比对**判定（mtime 会骗人）。
    ⚠️ 源文件不存在时保留已有缓存：宁可显示旧图，也不要毫无预兆地退回占位框。
    ⚠️ 任何写盘失败都**不外抛**：成片渲染不该因为拷一张图失败而整体 500。
    """
    if (getattr(v, "skin", "") or "").lower() != "talking_head":
        return ""
    aid = str(getattr(v, "avatar_id", "") or "").strip()
    # ⚠️ 未选择形象时按预置 01 兜底（与 build_stage 同一条口径）：自 2026-10-12 起预置
    #    形象是真图片，不再有「矢量绘制」那条不依赖任何资源的兜底路径 —— 也就是说
    #    **这里必须真的给出一个文件**，否则成片会落进「未选择形象」虚线框。
    if not aid:
        aid = svc.BUILTIN_AVATARS[0]["id"]
    # `.img` 是上传的现行格式；`.jpg/.png` 兼容历史数据与手工放置的素材
    up = None
    for ext in (".img", ".jpg", ".png"):
        cand = svc.avatars_dir() / f"{aid}{ext}"
        if cand.exists():
            up = cand
            break
    if up is None:
        # 预置形象：包内只读资源（白名单查 file，不拼接任何外部字符串）
        # ⚠️ 静态图选 alpha 还是原图，由 `bg` 口径决定（见 video.py::BUILTIN_AVATARS）：
        #    · keep  → **带背景**原图 jpg（影棚底是真实布光，配 CSS 羽化最自然）；
        #    · matte → 抠好的透明底 `{aid}_alpha.png`（绿幕必须去背）。
        #    2026-10-20 之前是「一律优先 alpha」—— 那会让保留背景的口径失效：
        #    视频是背景版、静态回落却是透明版，同一个形象两种观感。
        alpha = svc.preset_dir() / (aid + "_alpha.png")
        bg = str((svc.avatar_info(aid) or {}).get("bg") or "keep")
        up = None
        if bg == "matte":
            try:
                if alpha.is_file():
                    up = alpha
            except BaseException:
                up = None
            if up is None:
                up = svc.preset_file(aid)
        else:
            up = svc.preset_file(aid)
            if up is None:
                try:
                    if alpha.is_file():
                        up = alpha
                except BaseException:
                    up = None
    # 目标文件名跟源扩展名走（PNG 透明底必须保住 alpha，不能写成 .jpg）
    dst_name = "avatar.png" if (up is not None and up.suffix.lower() == ".png") else "avatar.jpg"
    src = svc.video_dir(v.id) / dst_name
    if up is not None:
        try:
            if not src.exists() or src.read_bytes() != up.read_bytes():
                src.write_bytes(up.read_bytes())
        except BaseException:
            pass
    # 口腔浮现层（2026-10-13 双层嘴部 v2）：预置形象有 `{aid}_inner.png` 时一并拷进
    # 作品目录，_th_dom 据此输出双层 DOM。无 inner 层即单层静态（其他形象）。
    try:
        inner = svc.preset_dir() / (aid + "_inner.png")
        idst = svc.video_dir(v.id) / "avatar_inner.png"
        if inner.is_file():
            if not idst.exists() or idst.read_bytes() != inner.read_bytes():
                idst.write_bytes(inner.read_bytes())
    except BaseException:
        pass
    return dst_name if src.exists() else ""


def _avatar_motion(v, avatar: dict | None) -> str:
    """舞台页里形象**动作视频**的路径（相对作品目录）；没有则返回 ""（回落静态图）。

    ⚠️⚠️ 必须与 `_avatar_src` 走**同一条路**：把素材**拷进作品目录**再用相对路径引用。
       渲染时舞台页是 `page.goto(stage_html.as_uri())` —— `file://` 上下文里
       `/api/videos/...` 这类绝对路径取不到后端，写了就是黑屏（实测过同类事故）。
    ⚠️ 判定「要不要出视频」依据的是**本函数的返回值**，不是「作品目录里有没有那个文件」：
       换形象后旧文件可能残留（本项目不用「删除」做一致性），靠返回值自然回落静态图，
       不会被残留文件劫持。
    ⚠️ 任何写盘失败都**不外抛**（与 `_avatar_src` 同）：成片不该因为拷一个视频失败而整体 500。
    """
    if (getattr(v, "skin", "") or "").lower() != "talking_head":
        return ""
    aid = str(getattr(v, "avatar_id", "") or "").strip()
    if not aid:
        aid = svc.BUILTIN_AVATARS[0]["id"]
    # 2026-10-13 晚用户拍板：形象路线回到**图生视频**（preset_01.webm 抠像版 +
    # A+C 包络调速）。PNG/inner 口腔层路线废弃（静态图的口型模拟观感不达标）。
    # 透明 PNG 仍保留：作为视频 poster（未就绪/解码失败时显示）与无视频形象的回落。
    # 上传形象的动作视频（未来扩展位）；当前只有预置形象配了视频
    up = None
    try:
        cand = svc.avatars_dir() / f"{aid}.motion"
        if cand.is_file():
            up = cand
    except BaseException:
        up = None
    if up is None:
        bg = str((svc.avatar_info(aid) or {}).get("bg") or "keep")
        if bg == "matte":
            # ⚠️ 绿幕形象只能用抠像版；`{aid}.webm` 是**绿幕原始版**，绝不能当兜底
            #    （成片里会是一整块绿）。没有抠像版就回落静态图 —— 宁可不动，不能出绿。
            up = svc.preset_matte_video(aid)
        else:
            # keep：优先「保留背景」的原始视频，没有才退回包内那支（可能是抠像版）
            up = svc.preset_bg_video(aid) or svc.preset_video(aid)
    if up is None:
        return ""
    dst = svc.video_dir(v.id) / "avatar.webm"
    try:
        if not dst.exists() or dst.read_bytes() != up.read_bytes():
            dst.write_bytes(up.read_bytes())
    except BaseException:
        pass
    return "avatar.webm" if dst.exists() else ""


def _norm_screens(raw, shot: dict) -> list:
    """每镜的画面分屏 `[{layout,a,b}]` —— 缺失/非法一律退回「整镜一屏」。

    ⚠️ 存量作品（shots 里没有 `screens`、points 里没有 `sc`）必须走这条兜底：
       一镜一屏 + 原 layout，渲染结果与改造前**逐字节一致**（零回归）。
    ⚠️ 区间必须**连续覆盖整个镜头**：渲染期按毫秒落在哪一屏来切画面，
       留缝会让那一刻两侧都不亮（画面全空）。这里再修一次连续性 ——
       不只信任落库值，落库值本身也可能来自旧版本或被手工改过。
    """
    a0 = int(shot.get("a") or 0)
    b0 = int(shot.get("b") or 0)
    out = []
    for s in raw or []:
        if not isinstance(s, dict):
            continue
        lay = str(s.get("layout") or "grid").strip().lower()
        try:
            a = int(s.get("a") or 0)
            b = int(s.get("b") or 0)
        except (TypeError, ValueError):
            continue
        out.append({"layout": lay if lay in svc.GRAPH_LAYOUTS else "grid",
                    "a": a, "b": b})
    if not out:
        lay = str(shot.get("layout") or "grid")
        return [{"layout": lay if lay in svc.GRAPH_LAYOUTS else "grid",
                 "a": a0, "b": max(a0 + 1, b0)}]
    out.sort(key=lambda x: x["a"])
    out[0]["a"] = a0
    for j in range(1, len(out)):
        out[j]["a"] = max(out[j]["a"], out[j - 1]["a"] + 1)
    for j in range(len(out) - 1):
        out[j]["b"] = out[j + 1]["a"]
    out[-1]["b"] = max(out[-1]["a"] + 1, b0)
    return out


def stage_payload(db, v) -> dict:
    """作品 → 舞台页数据（原型形状）。"""
    script = list(v.script or [])
    if not svc.script_has_timeline(script):
        raise ValueError("讲稿缺少时间轴（未合成音轨），无法生成舞台页")

    segs = _segs(script)
    shots = []
    for i, sh in enumerate(v.shots or [], 1):
        lay = str(sh.get("layout") or "grid").strip().lower()
        shots.append({"n": int(sh.get("n") or i), "l": str(sh.get("l") or f"第 {i} 段"),
                      "a": int(sh.get("a") or 0), "b": int(sh.get("b") or 0),
                      "layout": lay if lay in svc.GRAPH_LAYOUTS else "grid",
                      "screens": sh.get("screens") or []})
    if not shots:
        shots = [{"n": 1, "l": "全片", "a": 0, "b": int(v.duration_ms or 0), "layout": "grid"}]
    # 子屏归一：每镜至少一屏（存量作品 → 整镜一屏，行为与改造前一致）
    for d in shots:
        d["screens"] = _norm_screens(d.get("screens"), d)

    scene_of = _scene_of_script(script, shots)
    at_by_scene = _at_by_scene(shots, script, scene_of)

    # 要点按**时间归属**到场景（绝不按 points.group 对齐 shots.n）
    n_scene = len(shots)
    pts_by_scene = [[] for _ in range(n_scene)]
    for p in v.points or []:
        at = int(p.get("at") or 0)
        si = _scene_index(at, shots)
        k = str(p.get("k") or "concept")
        if k not in ("concept", "good", "cost"):
            k = "concept"
        row = {"at": at, "t": str(p.get("t") or ""), "k": k,
               "sc": max(0, int(p.get("sc") or 0)),
               "wa": 1 if k == "cost" else 0}
        # tex（可选，动效图解）：公式元素的 LaTeX 逐项数组，透传进 motion 的分页。
        if isinstance(p.get("tex"), list) and p["tex"]:
            row["tex"] = p["tex"]
        # chart（可选，动效图解 P1）：数据图元素，同 tex 的透传纪律。
        if isinstance(p.get("chart"), dict) and p["chart"].get("items"):
            row["chart"] = p["chart"]
        # sketch（可选，整屏图板 2026-10-21）：结构草图。**板书与动效图解都读**
        # （板书：ppt_slides(with_sketch=True) 画黑板简图；动效图解：motion_slides
        #  的 _expand_sketch 把 labels 变成一屏结构化图）。
        # ⚠️ PPT 课件的 `els` 契约**不放宽** —— 课件模板按 els 画，多一张空 els
        #    的图板页就是「只有标题的白页」。
        if isinstance(p.get("sketch"), dict):
            row["sketch"] = p["sketch"]
        pts_by_scene[si].append(row)
    for g in pts_by_scene:
        g.sort(key=lambda x: x["at"])

    steps = []
    for s in v.steps or []:
        if s.get("page") is None or s.get("x") is None:
            continue
        steps.append({"at": int(s.get("at") or 0), "p": int(s.get("page") or 1),
                      "hit": str(s.get("hit") or s.get("anchor") or ""),
                      "sc": float(s.get("sc") or 1.0),
                      "anchor": str(s.get("anchor") or ""),
                      "x": float(s.get("x") or 0.0), "y": float(s.get("y") or 0.0),
                      "w": float(s.get("w") or 0.0), "h": float(s.get("h") or 0.0)})
    steps.sort(key=lambda x: x["at"])

    return {
        "segs": segs,
        "scene_of": scene_of[:len(segs)],
        "shots": shots,
        "points": pts_by_scene,
        "at_by_scene": at_by_scene,
        "steps": steps,
        "total": int(v.duration_ms or (segs[-1]["b"] if segs else 0)),
    }


# ========== PPT 课件（方案 A：0ms 起就是内容页）2026-10-20 ==========
#
# 为什么分页与页标题算在**后端**而不是模板里：
#   ① 导出 `.pptx` 要复用同一份页结构 —— 两处各写一份必然漂移成
#      「视频里 16 页、导出的课件 15 页」这种最难解释的错；
#   ② 分页规则（过滤超时条目 / 均分）要能被探针**直接断言**，而不是从截图反推。
#
# 方案 A 的硬约束来自两份真实成片的实测：`SEGS[0].a` 都是 0 —— 第 0 帧就在讲内容，
# 没有片头空隙。所以这里**刻意不生成封面 / 目录页**：它们只能占用真实讲解时间
# （完整课件形态实测牺牲 18.4% 时长 + 18 条内容）。课件感改由「页眉面包屑 +
# 大页码 + 常驻节导航 + 底部时间轴」提供。
PPT_PER_PAGE = 6                         # 单页条目上限（均分时用）
_PPT_TITLE_LO, _PPT_TITLE_HI = 12, 20    # 回退标题的目标字数区间


def _ppt_punct_off(s: str) -> str:
    """去空白与中文标点 —— 只服务「是不是字面前缀」这一个判定。"""
    return re.sub(r"[\s，。；、：！？,;:!?…—·\"'（）()《》【】]+", "", str(s or ""))


def _ppt_short_title(text: str) -> str:
    """从讲稿句子切一个读得通的短标题（按标点累加，落在 12~20 字）。

    ⚠️ 只是**兜底**：正常数据走 LLM 给的镜头标题（见 `_ppt_shot_title`）。
       兜底存在的理由很具体 —— 存量作品的 `shots[].l` 是按字数硬截的
       （实测 video 6：「培训班要加直播第一反应往」这种半句话），不能当页标题。
    """
    t = re.sub(r"\s+", "", str(text or ""))
    if not t:
        return ""
    out = ""
    for p in re.split(r"(?<=[，。；、：！？,;:])", t):
        if len(out + p) > _PPT_TITLE_HI:
            break
        out += p
        if len(out) >= _PPT_TITLE_LO:
            break
    return out.rstrip("，。；、：,;:") or t[:_PPT_TITLE_HI]


def _ppt_is_cut(shot_label: str, first_seg: str) -> bool:
    """`shots[].l` 是「从首句硬截出的半句」还是「LLM 命名的标题」。

    判据：去标点后 `l` 是首句的**字面前缀**且 ≥10 字 → 判为截断。
    实测命中（video 6）：l="培训班要加直播第一反应往"，首句="培训班要加直播，第一反应往往…"。
    反例（LLM 命名）：l="直播平台的三种方案"，首句是口语「那第一个问题就是…」→ 不是前缀
    → 直接用 LLM 标题（质量远好于任何自动切分，所以这条判据的方向必须是「别误杀」）。
    """
    l, f = _ppt_punct_off(shot_label), _ppt_punct_off(first_seg)
    return len(l) >= 10 and bool(f) and f.startswith(l)


def _ppt_shot_title(shot: dict, segs: list) -> str:
    """一节的页标题：优先 LLM 标题，坏数据才回退「按标点切首句」。

    ⚠️ 回退取句规则：该节时间内**第一条 ≥6 字的句子**。纯口语填充（「对。」）
       做标题毫无信息量；实测 video 9 首句「那AgenticRAG和普通RAG比」就是半句话。
    """
    a = int(shot.get("a") or 0)
    b = max(int(shot.get("b") or 0), a + 1)
    first = ""
    for s in segs:
        if not (a <= int(s.get("a") or 0) < b):
            continue
        t = str(s.get("t") or "").strip()
        if not first:
            first = t
        if len(_ppt_punct_off(t)) >= 6:
            first = t
            break
    label = str(shot.get("l") or "").strip()
    if label and not _ppt_is_cut(label, first):
        return label
    return _ppt_short_title(first) or label or "第 %d 节" % int(shot.get("n") or 0)


# ========== 板书 · 整屏图板（sketch）2026-10-21 ==========
# 一张图 = 一板：带 sketch 的要点从条目流里**摘出来**独占一页，前后条目照常分页。
# ⚠️⚠️ 只有板书侧启用（`ppt_slides(payload, with_sketch=True)`）。
#     PPT 课件与 `.pptx` 导出**必须**保持旧签名，否则会多出空 `els` 的图板页。
# ⚠️ 校验与接地**不在本文件**：`svc.norm_sketch` / `svc.sketch_grounded`（video.py）。
#    落库时已校验过一轮，这里再校验是给「存量/手改数据」兜底 —— 两处必须同一份实现。

# 图板页的**时长下限**（毫秒）。低于这个数就不画图，降级成普通条目页：
# 标题要先写 1.48s，整张图还有 STROKE_MIN / LABEL_MIN 的硬下限，页长不够时
# **图根本画不完** —— 成片表现是「翻到一页空黑板，几百毫秒后又翻走」
# （实测 1.2s 页：图要到 b+1840ms 才画完，可见期内一笔都没落）。
# 阈值按最坏情形取余量：compare 6 标签实测需 ≈4.15s → 取 4.6s。
# ⚠️ 这不是抄模板的时序公式，是**策略值**：模板一旦调慢、这页画不完，
#    `probe_sketch_edge.py` 的 E14（最短允许页 · 四原语）会立刻红。
SKETCH_MIN_MS = 4600
# ⚠️ 吞的目标就是 SKETCH_MIN_MS 本身，**不要加余量**：实测（3 部真作品 × 243 个
#    位置注入）need=4600 → 84.0%、4660 → 81.9%、4800 → 81.5% —— 余量把「页长恰好
#    4600~4660、本来画得完」的位置判死，收益是负的。收敛规则顶的 +1ms 只影响页长
#    恰好等于 4600 的那极少数位置，不值得拿 2 个百分点换。
# 一板最多吞 2 条：再多就是「讲了半分钟、板上一张图不动」，注意力早走了。
SKETCH_ABSORB_MAX = 2
# 吞完之后这一板最多能有多长。极端稀疏处（图后面只有 1 条要点、再下一条在 20s 后）
# 吞 1 条会把页拉到 20s：图 4s 画完、剩下 16s 一动不动 —— 不如降级成文字页。
SKETCH_ABSORB_MAX_MS = 9000


def _ppt_el(e) -> dict:
    """一条要点 → 页内条目（板书模板与 `.pptx` 导出共用的一份结构）。"""
    t = str(e["t"])
    return {"at": int(e["at"]), "t": t,
            "k": str(e.get("k") or "concept"),
            # st：轨迹节点画勾还是叉（后端判，模板只读）
            "st": svc.item_state(t),
            # num：阶梯的数字；非数字条目为 null
            "num": svc.item_num(t)}


def _blk_first_at(blk) -> int:
    """分页块首条要点的时刻（sketch 块里的元素是 (e, sk) 二元组）。"""
    it = blk[0]
    return int((it[0] if isinstance(it, tuple) else it)["at"])


def _absorb_sketch_blocks(blocks: list, sec_a: int, sec_b: int) -> dict:
    """图板页太短时，把**紧邻的后续要点**吞进这一板（A 方案，2026-10-21）。

    为什么必须吞：一板图从标题写完到画完约 4.1s，而相邻要点的间距中位只有 2.2s ——
    65 个图板位置里 60 个不够长，原先全部降级成文字页，**一张图都出不来**。

    为什么吞了不算丢内容：被吞的要点**不再单独出页**（文字不上板），但它的语义由
    这张图的标签承载，讲稿照常讲、页区间 `b` 延到它讲完 —— **声画零不同步**。
    代价是整板静止时间变长（实测 30%+），换来图真的画得完。

    返回 {blocks 下标: [被吞条目]}；blocks **就地**改动（被吞条目从 list 块头部摘除）。
    ⚠️ 只在 with_sketch=True 的板书路径上调用。`b` 的估算口径必须与 `ppt_slides`
       的「b = 下一页 a」一致，否则这里以为够了、那边仍是 2s → 又降级。
    """
    absorbed = {}
    for j, (kind, blk) in enumerate(blocks):
        if kind != "sketch":
            continue
        # 这一板的起点 a（与 ppt_slides 同口径）：本节第一块取镜头起点，否则取该点 at
        start = sec_a if j == 0 else int(blk[0][0]["at"])
        # 只吞**紧邻的 list 块**：下一块也是图板 → 那一板自己都缺时间，吞了会连环塌
        if j + 1 >= len(blocks) or blocks[j + 1][0] != "list":
            continue
        nxt = blocks[j + 1][1]

        def _b_after(k: int) -> int:
            """吞掉 nxt 前 k 条之后，本板的 b（下一页起点）会落在哪。"""
            rest = nxt[k:]
            if rest:
                return int(rest[0]["at"])
            # 吞光了 → 下一页就是后面的块；本节没有后续块 → 节末
            if j + 2 < len(blocks):
                return _blk_first_at(blocks[j + 2][1])
            return sec_b

        need = SKETCH_MIN_MS
        k = SKETCH_ABSORB_MAX + 1          # 哨兵：吞满上限仍不够 / 一跳太远
        for kk in range(0, min(SKETCH_ABSORB_MAX, len(nxt)) + 1):
            span = _b_after(kk) - start
            if span < need:
                continue                   # 还不够长，继续往下吞
            # 够长了：kk==0 表示本来就不缺时间（不吞）；kk>0 还要保证没一跳跳太远
            if kk == 0 or span <= SKETCH_ABSORB_MAX_MS:
                k = kk
            break
        if k == 0 or k > SKETCH_ABSORB_MAX:
            # 本来够长 / 吞满上限也不够 → 不动：留给降级判定，别白丢文字
            continue
        absorbed[j] = list(nxt[:k])
        blocks[j + 1] = ("list", nxt[k:])
    return absorbed


def _page_blocks(els: list) -> list:
    """把一节的条目切成「分页块」：带 sketch 的点各自成块，其余连续点合一块。

    返回 [(kind, els)]，kind ∈ {"list", "sketch"}，顺序即讲稿顺序。
    """
    out, buf = [], []
    for e in els:
        sk = svc.norm_sketch(e.get("sketch")) if isinstance(e.get("sketch"), dict) else None
        if sk:
            if buf:
                out.append(("list", buf))
                buf = []
            out.append(("sketch", [(e, sk)]))
        else:
            buf.append(e)
    if buf:
        out.append(("list", buf))
    return out


def _sketch_title(e, fallback: str) -> str:
    """图板页的标题 = 这张图要讲什么（该要点自己的说法）；过长/过短退回镜头标题。"""
    t = str(e.get("t") or "").strip()
    n = len(_ppt_punct_off(t))
    return t if 4 <= n <= 18 else fallback


def ppt_slides(payload: dict, with_sketch: bool = False) -> tuple:
    """生成课件的 `(nav, slides)`：单一口径，舞台模板与 `.pptx` 导出共用。

    nav    = [{no, title, a, b}]                        左侧节导航 / 时间轴刻度
    slides = [{no, total, shot, title, a, b, els, shape}]  每页条目 / 时间区间 / 形状
    ⚠️ `shape` 只对 ppt 与 board 有意义（board 不读）；""= 清单，模板据此
       走「逐条入场」（存量行为），非空 = 整页常驻的形状页。

    ⚠️⚠️ 必须过滤 `at >= TOTAL` 的条目：实测 video 6 第 6 节有 4 条 at 落在
       218746~228566，而全片只到 216912 —— 它们在成片里**永远不出现**。
       不过滤的后果是页数/页码被虚高、末页多出 4 条永远看不见的条目。
       （导出 `.pptx` 时应保留它们：课件可以比视频全；视频侧只播能播到的。）
    ⚠️ 切页要**均分**不要按固定条数硬切：实测 7 条硬切 6+1 → 尾页只剩
       「开箱即用」四个字、整屏空白，是全套样张里最难看的一页。

    ⚠️⚠️ 页起点语义（2026-10-21 改，板书 + PPT 同步）：本节**第一页**的 `a` =
       镜头起点 `shot.a`，不是首条 `at`。本片数据里两者恰好相等（points 从镜头开头
       就编号，gap 全为 0），所以这条对现状是**语义加固**而不是效果改动：
       ① 将来若某节首条晚出现，章节一开讲就翻到新板，不必等第一条；
       ② 一个镜头被均分成多页时，第 2 页起的 `a` 仍是「该页首条 at」——
          那才是「页标题与首条同时出现」的真因，修法在板书模板侧
          （`LINE_MIN_DELAY`：第一条落笔不早于标题写完），不在分页。
       第一页 `a` 一律收敛到 0，否则片头是一段黑屏。
    """
    total = int(payload["total"])
    shots, pts, segs = payload["shots"], payload["points"], payload["segs"]

    nav, slides = [], []
    pending_sketch = []         # [(page, el, 镜头标题, 被吞条目)]：页边界定了再判时长
    for i, sh in enumerate(shots):
        title = _ppt_shot_title(sh, segs)
        sec_a = int(sh.get("a") or 0)
        nav.append({"no": i + 1, "title": title,
                    "a": sec_a, "b": int(sh.get("b") or 0)})
        els = [e for e in (pts[i] if i < len(pts) else []) if int(e["at"]) < total]
        if not els:
            continue            # 该节全部条目都超时 → 这一节没有可播内容，不占页
        # 切块：带 sketch 的点独占一块（整屏图板），其余连续点合一块再按条数均分。
        # ⚠️ with_sketch=False 时是单块 —— 与改造前**逐字节等价**的行为。
        blocks = _page_blocks(els) if with_sketch else [("list", els)]
        # A（2026-10-21）：图板页时长不够 → 吞掉紧邻的后续要点，而不是放弃这张图。
        # ⚠️ 必须在生成页**之前**做：它改的是 blocks（条目从后续 list 块里摘掉），
        #    页边界与页码随之后移；事后再去改 page["b"] 会让相邻两页重叠。
        absorbed = (_absorb_sketch_blocks(blocks, sec_a, int(sh.get("b") or 0))
                    if with_sketch else {})
        first_page = True       # 本节第一页取镜头起点（页起点语义，见 docstring）
        for j, (kind, blk) in enumerate(blocks):
            if kind == "sketch":
                e, sk = blk[0]
                page = {"shot": i,
                        # 图板页标题 = 这张图讲什么（不是镜头标题）：
                        # 这一板只有图，标题就是图的题；该要点的文字也不丢。
                        "title": _sketch_title(e, title),
                        "a": sec_a if first_page else int(e["at"]),
                        "els": [], "shape": "",
                        "sketch": sk}
                slides.append(page)
                ab = absorbed.get(j) or []
                if ab:
                    # 被吞要点：文字不上板（语义由这张图的标签承载），只留档给探针/日志
                    page["absorbed"] = [str(x["t"]) for x in ab]
                # 页边界要等全部页生成后才知道，先记下元素：太短就降级
                pending_sketch.append((page, e, title, ab))
                first_page = False
                continue
            n_page = max(1, -(-len(blk) // PPT_PER_PAGE))
            base, extra = divmod(len(blk), n_page)
            cur = 0
            for k in range(n_page):
                n = base + (1 if k < extra else 0)
                grp = blk[cur:cur + n]
                cur += n
                if grp:
                    # ⚠️ 必须是**新变量名**：这里若写回 `els`，第二轮 `grp = els[cur:...]`
                    #    切的就是上一页那条短列表 → 空 → 后续页整页跳过。
                    #    实测 13 条只出 5 条（丢 8 条），且不报错、截图也看不出。
                    # 结构构造统一走 _ppt_el（图板降级路径也要用同一份）
                    page = [_ppt_el(e) for e in grp]
                    slides.append({"shot": i, "title": title,
                                   # 页起点：本节第一页 = 镜头起点，
                                   # 节内后续页 = 该页首条 at。
                                   "a": sec_a if first_page else int(grp[0]["at"]),
                                   "els": page,
                                   # shape：""=清单（现状）。判定见 svc.pick_shape
                                   "shape": svc.pick_shape(page)})
                    first_page = False
    # ⚠️ 兜底：全片没有任何可播的视觉步点（points 为空、或所有 at 都 >= TOTAL）。
    #    不兜的话模板的 `slideAt()` 会拿到 undefined → paintSlide 抛错 → **白屏成片**，
    #    而用户看到的只是「这视频是黑的」，没有任何线索指向"这份作品的步点全超时了"。
    if not slides:
        slides = [{"shot": 0,
                   "title": _ppt_shot_title(shots[0], segs) if shots else "全片",
                   "a": 0, "b": max(1, total), "els": [], "shape": "", "no": 1, "total": 1}]
    # 页起点收敛（与上面页起点语义配套）：
    #   ① 第一页 a 一律 0 —— 否则片头 0~a 这段时间没有任何页，模板只能给黑屏；
    #   ② 相邻页 a 严格递增 —— 页区间首尾相接（b = 下一页 a）的前提，
    #      不能靠「b <= a 时 b = a+1」去救，那会把下一页的 b 也顶歪。
    slides[0]["a"] = 0
    for i in range(1, len(slides)):
        if int(slides[i]["a"]) <= int(slides[i - 1]["a"]):
            slides[i]["a"] = int(slides[i - 1]["a"]) + 1
    # 页边界首尾相接：b = 下一页 a，末页 = TOTAL。保证任何时刻「有且只有一页」
    for i, s in enumerate(slides):
        s["b"] = int(slides[i + 1]["a"]) if i + 1 < len(slides) else total
        if s["b"] <= s["a"]:
            s["b"] = s["a"] + 1
        s["no"] = i + 1
        s["total"] = len(slides)
    # 还是太短（没有可吞的后续要点 / 吞满上限也不够）→ 降级成普通条目页。
    # ⚠️ 只改 els/title/sketch，**不动 a/b** —— 页边界与页码因此完全不受影响。
    # ⚠️⚠️ 降级时**已吞的要点必须一起上板**：它们已经从后续 list 块里摘掉了，这一页
    #    若只显示自己那一条，这几条要点就既没有图、也没有文字 —— 内容凭空消失，
    #    而成片里只是「少了一句话」，不报错、截图也看不出来。
    for page, e, sec_title, ab in pending_sketch:
        if page["b"] - page["a"] >= SKETCH_MIN_MS:
            continue
        rows = sorted([e] + list(ab), key=lambda x: int(x["at"]))
        page["els"] = [_ppt_el(x) for x in rows]
        page["title"] = sec_title
        page.pop("sketch", None)
        page.pop("absorbed", None)     # 文字已上板，别让探针以为还在图里
    return nav, slides


def _build_ppt_stage(db, v) -> Path:
    """PPT 课件舞台页：独立模板，页结构与标题在注入前**已全部算好**。"""
    payload = stage_payload(db, v)
    nav, slides = ppt_slides(payload)
    title = getattr(v, "title", "") or "AI 视频"
    repl = {
        "__TITLE__": _esc(title),          # 只用于 <title>（HTML 上下文）
        "__TITLE_JS__": _j(title),         # JS 里用 JSON 字面量，避免引号转义踩坑
        "__NAV__": _j(nav),
        "__SLIDES__": _j(slides),
        "__SEGS__": _j(payload["segs"]),
        "__TOTAL__": str(int(payload["total"])),
    }
    tpl = _TEMPLATE_PPT.read_text(encoding="utf-8")
    for k, val in repl.items():
        tpl = tpl.replace(k, val)
    # ⚠️ 漏替换的占位符会让 JS 直接语法错 → **白屏成片**（黑屏 3 分钟没人知道为什么）。
    #    当场失败远好于渲出一片黑：与 source 皮肤「没有原件画面就报错」同一纪律。
    left = sorted(set(re.findall(r"__[A-Z][A-Z_]*__", tpl)))
    if left:
        raise ValueError("PPT 课件模板存在未替换的占位符：%s" % ", ".join(left))
    out = svc.stage_abspath(v.id)
    out.write_text(tpl, encoding="utf-8", newline="\n")
    return out


def _build_board_stage(db, v) -> Path:
    """板书 · 粉笔舞台页：独立模板。

    页结构复用 `ppt_slides()`（与 PPT 课件、.pptx 导出同一份分页口径）：
    每页 = 一板，条目按各自的 at 逐行「写上黑板」；good/cost 条目写完自动
    圈画/下划线（points 的语义标注直接变成教学动作）。
    """
    payload = stage_payload(db, v)
    # ⚠️ 只有板书要图板页；同一个 payload 走 PPT 时必须是旧签名（见 SKETCH_TYPES 段）。
    nav, slides = ppt_slides(payload, with_sketch=True)
    title = getattr(v, "title", "") or "AI 视频"
    repl = {
        "__TITLE__": _esc(title),
        # ⚠️ 没有 __TITLE_JS__：板书模板不在 JS 里用标题（只在 <title> 与页标题里，
        #    页标题来自 slides[].title）。原先把 PPT 的替换表整段照搬，多注入一个
        #    模板里根本不存在的占位符 —— 无害但是死代码，接手的人会以为有依赖。
        "__NAV__": _j(nav),
        "__SLIDES__": _j(slides),
        "__SEGS__": _j(payload["segs"]),
        "__TOTAL__": str(int(payload["total"])),
    }
    tpl = _TEMPLATE_BOARD.read_text(encoding="utf-8")
    for k, val in repl.items():
        tpl = tpl.replace(k, val)
    # ⚠️ 漏替换的占位符 = 白屏成片，当场报错（与 PPT 模板同一纪律）。
    left = sorted(set(re.findall(r"__[A-Z][A-Z_]*__", tpl)))
    if left:
        raise ValueError("板书模板存在未替换的占位符：%s" % ", ".join(left))
    out = svc.stage_abspath(v.id)
    out.write_text(tpl, encoding="utf-8", newline="\n")
    return out


# ========== 动效图解（motion，2026-10-21，论文讲解/Vibe 知识形态）==========
#
# 页 = (镜头, 屏)：「画布」随讲解生长；换屏时**同 id 元素滑动变换**（morph）
# 而不是整屏硬切 —— 这是它与所有既有皮肤的本质区别。
# ⚠️ 分页语义与 ppt_slides **刻意不同**：课件按条数均分重排，动效图解必须
#    保住模型的「屏」（sc）—— 一屏 = 画布的一个状态，均分会破坏「哪些元素
#    同屏」这件事本身（morph 的连续性就建立在同屏/跨屏关系上）。
# ⚠️⚠️ id 合成在**后端**，绝不交给模型（与「形状判定在后端」同一纪律）：
#    同镜头内「归一化文本相同 = 同一对象」；公式元素用 tex 全文做 id。
#    补点会复用文案 → 同屏撞 id 加序号后缀（同屏两块相同文案是**两块**，
#    跨屏相同文案才是「同一个」——后缀只在屏内去重，跨屏匹配只看主 id）。
def _motion_el_id(e: dict) -> str:
    tex = e.get("tex")
    if isinstance(tex, list) and tex:
        return "f:" + "|".join(str(x) for x in tex)
    return "t:" + re.sub(r"\s+", "", str(e.get("t") or "")).lower()


# ---- 容量预算（2026-10-21，长屏溢出守卫）----
# 根因：补点机制会给长句在同一 sc 里塞填充点 → 一屏实际元素数 = 模型给的（≤8）
# + 补点（长句再 +2~4）；实测 10 元素混合屏 4 个溢出视口底沿、6 个压字幕区。
# 拆法 = 「同 sc 连续子页」：元素 id 不变，morph/ghost 机制天然保住连续性，
# 语义零伤害（❌ 不要整栈缩放：字会糊）。
# 预算口径（与模板布局实测对齐）：垂直栈/对照列按总高（块高 + 行距 34），
# flow 横排按总宽（块宽 + 箭头位 44）。
# ⚠️⚠️ 2026-10-21 真实作品（id=12，89 个元素）标定：原估算比真实布局**小 22-23%**
#   （纯文本 76→真实 93 高、376→真实 461 宽），导致 flow 页实际总宽冲到 2150 把
#   末端算出画（右端 1947 > 1920）。系数已按实测校准（宽 34→41、常量 70→84；
#   高 76→91），预算同时改成**真实可用宽高**。改这两个数前必须重测，别按感觉调。
_MOTION_H_BUDGET = 680.0    # 垂直栈单列 / compare 每列的总高预算（校准后 ≈ 真实 px）
_MOTION_W_BUDGET = 1790.0   # flow 横排总宽预算（= 1920 - 两侧 65 边距）
_MOTION_GAP_H = 34.0
_MOTION_ARROW_W = 44.0


def _motion_el_hw(e: dict) -> tuple:
    """元素的估算占位（宽, 高）px —— 与模板布局同口径的保守估算（宁可高估）。

    ⚠️ 系数 90 / 49 / 104 是**真实作品标定值**（id=12，89 元素，按模板当前的
       主要字号档 44px 拟合：units 5..12 实测宽 323..652，拟合残差 ±5%）。
       ⚠️ 改模板 fsChip 档位后**必须重跑 `_calib_motion_width_20261021.py` 重新
       拟合**：字号一变，真实宽高就整体缩放，旧系数会让拆页判断失真。
       多元素页字更小 → 估算偏高 → 拆页更早（刻意偏保守，牺牲一页不牺牲出画）。
    """
    if e.get("chart"):
        return (790.0, 370.0)
    if e.get("mml"):
        return (900.0, 150.0)
    t = str(e.get("t") or "")
    return (min(1240.0, 90.0 + 49.0 * _text_units(t)), 104.0)


def _motion_rebalance(pages: list) -> list:
    """消除「单条元素页」（flow 画不出箭头、compare 右半屏全空）。

    ⚠️⚠️ 旧版借条不收敛：借出方 >=2 就借，借完可能变 1（[2,1] -> [1,2]，单条页
       只是从尾部搬到头部，而循环已经跳过）。实测 flow 3 条 >=10 字必现（拆分
       [2,1] -> 借后 [1,2]），新作品的高频路径。
    策略（收敛式，每轮要么单条页数 -1、要么页数 -1）：
      1. **借条**：只从「借完仍 >=2 条」（即 >=3 条）的相邻页借 —— 不制造新单条页；
      2. **并页**：借无可借（总量 <4 的 [2,1] 类）→ 并入相邻页。并入后可能超容量
         预算，**交模板侧缩字号 / 退版式兜底**（flow 有 26px 下限 + 退 grid 双保险，
         垂直栈 3 条文本根本不会超预算才走到这）——宁可字小一页，不要空屏一页。
    ⚠️ chart / mml 大图元素的单条页**豁免**（数据图 / 公式独占一页是合法排版，
       两个 chart 并一页 774px 反而超 680 预算出画）。
    """
    if len(pages) < 2:
        return pages
    pages = [list(p) for p in pages]

    def _big(page) -> bool:
        return any(e.get("chart") or e.get("mml") for e in page)

    while True:
        singles = [i for i, p in enumerate(pages)
                   if len(p) == 1 and not _big(p)]
        if not singles:
            return pages
        i = singles[0]
        # 借条：借出方借完必须仍 >=2（即 >=3 才借）
        if i > 0 and len(pages[i - 1]) >= 3:
            pages[i].insert(0, pages[i - 1].pop())
            continue
        if i + 1 < len(pages) and len(pages[i + 1]) >= 3:
            pages[i].append(pages[i + 1].pop(0))
            continue
        # 借无可借 → 并入元素较少的相邻页（超预算交模板缩字号兜底）
        if i > 0 and (i + 1 >= len(pages)
                      or len(pages[i - 1]) <= len(pages[i + 1])):
            pages[i - 1].append(pages[i][0])
            pages.pop(i)
        elif i + 1 < len(pages):
            pages[i + 1].insert(0, pages[i][0])
            pages.pop(i)
        else:
            return pages

def _motion_split(els: list, layout: str) -> list:
    """把一屏的元素按容量拆成若干连续子页（不拆 = 单元素列表原样返回）。

    ⚠️ 绝不拆单个元素本身（一个数据图超预算也独自成页 —— 拆块才是灾难）。
    """
    if not els:
        return []
    if layout == "flow":
        pages, cur, acc = [], [], 0.0
        for e in els:
            add = _motion_el_hw(e)[0] + (cur and _MOTION_ARROW_W or 0.0)
            if cur and acc + add > _MOTION_W_BUDGET:
                pages.append(cur)
                cur, acc = [], 0.0
                add = _motion_el_hw(e)[0]
            cur.append(e)
            acc += add
        if cur:
            pages.append(cur)
        return _motion_rebalance(pages)
    if layout == "compare":
        # 对照：模板按**页内**前一半左列、后一半右列分侧 —— 容量模拟必须用同一规则
        #    （曾按整组分侧：拆分后页内半边错位，左右列失衡）。
        pages, cur = [], []
        for e in els:
            cand = cur + [e]
            half = -(-len(cand) // 2)
            lh = (sum(_motion_el_hw(x)[1] for x in cand[:half])
                  + _MOTION_GAP_H * max(0, half - 1))
            rh = (sum(_motion_el_hw(x)[1] for x in cand[half:])
                  + _MOTION_GAP_H * max(0, len(cand) - half - 1))
            if cur and max(lh, rh) > _MOTION_H_BUDGET:
                pages.append(cur)
                cur = [e]
            else:
                cur = cand
        if cur:
            pages.append(cur)
        return _motion_rebalance(pages)
    # 垂直栈（grid / hub / pyramid 及未知版式的兜底）
    pages, cur, acc = [], [], 0.0
    for e in els:
        add = _motion_el_hw(e)[1] + (cur and _MOTION_GAP_H or 0.0)
        if cur and acc + add > _MOTION_H_BUDGET:
            pages.append(cur)
            cur, acc = [], 0.0
            add = _motion_el_hw(e)[1]
        cur.append(e)
        acc += add
    if cur:
        pages.append(cur)
    return _motion_rebalance(pages)


# ---- sketch 图板页（A，2026-10-21）----
# sketch.type → 动效图解版式。quadrant 没有专属版式：4 个标签正好落 2×2 网格
# （grid 分支在 n>=3 时就是两列网格），视觉上就是四宫格，不新造版式。
_SKETCH_LAYOUT = {"flow": "flow", "compare": "compare",
                  "pyramid": "pyramid", "quadrant": "grid"}


def _expand_sketch(slides: list) -> list:
    """把「带 sketch 的要点」从文字页里摘出来，独占一屏图板页。

    ⚠️⚠️ 为什么必须拆页而不是塞进原页：sketch 的 labels 是**另一套文案**
       （≤8 字的结构标签），和同屏的短句混在一起会既不像图也不像列表；
       板书皮肤就是「一张图 = 一板」的语义，两个皮肤必须同口径。
    ⚠️ before / after 是原页的**子集**（原页已过 _motion_split），所以不会超容量。
    ⚠️ 图板页 `a` = sketch 元素自己的 at —— 图卡必须在它被讲到的那一句出现。
    """
    out = []
    for s in slides:
        els = s.get("els") or []
        sk_rows = [e for e in els if isinstance(e.get("sk"), dict)]
        # ⚠️ 整屏只有 1 条 sketch 也要展开（修6，2026-10-21）：那正是模型
        # 专为这张图开了一屏的理想场景，旧逻辑 len(els)<2 会把它退化成文字 chip。
        if not sk_rows:
            out.append(s)
            continue
        se = sk_rows[0]
        at = int(se["at"])
        before = [e for e in els if e is not se and int(e["at"]) < at]
        after = [e for e in els if e is not se and int(e["at"]) >= at]
        labels = [str(x).strip()[:24] for x in (se["sk"].get("labels") or [])][:6]
        labels = [x for x in labels if x]
        if len(labels) < 2:
            # 标签不够撑一屏（接地校验放过了但太短）→ 不值得拆，保持原样
            out.append(s)
            continue
        lay = _SKETCH_LAYOUT.get(str(se["sk"].get("type") or ""), "grid")
        # ⚠️⚠️ quadrant 的象限顺序是「左上 / 右上 / 右下 / 左下」（顺时针），而 grid
        #   版式按行从左到右填充（2 列时 2=左下 3=右下）→ 不换位置就把「右下」摆到
        #   左下角（探针实测坐标 766 vs 992 反了）。位置正确优先于出现顺序。
        if str(se["sk"].get("type") or "") == "quadrant" and len(labels) == 4:
            labels[2], labels[3] = labels[3], labels[2]
        if before:
            out.append({**s, "els": before, "a": int(before[0]["at"])})
        used = {}
        rows = []
        for lb in labels:
            base = "%d|%s" % (s["shot"], _motion_el_id({"t": lb}))
            n_seen = used.get(base, 0) + 1
            used[base] = n_seen
            rows.append({"at": at, "t": lb, "k": "concept",
                         "id": base if n_seen == 1 else "%s#%d" % (base, n_seen),
                         "mml": None})
        # 页眉标题：优先用「这张图在讲什么」（sketch 的 t，4-18 字，与板书同口径），
        # 空/超长才回落镜头标题 —— 图板页顶着镜头名会与画面内容对不上。
        st = str(se.get("t") or "").strip()
        page = {"shot": s["shot"], "sc": s["sc"], "layout": lay,
                "title": st if 2 <= len(st) <= 20 else s["title"],
                "a": at, "els": rows, "skpage": True}
        # quadrant 的轴名随页下发（修3）：后端强制校验过 x/y，不传给模板就是
        # 「校验了却永远不画」——四宫格在画面上退化成 2x2 文字网格。
        if str(se["sk"].get("type") or "") == "quadrant":
            page["qaxes"] = {"x": str(se["sk"].get("x") or "")[:8],
                             "y": str(se["sk"].get("y") or "")[:8]}
        out.append(page)
        if after:
            # 图板页保底 1200ms（修4，2026-10-21）：sketch 与后一条同 at 时
            # （同一句连给两条），旧逻辑会让图板页被页边界压成 1ms —— 整张图
            # 0 帧消失。把 after 页起点后推，图板页至少活 1200ms；after 页
            # 元素 at 可能 < 页 a → 页首即全亮，语义等价于「提前点亮」，可接受。
            out.append({**s, "els": after, "a": max(at + 1200, int(after[0]["at"]))})
    return out


def motion_slides(payload: dict) -> tuple:
    """生成动效图解的 `(nav, slides)`。

    nav    = [{no, title, a, b}]                        与 ppt 同口径（按镜头）
    slides = [{no, total, shot, sc, title, a, b, els}]  页 = (镜头, 屏)
    els    = [{at, t, k, id, mml}]   mml = [MathML 字符串] | None（公式元素）

    ⚠️⚠️ 同样必须过滤 `at >= TOTAL` 的条目（ppt_slides 的实测教训：它们在成片
       里永远不出现，只虚高页数）。视频侧只播能播到的。
    """
    total = int(payload["total"])
    shots, pts, segs = payload["shots"], payload["points"], payload["segs"]

    nav, slides = [], []
    for i, sh in enumerate(shots):
        title = _ppt_shot_title(sh, segs)
        nav.append({"no": i + 1, "title": title,
                    "a": int(sh.get("a") or 0), "b": int(sh.get("b") or 0)})
        els = [e for e in (pts[i] if i < len(pts) else []) if int(e["at"]) < total]
        if not els:
            continue            # 该节全部条目超时 → 不占页（与 ppt_slides 同口径）
        # 按屏（sc）分组，屏内保持 at 升序（stage_payload 已按 at 排好）
        by_sc: dict = {}
        for e in els:
            by_sc.setdefault(int(e.get("sc") or 0), []).append(e)
        # 屏的版式：取 shots.screens 归一化后的 screens[sc]（LLM 已在选 flow/hub/
        # compare/pyramid —— 动效图解**不再丢弃它**：flow=横排箭头链、compare=左右
        # 对照，其余版式落回垂直栈）。sc 越界（旧数据/手工改）→ 最后一屏版式兜底。
        # ⚠️⚠️ 页顺序必须按屏内最早 at 排，不能按 sc 编号排（2026-10-22 真实数据
        #   抓到：LLM 给的 sc1 时间倒序在 sc0 前 —— 提示词「屏按讲稿顺序」是软约束，
        #   模型会违反）。按 sc 排会让页序列时间倒序 → 「b = 下一页 a」把前一页
        #   压成 1ms（整页 0 帧消失）。版式仍按 sc 取，只有页顺序按时间。
        screens = sh.get("screens") or []
        for sc in sorted(by_sc, key=lambda k: min(int(e["at"]) for e in by_sc[k])):
            grp = by_sc[sc]
            lay = ""
            if screens:
                src = screens[sc] if sc < len(screens) else screens[-1]
                lay = str(src.get("layout") or "")
            lay = lay if lay in svc.GRAPH_LAYOUTS else "grid"
            used: dict = {}
            out_els = []
            for e in grp:
                # 镜头作用域前缀（2026-10-22）：morph 语义是「同镜头内同一对象
                # 连续生长」，旧实现没有镜头前缀，跨镜头同名文案（成本/收益这类
                # 高频词）会被误认为同一对象 → 换镜头时元素从上一屏位置飞过来。
                base = "%d|%s" % (i, _motion_el_id(e))
                n_seen = used.get(base, 0) + 1
                used[base] = n_seen
                row = {"at": int(e["at"]), "t": str(e.get("t") or ""),
                       "k": str(e.get("k") or "concept"),
                       "id": base if n_seen == 1 else "%s#%d" % (base, n_seen),
                       "mml": svc.tex_to_mathml(e.get("tex"))
                              if isinstance(e.get("tex"), list) else None}
                # chart（数据图元素）进模板（模板自绘 SVG，无需转换）。
                # ⚠️ 这里再做一次数值校验：正常数据 `_clamp_point` 已拦过，但旧作品
                #    的 points 可能来自旧版本或被手工改过 —— float("abc") 直接抛会让
                #    整页白屏。脏数据 → 丢弃 chart（模板按 chip 画），绝不白屏。
                # A（2026-10-21）：sketch 透传到 motion 行 —— 图板页后处理要用
                # （板书侧走的是另一条 els 契约，互不影响）。
                if isinstance(e.get("sketch"), dict):
                    row["sk"] = e["sketch"]
                if isinstance(e.get("chart"), dict) and e["chart"].get("items"):
                    try:
                        ctype = str(e["chart"].get("type") or "")
                        items2 = [{"label": str(x.get("label") or "")[:10],
                                   "value": float(x.get("value"))}
                                  for x in e["chart"]["items"] if isinstance(x, dict)]
                        items2 = [x for x in items2
                                  if x["value"] == x["value"]
                                  and 0 <= x["value"] < float("inf")]
                        if ctype in ("bar", "line") and len(items2) >= 2:
                            row["chart"] = {"type": ctype, "items": items2[:8]}
                    except (TypeError, ValueError):
                        pass
                out_els.append(row)
            # 容量拆分（长屏溢出守卫）：超预算 → 同 sc 连续子页
            for chunk in _motion_split(out_els, lay):
                slides.append({"shot": i, "sc": sc, "layout": lay, "title": title,
                               "a": int(chunk[0]["at"]), "els": chunk})
    # ⚠️ 兜底（与 ppt_slides 同源）：全片没有可播步点时给一页空画布，
    #    否则模板 `slideAt()` 拿 undefined → 白屏成片。
    if not slides:
        slides = [{"shot": 0, "sc": 0, "layout": "grid",
                   "title": _ppt_shot_title(shots[0], segs) if shots else "全片",
                   "a": 0, "b": max(1, total), "els": [], "no": 1, "total": 1}]
    # A：sketch 要点摘出来独占一屏（必须在算页边界之前 —— 图板页也有 a/b）
    slides = _expand_sketch(slides)
    # 页边界首尾相接：b = 下一页 a，末页 = TOTAL。保证任何时刻「有且只有一页」
    for i, s in enumerate(slides):
        s["b"] = int(slides[i + 1]["a"]) if i + 1 < len(slides) else total
        if s["b"] <= s["a"]:
            s["b"] = s["a"] + 1
        # 图板页：labels 本身没有时间戳（它们不是讲稿句），同一 at 塞进来就会
        # 「一瞬间全亮」—— 动效图解的灵魂（逐个长出）就没了。按本页真实时长均分，
        # 系数 0.85 保证最后一个标签仍在 b 之前播完。
        if s.get("skpage"):
            n_e = len(s["els"])
            span = max(1, s["b"] - s["a"])
            step = span * 0.85 / max(1, n_e)
            for j, e in enumerate(s["els"]):
                e["at"] = int(s["a"] + step * j)
        # 孤条页（<2 元素）强制回落垂直栈：flow 单条画不出箭头、compare 单条
        # 右半屏全空 —— 单元素用「重点句式」展示反而成立。一行覆盖所有来源
        # （_motion_split 的豁免大图、_expand_sketch 拆出的单条 before/after）。
        if len(s["els"]) < 2 and s["layout"] in ("flow", "compare"):
            s["layout"] = "grid"
        s["no"] = i + 1
        s["total"] = len(slides)
    # 契约：第一页 a 必须收敛到 0（与 ppt_slides 同口径）—— 否则 slideAt 在
    # ms < a 时仍返回第一页，开片会有几百毫秒的空白画布（图板页居首时必现）。
    if slides and slides[0]["a"] > 0:
        slides[0]["a"] = 0
    return nav, slides


def _build_motion_stage(db, v) -> Path:
    """动效图解舞台页：独立模板，页结构（屏）与 id 在注入前**已全部算好**。"""
    payload = stage_payload(db, v)
    nav, slides = motion_slides(payload)
    title = getattr(v, "title", "") or "AI 视频"
    repl = {
        "__TITLE__": _esc(title),          # 只用于 <title>（HTML 上下文）
        "__TITLE_JS__": _j(title),         # JS 里用 JSON 字面量，避免引号转义踩坑
        "__NAV__": _j(nav),
        "__SLIDES__": _j(slides),
        "__SEGS__": _j(payload["segs"]),
        "__TOTAL__": str(int(payload["total"])),
    }
    repl["__RATE__"] = str(svc.render_rate_for("motion"))
    tpl = _TEMPLATE_MOTION.read_text(encoding="utf-8")
    for k, val in repl.items():
        tpl = tpl.replace(k, val)
    # ⚠️ 漏替换的占位符 = 白屏成片，当场报错（与 PPT/板书模板同一纪律）。
    left = sorted(set(re.findall(r"__[A-Z][A-Z_]*__", tpl)))
    if left:
        raise ValueError("动效图解模板存在未替换的占位符：%s" % ", ".join(left))
    out = svc.stage_abspath(v.id)
    out.write_text(tpl, encoding="utf-8", newline="\n")
    return out


def build_stage(db, v, avatar: dict | None = None) -> Path:
    """生成舞台页 HTML 并返回路径（`videos_root/{id}/stage.html`）。

    ⚠️ 材料页只在需要「原件画面」时导出（source 皮肤，或口播内层是 source）：
       PDF 按 150dpi 渲 57 页是分钟级的开销，结构图皮肤白付这笔账。
    """
    skin = (getattr(v, "skin", "") or "source").lower()
    if skin not in svc.SKINS:
        skin = "source"
    # 解压型皮肤（radio / night / ambient）走**独立模板**（DOM 结构与信息型完全不同）
    if skin in _TEMPLATE_RELAX:
        return _build_relax_stage(db, v, skin)
    # PPT 课件（2026-10-20）：同样走独立模板 —— 它不需要 graph_svg（课件页上是条目
    # 列表，不是节点图），也不必导出材料原件页（不读 SRC/原件）。
    # ⚠️ 必须放在 `need_pages` 之前：课件皮肤与 source 无关，白导 57 页 PDF 是分钟级浪费。
    if skin == "ppt":
        return _build_ppt_stage(db, v)
    # 板书（2026-10-20）：同为独立模板 + 不读原件页（理由同 ppt：白导 PDF 是分钟级浪费）。
    if skin == "board":
        return _build_board_stage(db, v)
    # 动效图解（2026-10-21）：同为独立模板 + 不读原件页（理由同上）。
    if skin == "motion":
        return _build_motion_stage(db, v)
    inner = _inner_of(v)
    need_pages = (skin == "source") or (inner == "source")

    names, aspect = ([], 0.0)
    if need_pages:
        names, aspect = export_pages(db, v)
        # ⚠️⚠️ 没有原件画面时必须**当场报错**，不能让它带着空的 SRC_PAGES 去渲染：
        #    舞台页的 `ready()` 会一直等 `pageImg.naturalWidth`，那是个**永远不成立**的条件
        #    → 无头浏览器干等 20 秒后抛一句 `Page.wait_for_function: Timeout`，
        #    用户看到的是一句与原因毫无关系的超时（实测踩到：talking_head 内层 source
        #    却没有任何来源材料）。静默降级 + 事后难懂的报错，比直接拒绝坏得多。
        if not names:
            raise ValueError(
                "该视觉方案需要「来源材料的原件画面」，但这份作品没有可用的来源材料："
                "请把视觉方案换成「结构图」，或先为作品选一份材料/笔记作为来源")

    payload = stage_payload(db, v)
    av_src = _avatar_src(v, avatar)
    av_mot = _avatar_motion(v, avatar)
    # 形象渲染数据：**一律是图片**（预置真人照 / 用户上传）——绝不让成片落到占位框。
    # 未选择形象（avatar_id 为空）时 `_avatar_src` 已按预置 01 兜底：作品 1 实测的
    # 「未选择形象」虚线框是工程态画面，不该烧进成片。
    # `tint` 只喂给形象背景柔光（.th-glow）的色相，**不参与任何绘制**。
    av_render: dict = {}
    if av_src:
        av_render["src"] = av_src
    if av_mot:
        av_render["motion"] = av_mot
    _aid = str(getattr(v, "avatar_id", "") or "").strip() or svc.BUILTIN_AVATARS[0]["id"]
    _info = svc.avatar_info(_aid) or {}
    _tint = _info.get("tint")
    av_render["tint"] = _tint if _tint is not None else svc.BUILTIN_AVATARS[0]["tint"]
    # 背景口径透传给 `_th_dom`：决定要不要免 mask（见 video.py::BUILTIN_AVATARS 的 bg 说明）
    av_render["bg"] = str(_info.get("bg") or "keep")
    th_dom = _th_dom(v, av_render)
    avatar_pos = str(getattr(v, "avatar_pos", "") or "right").lower()
    if avatar_pos not in ("left", "right", "pip"):
        avatar_pos = "right"

    # ⚠️ 铺满档（2026-10-14）：只给三个**非口播**结构图皮肤 —— 右栏移除后腾出的空间
    #    还给内容。数字人口播·结构图的 `skin` 是 talking_head（不在 `GRAPH_SKINS` 里），
    #    天然拿到 `wide=False`：那一档右侧站的是数字人形象，铺满会把卡片压到人身上。
    svg = graph_svg(payload["shots"], payload["points"], payload["at_by_scene"],
                    skin=skin,
                    wide=(skin in svc.GRAPH_SKINS))

    # 口播（talking_head）：注入真实人声 RMS 包络（A+C，2026-10-13）。
    #   舞台页据此让「说话活跃度 / 动作视频推进」跟着**真实声音**走，不再是与音频无关的合成动画。
    #   ⚠️ 占位符必须**无条件**替换：非口播皮肤注 `[]`（模板回落「合成开合」= 旧行为），
    #      否则非口播页会残留 `__LEVELS__` 字面量。
    th_levels: list = []
    if skin == "talking_head":
        _au = svc.resolve_audio(db, v)
        if _au.get("ok"):
            th_levels = waveform_svc.audio_levels(_au["path"], fps=svc.OUT_FPS)
        else:
            # ⚠️ 口播拿不到音轨时**不静默降级**：渲染日志里留一句可见的警告，
            #    模板回落旧行为（合成开合）—— 比「渲出一份没对上音频的成片却无人知晓」强。
            print("  [口播] 音轨不可用（%s）→ 说话活跃度回落合成开合"
                  % (_au.get("error") or "?"))

    tpl = _TEMPLATE.read_text(encoding="utf-8")
    repl = {
        "__TITLE__": _esc(getattr(v, "title", "") or "AI 视频"),
        "__SKIN__": skin,
        "__INNER__": inner,
        "__AVATAR_POS__": avatar_pos,
        "__TH_DOM__": th_dom,
        "__GRAPH_SVG__": svg,
        "__LEVELS__": _j(th_levels),
        "__FPS__": str(svc.OUT_FPS),
        "__SEGS__": _j(payload["segs"]),
        "__SCENE_OF__": _j(payload["scene_of"]),
        "__SHOTS__": _j(payload["shots"]),
        "__POINTS__": _j(payload["points"]),
        "__SRC__": _j({"aspect": (aspect or 0.75), "count": len(names),
                       "steps": payload["steps"]}),
        "__TOTAL__": str(int(payload["total"])),
        "__SRC_PAGES__": _j([f"pages/{n}" for n in names]),
    }
    for k, val in repl.items():
        tpl = tpl.replace(k, val)

    out = svc.stage_abspath(v.id)
    out.write_text(tpl, encoding="utf-8", newline="\n")
    return out


# ---------- 解压型皮肤（radio / night / ambient） ----------

# 电台 / 深色皮肤的动效开关（2026-10-12）。生产恒 True。
# ⚠️ 唯一用途：验收侧把它置 False，可以拿到「静止态」页面与改造前模板逐像素比对
#    （存量零回归判据 X1'，见 _fetch_probe/_probe_radio_motion_20261012.py）。
#    别在业务代码里改它 —— 关掉等于所有电台成片都失去动效。
MOTION_ENABLED = True


def _build_relax_stage(db, v, skin: str) -> Path:
    """解压型皮肤的舞台页：与信息型**共用时间轴与音轨**，但走独立模板（DOM 结构不同）。

    数据来源：
      · SEGS / total —— 复用 `stage_payload`（与信息型同一份讲稿时间轴）；
      · LEVELS —— 音轨的 RMS 包络（`waveform.audio_levels`，radio / night 的波形驱动）；
      · 插画 —— **确定性选择**（`artworks.pick_*`，同一材料同一皮肤永远同一组，否则指纹失效）。
    """
    payload = stage_payload(db, v)
    segs = payload["segs"]
    total = int(payload["total"])

    audio = svc.resolve_audio(db, v)
    if not audio.get("ok"):
        raise ValueError("解压皮肤需要音轨：" + str(audio.get("error") or "音轨不可用"))

    mat_key = svc.first_material_id(v) or f"video-{getattr(v, 'id', 0)}"

    # 自定义背景（radio / night / ambient 共用**一份全局库**，作品只存 id）。
    # ⚠️ 图丢了**不报错**、回落皮肤自带画面 —— 背景只是可选修饰，缺失对用户毫无损失；
    #    这与音轨缺失必须报错（没有声音的成片是残次品）容忍度完全不同。
    _bgp = bg_svc.resolve(str(getattr(v, "bg_id", "") or ""))
    bg_uri = _bgp.resolve().as_uri() if _bgp else ""

    tpl = _TEMPLATE_RELAX[skin].read_text(encoding="utf-8")
    # 预设变体（radio / night 各 3 套，2026-10-12）：空 = 默认套（模板默认配色原样，
    # 存量成片重渲染逐像素一致）。⚠️ 用 getattr：离线探针的假作品没有这一列。
    variant = svc.norm_variant(skin, getattr(v, "skin_variant", ""))
    repl = {
        "__TITLE__": _esc(getattr(v, "title", "") or "AI 视频"),
        "__SEGS__": _j(segs),
        "__TOTAL__": str(total),
        # 模板据此决定「要不要铺背景层 / 要不要加强遮罩」。
        # kind: user（用户图，加强遮罩）/ builtin（night 内置夜景，原遮罩）/ none（无背景）
        "__BG_URL__": bg_uri,
        "__BG_KIND__": "user" if bg_uri else "none",
        "__VARIANT__": variant,
        # 动效开关（见模块级 MOTION_ENABLED 注释）：模板里的 render(ms) 纯函数是否启用
        "__MOTION__": "1" if MOTION_ENABLED else "0",
        # 渲染倍速（2026-10-20）：页钟 × RATE。⚠️ 漏填 = 页面残留 __RATE__ 字面量直接坏
        # （占位符零残留探针会拦）；只有纯函数模板能 >1（口径 svc.render_rate_for）。
        "__RATE__": str(svc.render_rate_for(skin)),
    }

    if skin in ("radio", "night"):
        levels = waveform_svc.audio_levels(audio["path"], fps=svc.OUT_FPS)
        repl["__LEVELS__"] = _j(levels)

    # 画面署名（2026-10-13）：底部那行字从「插画配套文案」改为**作品署名**
    # （brand_text 优先，空则回落作品标题 —— 口径只此一处，见 svc.brand_text_for）。
    # ⚠️ radio 的三幅插画下都写同一个署名 →「名字随插画轮换」的观感随之消失。
    #    这是产品决策：名字属于**作品**，不属于插画（用户要的是「我的作品名上屏」）。
    # ⚠️ 走 _j 注入：署名是用户输入，裸拼进 JS 会被引号 / 换行 / `</script>` 打断。
    brand = svc.brand_text_for(v)
    if skin == "radio":
        arts = artwork_svc.pick_radio_artworks(mat_key)
        for _a in arts:
            _a["brand"] = brand
        repl["__ARTS__"] = _j(arts)
    elif skin == "night":
        if bg_uri:
            # 用户背景：不再叠内置夜景插画。品牌字（「夜 曲」）是**内置插画配套文案**，
            # 与用户这张图无关 → 一并置空，免得出现「图换成了自己的、字还是那幅画的」错配。
            repl["__BG__"] = bg_uri
            repl["__BG_KIND__"] = "user"
        else:
            # 按变体选夜景（空变体 = 固定的 night_dusk，存量零回归）
            bg = artwork_svc.pick_night_background(mat_key, variant)
            bg_path = artwork_svc.night_background_path(bg)
            if not bg_path.exists():
                raise ValueError(f"深色插画资产缺失：{bg_path.name}（请检查 assets/night/）")
            repl["__BG__"] = bg_path.resolve().as_uri()
            repl["__BG_KIND__"] = "builtin"
        # ⚠️ 署名**两种背景情况都显示**（2026-10-13 语义变更）：以前 __BRAND__ 是内置
        #    插画的配套文案（「夜 曲」），与用户自己那张图无关，所以用自定义背景时
        #    置空是对的；现在它是**作品署名**，用哪张底图都该显示 —— 否则会出现
        #    「我明明取了名字，画面却空白」。
        # ⚠️ 必须走 _j 包成 JSON 字面量（模板里也不带引号），理由同 radio 那处。
        repl["__BRAND__"] = _j(brand)
    elif skin == "ambient":
        inner = svc.resolve_inner(v)
        repl["__GLOW_CFG__"] = _j(AMBIENT_GLOW)
        repl["__INNER__"] = inner
        repl["__DOC_TAG__"] = "材料原件 · 讲到哪一处，光就落在哪一处"
        if inner == "source":
            # 与 build_stage 同一口径：需要原件画面时，拿不到就当场报错（不带空 SRC 去渲染）。
            names, aspect = export_pages(db, v)
            if not names:
                raise ValueError("ambient 的「材料原件」内容层需要可用的来源材料")
            repl["__SRC__"] = _j({"aspect": aspect or 1.6, "count": len(names),
                                  "steps": payload["steps"]})
            repl["__SRC_PAGES__"] = _j([f"pages/{n}" for n in names])
        else:
            repl["__SRC__"] = _j({"aspect": 1.6, "count": 0, "steps": []})
            repl["__SRC_PAGES__"] = _j([])

    for k, val in repl.items():
        tpl = tpl.replace(k, val)
    out = svc.stage_abspath(v.id)
    out.write_text(tpl, encoding="utf-8", newline="\n")
    return out
