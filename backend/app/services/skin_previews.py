"""视觉方案**示例图**（「显示示例，帮助用户提前了解视觉风格效果」）。

【为什么不是预先生成好、随包分发的静态图】
7 张示例必须跟着**模板**走：改了 `stage_tpl_*.html`（配色 / 布局 / 元素）之后，
静态图就成了「说的和做的不是一回事」——用户按示例选了一个方案，拿到的是另一个样子。
所以这里用**指纹**判定：模板（或演示数据 / 取帧参数）一变，示例自动重建。

⚠️⚠️【但信息型 5 张是刻意的例外：它们用**设计版**静态图，不参与真渲染】
    即 `SEED_FIRST` 里的 source / graph / graph_paper / graph_clean / talking_head。
    原因是**显示尺寸**：卡片里示例图只有 **160×90**。真渲染是 1920×1080 舞台 → 640×360
    → 卡片 160×90，累计 12 倍缩小 —— 模板里 20px 的正文显示时不到 2px；再加上 7 张卡片、
    右侧栏、角落徽标一起挤进来，缩略图上就是「一片深灰 + 一堆模糊文字」（用户反馈：不好看）。
    对照组 radio/night/ambient 是「大面积底色 + 单一主体」的构图，小尺寸下依然成立。
    故这 4 张改由 `_fetch_probe/_gen_skin_previews_design_20261012.py` 按**同一套模板配色**
    手绘（元素 ≤4 个、字号 46–96px、对比拉满），随包分发；`_render_all()` 对它们只做
    「拷进 live 目录」，不取帧。**代价**：模板改了这 4 张不会自动跟（它们不再是模板的截图）。
    回退成真渲染：把皮肤名从 `SEED_FIRST` 移除，再把 `SPEC_REV` +1。

【两层结果，取用优先级】
1. `_live_dir(sig())`：当前指纹的图 —— 真渲染的皮肤是现渲的，`SEED_FIRST` 的皮肤是拷来的；
2. `seed_dir()`：随包分发的图（没有 Chromium 的环境 / 重建还没跑完时用）。
   前端只看 URL，不区分两者；`list_previews()` 里带 `source` 字段供排查用
   （`live` = 现渲 · `design` = 设计版 · `seed` = 兜底）。

【为什么渲染在后台线程】
起一次 Chromium + 7 个皮肤 ≈ 8–15 秒。放在 `/options` 的请求里会让整个视频页卡住。
`ensure_async()` 只负责「起一个线程然后立刻返回」，页面拿到的是**上一次的图或种子图**。

【⚠️⚠️ 一个必须守住的边界：绝不污染真实作品的渲染】
示例图的舞台页也是 `stage.build_stage` 造的，而那个函数在有「原件画面」需求时会调
`stage.export_pages` 去导出用户材料。示例图没有用户材料，必须替身。
替身写成**委托式**（只接管带 `_asc_preview` 标记的假作品，其余原样转发给原函数）——
直接替换会让**真实作品**在窗口期内拿到示例页图（静默出错的成片，且只有成片才看得出来）。
"""
from __future__ import annotations

import hashlib
import json
import threading
import wave
from pathlib import Path

from ..core.config import settings
from . import renderer
from . import stage
from . import video as svc

# 取图入口。前端 `<img src>` 是**直连**（不走 axios 的 baseURL）→ 必须带全 `/api` 前缀。
PREVIEW_URL_PREFIX = "/api/videos/skin-previews"

# 出图规格。⚠️ 面板里按卡片宽度显示（2 列布局下约 480px），640×360 是 1.3 倍图；
#    1920×1080 的整图是 1.3MB，7 张就是 9MB —— 面板每次刷新都拉一遍，不可接受。
PREVIEW_W, PREVIEW_H = 640, 360
PREVIEW_QUALITY = 84

# 演示讲稿时长。⚠️ 太短会让结构图的卡片来不及长出来（示例看起来是空屏）。
DEMO_MS = 24000
DEMO_AUDIO_NAME = "demo.wav"

# 示例图专用作品的 id。⚠️⚠️ 必须是**负数**：
#   · 真实作品 id 是正整数自增（`Video.id`），负数永不撞车；
#   · 按 DB 查作品列表也永远不会把它列出来（它没有 DB 行）。
# ⚠️ 7 个皮肤**共用同一个 id** 是刻意的：`stage_abspath(v.id)` 是「一作品一目录」，
#    共用意味着必须**建一个渲一个**（不能先全建好再一起渲，后建的会覆盖先建的 —— 实测踩到过）。
PREVIEW_VID = -1001

# 7 个视觉方案 + 取帧时刻。
# ⚠️ 时刻不是随便挑的：结构图/纸感/口播的第 1 屏是 grid 版式，7600ms 时 7 张卡片已全部
#    点亮；材料原件用 9000ms（那时第 2 个步点已生效，能展示「讲到哪框住哪一处」）；
#    解压型三皮肤用 9000ms（第 2 幅插画已淡入、波形是活的）。
CASES = (
    ("source", "", 9000, "材料原件"),
    ("graph", "", 7600, "结构图"),
    ("graph_paper", "", 7600, "结构图 · 纸感"),
    ("graph_clean", "", 7600, "结构图 · 清透"),
    ("talking_head", "graph", 7600, "数字人口播"),
    ("radio", "", 9000, "电台 · 浅色"),
    ("night", "", 9000, "电台 · 深色"),
    ("ambient", "none", 9000, "氛围"),
)

# ⚠️⚠️ 这 5 个皮肤用「设计版」静态图（seed），**不参与真渲染**（见文件头说明）。
#    卡片里只有 160×90，真渲染帧在小尺寸下全是糊团（用户反馈「不好看」）。
#    回退：从这里去掉皮肤名，并把 SPEC_REV +1。
SEED_FIRST = ("source", "graph", "graph_paper", "graph_clean", "talking_head")

# 取帧/渲染方式的版本号。**改了取帧细节（等待时长、注入的样式、缩放算法）必须 +1**，
# 否则指纹不变 → 老图不会被重建 → 改了等于没改。
# v2：信息型 4 张改用设计版静态图（`_render_all` 对它们只拷贝 seed）。
#     ⚠️ 必须 +1：老 live 目录里躺着那 4 张旧的真渲染帧，指纹不变就不会重建，
#        用户看到的还是旧图（改了等于没改）。
# v3：+graph_clean（结构图·清透，2026-10-13），设计版种子图随包分发。
# ⚠️⚠️ v4 / v5 是**基于错误前提**的一次改动（2026-10-14，已在本版回滚）——
#     当时以为「示例图 = 模板的真渲染帧」，于是每改一次模板就重渲一批真渲染图塞进 assets。
#     但 `SEED_FIRST` 这 5 张的**存在理由恰恰是「不要真渲染」**（见文件头：160×90 卡片里
#     12 倍缩小，真渲染帧必然糊成「一片深灰 + 一堆模糊文字」，用户当初就是为此反馈的）。
#     后果：那 4 张被换成真渲染帧 → 缩略图退化，且**与「移除镜头字样」这件事毫无关系**。
#     ⚠️ 教训：设计版示例图**不跟模板走**，它是为小尺寸可读性单独画的设计稿
#        （元素 ≤4 个、字号 46–96px）。模板改版式/文字**不需要**动它。
# v6：① 回滚 v4/v5 —— 4 张恢复为设计版（`_gen_skin_previews_design_20261012.py` +
#        `_gen_skin_preview_clean_20261013.py` 重跑）；
#     ② 用户上一轮真正诉求的**正确落点**：设计版里也有「镜头」字样，改的是**脚本**——
#        `.tag` 的「镜头 1 · 结构图 / 纸感 / 数字人口播 / 清透」→ 去掉「镜头 1 · 」；
#        清透的 `.kick2`「镜头 · 3 个要点」→「3 个要点」（与模板 `.ckick2` 同口径）。
#     **必须 +1**：live 目录里躺着 v5 那批真渲染帧，指纹不变不会重建。
SPEC_REV = "6"

_SVC_DIR = Path(__file__).resolve().parent
_TEMPLATES = (_SVC_DIR / "stage_tpl.html", _SVC_DIR / "stage_tpl_radio.html",
              _SVC_DIR / "stage_tpl_night.html", _SVC_DIR / "stage_tpl_ambient.html")

# ---------------------------------------------------------------- 目录

def previews_root() -> Path:
    p = Path(settings.data_dir) / "skin_previews"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _live_dir(sig: str) -> Path:
    return previews_root() / sig


def seed_dir() -> Path:
    """随包分发的兜底图（`services/assets/skin_previews/{skin}.jpg`）。"""
    return _SVC_DIR / "assets" / "skin_previews"


# ---------------------------------------------------------------- 演示数据
# ⚠️ 内容刻意中性、指向「一门假想的课」，不让用户以为软件里预置了某份真实资料。

DEMO_TEXTS = [
    "先想清楚这门课要解决的真实问题是什么", "再把问题拆成三个可以分别验证的假设",
    "第一个假设是用户根本没有意识到自己需要它", "第二个假设是知道需要但找不到合适的工具",
    "第三个假设是找到了却因为操作太复杂而放弃", "这三个假设对应三种完全不同的设计方向",
    "所以第一步不是画界面而是去访谈真实用户", "访谈要问过去的行为而不是对未来的意愿",
    "因为对未来的意愿几乎总是被礼貌和想象污染", "而过去的行为留下了可以核对的时间与痕迹",
    "把访谈记录按假设归类之后再决定做哪一个", "优先做那个失败成本最低但信息量最大的假设",
    "确定方向之后才进入方案设计这一步", "方案设计要同时给出理想态和最小可行态",
    "理想态用来对齐团队对终局的想象", "最小可行态用来在两周内拿到第一轮反馈",
    "然后才开始讨论视觉风格与交互细节", "细节必须服从前面定下来的假设与方向",
    "最后把整个过程写成可以复用的检查清单", "清单的价值在于让下一个人不重复踩坑",
    "这样一轮下来团队会积累出自己的方法论",
]
DEMO_SHOTS = [
    {"n": 1, "l": "先定义真实问题", "a": 0, "b": 8000, "layout": "grid",
     "screens": [{"layout": "grid", "a": 0, "b": 8000}]},
    {"n": 2, "l": "三个可验证的假设", "a": 8000, "b": 16000, "layout": "hub",
     "screens": [{"layout": "hub", "a": 8000, "b": 16000}]},
    {"n": 3, "l": "从访谈回到方案", "a": 16000, "b": 24000, "layout": "compare",
     "screens": [{"layout": "compare", "a": 16000, "b": 24000}]},
]
_DEMO_PT = [
    ("定义真实问题", "concept"), ("拆成三个假设", "concept"), ("分别可验证", "good"),
    ("访谈真实用户", "concept"), ("问过去的行为", "good"), ("不看未来意愿", "cost"),
    ("按假设归类", "concept"), ("失败成本最低", "good"), ("信息量最大", "good"),
    ("先定方向", "concept"), ("再做方案设计", "concept"), ("理想态对齐终局", "good"),
    ("最小可行态", "concept"), ("两周拿反馈", "good"), ("视觉服从假设", "cost"),
    ("写成检查清单", "good"), ("下一个人不踩坑", "good"), ("沉淀方法论", "concept"),
    ("可复用的过程", "concept"), ("不重复劳动", "cost"), ("持续迭代", "concept"),
]
DEMO_POINTS = [
    {"t": _DEMO_PT[i][0], "k": _DEMO_PT[i][1], "sc": i // 7,
     "at": (i // 7) * 8000 + int(8000 * (i % 7 + 1) / 8), "group": i // 7 + 1}
    for i in range(len(_DEMO_PT))
]
DEMO_STEPS = [
    {"at": 0, "page": 1, "anchor": DEMO_TEXTS[0], "hit": "真实问题",
     "sc": 1.0, "x": 16, "y": 20, "w": 44, "h": 9},
    {"at": 8000, "page": 2, "anchor": DEMO_TEXTS[7], "hit": "过去的行为",
     "sc": 1.0, "x": 12, "y": 44, "w": 48, "h": 10},
    {"at": 16000, "page": 2, "anchor": DEMO_TEXTS[15], "hit": "最小可行态",
     "sc": 1.0, "x": 18, "y": 66, "w": 40, "h": 9},
]

# 演示材料页的正文（两张）。第三行会被涂成「正在讲到」的底色，与 steps 的高亮框呼应。
_PAGE_TEXT = [
    ("示例材料 · 第一页", [
        "一、先把问题定义清楚",
        "与其急着画界面，不如先写下这门课要解决的",
        "真实问题，并把它拆成可以分别验证的假设。",
        "每一个假设都应当能用一次访谈或一次实验推翻。",
        "定义不清的问题，做出来的方案几乎一定会返工。",
        "所以这一步看起来最慢，其实是后续最快的路径。",
    ]),
    ("示例材料 · 第二页", [
        "二、从访谈回到方案",
        "访谈要问过去的行为，而不是对未来的意愿；",
        "过去的行为留下了可以核对的时间与痕迹。",
        "把记录按假设归类之后，再决定先做哪一个。",
        "优先做失败成本最低、但信息量最大的那个假设。",
        "方向定下来之后，才进入方案设计与视觉细节。",
    ]),
]


def _cjk_fonts():
    """找一对中文字体（标题 / 正文）。**找不到就返回 (None, None)** —— 调用方退化画灰条。

    ⚠️ 刻意不抛错：示例图是锦上添花，缺字体最多是「示意图略糙」，
       绝不该让整个功能在外语环境 / 精简系统上直接不可用。
    """
    try:
        from PIL import ImageFont
    except BaseException:
        return None, None
    for c in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
              "C:/Windows/Fonts/simsun.ttc",
              "/System/Library/Fonts/PingFang.ttc",
              "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
              "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"):
        try:
            if Path(c).is_file():
                return ImageFont.truetype(c, 34), ImageFont.truetype(c, 22)
        except BaseException:
            continue
    return None, None


def _demo_pages(outdir: Path) -> tuple:
    """生成两张演示用材料页，返回 `(文件名列表, aspect)`（与 `stage.export_pages` 同契约）。

    ⚠️ 文件名与 `stage.py` 的口径无关（那边是 `img_NN.jpg`）—— 这里返回什么名字，
       `__SRC_PAGES__` 就用什么名字，舞台页按数组顺序取，不存在排序错位问题。
    """
    from PIL import Image, ImageDraw
    outdir.mkdir(parents=True, exist_ok=True)
    W, H = 1334, 750
    ft, fb = _cjk_fonts()
    names = []
    for i, (title, body) in enumerate(_PAGE_TEXT, 1):
        im = Image.new("RGB", (W, H), (253, 253, 251))
        d = ImageDraw.Draw(im)
        d.rectangle([0, 0, W, 7], fill=(124, 134, 156))
        y = 56
        if ft:
            d.text((80, y), title, font=ft, fill=(30, 34, 44))
        else:
            d.rectangle([80, y + 8, 80 + 300, y + 32], fill=(186, 191, 203))
        y += 62
        for k, line in enumerate(body):
            if k == 2:      # 「正在讲到的那一段」
                d.rectangle([64, y - 9, W - 64, y + 36], fill=(255, 245, 210))
            if fb:
                d.text((80, y), line, font=fb, fill=(58, 62, 74))
            else:
                w = int(W * 0.60 * (0.58 + 0.42 * ((k * 7) % 5) / 4.0))
                d.rectangle([80, y + 8, 80 + w, y + 24], fill=(203, 208, 218))
            y += 44
        p = outdir / f"p{i}.png"
        im.save(p, "PNG")
        names.append(p.name)
    return names, float(W) / float(H)


def _demo_audio(path: Path) -> None:
    """生成演示音轨（16kHz 单声道 WAV）。

    ⚠️ 不能省成静音或匀速正弦：`waveform.audio_levels` 对静音轨会给全 0，
       `radio` / `night` 的波形条恒平 —— 示例图恰好丢掉了「跟着人声起伏」这个卖点。
       所以这里按句给包络 + 音节级抖动，波形才有东西可动。
    """
    import numpy as np
    sr = 16000
    n = int(DEMO_MS / 1000 * sr)
    rng = np.random.default_rng(20261012)
    t = np.arange(n) / sr
    env = np.zeros(n, np.float32)
    seg = n / float(len(DEMO_TEXTS))
    for i in range(len(DEMO_TEXTS)):
        a = int(i * seg)
        b = min(n, int(a + seg * 0.86))
        if b <= a:
            continue
        j = np.arange(b - a) / sr
        e = np.minimum(1.0, j / 0.045) * np.exp(-j / (seg / sr * 0.55))
        env[a:b] = e * (0.62 + 0.38 * np.abs(np.sin(2 * np.pi * 5.2 * j + i)))
    f0 = 118.0 + 16.0 * np.sin(2 * np.pi * 0.21 * t)
    ph = 2 * np.pi * np.cumsum(f0) / sr
    x = (np.sin(ph) + 0.46 * np.sin(2 * ph) + 0.24 * np.sin(3 * ph) + 0.12 * np.sin(4 * ph))
    x = x * env + rng.normal(0, 0.006, n)
    peak = float(np.abs(x).max()) or 1.0
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(x / peak, -1, 1) * 30000).astype(np.int16).tobytes())


class _PreviewVideo:
    """示例图专用假作品（`stage.build_stage(None, v)` 只读这些字段）。

    ⚠️ `_asc_preview` 是**替身函数的判据**（见模块 docstring 最后一段）：
       少了它，真实作品的渲染会走进示例分支。
    """
    _asc_preview = True

    def __init__(self, skin: str, inner: str):
        self.id = PREVIEW_VID
        self.title = "示例"
        self.skin = skin
        self.inner_skin = inner
        self.duration_ms = DEMO_MS
        seg = DEMO_MS // len(DEMO_TEXTS)
        self.script = [{"text": DEMO_TEXTS[i], "start_ms": i * seg,
                        "end_ms": (i + 1) * seg, "speaker": "host"}
                       for i in range(len(DEMO_TEXTS))]
        self.shots = [dict(x) for x in DEMO_SHOTS]
        self.points = [dict(p) for p in DEMO_POINTS]
        self.steps = [dict(x) for x in DEMO_STEPS]
        # ⚠️ 走 "own" + 真实写盘的 WAV，是为了**不去替换 `svc.resolve_audio`**：
        #    少一个替身，就少一处「真实作品可能被污染」的风险面。
        self.audio_source = "own"
        self.audio_name = DEMO_AUDIO_NAME
        self.audio_bytes = 0
        self.audio_sig = "preview"
        self.podcast_id = None
        self.source_refs = []
        self.avatar_id = ""
        self.avatar_pos = "right"
        self.bg_id = ""


_REAL_EXPORT_PAGES = stage.export_pages


def _preview_export_pages(db, v):
    """`stage.export_pages` 的**委托式**替身。

    ⚠️⚠️ 只接管带 `_asc_preview` 标记的假作品，其余**原样转发**给原函数。
       直接替换的话，真实作品在示例图渲染期间调用 `build_stage` 会拿到示例页图 ——
       那是一份静默出错的成片（画面上是「示例材料」而不是用户的资料）。
    """
    if not getattr(v, "_asc_preview", False):
        return _REAL_EXPORT_PAGES(db, v)
    return _demo_pages(svc.video_dir(v.id) / "pages")


if not getattr(stage.export_pages, "_asc_preview_stub", False):
    _preview_export_pages._asc_preview_stub = True
    stage.export_pages = _preview_export_pages


# ---------------------------------------------------------------- 指纹

def sig() -> str:
    """示例图的指纹：模板 / 布局代码 / 演示数据 / 取帧参数，任一变化都要重建。

    ⚠️ 必须包含 `stage.py` 与 `artworks.py`：布局与插画都是**代码**生成的，
       只哈希模板会漏掉「卡片位置改了但 HTML 没变」这种情况。
    """
    h = hashlib.md5()
    for p in (*_TEMPLATES, _SVC_DIR / "stage.py", _SVC_DIR / "artworks.py"):
        try:
            h.update(p.read_bytes())
        except OSError:
            h.update(p.name.encode("utf-8"))
    h.update(json.dumps({
        "rev": SPEC_REV, "cases": [list(c) for c in CASES], "texts": DEMO_TEXTS,
        "shots": DEMO_SHOTS, "points": DEMO_POINTS, "steps": DEMO_STEPS,
        "w": PREVIEW_W, "h": PREVIEW_H, "ms": DEMO_MS, "pages": _PAGE_TEXT,
    }, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    return h.hexdigest()[:10]


def _all_present(s: str) -> bool:
    d = _live_dir(s)
    try:
        return all((d / f"{c[0]}.jpg").is_file() for c in CASES)
    except BaseException:
        return False


# ---------------------------------------------------------------- 渲染

def _downscale(src: Path, dst: Path) -> None:
    from PIL import Image
    with Image.open(src) as im:
        im = im.convert("RGB").resize((PREVIEW_W, PREVIEW_H), Image.LANCZOS)
        im.save(dst, "JPEG", quality=PREVIEW_QUALITY, optimize=True)


def _prune_old_sigs(keep: str) -> None:
    """清掉其它指纹的图（派生缓存，随时可重建）。绝不抛错、只删文件不删目录。"""
    try:
        for d in previews_root().iterdir():
            if not d.is_dir() or d.name == keep or len(d.name) != 10:
                continue
            try:
                for f in d.iterdir():
                    try:
                        if f.is_file():
                            f.unlink()
                    except BaseException:
                        pass
                d.rmdir()
            except BaseException:
                pass
    except BaseException:
        pass


def _render_all(s: str) -> int:
    """用真实模板渲染 7 张示例图。返回成功张数（**单个失败不影响其余**）。"""
    exe = renderer.find_chromium()
    if not exe:
        return 0
    out_dir = _live_dir(s)
    out_dir.mkdir(parents=True, exist_ok=True)
    av = svc.video_abspath(PREVIEW_VID, DEMO_AUDIO_NAME)
    try:
        if not av.is_file() or av.stat().st_size < 4000:
            _demo_audio(av)
    except BaseException:
        return 0

    from playwright.sync_api import sync_playwright
    done = []
    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=True, executable_path=exe,
                                args=list(renderer.CHROME_BASE_ARGS))
        try:
            ctx = br.new_context(viewport={"width": 1920, "height": 1080},
                                 device_scale_factor=1)
            page = ctx.new_page()
            for skin, inner, at_ms, _label in CASES:
                # 设计版皮肤：不取帧，把随包分发的图拷进 live 目录即可（见 SEED_FIRST 说明）。
                # ⚠️ 是「拷贝」而不是「跳过」：`list_previews` / `preview_file` 都按
                #    「live 优先」取图 —— live 里没有会退回 seed，那样 `source` 会谎报成 seed。
                if skin in SEED_FIRST:
                    sf = seed_dir() / f"{skin}.jpg"
                    if sf.is_file():
                        try:
                            (out_dir / f"{skin}.jpg").write_bytes(sf.read_bytes())
                            done.append(skin)
                        except BaseException:
                            pass
                    continue
                try:
                    # ⚠️ 建一个渲一个：7 个皮肤共用同一个 PREVIEW_VID，
                    #    而 `stage_abspath(v.id)` 是「一作品一目录」——先全建再一起渲，
                    #    后建的会把先建的 stage.html 覆盖掉（实测踩到，7 张图全一样）。
                    html = stage.build_stage(None, _PreviewVideo(skin, inner))
                    page.goto(html.as_uri())
                    page.wait_for_function("() => window.__stage_ready === true",
                                           timeout=20000)
                    page.wait_for_function(
                        "() => window.__stage.ready && window.__stage.ready()", timeout=20000)
                    # ⚠️⚠️ 必须先把 CSS 过渡时长归零再 seek。
                    #    信息型模板的元素可见性由 `transition` 补（`.el{opacity:0;
                    #    transition:.5s}`），而 `render(ms)` 只切 `.on` 类 ——
                    #    seek 完立刻截图会抓在过渡早期（实测元素 opacity 只有 0.371），
                    #    示例图看起来就是「一片空画面」。
                    page.add_style_tag(content="*, *::before, *::after"
                                               "{ transition-duration: 0s !important; }")
                    page.evaluate("(m) => window.__stage.seek(m)", at_ms)
                    page.wait_for_timeout(220)
                    png = out_dir / f"{skin}.png"
                    page.screenshot(path=str(png), clip={"x": 0, "y": 0,
                                                         "width": 1920, "height": 1080})
                    _downscale(png, out_dir / f"{skin}.jpg")
                    try:
                        png.unlink()
                    except BaseException:
                        pass
                    done.append(skin)
                except BaseException:
                    continue     # 这一张失败 → 列表里缺它，前端就不显示示例（不整体失败）
        finally:
            # ⚠️ 不显式 `br.close()`：真机实测它会挂住很久（191.5s 才抛错）。
            #    `with sync_playwright()` 退出时会终止 driver 并回收浏览器进程。
            try:
                ctx.close()
            except BaseException:
                pass
    if len(done) == len(CASES):
        _prune_old_sigs(s)
    return len(done)


# ---------------------------------------------------------------- 对外接口

_LOCK = threading.Lock()
_THREAD: threading.Thread | None = None
_FAILED: dict = {}


def ensure_async() -> None:
    """把「当前指纹缺图」丢给后台线程。**绝不在请求线程里渲染。**

    ⚠️ 渲染要起 Chromium、约 8–15 秒 —— 放在 `/options` 请求里会让整个视频页卡住。
    ⚠️ 同一进程内只起一次；**失败后不再自动重试**（`_FAILED` 记住这个指纹）。
       否则一个没有 Chromium、或渲染持续报错的环境会每隔几十秒就起一个浏览器进程。
       要重试就重启应用 —— 与「不无限重试」同一纪律。
    """
    global _THREAD
    try:
        s = sig()
    except BaseException:
        return
    if _all_present(s):
        return
    with _LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return
        if _FAILED.get("sig") == s:
            return

        def _run():
            try:
                if _render_all(s) < len(CASES):
                    _FAILED["sig"] = s
            except BaseException:
                _FAILED["sig"] = s

        t = threading.Thread(target=_run, name="skin-previews", daemon=True)
        _THREAD = t
        t.start()


def list_previews() -> dict:
    """示例图清单（前端按 `skin` 匹配卡片）。

    `source` 字段只用于排查：`live` = 当前指纹下现渲的图，`design` = 设计版静态图，
    `seed` = 随包兜底图（没有 Chromium / 重建还没跑完时会落到这里）。
    """
    s = sig()
    live, seed = _live_dir(s), seed_dir()
    items = []
    for skin, _inner, _at, label in CASES:
        try:
            has_live = (live / f"{skin}.jpg").is_file()
            has_seed = (seed / f"{skin}.jpg").is_file()
            if skin in SEED_FIRST:
                # 设计版：它本来就是从 seed 拷进 live 的 —— 如实报 design，不要谎报 live
                src = "design" if has_seed else ("live" if has_live else None)
            elif has_live:
                src = "live"
            elif has_seed:
                src = "seed"
            else:
                src = None
            if src is None:
                continue         # 一张都没有 → 不给这一项（前端不显示，而不是显示破图框）
        except BaseException:
            continue
        items.append({"skin": skin, "label": label, "source": src,
                      "url": f"{PREVIEW_URL_PREFIX}/{skin}.jpg?v={s}"})
    return {"sig": s, "items": items}


def preview_file(skin: str) -> Path | None:
    """取图路径。非法 skin → None。

    · 设计版皮肤（`SEED_FIRST`）：**seed 直接就是权威图**（live 里那份只是它的拷贝），
      所以先取 seed —— 即使 live 目录被清掉也不会掉图。
    · 其余皮肤：优先当前指纹的现渲图，其次随包兜底图。
    """
    if skin not in {c[0] for c in CASES}:
        return None
    if skin in SEED_FIRST:
        try:
            f = seed_dir() / f"{skin}.jpg"
            if f.is_file():
                return f
        except BaseException:
            pass
    try:
        f = _live_dir(sig()) / f"{skin}.jpg"
        if f.is_file():
            return f
    except BaseException:
        pass
    try:
        f = seed_dir() / f"{skin}.jpg"
        return f if f.is_file() else None
    except BaseException:
        return None
