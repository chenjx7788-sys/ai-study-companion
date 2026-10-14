"""AI 视频服务：音轨解析 → 讲稿与分镜 → 视觉步点 → 质量闸门 → 渲染调度。

【为什么成本可控】视频**不重造链路**：播客链路已经把最难的三件事做完
（素材抽取 / 讲稿与逐句时间轴 / 音色·BGM·合成），视频只在既有音频作品之上
补一层「画面」+ 一次合成。本模块只做那层画面。

【复用边界（写错会改脏播客记录 —— PRD 4.5 + 附录 A #16）】
- ✅ 纯函数：`podcast.collect_source` / `plan_for` / `split_verbatim` /
  `script_signature` / `script_to_srt` / `script_to_text` / `audio_is_stale`
- ✅ 合成核心：`tts.synthesize(..., cache_dir=视频自己的目录)`（已与落盘位置解耦）
- ❌ `routers/podcasts.py::_finalize_audio` **绝不可复用** —— 它把结果直接写进
  `Podcast` 的 audio_name / duration_sec / audio_sig / status。视频调用它等于
  **把视频的音频指纹写进播客记录** → 播客界面立刻误报「音频对应的是修改前的文案」，
  而实际什么都没改。视频一律走本模块的 `finalize_video()`。

【两条硬约束，任何"优化"都不得放宽】
1. **音轨不重复合成**：音轨来自播客时**不得**重跑 TTS，直接引用其音频产物与 SRT。
   同一份文案合成两次会因句间留白差异导致两条时间轴漂移，之后「画面与声音对不上」
   将无法排查。
2. **时间轴唯一来源是音轨**：视觉步点、字幕、进度条三者用同一条时间轴
   （`script[i].start_ms/end_ms`），任何一处另算一套都是缺陷。

【本模块的两条位置不变式（PRD 3.5.5 约束 3）】
- 相邻两步的框**必须不同**（否则画面连着两步不动）；
- **同一页的「连续段」内位置两两不同** —— 挪走又挪回同一处 = 观感上来回弹。
  ⚠️ 不要写成「每页步数 == 每页位置数」：同一张卡在讲稿里可能出现两次，
  后半段复用前半段的框是正常的。正确写法：按页号切**连续段**，段内位置两两不同。
"""
import hashlib
import re
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path

from ..core.config import settings

# ---------- 常量 ----------

# 视觉方案（皮肤）。talking_head 是**叠加层不是替代层** → 必须同时有 inner_skin。
# radio / night / ambient 是「解压型」皮肤（边听边看），与信息型三皮肤并列（解压视觉方案 V2.1）。
# graph_paper 是**结构图的纸感变体**（不是新语义）：知识结构与 points/shots 完全复用 graph，
# 只把画面换成暖白纸张 + 纵向知识条目。留成独立皮肤而不是 graph 的一个开关，
# 是为了让「旧作品按落库的 skin 回放」这条不变式继续成立（改 graph 的默认渲染会改动历史成片）。
# graph_clean 同理（2026-10-13）：结构图的**清透变体**（浅灰白底 + 紫主色白卡片，
# 参照知识类课件的信息图风格：hub=左根右支树状、compare=紫表头表格），同样只换画法。
SKINS = ("source", "graph", "talking_head", "radio", "night", "ambient", "graph_paper",
         "graph_clean")
# graph_paper / graph_clean 与 graph 共用同一套知识点生成（都要 points，都不要锚点）。
GRAPH_SKINS = ("graph", "graph_paper", "graph_clean")
# talking_head 的「内容层」白名单（叠加层之下真正做画面的那个）。
INNER_SKINS = ("source", "graph")
# 口播「内容层」的兜底。⚠️ 为什么是 graph 而不是 source：结构图由讲稿产出，
# **任何来源都能做**；source 需要可定位的原件页面，拿不到时会把作品卡死。
INNER_DEFAULT = "graph"
# ambient 的「内容层」白名单。⚠️⚠️ 绝不能把 "none" 塞进 INNER_SKINS（它被 talking_head
# 共用 —— 一旦有 none，口播就可能取到空内容层 → 重现「永远生成不出来」的死状态）。
# 氛围层允许 none（纯氛围是一等公民）；去掉 graph 档（要结构图直接用 graph 皮肤）。
AMBIENT_INNERS = ("none", "source")
AMBIENT_INNER_DEFAULT = "none"
INNER_LABELS = {"source": "材料原件", "graph": "结构图", "none": "纯氛围"}
SKIN_LABELS = {"source": "材料原件", "graph": "结构图", "talking_head": "数字人口播",
               "radio": "电台 · 浅色", "night": "电台 · 深色", "ambient": "氛围",
               "graph_paper": "结构图 · 纸感", "graph_clean": "结构图 · 清透"}

# 电台皮肤的预设变体（2026-10-12）：radio / night 各 3 套，空 key = 默认套（存量零回归）。
# ⚠️ swatch / ink 只用于生成台的色板胶囊展示；真正的配色写在各舞台模板的
#    [data-variant="..."] CSS 覆盖里（模板是渲染真值，这里是元数据，两边靠探针对齐）。
# ⚠️ night 的变体还各配一张夜景插画（artworks.NIGHT_VARIANT_BG）；radio 插画是
#    程序化线条 SVG，线条色走 currentColor 随变体上色，零资产成本。
SKIN_VARIANTS = {
    "radio": (
        {"key": "", "label": "米色 · 经典", "swatch": "#e8dfcd", "ink": "#2e2a24"},
        {"key": "mist", "label": "雾蓝", "swatch": "#dfe7ec", "ink": "#24323c"},
        {"key": "matcha", "label": "抹茶", "swatch": "#d9e2c6", "ink": "#2f3a24"},
    ),
    "night": (
        {"key": "", "label": "夜幕蓝", "swatch": "#0d1020", "ink": "#f0d080"},
        {"key": "violet", "label": "紫罗兰", "swatch": "#16102a", "ink": "#c9a7f5"},
        {"key": "teal", "label": "墨青", "swatch": "#081a1c", "ink": "#7fd4c8"},
    ),
}


def norm_variant(skin, variant) -> str:
    """皮肤变体归一化：仅 radio / night 支持；空 = 默认套；非法一律归 ""（不报错）。

    ⚠️ 与 inner_skin 的死状态教训不同：变体**没有死状态** —— 非法值落回默认套，
       画面永远渲得出来；留着垃圾值才会让模板掉进「无覆盖的默认样」还以为是选择了。
    """
    s = str(skin or "").strip().lower()
    v = str(variant or "").strip().lower()
    pool = SKIN_VARIANTS.get(s)
    if not pool or not v:
        return ""
    return v if any(x["key"] == v for x in pool) else ""


def variant_label(skin, variant) -> str:
    """变体的展示名（空变体也有名字「米色 · 经典」等；是否展示由调用方决定）。"""
    v = str(variant or "").strip().lower()
    for x in SKIN_VARIANTS.get(str(skin or "").strip().lower(), ()):
        if x["key"] == v:
            return x["label"]
    return ""


# ---------- 画面署名（电台画面底部那行字）2026-10-13 ----------
# 语义：**空 = 回落作品标题**；非空 = 用这个（给「标题太长、画面放不下」改短用）。
# ⚠️⚠️ 只有 radio / night 的画面上有这行字。其余皮肤的画面里根本没有这个位置，所以：
#   ① 归一化**不按皮肤清空** —— 在电台和别的皮肤之间来回切，不该丢掉用户填的字；
#   ② 但 visual_sig **只在电台皮肤下**才把它拼进指纹（见该函数）—— 否则全部非电台
#      历史作品的指纹会无谓变化，集体误报「视觉方案已更换」（与 bg_id 同款纪律）。
# 上限 20 不是拍的：58px 楷体 + 14px 字距下画面横向约容 26 字，留出缩排后仍清晰的余量。
# 改这个值必须同步重跑 `_probe_brand_fit_20261013.py`（实测渲染宽度）与前端 maxlength。
BRAND_TEXT_MAX = 20
# 画面里真的有「署名」这个位置的皮肤（口径只此一处，供 stage / 接口 / 探针共用）。
BRAND_SKINS = ("radio", "night")


def norm_brand_text(s) -> str:
    """画面署名归一化：去首尾空白 + 按 BRAND_TEXT_MAX 截断。

    ⚠️ **不按皮肤清空**：皮肤是随时可改的视觉参数，而署名是用户填的内容。
       按皮肤清空会让「电台 → 结构图 → 电台」这个来回把字吃掉（静默改作品）。
    """
    return str(s or "").strip()[:BRAND_TEXT_MAX]


def brand_text_for(v) -> str:
    """实际会渲染到画面上的署名：brand_text 优先，空则回落作品标题。

    ⚠️⚠️ 这是**渲染与指纹的唯一口径**：stage.py 用它注入模板、visual_sig 用它拼指纹。
       两处各写一遍「or title」迟早漂移成「指纹说没变、画面其实变了」这种最难查的错。
    """
    t = norm_brand_text(getattr(v, "brand_text", ""))
    if t:
        return t
    return norm_brand_text(getattr(v, "title", ""))


# 「有效画面生产者」白名单 —— resolve_visual 只返回这里面的值。
# ⚠️ talking_head 不在其中（它是叠加层，映射到 inner）；ambient 在其中（纯氛围自己产画面）。
VISUAL_KINDS = ("source", "graph", "radio", "night", "ambient", "graph_paper",
                "graph_clean")
# 支持**自定义背景图**的皮肤（解压型三皮肤）。
# ⚠️ 为什么只有这三个：它们的画面结构就是「一张铺满的底 + 少量叠加元素」，
#    换底不影响信息层级；信息型四皮肤的每一寸画面都在承载可读内容（原件 / 卡片 /
#    要点栏），加一层照片只会同时牺牲可读性与信息密度。
# ⚠️⚠️ 口径只此一处：`/options` 下发它、前端据此决定入口出不出现、`stage.py` 按皮肤
#    取用。多处各写一份必然漂移（出现「界面有入口、后端不认」这类静默错配）。
BG_SKINS = ("radio", "night", "ambient")
AVATAR_POS = ("left", "right", "pip")

# 结构图版式白名单（grid 是兜底；LLM 给的版式元素数越界时在解析钳制层降级回 grid）。
# ⚠️ 改这里要同步三处：llm 的 prompt_video_points 文案、stage.py 的布局分发、
#    前端若展示版式名也要跟着加（与「新增 kind 同步前端 3 处」同一纪律）。
GRAPH_LAYOUTS = ("grid", "flow", "hub", "compare", "pyramid")
# ⚠️ grid 的上限必须与 `llm.VIDEO_MAX_GROUP` 一致（8）：「一屏 ≤8」这条硬约束
#    只有一个权威口径，两边漂移会出现「提示词说 8、钳制放行 10」的静默不一致。
# ⚠️ 这几个上限现在承担**两层**语义：① 低于下限 → 该屏降级 grid；
#    ② 高于上限 → 把这一屏**切开成多屏**（不是降级，见 `llm._split_screen_units`）——
#    真实链路每镜 15-21 个元素，只降级会让版式永远退化成 grid（结构化等于没生效）。
GRAPH_LAYOUT_LIMITS = {"grid": (1, 8), "flow": (3, 6), "hub": (3, 7),
                       "compare": (4, 6), "pyramid": (3, 6)}

# V1 输出规格（PRD 待确认 #3 默认建议：仅 1920×1080 / 30fps 一档）
OUT_W, OUT_H, OUT_FPS = 1920, 1080, 30

# 状态机（PRD 2.2.5）。⚠️ 唯一权威是 DB 的 videos.status，渲染进程只是执行器。
STATUSES = ("draft", "scripting", "gate_check", "queued", "rendering", "finished", "failed")
RUNNING_STATUSES = ("scripting", "queued", "rendering")

# 闸门阈值（PRD 3.9.5，来源为原型实测基线：v5 181.68s/42 步、v6 300.00s/77 步、最长静止 5.13s）
GATE_MAX_STILL_MS = 6000        # 判据 1：最长静止
GATE_AVG_GAP_MS = 3900          # 判据 2：平均间隔
GATE_EVENTS_PER_SHOT = 8        # 判据 3：视觉事件总数 ≥ 分镜数 × 8
GATE_HIT_RATE = 0.95            # 判据 4：定位命中率（source 皮肤）
GATE_DISK_MULT = 2              # 判据 8：剩余空间 ≥ 预估体积 × 2

# 视觉事件数规划：目标间隔（PRD 实测基线 300 s / 77 步 ≈ 3.9 s/事件）。
# 取 4000 ms 作为「每多少毫秒至少长出来一次」的**规划值** —— 比闸门阈值（6000 ms）严 33%：
# 真实分布不会均匀，均值必须显著低于上限，才可能整条片子都没有峰。
# ⚠️⚠️ 这个数**不是拍的**，是两条闸门判据反解出来的**有效间隔**：
#     · 判据 2（平均间隔 ≤ 3900ms）→ 间隔 ≤ 3900
#     · 判据 1（最长静止 ≤ 6000ms）→ 实测「均值 → 峰值」比值 **2.06×**（33 分钟片
#       avg=2969 时 max=6120），故间隔 ≤ 6000 / 2.06 ≈ 2913
#     取两者较严者的 2400 —— 实测三次 max_still = 5296/5894/6120 波动偏大，
#     2800 只留 4% 余量太薄（一次就顶到 6120 红），收到 2400 留约 18%。
# ⚠️ 原取 4000（甚至 > 判据2 阈值 3900）→ 长视频的判据 2 在数学上**必然**失败；
#     取 3800 时判据 2 过了、判据 1 仍差 120ms（6120 > 6000）—— 只有反解才够。
EVENT_TARGET_GAP_MS = 2400
# 事件总数上限。⚠️ 原值 240 是为了「绕开模型输出被 max_tokens 截断」而故意压低的
#     （注释原话：防止 20 分钟档算出 300+ 个元素、模型输出必然被截断）——
#     分批生成上线后截断已解决，这个上限就变成了**长视频事件密度不足**的根因：
#     时长 > 240 × 4s ≈ 16 分钟后 needed_events 被硬顶住，判据 1/2 永远过不了。
#     现在按「最长 45 分钟（2700s）× 3800ms」取 900；更长由封顶保护并在界面提示。
MAX_EVENTS = 900

# 目标分镜时长（PRD 实测 300s 档 = 8 个分镜 → 37.5 s/分镜）
SHOT_SECONDS = 37.5
MIN_SHOTS, MAX_SHOTS = 2, 12

# 单句最多拆几个视觉步点（PRD 3.5.5 约束 1：长句拆 2–4 步；这里是上限）
MAX_STEPS_PER_SEG = 4
# 补步次数上限（每次切开当前最大间隔的一半）
MAX_FILL_ROUNDS = 8

# 单句文本上限（与播客路由同口径：过长会让时间戳与逐句高亮失去意义）
MAX_SEG_CHARS = 400

# 产物体积预估（PRD 可行性实测：10 分钟视频 7–11MB ≈ 1.2 MB/分）。
# 取 2.4 MB/分（1080p30 比实测的 720p 高）作为**保守**预检值 ——
# 预检宁可高估（拦住一次渲染的代价远小于渲染到一半磁盘写满）。
EST_BYTES_PER_SEC = 40_000

# ⚠️⚠️ 按皮肤分档的体积预估（解压视觉方案 V2.1 §7.1，20s 样张实测基线）：
#    原固定值 40000（320kbps）**连现有层都低估**（实测 source/graph ≈ 550kbps），
#    对 night(850kbps)/ambient(1577kbps) 低估 2–5 倍 → 磁盘预检（判据 8）会放行
#    「磁盘不够」的渲染。下表取实测码率的约 1.3 倍作保守预检（宁可高估）。
#    分档键是「舞台模板」而非内容层：ambient 不论 inner 都是光晕底（全屏动，主成本）。
EST_BYTES_PER_SEC_BY_CLASS = {
    "source": 90_000,      # source / graph / talking_head（步点间静止，实测 ≈550kbps）
    "graph_paper": 120_000,  # 纸感结构图：每次显现=一整块纸卡 + 全幅亮底重绘
    "graph_clean": 120_000,  # 清透结构图：同族亮底整卡显现，暂与纸感同档（待实测探针标定）
    "radio": 64_000,       # 大面积纯色 + 局部动（实测 391kbps ≈ 49k B/s）
    "night": 138_000,      # 静态插画背景 + 局部动（实测 850kbps ≈ 106k B/s）
    "ambient": 256_000,    # 光晕底全屏动（实测 none 1577 / source 1314 kbps）
}
# ⚠️ graph_paper 单独一档而不是并进 source：**判据要从实测来，不从类比来**。
#    实测（_probe_avideo_paper_render_20261010.py：6s / 3 镜 / 每镜 3 条逐条显现，
#    即 ~1.5 次显现/秒 —— 刻意取**极限密度**）成片 942KB / 6s = **157 kB/s（1257kbps）**，
#    比 source 档（90k）高 1.7 倍。真实片子的显现密度约 0.2 次/秒（闸门按最长静止反推），
#    所以 90k 对常态够用；但它只在**最高密度**下才不够 → 这里取 120k（=960kbps）：
#    对常态绝对保守，对极限密度仍偏乐观一点点，属于「宁可高估」的取舍。
#    真正的兜底在 `disk_ok` 的 ×2 系数，不在这一格。

AVATAR_POS_LABEL = {"left": "左侧", "right": "右侧", "pip": "画中画"}

# 预置形象：**随包分发的真人照片**（`services/assets/avatars/*.jpg`）。
# ⚠️ 2026-10-12 由 SVG 矢量色块人整体替换（用户明确要求「svg 数字人形象不要了」）：
#    矢量路径（stage.py::_preset_fig / 模板的 .th-vec / 眨眼 / 改 path 的口部开合）已一并删除。
# ⚠️ 合规（PRD 3.7.6）：只允许预置素材或用户本人上传；上传入口必须提示「请勿上传他人肖像」。
#    这里的预置图是 **AI 合成面孔**（非任何真实人物肖像），随包分发即「预置素材」。
# ⚠️ 诚实边界：形象**不做面部变形**，说话状态由外圈光环 + 头部微摆 + 句首点头表达，
#    不是音频驱动的真实唇形同步；UI 文案义务不变（见 stage.py::_th_dom）。
# ⚠️ `tint` 只用于形象背景柔光（.th-glow）的色相，**不参与任何绘制**。
BUILTIN_AVATARS = [
    {"id": "preset_01", "label": "讲师 · 知性", "tint": 214, "kind": "lecturer",
     "file": "preset_01.jpg"},
    {"id": "preset_02", "label": "讲师 · 沉稳", "tint": 222, "kind": "lecturer",
     "file": "preset_02.jpg"},
    {"id": "preset_03", "label": "讲师 · 亲和", "tint": 28, "kind": "lecturer",
     "file": "preset_03.jpg"},
    {"id": "preset_04", "label": "讲师 · 阳光", "tint": 205, "kind": "lecturer",
     "file": "preset_04.jpg"},
]
AVATAR_UPLOAD_MAX_MB = 8
# 形象清单一次最多列多少个上传图（预置不算）：只影响 UI 列表长度，不删文件
MAX_AVATARS = 50
# 上传形象的取图入口。前端 `<img src>` 是**直连**（不走 axios 的 /api baseURL），
# 所以这里的值必须带全 `/api` 前缀。口径只有这一处：list_avatars() 出 url，
# 路由在同一个路径上提供文件。
AVATAR_URL_PREFIX = "/api/videos/avatar"


# ---------- 目录与路径 ----------
# ⚠️ 库里只存**相对文件名**，绝对路径在读取时用 data_dir 拼：
#    开发态(backend/data)与打包版(~/.ai-study-companion)数据目录不同，
#    存绝对路径会让切换环境后历史记录全部失效。

def videos_root() -> Path:
    p = Path(settings.data_dir) / "videos"
    p.mkdir(parents=True, exist_ok=True)
    return p


def video_dir(vid: int) -> Path:
    p = videos_root() / str(vid)
    p.mkdir(parents=True, exist_ok=True)
    return p


def parts_dir(vid: int) -> Path:
    """本片单独生成音轨时的分句缓存目录（失败续传用；复用播客时不产生文件）。"""
    p = video_dir(vid) / "parts"
    p.mkdir(parents=True, exist_ok=True)
    return p


def avatars_dir() -> Path:
    """用户上传的形象图（**与作品目录同级**，删作品不删形象）。"""
    p = videos_root() / "_avatars"
    p.mkdir(parents=True, exist_ok=True)
    return p


def preset_dir() -> Path:
    """**随包分发**的预置形象目录（`services/assets/avatars/`）。

    ⚠️ 与 `avatars_dir()` 刻意分开：预置图是**只读的包内资源**，用户上传图在**数据目录**。
       混在一起的话，清理用户数据会把预置形象一起删掉。
    ⚠️ 打包版必须带上这个目录（`AIStudyCompanion.spec` 的 datas 显式收集
       `app/services/assets`）：漏收 = 预置形象集体 404，而**源码态永远看不见**。
    """
    return Path(__file__).resolve().parent / "assets" / "avatars"


def preset_file(aid: str) -> Path | None:
    """预置形象 id → 图片绝对路径；非预置 / 文件缺失时返回 None。

    ⚠️ 只认 `BUILTIN_AVATARS` 里登记的 `file` 名（白名单），**绝不拼接 URL 传来的字符串**：
       路径穿越在这个接口上同样是致命的（`../` 能直接读到包外文件）。
    """
    a = (aid or "").strip()
    for it in BUILTIN_AVATARS:
        if it["id"] == a:
            p = preset_dir() / str(it.get("file") or "")
            try:
                return p if p.is_file() else None
            except BaseException:
                return None
    return None


def preset_video(aid: str) -> Path | None:
    """预置形象 id → **动作视频**（无缝循环 WebM）绝对路径；没有就返回 None。

    ⚠️ 白名单同 `preset_file()`：只认 `BUILTIN_AVATARS` 里登记的 id，文件名由 id 推导，
       **绝不把 URL 传来的字符串拼进路径**（`../` 穿越在这个接口上同样致命）。
    ⚠️ 动作视频是**可选**的：没有就回落静态图（`stage.py::_avatar_motion` 返回空串），
       所以「只给部分形象配视频」是合法状态，不是错误状态。
    ⚠️ 与图片同目录（`assets/avatars/preset_01.webm`）：打包靠 `AIStudyCompanion.spec`
       的 datas 收 `app/services/assets` 整目录，新文件自动带上，无需改 spec。
    """
    a = (aid or "").strip()
    for it in BUILTIN_AVATARS:
        if it["id"] == a:
            p = preset_dir() / (a + ".webm")
            try:
                return p if p.is_file() else None
            except BaseException:
                return None
    return None


def video_abspath(vid: int, name: str) -> Path:
    return video_dir(vid) / (name or "video.mp4")


def poster_abspath(vid: int, name: str) -> Path:
    return video_dir(vid) / (name or "poster.jpg")


def stage_abspath(vid: int) -> Path:
    """舞台页（无头渲染器加载的自包含 HTML），落盘便于复现与排查。"""
    return video_dir(vid) / "stage.html"


def first_material_id(v) -> int | None:
    """作品来源里的第一份材料 id（source 皮肤要有「原件」可定位，就靠它）。

    ⚠️ 口径必须唯一：路由的预检、舞台页导出、渲染器都要用同一个「哪份材料」的答案，
       否则会出现「预检说可用、渲染时说没有原件页面」这种自相矛盾。
    """
    for r in (getattr(v, "source_refs", None) or []):
        if isinstance(r, dict) and r.get("type") == "material" and r.get("id") is not None:
            try:
                return int(r["id"])
            except (TypeError, ValueError):
                continue
    return None


def save_audio(vid: int, audio: bytes) -> str:
    name = "audio.mp3"
    video_abspath(vid, name).write_bytes(audio)
    return name


def remove_video_files(vid: int) -> None:
    """删除该作品的全部产物（音轨 / 分片 / 成片 / 封面 / 舞台页）。**绝不抛错**。

    文件清理失败不应让调用方（删除接口）整体失败 —— 记录删掉了、文件残留可以接受，
    反过来（记录删不掉）才是真正的问题。

    ⚠️ 必须逐文件 `unlink()` + 逐目录 `rmdir()`，**不能用 shutil.rmtree**：
       本机的 safe-delete 护栏会把整目录删除判为 FAIL_CLOSED，且批量删除超阈值时
       会 raise `SystemExit` —— 而 `SystemExit` 不是 `Exception` 的子类，
       冒泡出去会让删除接口直接 500。
    """
    try:
        d = videos_root() / str(vid)
        if not d.exists():
            return
        for f in sorted(d.rglob("*"), reverse=True):
            try:
                if f.is_file():
                    f.unlink()
            except BaseException:
                pass
        for sub in sorted([p for p in d.rglob("*") if p.is_dir()], reverse=True):
            try:
                sub.rmdir()
            except BaseException:
                pass
        try:
            d.rmdir()
        except BaseException:
            pass
    except BaseException:
        pass


# ---------- 输出体量 / 磁盘预检 ----------

def _volume_class(skin: str) -> str:
    """把皮肤归到「体积档」。分档键是**舞台模板**（决定画面每帧动多少）：
    talking_head 落到内层（source/graph，步点间静止）；ambient 不论 inner 都是
    光晕底（全屏动是主成本）；radio / night / ambient 各自一档；未知按 source。"""
    s = (skin or "").strip().lower()
    if s in ("radio", "night", "ambient", "graph_paper", "graph_clean"):
        return s
    return "source"          # source / graph / talking_head / 未知 → 静止为主的档


def estimate_output_bytes(duration_ms: int, skin: str = "") -> int:
    """按音轨时长预估成片体积（渲染前磁盘预检用；只用于判断，不写库）。

    ⚠️ skin 缺省（旧调用）时按 source 档 —— 比原固定值 40000 更保守（90000），
       不会再低估现有层。
    """
    sec = max(1.0, (duration_ms or 0) / 1000.0)
    rate = EST_BYTES_PER_SEC_BY_CLASS.get(_volume_class(skin),
                                          EST_BYTES_PER_SEC_BY_CLASS["source"])
    return int(sec * rate)


def free_bytes(path: Path | None = None) -> int:
    try:
        return int(shutil.disk_usage(str(path or settings.data_dir)).free)
    except BaseException:
        return -1


def disk_ok(duration_ms: int, skin: str = "") -> tuple:
    """判据 8：剩余空间 ≥ 预估体积 × 2。返回 (ok, free, need)。

    ⚠️ skin 必须传（分档预估）：不传则按 source 档。调用方（gate_check / precheck）
       应传 `v.skin` —— ambient 的预估是 source 的近 3 倍，不分档会放行磁盘不够的渲染。
    """
    need = estimate_output_bytes(duration_ms, skin) * GATE_DISK_MULT
    free = free_bytes()
    if free < 0:
        return True, free, need      # 查不到就不拦（宁可放过也不误拦）
    return free >= need, free, need


# ---------- 音轨解析 ----------

def resolve_audio(db, v) -> dict:
    """解析音轨：返回 {ok, path, bytes, duration_ms, error}。

    ⚠️ 复用模式（audio_source="podcast"）**绝不重新合成**：直接引用播客的音频产物。
    这条不是性能优化，是正确性要求 —— 见模块 docstring 硬约束 1。
    """
    from ..models import Podcast
    if v.audio_source == "podcast":
        p = db.get(Podcast, v.podcast_id) if v.podcast_id else None
        if not p:
            return {"ok": False, "error": "来源播客已不存在，请改用「本片单独生成」音轨"}
        if not (p.audio_name or ""):
            return {"ok": False, "error": "这条播客还没有音频，请先在播客页生成音频"}
        from .podcast import audio_abspath as pod_audio_path
        path = pod_audio_path(p.id, p.audio_name)
        if not path.exists():
            return {"ok": False, "error": "音轨文件已丢失，可去播客页重新生成"}
        return {"ok": True, "path": path, "bytes": int(p.audio_bytes or path.stat().st_size),
                "duration_ms": int(p.duration_sec or 0) * 1000,
                "audio_sig": p.audio_sig or "", "podcast": p}
    # 本片单独生成
    if not (v.audio_name or ""):
        return {"ok": False, "error": "还没有音轨，请先生成本片音频"}
    path = video_abspath(v.id, v.audio_name)
    if not path.exists():
        return {"ok": False, "error": "音轨文件已丢失，请重新生成音频"}
    return {"ok": True, "path": path, "bytes": int(v.audio_bytes or path.stat().st_size),
            "duration_ms": int(v.duration_ms or 0), "audio_sig": v.audio_sig or ""}


def duration_ms_from_file(path) -> int:
    """从音频容器**回读真实时长**（PRD 3.3.6 约束 3：时长取真值不取估算）。

    edge-tts 输出 CBR MP3（48kbps / 24kHz / mono，6 字节每毫秒）→ 时长 = 字节数 ÷ 6，
    精确且不需要解码。非本链路产出的 mp3 走 mutagen 兜底。
    """
    from .tts import BYTES_PER_MS
    p = Path(path)
    if not p.exists():
        return 0
    try:
        return int(round(p.stat().st_size / BYTES_PER_MS))
    except BaseException:
        return 0


# ---------- 讲稿与分镜 ----------

def video_plan(target_minutes: int) -> dict:
    """时长档 → 讲稿字数 / 句数。

    ⚠️ 刻意**不做播客的「单人精讲句数减半」**（`podcast.SOLO_SEGMENT_RATIO`）：
    那个系数是为「省 TTS 调用」设计的，而视频的诉求正相反 —— 句子越少越长，
    一句话里的画面就越单调（PRD 3.5.5 约束 1 的实测：一句 15 秒的话一框到底
    → 最长静止 13.27s）。视频要的是**更密的句子**，不是更少的调用。
    """
    from .podcast import LENGTH_PRESETS
    return dict(LENGTH_PRESETS.get(target_minutes, LENGTH_PRESETS[3]))


def needed_events(duration_ms: int, shot_count: int) -> int:
    """要让质量闸门（判据 1 最长静止 + 判据 3 事件总数）通过，至少需要多少个视觉事件。

    ⚠️ **绝不能按「句数」估算**（实测抓到的缺陷）：句长由 TTS 决定，真实音轨上差异极大
       —— 实测一份 19 句的播客音轨是约 200 秒（平均 10.5 s/句）。
       按「一句一个元素」生成 → 9 个元素、**最长静止 42.3 秒**，
       闸门判据 1 / 2 / 3 三条同时失败，片子无法进入渲染。
       PRD 3.6.6 的原型口径是「一句 15 秒的话里画面能长 3–4 次」→ 密度必须按时长算。
    ⚠️ 取「时长口径」与「闸门口径」的**较严者**：判据 3（≥ 分镜数 × 8）是硬阈值，
       只按时长算会在「短时长 + 多分镜」的组合上被它单独拦下。
    """
    dur = max(0, int(duration_ms or 0))
    by_dur = -(-dur // EVENT_TARGET_GAP_MS) if dur else 0        # 向上取整
    by_gate = max(1, int(shot_count or 0)) * GATE_EVENTS_PER_SHOT
    return max(8, min(MAX_EVENTS, max(by_dur, by_gate)))


def normalize_script(raw: list, prev: list | None = None) -> list:
    """清洗讲稿：过滤空句、归一说话人、裁剪过长文本。

    prev 传「保存前的旧讲稿」时，对 (说话人, 文本) 未变的句子**沿用旧时间戳**
    （与播客 `_normalize_script` 同理）：否则每次保存都会把整份讲稿的 start_ms /
    end_ms 剥光 —— 哪怕只改标题，其余句子的点句跳转也会一起失效。

    时间戳不在音频指纹里（见 `podcast.script_signature`），沿用它们不干扰过期判定。
    """
    prev_map: dict = {}
    for s in prev or []:
        if not isinstance(s, dict):
            continue
        t = str(s.get("text") or "").strip()[:MAX_SEG_CHARS]
        if not t:
            continue
        sp = "expert" if s.get("speaker") == "expert" else "host"
        prev_map.setdefault((sp, t), []).append(s)

    out = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        speaker = "expert" if item.get("speaker") == "expert" else "host"
        seg = {"speaker": speaker, "text": text[:MAX_SEG_CHARS]}
        bucket = prev_map.get((speaker, seg["text"]))
        if bucket:
            old = bucket.pop(0)
            for k in ("start_ms", "end_ms", "voice", "voice_name"):
                if old.get(k) is not None:
                    seg[k] = old[k]
        out.append(seg)
    return out


def script_has_timeline(script: list) -> bool:
    """讲稿是否带真实时间戳（时间轴唯一来源，没有它就无法编排画面）。"""
    return bool(script) and all(s.get("start_ms") is not None and s.get("end_ms") is not None
                                for s in script if (s.get("text") or "").strip())


def script_duration_ms(script: list) -> int:
    ends = [int(s.get("end_ms") or 0) for s in (script or [])]
    return max(ends) if ends else 0


def script_text_sig(script: list) -> str:
    """讲稿文本指纹（**不含时间戳**）：改文案 → 变；只重新合成 → 不变。

    ⚠️ 不含时间戳的原因同 `podcast.script_signature`：合成后会回填 start_ms/end_ms，
    把它们算进去会让「刚渲染完」的成片立刻被判定为「对应上一版文案」。
    """
    parts = []
    for s in script or []:
        t = ((s or {}).get("text") or "").strip()
        if not t:
            continue
        parts.append(f"{(s or {}).get('speaker') or 'host'}|{t}")
    return hashlib.md5("\n".join(parts).encode("utf-8")).hexdigest()[:8]


def build_shots(script: list) -> list:
    """把讲稿按语义切成若干分镜。

    ⚠️ 分镜是**展示单位，不是渲染单位**（PRD 3.4.5 约束 2）：渲染由视觉步点驱动。
    ⚠️ 时间跨度**必须从数据算**，不得手写（本项目踩过：手写「0.0 – 15.6s」
       而实际是该镜头覆盖的第 1–2 句的并集「0.0 – 27.9s」）。
    ⚠️ 切分位置按**句间停顿**取（TTS 的句间留白天然反映了语义段落），
       而不是「每 N 句砍一刀」——后者会把同一段话劈成两半。
    ⚠️ 但纯按「间隔最大的 n-1 处」切会**严重失衡**：所有间隔一样长时（合成音的
       均匀留白就是这种），排序退化成按下标取前几个 → 切出「1 句一镜头 + 15 句一镜头」。
       所以改成：**在每个期望切点附近开一个窗口，取窗口内间隔最大处** ——
       既对齐到语义停顿，又保证各镜头句数均衡。
    """
    items = [s for s in (script or []) if (s.get("text") or "").strip()]
    if not items:
        return []
    total = script_duration_ms(items)
    n = int(round(total / (SHOT_SECONDS * 1000))) if total else len(items) // 3
    n = max(MIN_SHOTS, min(MAX_SHOTS, n or MIN_SHOTS))
    n = min(n, len(items))
    if n <= 1:
        return [{"n": 1, "l": _shot_label(items, 0, len(items)), "a": 0, "b": total}]

    # 句间间隔：gaps[j] = 第 j 句结束 → 第 j+1 句开始（切点 j+1 处）
    gaps = {}
    for i in range(len(items) - 1):
        gaps[i + 1] = int(items[i + 1].get("start_ms") or 0) - int(items[i].get("end_ms") or 0)

    m = len(items)
    ideal = m / n
    win = max(1, int(ideal / 2))
    cuts: list = []
    for k in range(1, n):
        target = max(1, min(m - 1, int(round(k * m / n))))
        lo = max((cuts[-1] + 1) if cuts else 1, target - win)
        hi = min(m - 1, target + win)
        if hi < lo:
            hi = lo
        # 窗口内取间隔最大者；并列时取最接近期望切点的
        best = max(range(lo, hi + 1), key=lambda j: (gaps.get(j, 0), -abs(j - target)))
        if not cuts or best > cuts[-1]:
            cuts.append(best)
    bounds = [0] + cuts + [m]
    shots = []
    for k in range(len(bounds) - 1):
        a, b = bounds[k], bounds[k + 1]
        grp = items[a:b]
        if not grp:
            continue
        shots.append({
            "n": len(shots) + 1,
            "l": _shot_label(items, a, b),
            "a": int(grp[0].get("start_ms") or 0),
            "b": int(grp[-1].get("end_ms") or 0),
        })
    return shots


def _shot_label(items: list, a: int, b: int) -> str:
    """分镜名 = 该镜头首句的开头（去掉标点），便于用户对上内容。"""
    head = re.sub(r"[，。！？；：、\s「」『』\"']+", "", str(items[a].get("text") or ""))
    return (head[:12] or f"第 {a + 1} 段")


# ---------- 视觉步点：定位 ----------

def clause_anchors(text: str, limit: int = 6) -> list:
    """把一句话切成若干「可去原文里找」的候选 anchor（兜底用）。

    用途：LLM 没给 anchor（过渡句）、或给的 anchor 定位不到时，
    退到「用句子自己的词块去原文里找最接近的区域」，并在交付时**如实说明**。
    """
    t = str(text or "").strip()
    if not t:
        return []
    out = []
    for part in re.split(r"[，。！？；：、,.;:!?\s（）()【】\[\]「」『』“”\"']+", t):
        s = part.strip()
        if 4 <= len(s) <= 12:
            out.append(s)
        elif len(s) > 12:
            for i in range(0, len(s) - 12 + 1, 6):
                out.append(s[i:i + 12])
                if len(out) >= limit:
                    break
        if len(out) >= limit:
            break
    seen, uniq = set(), []
    for a in out:
        if a not in seen:
            seen.add(a)
            uniq.append(a)
    return uniq[:limit]


def _spread(start: int, end: int, k: int) -> list:
    """把 k 个步点均分到 [start, end)（第一步严格落在 start）。"""
    if k <= 0:
        return []
    if k == 1:
        return [int(start)]
    span = max(0, int(end) - int(start))
    return [int(start) + round(span * j / k) for j in range(k)]


def build_source_steps(locator, script: list, anchors_by_idx: dict) -> dict:
    """按讲稿逐句定位，产出视觉步点（source 皮肤）。

    返回 {"steps": [...], "unlocated": [句序号...], "tried": n, "hit": m}

    算法（PRD 3.5.9 的硬要求）：
    1. 每句用 LLM 给的 anchor 定位；LLM 没给 → 用句子自己的词块兜底；
    2. 同一句的多个 anchor 均分到该句时间跨度上（**按句内位置排，不是平均砍两刀**）；
    3. 长句导致的超长静止 → `fill_long_gaps()` 逐点补步；
    4. 两条位置不变式 → `dedupe_positions()`。
    """
    steps: list = []
    unlocated: list = []
    tried = hit = 0

    for idx, seg in enumerate(script, 1):
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        start = int(seg.get("start_ms") or 0)
        end = int(seg.get("end_ms") or (start + 1000))
        cands = [a for a in (anchors_by_idx.get(idx) or [])]
        for a in clause_anchors(text):
            if a not in cands:
                cands.append(a)
        cands = cands[:MAX_STEPS_PER_SEG]
        tried += 1

        found = []
        for a in cands:
            hits = locator.locate(a)
            if not hits:
                continue
            best = hits[0]
            found.append({"anchor": a, "hit": best.get("hit") or a, "sc": best.get("sc", 1.0),
                          "page": best["page"], "x": best["x"], "y": best["y"],
                          "w": best["w"], "h": best["h"], "seg": idx,
                          "_alts": hits[1:]})
            if len(found) >= MAX_STEPS_PER_SEG:
                break

        if not found:
            unlocated.append(idx)
            continue
        hit += 1
        times = _spread(start, end, len(found))
        for a, t in zip(found, times):
            steps.append({"at": int(t), "page": a["page"], "anchor": a["anchor"],
                          "hit": a["hit"], "sc": a["sc"], "x": a["x"], "y": a["y"],
                          "w": a["w"], "h": a["h"], "seg": idx, "_alts": a["_alts"]})

    steps.sort(key=lambda s: (s["at"], s["seg"]))
    steps = fill_long_gaps(steps, script, locator)
    steps = dedupe_positions(steps, script, locator)

    # 闸门判据 4 的分母是「有内容的句子数」，不是「被拆出的步点数」
    return {"steps": steps, "unlocated": unlocated, "tried": tried, "hit": hit}


def fill_long_gaps(steps: list, script: list, locator) -> list:
    """按秒表补步：反复切开当前最大的超长静止区间，直到 ≤ 阈值或补不动。

    ⚠️ 这是「最长静止 13.27s → 5.13s」（42 步 → 77 步）那一步的做法：
       按**失败点**逐个补，而不是「每句平均砍两刀」。阈值断言写在闸门里。
    ⚠️ 补不动就停（材料里确实没有更多可指的位置）——**绝不**为了凑数造一个假框。
    """
    used = {s["anchor"] for s in steps}
    for _ in range(MAX_FILL_ROUNDS):
        if len(steps) < 2:
            break
        gaps = [(steps[i + 1]["at"] - steps[i]["at"], i) for i in range(len(steps) - 1)]
        g, i = max(gaps)
        if g <= GATE_MAX_STILL_MS:
            break
        a, b = steps[i], steps[i + 1]
        seg_no = int(a.get("seg") or 1)
        seg = script[seg_no - 1] if 0 < seg_no <= len(script) else None
        added = False
        for cand in clause_anchors((seg or {}).get("text") or ""):
            if cand in used:
                continue
            hits = locator.locate(cand)
            if not hits:
                continue
            best = hits[0]
            mid = a["at"] + (b["at"] - a["at"]) // 2
            steps.append({"at": int(mid), "page": best["page"], "anchor": cand,
                          "hit": best.get("hit") or cand, "sc": best.get("sc", 1.0),
                          "x": best["x"], "y": best["y"], "w": best["w"], "h": best["h"],
                          "seg": seg_no, "_alts": hits[1:]})
            used.add(cand)
            added = True
            break
        if not added:
            break
        steps.sort(key=lambda s: (s["at"], s["seg"]))
    return steps


def _pos(s: dict) -> tuple:
    """位置键：按**百分比取整**比较（亚像素差异不算「不同位置」）。"""
    return (int(s.get("page") or 0), round(float(s.get("x") or 0)),
            round(float(s.get("y") or 0)), round(float(s.get("w") or 0)),
            round(float(s.get("h") or 0)))


def dedupe_positions(steps: list, script: list, locator) -> list:
    """落实两条位置不变式：相邻框不同 + 同页连续段内位置两两不同。

    修法（按优先级）：① 换用同一 anchor 的**备用命中**；② 换用本句另一个词块；
    ③ 都做不到则**丢弃该步**（画面停在上一处，比来回弹更好看）。
    一切修复都记进步骤自身的 `_fixed` 字段，供审计与「如实告知」使用。
    """
    if not steps:
        return steps
    out = []
    run_page = None
    run: set = set()          # 当前页「连续段」内已用过的位置集合
    prev = None
    for s in steps:
        page = int(s.get("page") or 0)
        if page != run_page:
            run_page, run = page, set()
        pos = _pos(s)
        if (prev is not None and pos == _pos(prev)) or pos in run:
            fixed = _relocate(s, run, prev, script, locator)
            if fixed is None:
                continue                      # 丢弃：不保留一个"画面上没变化的步点"
            s = fixed
        page = int(s.get("page") or 0)
        if page != run_page:
            run_page, run = page, set()
        pos = _pos(s)
        if (prev is not None and pos == _pos(prev)) or pos in run:
            continue                          # 换位后仍冲突 → 丢弃，交由闸门报最长静止
        run.add(pos)
        out.append(s)
        prev = s
    return out


def _relocate(s: dict, run: set, prev: dict, script: list, locator):
    """为一个位置重复的步点找替代位置；找不到返回 None。"""
    for alt in (s.get("_alts") or []):
        if _pos(alt) not in run and (prev is None or _pos(alt) != _pos(prev)):
            return {**s, "hit": alt.get("hit") or s["hit"], "sc": alt.get("sc", s.get("sc", 1.0)),
                    "page": alt["page"], "x": alt["x"], "y": alt["y"],
                    "w": alt["w"], "h": alt["h"], "_alts": [],
                    "_fixed": "alt-hit"}
    seg_no = int(s.get("seg") or 1)
    seg = script[seg_no - 1] if 0 < seg_no <= len(script) else None
    for cand in clause_anchors((seg or {}).get("text") or ""):
        if cand == s.get("anchor"):
            continue
        hits = locator.locate(cand)
        for h in hits:
            if _pos(h) not in run and (prev is None or _pos(h) != _pos(prev)):
                return {"at": s["at"], "page": h["page"], "anchor": cand,
                        "hit": h.get("hit") or cand, "sc": h.get("sc", 1.0),
                        "x": h["x"], "y": h["y"], "w": h["w"], "h": h["h"],
                        "seg": seg_no, "_alts": [], "_fixed": "alt-clause"}
    return None


def public_steps(steps: list) -> list:
    """剥掉内部字段（`_alts` / `_fixed`）后落库 —— 库里的结构就是 PRD 3.5.10 的形状。"""
    out = []
    for s in steps or []:
        d = {k: v for k, v in s.items() if not k.startswith("_")}
        out.append(d)
    return out


def shot_seg_ranges(script: list, shots: list) -> list:
    """分镜（毫秒区间）→ 每个镜头覆盖的句号范围 [{n, a, b}]（1 基，闭区间）。

    用途：给 LLM 的分镜表（「镜头 k 覆盖第 a–b 句」），让它按镜头出标题与版式。
    ⚠️ 归属判据与舞台页 `_scene_of_script` 同源：句首 start_ms ≥ 镜头起点即进入该镜头
       （build_shots 的切点落在句间留白里，句子的 start 恰好等于所属镜头的 a）。
    """
    items = [s for s in (script or []) if (s.get("text") or "").strip()]
    if not items or not shots:
        return []
    out = []
    for si, sh in enumerate(shots):
        a_ms = int(sh.get("a") or 0)
        nxt = int(shots[si + 1].get("a") or 0) if si + 1 < len(shots) else None
        lo = hi = None
        for j, s in enumerate(items, 1):
            st = int(s.get("start_ms") or 0)
            if st < a_ms:
                continue
            if nxt is not None and st >= nxt:
                break
            if lo is None:
                lo = j
            hi = j
        if lo is None:
            # 防御：理论不可达（build_shots 保证每镜至少一句），保持覆盖连续不炸链路
            lo = hi = (out[-1]["b"] if out else 1)
        out.append({"n": int(sh.get("n") or si + 1), "a": lo, "b": hi})
    if out and out[-1]["b"] < len(items):
        out[-1]["b"] = len(items)
    return out


def _screens_to_ms(screens: list, shot: dict, script: list) -> list:
    """屏（句号区间）→ 毫秒区间 `[{layout,a,b}]`，并**填补缝隙**。

    ⚠️ 区间必须连续覆盖整个镜头：渲染期按「当前毫秒落在哪一屏」切换画面，
       留缝会让那一刻**两侧都不亮**（画面全空）。所以首屏从镜头起点开始、
       末屏到镜头终点结束、中间用「后一屏的起点」当「前一屏的终点」。
    ⚠️⚠️ **同句多屏必须「均分这一句的时长」，绝不能把前一屏压成 1ms**：
       新版式会把**一句话**的元素按版式上限切成多屏（`_split_screen_units`），
       于是相邻屏的 `s` 就是**同一句**、起点完全相同。旧实现把后一屏起点推后 1ms，
       结果前一屏宽度只剩 1ms —— 它的卡片**永远看不到**，而闸门只看点位密度、
       不会报错（2026-10-09 真机复现：镜头5 屏2 的 4 个知识点被整屏吞掉）。
       正确做法：把这一句的时长**按屏数均分**（每屏 ≥1ms），组间仍首尾相接。
    """
    n = len(script or [])
    if not n:
        return []
    a0 = int(shot.get("a") or 0)
    b0 = int(shot.get("b") or 0) or int(script[-1].get("end_ms") or 0)
    if b0 <= a0:
        b0 = a0 + 1

    def _start(seg) -> int:
        try:
            s = max(1, min(int(seg or 1), n))
        except (TypeError, ValueError):
            s = 1
        return int(script[s - 1].get("start_ms") or 0)

    raw = []
    for s in screens or []:
        if not isinstance(s, dict):
            continue
        lay = str(s.get("layout") or "grid").strip().lower()
        # `s0` = 该屏**首句**的起点（分组用）；`a` 稍后再算
        raw.append({"layout": lay if lay in GRAPH_LAYOUTS else "grid",
                    "s0": _start(s.get("s")), "a": 0})
    if not raw:
        return []
    # 句子起点单调化：后屏起点绝不早于前屏（模型给的句号可能乱序/重叠）
    for j in range(1, len(raw)):
        raw[j]["s0"] = max(raw[j]["s0"], raw[j - 1]["s0"])

    # ⚠️ 把「句子起点相同的连续屏」（= 同一句话被切成多屏）合成一组，
    #    组内**均分**这一句可用的时长；组与组之间仍首尾相接（区间保持连续）。
    fixed: list = []
    j = 0
    while j < len(raw):
        k = j
        while k + 1 < len(raw) and raw[k + 1]["s0"] == raw[j]["s0"]:
            k += 1
        lo = raw[j]["s0"]
        hi = raw[k + 1]["s0"] if k + 1 < len(raw) else b0
        if fixed:
            lo = max(lo, fixed[-1]["b"])      # 不回头（与前一组首尾相接）
        m = k - j + 1
        if hi < lo + m:                       # 可分配时长不足每屏 1ms → 往后借
            hi = lo + m
        base, rem = divmod(hi - lo, m)
        cur = lo
        for idx in range(m):
            w = base + (1 if idx < rem else 0)
            fixed.append({"layout": raw[j + idx]["layout"], "a": cur, "b": cur + w})
            cur += w
        j = k + 1
    fixed[0]["a"] = min(a0, fixed[0]["a"])    # 首屏从镜头起点开始
    fixed[0]["b"] = max(fixed[0]["b"], fixed[0]["a"] + 1)
    fixed[-1]["b"] = max(fixed[-1]["b"], b0)  # 末屏到镜头终点结束
    return fixed


def apply_scene_meta(shots: list, scenes: list, script: list | None = None) -> list:
    """把 LLM 给的镜头标题/屏（版式+时间区间）回写到 shots；没给到的保持原标题 + grid。

    ⚠️ 只补不改坏：scenes 为空（旧格式输出 / 用户自定义提示词）时 shots 原样返回，
       存量作品与旧提示词的行为与改造前**完全一致**（旧数据没有 `screens` 键，
       渲染层按「整镜一屏」处理）。
    ⚠️ 镜头标题由此成为 LLM 命名（完整短语），不再用 `_shot_label` 按字数硬截
       —— 那是「现在做AIAgent的团」这种断句硬伤的根因。
    ⚠️ `screens` 里的时间是**毫秒**（与 shots 的 a/b 同单位）；LLM 只给句号，
       换算必须在这里做 —— 与「seg → 绝对毫秒」同一条纪律。
    """
    by_shot = {}
    for sc in scenes or []:
        try:
            by_shot[int(sc.get("shot") or 0)] = sc
        except (TypeError, ValueError):
            continue
    if not by_shot:
        return [dict(sh, layout=str(sh.get("layout") or "grid")) for sh in (shots or [])]
    out = []
    for i, sh in enumerate(shots or [], 1):
        d = dict(sh)
        sc = by_shot.get(int(d.get("n") or i))
        if sc:
            t = str(sc.get("title") or "").strip()
            if t:
                d["l"] = t[:16]
            screens = _screens_to_ms(sc.get("screens") or [], d, script or [])
            if screens:
                d["screens"] = screens
                # 兼容仍只读 layout 的旧读者（右侧预览、探针）：取首屏版式
                d["layout"] = screens[0]["layout"]
            else:
                d.setdefault("layout", "grid")
        else:
            d.setdefault("layout", "grid")
        out.append(d)
    return out


# ---------- 视觉步点：结构图（graph 皮肤） ----------

def build_graph_points(script: list, elements: list) -> list:
    """结构图元素 → 要点栏点位 `[{t,k,at,group,seg}]`。

    ⚠️ 元素只由模型给出「第几句」（seg），**绝对毫秒在这里算** ——
       让模型写毫秒是必错的（它不知道 TTS 之后的真实时间轴）。
    ⚠️ **同一句的多个元素必须在句内铺开**（`_spread`），不能都挂在句首：
       一句 12 秒的话如果 k 个元素全在同一毫秒点亮，画面变化次数就只有 1 次
       → 最长静止 12 秒，等于把「画面单调」原样搬到结构图皮肤上
       （这正是 PRD 3.6.6 #1「视觉事件从按句改成按毫秒」要解决的问题）。
    ⚠️ 同一 group（镜头）的元素仍然一起出现在画面上；但 `at` 可以不同 ——
       镜头是「这一屏画什么」，`at` 是「哪一刻长出来」，两者正交。
    """
    n = len(script)
    by_seg: dict = {}
    for e in elements or []:
        try:
            seg = int(e.get("seg") or 1)
        except (TypeError, ValueError):
            seg = 1
        seg = max(1, min(seg, max(1, n)))
        t = str(e.get("t") or "").strip()
        if not t:
            continue
        k = str(e.get("k") or "concept")
        if k not in ("concept", "good", "cost"):
            k = "concept"
        by_seg.setdefault(seg, []).append({"t": t[:24], "k": k,
                                           "group": int(e.get("group") or 1),
                                           "sc": max(0, int(e.get("sc") or 0))})
    # ⚠️⚠️ 必须遍历**全部**句号，不能只遍历 `by_seg`（只有出过元素的句子）：
    #     实测抓到「整句一个元素都没有」——模型对第 3/5/7/9/13/15/17 句一条都没给，
    #     于是该句**整段没有任何点亮**，前后两个事件的间隔直接跨过整句：
    #     第 8 句 +10.4s → 第 10 句 +0.0s = **8405 ms**，判据 1 被拦（其余 7 条全过）。
    #     这与「句内点太少」是两个独立缺陷：补点只对**有**元素的句子生效，
    #     零元素的句子连进 by_seg 都进不去 —— 必须在这里单独补。
    def _nearest_group(i: int) -> int:
        for j in list(range(i - 1, 0, -1)) + list(range(i + 1, n + 1)):
            g = by_seg.get(j)
            if g:
                return int(g[0].get("group") or 1)
        return 1

    def _nearest_sc(i: int) -> int:
        """整句无元素时补出来的点属于哪一屏：沿用**最近的**有元素那句的屏号。

        ⚠️ 不能写死 0：那会让补点全部落到本镜第一屏，而第一屏这时早已被切走
           → 补点在画面上永远不出现，等于「补了点、但最长静止照旧」。
        """
        for j in list(range(i - 1, 0, -1)) + list(range(i + 1, n + 1)):
            g = by_seg.get(j)
            if g:
                return int(g[0].get("sc") or 0)
        return 0

    pts = []
    for seg in range(1, n + 1):
        row = script[seg - 1] if n else {}
        start = int(row.get("start_ms") or 0)
        end = int(row.get("end_ms") or (start + 1000))
        span = max(0, end - start)
        group = by_seg.get(seg) or []
        if not group:
            # 整句无元素 → 用该句讲稿自己的词块补足（不编造；没有词块才回退原文截断）
            _txt = str(row.get("text") or "").strip()
            _need = max(1, -(-span // GATE_MAX_STILL_MS)) if span else 1
            _chunks = [str(c).strip()[:24]
                       for c in clause_anchors(_txt) if str(c).strip()]
            if not _chunks and _txt:
                _chunks = [_txt[:24]]
            _grp = _nearest_group(seg)
            _sc = _nearest_sc(seg)
            group = [{"t": _chunks[i % len(_chunks)] if _chunks else f"第{seg}句",
                      "k": "concept", "group": _grp, "sc": _sc} for i in range(_need)]
        k = len(group)
        # ⚠️⚠️ 句内**最少**点数：span / GATE_MAX_STILL_MS（向上取整）。
        #     实测抓到：模型对一句 22 秒的长句只给 1 个元素 → `_spread` 只在句首落一个点，
        #     该句后半段**没有任何点亮** → 最长静止 10968 ms（33 分钟片，判据 1 直接被拦）。
        #     注意这与「事件总数够不够」是**两个独立问题**：avg_gap 与 events 都过了，
        #     单独被最长静止拦住 —— 只有句内补点才治得了。
        # ⚠️ 补点内容取自**该句讲稿自己的词块**（与 source 皮肤的 `fill_long_gaps` 同源，
        #    不是编造）；该句确实没有更多词块时才复用已有元素（视觉上同一短语多亮一次，
        #    比「半句画面不动」好得多）。
        # ⚠️ 间隔预算取 `GATE_MAX_STILL_MS - 500`：句间还有停顿（实测本机统一 288 ms），
        #    句尾最后一个点到下一句第一个点的间隔 = 尾部空 + 停顿，只按 6000 算会**恰好超限**
        #    （实测 6288 ms 就是这么来的）。减 500 留出停顿与取整误差。
        need_k = max(k, -(-span // (GATE_MAX_STILL_MS - 500))) if span else k
        if need_k > k:
            extras = [str(c).strip()[:24] for c in clause_anchors(str(row.get("text") or ""))
                      if str(c).strip()]
            fill, i = [], 0
            while len(fill) < need_k - k:
                src = extras[i % len(extras)] if extras else group[i % k]["t"]
                e = group[i % k]
                fill.append({"t": src or e["t"], "k": e["k"], "group": e["group"],
                             "sc": int(e.get("sc") or 0)})
                i += 1
            group = list(group) + fill
            k = len(group)
        for e, at in zip(group, _spread(start, end, k)):
            pts.append({"t": e["t"], "k": e["k"], "group": e["group"],
                        "sc": int(e.get("sc") or 0), "seg": seg, "at": int(at)})
    pts.sort(key=lambda p: (p["at"], p["group"]))
    return pts


def graph_steps(points: list) -> list:
    """结构图皮肤也产出 `steps`（供统一的密度计算与分镜导航使用）。

    ⚠️ 结构图的步点**不带 (页, 矩形)** —— 它们点亮的是 SVG 元素（`data-at`），
       所以这里只保留 `at` 与元素下标，渲染层按同一毫秒时间轴逐个点亮。
    """
    return [{"at": int(p["at"]), "group": int(p["group"]), "t": p["t"], "k": p["k"]}
            for p in points or []]


# ---------- 密度与质量闸门 ----------

def density(steps: list, duration_ms: int) -> dict:
    """画面密度指标：事件数 / 平均间隔 / **最长静止**。

    ⚠️ 最长静止是「画面单调」最有诊断力的**单一**指标（PRD 1.2 / 3.9.5 判据 1）：
       事件总数多但分布不均时，它仍能抓住「中间 13 秒画面没动」。
    ⚠️ 留白（句间停顿）里画面**停在前一句**（PRD 3.3.6 / 附录 A #2）：
       所以时间轴的终点取 `duration_ms`（音轨真实时长），而不是最后一步的时刻 ——
       否则「录到末尾还有 2.5 秒画面不动」会被漏掉。
    """
    ats = sorted({int(s.get("at") or 0) for s in (steps or [])})
    end = max(int(duration_ms or 0), ats[-1] if ats else 0)
    marks = sorted(set([0] + ats + ([end] if end > (ats[-1] if ats else 0) else [])))
    gaps = [marks[i + 1] - marks[i] for i in range(len(marks) - 1)]
    return {
        # ⚠️ 事件数按**不同时刻**计数：同一毫秒点亮 3 个元素，画面上只发生 1 次变化
        #    （按条数计会让「一屏同时长出来」看起来像 3 次事件，指标就此失真）。
        "events": len(ats),
        "avg_gap_ms": int(round(sum(gaps) / len(gaps))) if gaps else 0,
        "max_still_ms": max(gaps) if gaps else 0,
        "first_at": ats[0] if ats else 0,
    }


def hit_rate(stats: dict) -> float:
    """定位命中率（判据 4）：命中句数 / 有内容的句子数。"""
    tried = int(stats.get("tried") or 0)
    return round(int(stats.get("hit") or 0) / tried, 4) if tried else 0.0


def gate_check(v, steps: list, shots: list, hits: dict, tts_conf: dict | None = None) -> dict:
    """质量闸门：进入渲染队列**之前**的量化自检（PRD 3.9.5 的 8 条判据）。

    闸门不通过就不允许渲染 —— 渲染成本与视频时长同量级，绝不能用渲染来试错。

    返回 gate_report（结构见 PRD 3.9.8）+ `items`（逐条明细，供 UI 列出失败项）。
    """
    from .podcast import audio_is_stale as pod_audio_stale

    dur = int(v.duration_ms or 0)
    d = density(steps, dur)
    event_min = max(1, len(shots)) * GATE_EVENTS_PER_SHOT
    rate = hit_rate(hits)
    ok_disk, free, need = disk_ok(dur, getattr(v, "skin", ""))

    # 判据 6：句间留白归属 —— 时间轴的终点若超出最后一步，那段「画面停住」是**正确**行为
    # （停在前一句），不是缺陷。这里不改数据，只如实记录被覆盖的留白处数。
    gaps_fixed = _count_pause_spans(v.script or [])

    # 判据 7：音频与讲稿指纹一致（或已标注）
    if v.audio_source == "podcast":
        audio_stale = bool(pod_audio_stale(v_podcast_src(v))) if v_podcast_src(v) else False
    else:
        # ⚠️ BGM 必须纳入比对：finalize_video 落库的指纹含 bgm 段，这里不算进去
        #    会把「刚混好 BGM 的新音轨」误判成指纹不一致 → 永远过不了闸门。
        from .podcast import script_signature as _sig
        conf = tts_conf or {}
        audio_stale = bool(v.audio_sig) and v.audio_sig != _sig(
            v.script or [], {},
            str(getattr(v, "bgm_id", "") or ""), getattr(v, "bgm_volume", None),
            rate=conf.get("rate"), gap_ms=conf.get("gap_ms"))

    # 判据 5 只对**带矩形**的步点成立：
    # 结构图 / 口播皮肤的 `graph_steps()` 只产出 `{at, group, t, k}` —— 点亮的是 SVG 元素
    # （`data-at`），根本没有 (页, 矩形) 可言。无条件跑 `_positions_ok()` 会把缺字段的步点
    # 全退化成 (0,0,0,0,0) → 相邻两步「同页同位置」→ **结构图皮肤永远无法通过闸门**
    # （实测捕获：58 个步点、最长静止 4.79s、其余判据全过，却卡在 positions 上）。
    # ⚠️ 用**数据**判断而不是皮肤名：talking_head 的内层可能是 source（有矩形，要查）
    #    或 graph（无矩形，不适用）—— 靠数据只要一个条件。
    has_rects = any(isinstance(s, dict) and "page" in s and "x" in s
                    for s in (steps or []))
    positions_ok = _positions_ok(steps) if has_rects else True

    # 解压型皮肤（radio / night / ambient）：密度类判据（最长静止 / 平均间隔 / 事件总数）
    # 不适用 —— 它们本来就没有「点亮事件」，「画面在动」由舞台页 `ms→状态` 纯函数保证。
    # ⚠️ 绝不能让解压皮肤去凑事件密度（方向自相矛盾），只能按皮肤分档豁免（解压方案 §8.2）。
    skin = str(getattr(v, "skin", "") or "").strip().lower()
    relax = skin in ("radio", "night", "ambient")

    # 判据 9（解压皮肤专属）：插画资产可用。radio 的线条插画是代码内置恒可用；
    # night 依赖 assets/night/ 的插画 PNG，缺失则渲染不出 → **渲染前可判**。
    art_ok = True
    if skin == "night":
        from . import artworks as _art
        art_ok = bool(_art.night_backgrounds_available())

    items = [
        {"no": 1, "key": "max_still", "ok": True if relax else d["max_still_ms"] <= GATE_MAX_STILL_MS,
         "value": d["max_still_ms"], "threshold": GATE_MAX_STILL_MS, "unit": "ms",
         "label": "最长静止", "fix": "fill_steps",
         "na": relax},
        {"no": 2, "key": "avg_gap", "ok": True if relax else d["avg_gap_ms"] <= GATE_AVG_GAP_MS,
         "value": d["avg_gap_ms"], "threshold": GATE_AVG_GAP_MS, "unit": "ms",
         "label": "平均间隔", "fix": "",
         "na": relax},
        {"no": 3, "key": "events", "ok": True if relax else d["events"] >= event_min,
         "value": d["events"], "threshold": event_min, "unit": "",
         "label": "视觉事件总数", "fix": "fill_steps",
         "na": relax},
        {"no": 4, "key": "hit_rate", "ok": (v.skin != "source" and v.inner_skin != "source")
         or rate >= GATE_HIT_RATE,
         "value": rate, "threshold": GATE_HIT_RATE, "unit": "",
         "label": "定位命中率", "fix": "manual_locate"},
        {"no": 5, "key": "positions", "ok": positions_ok,
         "value": 0 if positions_ok else 1, "threshold": 0, "unit": "",
         "label": "高亮框位置互不相同", "fix": "fill_steps",
         # na=True：当前皮肤没有「高亮框」这个概念（结构图/口播）→ UI 显示「不适用」
         "na": not has_rects},
        {"no": 6, "key": "pause_owner", "ok": True,
         "value": gaps_fixed, "threshold": 0, "unit": "",
         "label": "句间留白归属正确", "fix": ""},
        {"no": 7, "key": "audio_sig", "ok": not audio_stale,
         "value": 1 if audio_stale else 0, "threshold": 0, "unit": "",
         "label": "音频与讲稿指纹一致", "fix": "resynth_audio"},
        {"no": 8, "key": "disk", "ok": ok_disk,
         "value": free, "threshold": need, "unit": "B",
         "label": "磁盘空间（≥ 预估体积 ×2）", "fix": "free_space"},
        {"no": 9, "key": "art_assets", "ok": art_ok,
         "value": 0 if art_ok else 1, "threshold": 0, "unit": "",
         "label": "插画资产可用", "fix": "",
         "na": not relax},
    ]
    passed = all(it["ok"] for it in items)
    return {
        "max_still_ms": d["max_still_ms"], "avg_gap_ms": d["avg_gap_ms"],
        "events": d["events"], "locate_hit_rate": rate, "gaps_fixed": gaps_fixed,
        "passed": passed, "checked_at": datetime.utcnow().isoformat(timespec="seconds"),
        "items": items, "unlocated": list(hits.get("unlocated") or []),
    }


def _positions_ok(steps: list) -> bool:
    """复算两条不变式（**在数据上算，不看修复过程**）。

    ⚠️ 不要写成「每页步数 == 每页位置数」：同一张卡在讲稿里可能出现两次，
       后半段复用前半段的框是正常的。正确写法：按页号切**连续段**，段内位置两两不同。
       「连续段」= 页号不变的最长连续区间 —— 页号一变就**重置**已用位置集合。
    """
    run_page = None
    run: set = set()
    prev = None
    prev_pos = None
    for s in steps or []:
        page = int(s.get("page") or 0)
        if page != run_page:
            run_page, run = page, set()
        pos = _pos(s)
        if prev is not None and page == prev and pos == prev_pos:
            return False                       # 相邻两步同页同位置 → 画面连着两步不动
        if pos in run:
            return False
        run.add(pos)
        prev, prev_pos = page, pos
    return True


def _count_pause_spans(script: list) -> int:
    """统计句间「有留白」的处数（用于如实展示「留白归前一句」的覆盖范围）。

    ⚠️ 这是**统计**，不是修复：留白归属由渲染层的 `segAt(ms)` 保证
       （落在留白里必须停在**前一句**，绝不跳到最后一句 —— 那是原型阶段实测到的真实缺陷）。
    """
    items = [s for s in (script or []) if (s.get("text") or "").strip()]
    n = 0
    for i in range(len(items) - 1):
        if int(items[i + 1].get("start_ms") or 0) > int(items[i].get("end_ms") or 0):
            n += 1
    return n


def v_podcast_src(v):
    """复用模式下取来源播客对象（拿不到返回 None）。"""
    try:
        from ..database import SessionLocal
        from ..models import Podcast
        if v.audio_source != "podcast" or not v.podcast_id:
            return None
        db = SessionLocal()
        try:
            return db.get(Podcast, v.podcast_id)
        finally:
            db.close()
    except BaseException:
        return None


# ---------- 派生指纹与「上一版」判定（PRD 3.11.4） ----------

def visual_sig(v) -> str:
    """视觉参数指纹：换皮肤 / 换内层 / 换形象 / 换形象位置 / 换背景 / 换输出规格都会变。

    ⚠️⚠️ `bg_id` 只在**非空时**才拼进 key —— 不能无条件占一个位置。
       无条件拼接会让**全部历史作品**（bg_id 都是空串）的视觉指纹同时变化 →
       `stale_kinds` 立刻对每一条已完成的成片报「视觉方案已更换」，
       而它们其实一帧都没变（本项目已记过这条教训：改指纹口径 = 集体误报过期）。
       条件拼接的语义也更准：`bg_id` 从 A 改成 B 会变、从「有」改回「没有」也变，
       而「从来没有背景」前后恒等。
    """
    parts = [str(v.skin or ""), str(v.inner_skin or ""), str(v.avatar_id or ""),
             str(v.avatar_pos or "")]
    bg = str(getattr(v, "bg_id", "") or "")
    if bg:
        parts.append("bg:" + bg)
    # 皮肤变体（radio/night 预设套）：与 bg_id 同款纪律 —— 只在非空时拼接，
    # 存量作品（空串）指纹与升级前逐字节一致，不集体误报「视觉方案已更换」。
    sv = str(getattr(v, "skin_variant", "") or "")
    if sv:
        parts.append("sv:" + sv)
    # 画面署名：**只在真正把它渲上屏的皮肤下**拼。
    # ⚠️ 非电台皮肤不拼 —— 它们画面里没有这个位置，拼进去只会让全部历史作品的
    #    指纹无谓变化、集体误报「视觉方案已更换」（与上面 bg_id / skin_variant 同款纪律）。
    # ⚠️⚠️ 电台皮肤的**存量**作品升级后指纹一定会变：画面那行字确实从内置装饰字
    #    （且 听 / 夜 曲）换成了作品标题 —— 那是真实变化，如实报过期正是我们要的。
    if str(v.skin or "") in BRAND_SKINS:
        parts.append("brand:" + brand_text_for(v))
    parts += [str(v.width or OUT_W), str(v.height or OUT_H), str(v.fps or OUT_FPS)]
    return hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()[:8]


def signature(v) -> str:
    """成片派生指纹 = 讲稿指纹 + 音轨指纹 + 视觉参数指纹（三段拼一行）。

    ⚠️ 为什么拼三段而不是一个大哈希：PRD 3.11.4 要求**区分**三种不一致
       （讲稿改了 / 音轨改了 / 视觉参数改了），单哈希无法反查是哪一类变了。
       三段共 8+1+32+1+8 = 50 字符，仍在 `String(64)` 内。
    ⚠️ 判定同步状态一律用**指纹**，不用「最后修改时间」—— 时间戳无法表达
       「哪个参数变了」，也无法表达「重新合成但内容没变」。
    """
    return f"{script_text_sig(v.script or [])}-{(v.audio_sig or '').ljust(32, '0')[:32]}-{visual_sig(v)}"


STALE_LABEL = {
    "script": "讲稿已修改，音轨仍是上一版",
    "audio": "音轨已更换，成片对应旧音轨",
    "audio_source": "来源播客的音频已重新合成，成片对应旧音轨",
    "visual": "视觉方案已更换，成片对应旧方案",
}


def stale_kinds(db, v) -> list:
    """成片相对**当前配置**不一致的类型列表（空列表 = 一致）。

    ⚠️ **不以「删除」作为一致性手段**：不一致时保留旧成片 + 标注 + 提供一键重建
       （PRD 3.11.4 第 2 条，本项目强制纪律）。
    """
    if (v.status or "") != "finished" or not (v.signature or "").strip():
        return []
    parts = (v.signature or "").split("-")
    if len(parts) != 3:
        return []
    s, mid, vis = parts
    out = []
    _script_changed = s != script_text_sig(v.script or [])
    if _script_changed:
        out.append("script")
    if mid.strip("0") != (v.audio_sig or "").strip("0"):
        out.append("audio")
    # 换音色 / 换 BGM / 调音量 / 改语速停顿但尚未重新合成：成片仍是旧配置的产物 → 标 audio 过期。
    # ⚠️ 音色必须走 `_voice_changed` 单独比（script_signature 是逐句 voice 优先，
    #    改了作品音色但未重合成时签名不变 —— 那条优先级是存量指纹的保命口径，绝不动）；
    #    BGM 与语速/停顿则直接体现在指纹里，与 `_current_audio_sig` 重算比对即可。
    # ⚠️⚠️ `not _script_changed` 这层守卫**不是优化，是 PRD 3.11.4 第 3 条的硬要求**：
    #    「区分两种不一致（**不能混为一谈**）」——「讲稿改了，音轨没变」只报 `script`。
    #    `_current_audio_sig()` 内部就是 `script_signature(v.script, ...)`，所以**讲稿一变它必然
    #    跟着变**；少了这层守卫，改一次文案会同时报出 `['script', 'audio']`，
    #    界面就会多打一行**文案是错的**提示（「音轨已更换」其实只是讲稿改了）。
    #    2026-10-12 全量门禁在 `_probe_avideo_stage1_20261008.py` 上抓到，见该探针同名注释。
    elif not _script_changed and v.audio_source != "podcast" and bool(v.audio_sig) and (
            _voice_changed(v, v.voice_map or {})
            or v.audio_sig != _current_audio_sig(v)):
        out.append("audio")
    # 复用播客时，播客重新合成不会改 videos.audio_sig → 必须单独比一次
    if v.audio_source == "podcast":
        p = v_podcast_src(v)
        if p is not None and (p.audio_sig or "") and (p.audio_sig or "") != (v.audio_sig or ""):
            out.append("audio_source")
    if vis != visual_sig(v):
        out.append("visual")
    return out


# ---------- 音轨/讲稿一致性（生成台展示用） ----------

def audio_stale(v, tts_conf: dict | None = None, voice_map: dict | None = None) -> bool:
    """当前音轨是否与「本片讲稿」不一致（新建模式）/ 与来源播客自己的脚本不一致（复用模式）。

    ⚠️ 复用模式判定的是**播客自己**的 audio_sig vs script_signature ——
       这正是绝不能复用 `podcasts._finalize_audio` 的原因（见模块 docstring）。

    ⚠️ 音色单独比一次（`voice_map` 传**作品级原始选择**，空/缺省表示没选过）：
       `script_signature` 是「逐句 voice 优先」，所以改了作品音色但还没重新合成时，
       签名**不会变** —— 那条优先级是为了让存量指纹不集体误报，绝不能动。
       于是这里独立比：script 里回填的是**上次合成时**的音色，与当前所选不同即过期。
    """
    from .podcast import script_signature as _sig, audio_is_stale as pod_stale
    if v.audio_source == "podcast":
        p = v_podcast_src(v)
        return bool(p) and bool(pod_stale(p))
    if not (v.audio_name or ""):
        return False
    conf = tts_conf or {}
    cur = _sig(v.script or [], {},
               str(getattr(v, "bgm_id", "") or ""), getattr(v, "bgm_volume", None),
               rate=conf.get("rate"), gap_ms=conf.get("gap_ms"))
    if bool(v.audio_sig) and v.audio_sig != cur:
        return True
    return _voice_changed(v, voice_map)


def _voice_changed(v, voice_map: dict | None) -> bool:
    """本片音轨的实际音色是否已与「当前所选音色」分叉（只对 self 音轨有意义）。

    没选过作品音色（空）→ 一律 False：与播客侧行为一致，避免设置页改音色后
    全部老作品集体冒出「音轨过期」。
    """
    own = str(((voice_map or {}) or {}).get("host") or "").strip()
    if not own:
        return False
    from . import tts as _tts
    want = _tts.resolve_voice(own, "host")
    got = ""
    for s in (v.script or []):
        if (s or {}).get("voice"):
            got = str(s.get("voice") or "").strip()
            break
    return bool(got) and got != want


# ---------- 落库（视频自实现；绝不复用 podcasts 的 _finalize_audio） ----------

def _current_audio_sig(v) -> str:
    """按**当前**设置与作品级配置重算的音轨指纹（finalize_video / stale_kinds 共用，同口径）。

    ⚠️ BGM 纳入指纹：换 BGM / 调音量后旧音轨的听感就不对了（与播客同口径）；
       `bgm_id` 为空时 script_signature 不追加任何字符 → 与升级前算法逐字节一致，
       历史作品不会集体误报过期。
    ⚠️ 语速 / 句间停顿一律取**设置页当前值**（与播客链路同口径）：若用模块默认值
       （+0% / 280ms）而用户改过语速，成片一生成就会被判「音轨过期」（假过期）。
    """
    from .podcast import script_signature
    from . import settings_store
    conf = settings_store.load()
    _rate = (conf.get("tts_rate") or "").strip() or None
    _gap = int(conf.get("tts_gap_ms") or 0) or None
    return script_signature(v.script or [], {},
                            str(getattr(v, "bgm_id", "") or ""),
                            getattr(v, "bgm_volume", None),
                            rate=_rate, gap_ms=_gap,
                            provider=(conf.get("tts_provider") or None))


def finalize_video(db, v, *, audio: bytes = None, audio_name: str = None,
                   script: list = None, duration_ms: int = 0) -> None:
    """音轨结果落库：只写 **Video** 自己的字段（见 PRD 4.4）。

    ⚠️ 与 `podcasts._finalize_audio` **不共享任何写库代码**，只共享纯函数与
       `tts.synthesize`。原因见模块 docstring 的「复用边界」。
    """
    if audio is not None and audio_name is not None:
        v.audio_name = audio_name
        v.audio_bytes = len(audio)
    if script is not None:
        v.script = script
    if duration_ms:
        v.duration_ms = int(duration_ms)
    elif v.audio_name:
        v.duration_ms = duration_ms_from_file(video_abspath(v.id, v.audio_name))
    # 音轨指纹：语速 / 句间停顿 / BGM 一律取**当前配置**（口径集中在 `_current_audio_sig`，
    # 与 stale_kinds / 闸门判据 7 共用同一函数，三处绝不许各算各的）。
    # ⚠️ 只写 Video 自己的列 —— 绝不碰 podcasts 表（见模块 docstring 复用边界）。
    v.audio_sig = _current_audio_sig(v)
    db.commit()
    db.refresh(v)


def finalize_render(db, v, *, video_name: str, poster_name: str, video_bytes: int,
                    duration_ms: int, width: int, height: int, fps: int,
                    frame_count: int, render_seconds: float = 0,
                    sig: str | None = None) -> None:
    """成片落库：写渲染产物字段 + 派生指纹 + 状态置 finished。

    ⚠️ `sig` 必须是**渲染开工那一刻**的指纹快照（`signature(v)`），不是完工时现算。
       渲染耗时与视频时长同量级（分钟级），期间用户完全可能改讲稿/换音轨；现算会把
       「按旧文案渲染出来的片子」记成新指纹 → 界面显示「已是最新」，实际像素是上一版。
       这是**静默的错误状态**，比报错危险得多。不传才退回现算（仅供离线探针用）。
    """
    v.video_name = video_name
    v.poster_name = poster_name
    v.video_bytes = int(video_bytes)
    v.duration_ms = int(duration_ms)
    v.width, v.height, v.fps = int(width), int(height), int(fps)
    v.frame_count = int(frame_count)
    v.render_seconds = float(render_seconds or 0.0)
    v.signature = sig or signature(v)
    v.status = "finished"
    v.progress = 1.0
    v.error = ""
    v.rendered_at = datetime.utcnow()
    db.commit()
    db.refresh(v)


# ---------- 启动期孤儿回收（PRD 2.2.6 ①） ----------

def recover_orphans(db, now: datetime | None = None) -> int:
    """把超时未更新的「运行中」作品置为 failed，返回处理条数。

    ⚠️ 不做这一回收就会出现「永远渲染中且点不开」的僵尸卡片，
       并且永久占住并发槽（updated_at 不再刷新 → 槽位只进不出）。
    ⚠️ 窗口必须 ≥ 单条视频渲染超时上限（PRD 4.3：视频时长 × 3 + 5 分钟），
       否则**正在渲染**的作品会在重启时被误杀。
    """
    from ..models import Video
    now = now or datetime.utcnow()
    win = int(getattr(settings, "video_orphan_minutes", 30) or 30)
    cutoff = now - timedelta(minutes=win)
    rows = db.query(Video).filter(Video.status.in_(list(RUNNING_STATUSES))).all()
    n = 0
    for v in rows:
        if v.updated_at and v.updated_at >= cutoff:
            continue
        v.status = "failed"
        v.error = "服务重启中断"
        v.progress = 0.0
        n += 1
    if n:
        db.commit()
    return n


def running_count(db) -> int:
    """渲染并发闸按 **DB** 统计，不用进程内计数（PRD 2.2.6 ②）。

    回收窗口取孤儿窗口的两倍：太短会把**正在渲染**的作品算漏（槽位凭空多出），
    太长会把已中断的作品算进去（永远 429）。
    """
    from ..models import Video
    try:
        win = int(getattr(settings, "video_orphan_minutes", 30) or 30) * 2
    except BaseException:
        win = 60
    cutoff = datetime.utcnow() - timedelta(minutes=win)
    return (db.query(Video)
            .filter(Video.status.in_(list(RUNNING_STATUSES)))
            .filter(Video.updated_at > cutoff)
            .count())


# ---------- 转笔记 / 导出用的文本 ----------

def to_note_payload(v) -> dict:
    """讲稿 + 分镜 + 视觉步点概览 → 一篇笔记（PRD 3.10.5「转笔记」）。"""
    from .podcast import script_to_text
    lines = [f"# {v.title}", ""]
    dur = int(v.duration_ms or 0) / 1000.0
    lines.append(f"> 视觉方案：{skin_label(v)} · 时长 {dur:.1f} 秒 · "
                 f"{len(v.shots or [])} 个分镜 · {len(v.steps or [])} 个视觉步点")
    lines.append("")
    lines.append("## 讲稿")
    lines.append("")
    lines.append(script_to_text(v.script or []))
    if v.shots:
        lines.append("")
        lines.append("## 分镜")
        lines.append("")
        for s in v.shots:
            lines.append(f"- **{s.get('l') or ''}** {_fmt_ms(s.get('a'))} – {_fmt_ms(s.get('b'))}")
    if v.points:
        lines.append("")
        lines.append("## 结构要点")
        lines.append("")
        for p in v.points:
            lines.append(f"- [{p.get('k')}] {p.get('t')}")
    return {"title": f"{v.title}（视频文稿）", "content": "\n".join(lines)}


def _fmt_ms(ms) -> str:
    """毫秒 → `m:ss`（分镜导航与笔记里用的粗粒度时刻）。"""
    t = max(0, int(ms or 0))
    m, t = divmod(t, 60000)
    s, _ = divmod(t, 1000)
    return f"{m}:{s:02d}"


def resolve_inner(v) -> str:
    """叠加层皮肤的**内容层**（叠加层之下真正做画面的那个）；非叠加层皮肤返回空串。

    ⚠️⚠️ 「叠加层选了、内容层没选」是**能自愈**的输入，绝不能报错：
       它曾让作品进入**死状态** —— 生成路径抛「未知的视觉方案：talking_head」
       （消息还把 `v.skin` 当未知值打出来，而 talking_head 明明是已知皮肤），
       于是这份作品**永远生成不出来**；而渲染路径一直按 graph 兜底 → 两条路径自相矛盾。
    ⚠️ 本函数是**唯一出口**：`stage._inner_of` 与 `routers/videos` 都必须走它，
       否则口径会再次漂移（本次事故的根因就是两处各写一份）。
    ⚠️ 按皮肤分派白名单：talking_head → `INNER_SKINS`（**无 none**）；ambient →
       `AMBIENT_INNERS`（**含 none**）。两个白名单**绝不混用**（none 进口播 = 死状态，见上）。
    """
    skin = str(getattr(v, "skin", "") or "").strip().lower()
    if skin == "talking_head":
        inner = str(getattr(v, "inner_skin", "") or "").strip().lower()
        return inner if inner in INNER_SKINS else INNER_DEFAULT
    if skin == "ambient":
        inner = str(getattr(v, "inner_skin", "") or "").strip().lower()
        return inner if inner in AMBIENT_INNERS else AMBIENT_INNER_DEFAULT
    return ""


def resolve_visual(v) -> str:
    """作品的**有效内容层** —— 生成流程据此分派要生成锚点 steps 还是结构图 points。

    返回值 ∈ `VISUAL_KINDS`（source / graph / graph_paper / graph_clean / 解压三皮肤）。

    ⚠️ talking_head 是**叠加层不是替代层**，它自己不产画面 → 必须看内容层。
    ⚠️ graph_paper / graph_clean（结构图变体）与 graph 同族：返回自身，
       由 `_gen_visuals` 走 points 那条分支。
    ⚠️ ambient 按 inner 分：`inner=source` → "source"（要锚点定位）；`inner=none` →
       "ambient"（纯氛围，不需要任何内容生成）。
    ⚠️ radio / night 返回自身（电台皮肤不需要锚点 / 结构图，纯陪伴画面）。
    ⚠️ 绝不返回空串、绝不抛错（空串会让调用方落进「未知的视觉方案」分支）。
    ⚠️ 本函数只管「生成逻辑分派」；**舞台模板选择是另一条路**（`build_stage` 用
       `v.skin` + `resolve_inner`）—— ambient+source 的模板是 ambient、内容层是
       source，两个维度不能混（这正是为什么不能只看 `v.skin`）。
    """
    skin = str(getattr(v, "skin", "") or "").strip().lower()
    if skin == "talking_head":
        return resolve_inner(v)
    if skin == "ambient":
        return "source" if resolve_inner(v) == "source" else "ambient"
    return skin if skin in VISUAL_KINDS else "source"


def skin_label(v) -> str:
    base = SKIN_LABELS.get(v.skin or "", v.skin or "")
    inner = resolve_inner(v)          # ⚠️ 走同一出口：空/非法内容层按默认显示
    if inner and inner != "none":     # none 不追加（纯氛围就是「氛围」，不是「氛围 · 纯氛围」）
        base = f"{base} · {INNER_LABELS.get(inner, inner)}"
    # 变体只在**非默认套**时追加（默认套 = 存量作品的既有显示，不能改名）
    sv = str(getattr(v, "skin_variant", "") or "")
    if sv:
        vl = variant_label(v.skin, sv)
        if vl:
            base = f"{base} · {vl}"
    return base


# 图片魔数 → MIME（上传落盘无后缀，只能自己嗅探）
_IMAGE_MAGIC = (
    (b"\xff\xd8\xff", "image/jpeg"),       # JPEG
    (b"\x89PNG\r\n\x1a\n", "image/png"),  # PNG
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def sniff_image_mime(raw: bytes) -> str:
    """按魔数判定图片 MIME。

    ⚠️ 上传落盘是 `{aid}.img`（**没有后缀**，存的是原始字节）→ 不能靠扩展名猜，
       否则 FileResponse 会给出 application/octet-stream，浏览器可能不拿它当图渲染。
    ⚠️ 只读开头若干字节即可判定；未知格式兜底 octet-stream（不让接口 500）。
    """
    for magic, mime in _IMAGE_MAGIC:
        if raw.startswith(magic):
            return mime
    if len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def avatar_label(avatar_id: str) -> str:
    """上传形象的展示名：从 id 里解出可读时间。

    ⚠️ 不使用 id 本身做展示名（`u-20261008204512-1234`），也不统一叫「已上传形象」——
       多张上传图会变成一串同名项，用户根本分不清哪个是哪个。
    """
    m = re.match(r"^u-(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})", avatar_id or "")
    if m:
        return f"已上传 · {m.group(2)}-{m.group(3)} {m.group(4)}:{m.group(5)}"
    return "已上传形象"


def list_avatars() -> list:
    """可选形象 = 预置 + **用户已上传的**。

    ⚠️ 上传过的形象必须能被枚举出来：否则「上传」退化成一次性动作 ——
       上传完刷新页面就再也选不到（作品还引用着，界面里却找不到），
       用户只会以为上传没生效，然后反复重传，`_avatars/` 里堆一堆没人引用的图。
       （实测捕获：`avatars_dir()` 此前只被写入，全项目没有任何地方枚举它。）
    """
    # ⚠️ builtin 显式给预置项：前端用它（而不是猜 id 前缀）判断「这一项能不能删」。
    # ⚠️ 预置项**同样必须带 url**：自 2026-10-12 起预置形象是真图片，
    #    不给地址的话列表里就是一排无字空点，用户认不出哪个是哪个。
    #    （`file` 不下发 —— 那是包内实现细节，前端只认 url。）
    out = []
    for it in BUILTIN_AVATARS:
        p = preset_file(it["id"])
        url = ""
        if p is not None:
            try:
                url = f"{AVATAR_URL_PREFIX}/{it['id']}/file?v={int(p.stat().st_mtime)}"
            except BaseException:
                url = ""
        item = {**it, "builtin": True, "exists": p is not None, "url": url}
        item.pop("file", None)
        # 「这个形象有没有动作视频」：下发供前端与验收探针断言。
        # 前端当前不据此改 UI —— 有视频时舞台自己会播，用户无需额外配置。
        # 2026-10-13 晚拍板：形象路线回到图生视频（PNG/inner 路线废弃），
        # motion 标记恢复只看 webm 是否存在。
        item["motion"] = preset_video(it["id"]) is not None
        out.append(item)
    have = {x["id"] for x in out}
    try:
        files = [f for f in avatars_dir().iterdir()
                 if f.is_file() and f.suffix.lower() == ".img"]
        files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        for f in files[:MAX_AVATARS]:
            aid = f.stem
            if aid in have:
                continue
            have.add(aid)
            try:
                ver = int(f.stat().st_mtime)
            except OSError:
                ver = 0
            # ⚠️ 必须带 url：不给缩略图地址的话列表里就是一个空点，
            #    用户认不出自己传了哪张。带 mtime 当版本号，换图后浏览器不会拿旧缓存。
            #    （预置项同样带 url —— 见上方。）
            out.append({"id": aid, "label": avatar_label(aid), "kind": "upload",
                        "builtin": False, "exists": True,
                        "url": f"{AVATAR_URL_PREFIX}/{aid}/file?v={ver}"})
    except BaseException:
        pass
    return out


def delete_avatar(aid: str) -> str:
    """删除一个**用户上传的**形象文件，返回四态字符串。

    返回值（调用方据此定 HTTP 码，不要用异常代替状态）：
      "deleted"  文件确实已不存在（本次删掉，或本来就没了 —— 幂等）
      "invalid"  不是上传形象（预置 id / 空 / 含路径分隔符）→ 预置形象无文件，永不可删
      "failed"   文件此刻仍然存在 → 没删掉（被占用 / 护栏拦截 / 权限）

    ⚠️ 判据是「文件是否真的不存在」，**不是「有没有抛异常」**：
       本机删除走 safe-delete 护栏（单 turn 累计删除数超阈值会抛 SystemExit），
       `except Exception` 接不住它；一旦吞掉它又按「没抛异常」判成功，
       就会得到「接口报成功、文件还在」的假删除。
    ⚠️ 绝不把 SystemExit 外抛：这段跑在 uvicorn 请求线程里，外抛会波及服务进程。
       一律吞掉，最后用文件系统事实（p.exists()）说话。
    """
    a = (aid or "").strip()
    if not a.startswith("u-") or "." in a or "/" in a or "\\" in a:
        return "invalid"
    root = avatars_dir()
    try:
        p = (root / f"{a}.img").resolve()
        if p.parent != root.resolve():
            return "invalid"
    except BaseException:
        return "invalid"
    # ⚠️ Windows 现实：文件正被读着时 unlink 会失败 —— 页面刚请求过这个形象的缩略图
    #    （uvicorn 的 FileResponse 刚打开过它），此时点删除会抛 PermissionError。
    #    实测：页面上点删除 → 500；**同一个请求隔 2 秒重发 → 200**。
    #    所以这里做**有界短重试**（最多 6 次、累计约 2.25s），给句柄释放的时间；
    #    既不忽略失败（真删不掉仍返回 failed），也不无限重试拖住请求线程。
    for _i in range(6):
        try:
            if p.is_file():
                p.unlink()
        except BaseException:
            pass                  # ← 护栏 SystemExit / 文件占用都在这里，不外抛
        try:
            if not p.exists():
                return "deleted"
        except BaseException:
            return "failed"
        if _i < 5:
            time.sleep(0.15 * (_i + 1))     # 0.15 / 0.30 / 0.45 / 0.60 / 0.75s
    try:
        return "deleted" if not p.exists() else "failed"
    except BaseException:
        return "failed"


def avatar_info(avatar_id: str) -> dict:
    """形象信息：预置或上传。查不到返回一个占位（**不静默改作品**）。"""
    a = (avatar_id or "").strip()
    for it in BUILTIN_AVATARS:
        if it["id"] == a:
            return {**it, "builtin": True, "exists": preset_file(a) is not None}
    if a.startswith("u-"):
        p = avatars_dir() / f"{a}.img"
        # 标签与 list_avatars() 同源：同一个人在两处必须同名
        return {"id": a, "label": avatar_label(a), "kind": "upload", "builtin": False,
                "exists": p.exists()}
    return {"id": a, "label": a or "未选择", "kind": "unknown", "builtin": False,
            "exists": False}
