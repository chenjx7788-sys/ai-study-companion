"""渲染器（阶段 2，PRD 3.8）：把「舞台页 + 音轨」合成为一条 MP4。

链路（与 PRD 3.8.5 逐步对应）：
  1. 无头 Chromium（子进程）+ CDP `Page.startScreencast{jpeg,q78,1920×1080,everyNthFrame:1}`
  2. 每帧回 `Page.screencastFrameAck`（不回很快停流）
  3. 对齐锚点：`armed` 之后进来的第一帧即 t=0（arming 发生在起驱动之前一点点）
  4. 帧流**单遍重采样**到严格 CFR 30fps（等价于 `pick_grid`：每槽位取时间上最接近的帧）
  5. PyAV `libx264` 编码视频轨（crf20 / veryfast / high / g=60）
  6. PyAV 解码音轨、重采样 48 kHz（**必须显式给输入帧 pts/time_base**）
  7. 按时间戳交错 mux + `movflags=+faststart`
  8. 抽封面帧（取有代表性的时刻，不是第 0 帧的黑场）
  9. **收尾自检：逐槽位对齐误差**，99 分位 / 平均 / 末槽 三条判据（见 `slot_errors`）

为什么要 CDP screencast 而不是「逐帧 seek + 截图」：原型的动效（高亮框 .42s 缓动、
浮层 .25s 淡入）都是 CSS 过渡，按真实时间走、**无法 seek**。screencast 让页面按真实时间
播放、由合成器出帧，时间与画面天然一致。

⚠️ 本机常不存在系统 `ffmpeg` —— 但 PyAV 内含整套 libav*（libx264 / aac 全在），
   整条链路在**单个 Python 进程内**完成：解码 → 重采样 → 编码 → 合流。

⚠️ 三条实测出来的硬约束（错一条就静默出废片）：
   ① **同一浏览器里必须串行渲染**：两个页只有一个算「前台」，后台页的 rAF 被节流
      → 自驱动时钟停摆、screencast 零帧。并发只增大内存，不会更快。
   ② **`everyNthFrame` 不能用 2**：起驱动前页面是静止的，整段只产出 1 帧、被 everyNth
      跳过 → 一帧都收不到。
   ③ **不能取「settle 期间最后一帧」当锚点**：取决于当时还剩多少损伤要刷，
      两支片子能差 600 ms（实测 source 皮肤 settle 出 27 帧、graph 只出 1 帧）。
   ④ **锚点只能「先喂进重采样器、喂成功了」才算认下**，`state["anchor_ts"] = ts`
      绝不能写在 try 外面。踩过的坑：那一帧的 feed 抛异常被 `except` 吞掉，
      锚点却已写死 → 这一帧从没进过重采样器 → 第一帧真正入队的 rel 变成几百 ms
      → 现象是「第 0 槽偏差 317 ms」，看起来跟锚点毫无关系（槽位 0 在数学上只会是 0）。
   ⑤ **帧回调与主线程不保证同拍**（Playwright 的事件派发可能发生在 pump 之外）
      → `armed / anchor_ts / ts_abs` 这套共享状态必须上锁；`filler` 必须在
      arming **之前**建好，否则第一帧进回调时 `filler_holder["filler"]` 还不存在。

⚠️⚠️ 对齐自检**必须逐槽位**做，**不能只比「末帧相对时刻 vs 页面时钟」**（冒烟实测踩到）：
   页面在 `TOTAL` 就停笔（`stage_tpl.html` 里 `ms = Math.min(TOTAL, …)`），但 ack 反馈环
   让 Chrome 继续合成、帧时间戳继续走 —— 收尾那 `DRAIN_S` 的排水尾巴会被整段算进
   「末帧相对时刻」，实测 310 ms > 150 ms 直接误判锚点错。
   同一个坑还有第二种形态（更隐蔽）：**「录制时长 ≥ 音轨 − 250 ms」也会被排水帧顶过 `total`
   而永远通过** —— 页面提前 500 ms 停了照样判绿，是典型的恒真断言。
   逐槽位口径 `|rel_k − k/fps|` 天然同时覆盖这两件事：噪声底只有「重采样半个帧距」
   （≈16 ms @30fps），锚点早/晚都对称可见，录制不足会让尾部槽位误差直接爆掉。
"""

from __future__ import annotations

import base64
import io
import math
import threading
import time
from fractions import Fraction
from pathlib import Path

OUT_W, OUT_H, OUT_FPS = 1920, 1080, 30
JPEG_QUALITY = 78
# 逐槽位对齐误差上限（PRD 3.8.6 的 150 ms 判据，逐槽位形式）。
# 噪声底 = 重采样半个帧距 ≈ 16 ms @30fps，所以 150 ms 留了 ~9× 余量。
SKEW_LIMIT_MS = 150.0
# 平均偏差上限：**系统性偏移**（锚点错）会让所有槽一起偏，平均最灵敏。
# 单点卡顿几乎不影响平均（实测 1 处 122 ms 空洞只把平均推到 6.8 ms），所以这条可以收紧。
MEAN_SKEW_LIMIT_MS = 60.0
# 渲染超时：视频时长 × 3 + 5 分钟（PRD 3.8.7）
TIMEOUT_FACTOR = 3.0
TIMEOUT_EXTRA_S = 300.0
# 帧时间戳**秩位移**上限（PRD 3.8.7）。
#   秩位移 = |原序第 i 项 − 升序第 i 项|，量的是「这串时间戳有多不单调」（秒 → ×1000 ms）。
# ⚠️⚠️ 判据用 **p99**、不用最大值 —— 与 3.8.6 完全同口径（见 `slot_errors` 的 docstring）：
#    抓帧流会被页面自身的卡顿顶出**单点**空洞（实测：切页图那一刻 150dpi 大图解码阻塞
#    合成器 → 244 ms 空洞），相邻几帧的时间戳就出现一次局部逆序。这种单点乱序
#    **渲染不出来更好的**（`_GridFiller` 是按到达顺序流式重采样，只影响相邻槽位
#    ±1 帧 ≈ 33 ms 的画面，肉眼不可见），拿它当判据只会让**好片子被判失败**。
#    实测（作品 6）：峰值秩位移 130 ms，而 p99 为 0 —— 旧口径（max）直接判死。
# 阈值沿用原 `REORDER_LIMIT_MS = 60.0`：p99 口径下正常渲染恒为 0，余量极大。
REORDER_P99_LIMIT_MS = 60.0
# ⚠️ 另留一道**峰值**安全网 —— 与 p99 互补、不是重复：
#    p99 是**长度相关**的（1% 的份额随帧数变宽），所以长片里一次「秒级」单点逆序
#    可能被 p99 放过（实测合成序列：3000 帧 + 单点 900 ms → p99 仍是 0）。
#    峰值是**长度无关**的硬上限，专拦「几何级离谱」的事件（CDP 批量重投、编码前
#    缓冲错序），不参与日常判定。实测正常渲染最长单点 244 ms（同上那次切图）
#    → 取约 2× 余量 = 500 ms。
REORDER_MAX_LIMIT_MS = 500.0
# 开始抓帧后，等这么久让初始静止帧出完，再读锚点帧数
SETTLE_S = 0.30
# 泵的节拍：每轮都回 ack（**回得越快，抓到的帧越多** —— Chrome 在上一帧没被 ack 时会跳过
# 新的合成帧；实测 40ms 节拍只有 ~46fps，空洞最大 281ms）。
PUMP_MS = 16
# 页面状态（时钟/进度/done）查询降频：每次 evaluate 都是一次往返，没必要 60Hz。
# 代价是 done 的发现最多晚 PUMP_MS × STATE_EVERY。
STATE_EVERY = 3
# 收尾等最后几帧投递
# ⚠️ 排水只是为了**内容**（末槽位要有可用帧），**绝不要**把它算进任何时间判据 ——
#    排水帧的时间戳会一路推过 TOTAL（实测推了 310 ms），只看末帧的话两个判据都会假绿。
DRAIN_S = 0.45
# 断流判据：连续这么久没抓到任何新帧就判中断（页面在连续播放，帧流不该静默）
STALL_S = 4.0

# ⚠️ `--disable-features=CalculateNativeWinOcclusion` 是必需的：
#    否则被系统判定「遮挡」的页面 **CSS 过渡不推进**（表现：截图停在旧状态，
#    看起来像视频内容错了）。`--hide-scrollbars` 干净取景；
#    `--force-device-scale-factor=1` 与 1920×1080 取景严格对应。
CHROME_BASE_ARGS = [
    "--disable-features=CalculateNativeWinOcclusion",
    "--hide-scrollbars",
    "--force-device-scale-factor=1",
    "--disable-gpu-vsync",
    "--mute-audio",
    "--no-first-run",
    "--no-default-browser-check",
]

_CHROME_HINTS = [
    Path(r"C:\Users\陈锦祥\AppData\Local\ms-playwright"),
    Path.home() / "AppData" / "Local" / "ms-playwright",
    Path.home() / ".cache" / "ms-playwright",
]


def find_chromium() -> str | None:
    """找一个已安装的 Chromium 可执行文件（**不现下载**：浏览器版本会漂移）。"""
    import os
    env = os.environ.get("ASC_CHROME_PATH")
    if env and Path(env).exists():
        return env
    for base in _CHROME_HINTS:
        if not base.is_dir():
            continue
        cands = sorted(base.glob("chromium-*/chrome-win64/chrome.exe"), reverse=True)
        cands += sorted(base.glob("chromium-*/chrome-linux/chrome"), reverse=True)
        cands += sorted(base.glob("chromium-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium"),
                        reverse=True)
        for c in cands:
            if c.exists():
                return str(c)
    return None


# ---------- 重采样（CFR） ----------

def pick_grid(ts: list, n: int, fps: int) -> list:
    """每个 CFR 槽位选一个帧下标：取时间上最接近 `k/fps` 的那一帧。

    `ts` 升序（相对秒）。这是**离线参照实现** —— 在线路径用同构的单遍流式版
    `_GridFiller`，两者必须给出同一答案（验收探针会交叉比对）。
    """
    out = []
    j = 0
    m = len(ts)
    if m == 0:
        return [0] * max(0, n)
    for k in range(max(0, n)):
        t = k / float(fps)
        while j + 1 < m and abs(ts[j + 1] - t) <= abs(ts[j] - t):
            j += 1
        out.append(j)
    return out


class _GridFiller:
    """单遍流式重采样：把「到达时刻不规则」的帧流填进严格 CFR 槽位。

    ⚠️ 为什么必须流式：一条 300 秒的片子有 9000 帧 JPEG（每帧 ~150 KB）——
       全量缓存是 1.3 GB 内存。流式只需要「当前帧 + 下一帧」。
    ⚠️ 判据与 `pick_grid` 同构：槽位 k 归给帧 i，当 `k/fps` 落在
       帧 i-1 与 i 的中点、帧 i 与 i+1 的中点之间。所以需要**拖一帧**才敢定归属
       （否则不知道下一帧什么时候来）。
    ⚠️ 每个槽位存的是 `(rel, payload)` 而不是只存 payload：`rel` 是判「这一槽是不是
       真的取到了那个时刻的画面」的唯一依据（逐槽位对齐自检）。
    """

    __slots__ = ("fps", "n", "next_k", "prev", "chosen", "flushed")

    def __init__(self, n: int, fps: int):
        self.fps = fps
        self.n = max(0, int(n))
        self.next_k = 0
        self.prev = None            # (rel_ts, payload) 待定归属的帧
        self.chosen: list = []      # [(rel_ts, payload)] 每槽位选中的帧
        self.flushed = False

    def feed(self, rel: float, payload) -> None:
        if self.prev is None:
            self.prev = (rel, payload)
            return
        r0 = self.prev[0]
        mid = (r0 + rel) / 2.0
        while self.next_k < self.n and self.next_k / float(self.fps) < mid:
            self.chosen.append(self.prev)
            self.next_k += 1
        self.prev = (rel, payload)

    def finish(self) -> None:
        """把剩余槽位全部交给最后一帧（静止画面在尾部天然重复）。

        ⚠️ 尾部槽位若真被走到这里，说明**没抓够帧** —— 它们的 `rel` 与目标时刻的差
           会在逐槽位自检里直接暴露成失败（这正是我们想要的，不能默默出一支断尾片）。
        """
        if self.flushed:
            return
        self.flushed = True
        while self.next_k < self.n:
            self.chosen.append(self.prev)        # 可能为 None（一帧都没收到）
            self.next_k += 1


# ---------- 编码 ----------

def _video_stream(container, fps: int, width: int, height: int):
    st = container.add_stream("libx264", rate=fps)
    st.width, st.height = width, height
    st.pix_fmt = "yuv420p"
    # 关键帧间隔 60；CRF 20 / veryfast：PRD 指定
    st.options = {"crf": "20", "preset": "veryfast", "profile": "high", "g": "60"}
    return st


def encode_audio(container, audio_path: Path, rate: int = 48000):
    """解码音轨 → 重采样到 `rate` → 返回 (aac_stream, [packets], duration_s)。

    ⚠️ 必须给**输入帧**显式 `pts` / `time_base`：MP3/AAC 解出来的包常常 `pts=None`
       （缺容器时间戳），不补的话重采样后的帧也没有 pts。
    ⚠️ 也必须给**重采样后的输出帧**显式 pts（自增样本计数）—— 这是实测踩出来的：
       设成 `None` 会让 aac 编码出的**每个包 pts/dts 都是 None**（实测 152/152），
       交错排序时全部落到 0 秒 → 顺序荒谬 → muxer EINVAL。
    """
    import av

    st = container.add_stream("aac", rate=rate)
    st.bit_rate = 128000

    in_c = av.open(str(audio_path))
    try:
        in_s = in_c.streams.audio[0]
        resampler = av.audio.resampler.AudioResampler(format="fltp", layout="stereo", rate=rate)
        packets = []
        in_pts = 0
        out_pts = 0
        dur = 0.0
        for frame in in_c.decode(in_s):
            frame.pts = in_pts
            frame.time_base = in_s.time_base or Fraction(1, in_s.rate or rate)
            in_pts += int(frame.samples or 0)
            out_frames = resampler.resample(frame)
            if out_frames is None:
                continue
            if not isinstance(out_frames, (list, tuple)):
                out_frames = [out_frames]
            for rf in out_frames:
                if rf is None:
                    continue
                rf.pts = out_pts                  # ⚠️ 不能是 None（见 docstring）
                rf.time_base = Fraction(1, rate)
                out_pts += int(rf.samples or 0)
                dur += float(rf.samples or 0) / float(rate)
                for pkt in st.encode(rf):
                    packets.append(pkt)
        for pkt in st.encode(None):
            packets.append(pkt)
    finally:
        try:
            in_c.close()
        except BaseException:
            pass
    return st, packets, dur


def save_poster(frame_img, out_poster: Path, at_ratio: float = 0.08) -> bool:
    """抽封面帧。调用方给的是**代表性时刻**的图（不是第 0 帧的黑场）。"""
    try:
        out_poster.parent.mkdir(parents=True, exist_ok=True)
        im = frame_img.copy()
        im.thumbnail((1280, 1280))
        im.save(str(out_poster), "JPEG", quality=88)
        return True
    except BaseException:
        return False


# ---------- 对齐自检 ----------

def slot_errors(chosen: list, fps: int) -> dict:
    """逐槽位对齐误差（毫秒）：槽位 k 的目标时刻是 `k/fps`，看选中的那帧差多少。

    返回 `{worst_ms, p99_ms, mean_ms, worst_k, slots, tail_ms}`。
    `tail_ms` = 最后一个槽位的**有符号**误差（正 = 取到的帧比目标晚）。

    ⚠️ 为什么要 p99 而不是只看最大：抓帧流会被页面自身的卡顿顶出**单点**空洞
       （实测 source 皮肤在切页图那一刻，一张 150dpi 大图解码头阻塞了合成器，
       留下一个 244 ms 的洞 → 第 348 槽误差 122 ms，而平均只有 6.8 ms）。
       这种单点误差是**渲染不出来更好的**（就近取帧已是最优，跨越空洞反而更旧），
       拿它当判据只会让好片子被判失败。真正要拦的是**系统性偏移**（锚点错 → 所有槽一起偏）
       和**录制断尾**（尾部整段偏）—— 前者 p99 会一起偏，后者看 tail。
    """
    ds = []
    worst = -1.0
    worst_k = -1
    tail = 0.0
    tot = 0.0
    n = 0
    for k, item in enumerate(chosen):
        if item is None:
            continue
        rel = item[0]
        d = (rel - k / float(fps)) * 1000.0
        ds.append(abs(d))
        tot += abs(d)
        n += 1
        if abs(d) > worst:
            worst = abs(d)
            worst_k = k
        tail = d
    if n == 0:
        return {"worst_ms": float("inf"), "p99_ms": float("inf"),
                "mean_ms": float("inf"), "worst_k": -1, "slots": 0, "tail_ms": 0.0}
    ds.sort()
    p99 = ds[min(n - 1, int(math.ceil(n * 0.99)) - 1)]
    return {"worst_ms": round(worst, 1), "p99_ms": round(p99, 1),
            "mean_ms": round(tot / n, 1), "worst_k": worst_k,
            "slots": n, "tail_ms": round(tail, 1)}


def reorder_disp_ms(rel_list: list) -> dict:
    """帧时间戳**秩位移**（毫秒）：量这串时间戳有多不单调。

    定义：把 `rel_list` 升序排成 `srt`，逐位取 `|rel_list[i] − srt[i]|`。
    单调递增的序列恒为 0；某帧的时间戳偏了 δ，会让**它到它该在的位置之间**那一段
    都产生 ≈ δ 的位移（受影响的帧数 ≈ δ × fps）。

    返回 `{p99_ms, max_ms, n, affected}`；`n == 0` 时全为 0。
    `affected` = 位移 > 0 的帧数（诊断用：正常渲染只被单点卡顿顶出**个位数**）。

    ⚠️ 抽成**模块级纯函数**是为了可离线自检 —— 判据的松紧（p99 vs max）不该靠跑一遍
       50 秒真渲染才敢改：契约探针直接喂合成序列做分辨力自检（同 `slot_errors` 的理由）。
    """
    n = len(rel_list)
    if n == 0:
        return {"p99_ms": 0.0, "max_ms": 0.0, "n": 0, "affected": 0}
    srt = sorted(rel_list)
    disp = sorted(abs(a - b) for a, b in zip(rel_list, srt))
    p99 = disp[min(n - 1, int(math.ceil(n * 0.99)) - 1)] * 1000.0
    return {"p99_ms": round(p99, 1), "max_ms": round(disp[-1] * 1000.0, 1),
            "n": n, "affected": sum(1 for d in disp if d > 0)}


def _with_notes(msg: str, state: dict) -> str:
    """给失败信息补上现场数据。

    ⚠️ 血泪：失败信息若只有一句「对齐异常」，排查时完全看不出区别 ——
       「锚点帧被丢」「驱动前帧被当锚点」「一帧都没收到」在外部表现上几乎一样，
       而它们的修法完全不同。现场数据必须随错误一起回传。
    """
    bits = []
    if state.get("dropped"):
        bits.append(f"丢帧 {state['dropped']}")
    if state.get("pre"):
        bits.append(f"驱动前丢 {state['pre']}")
    if state.get("first_skew_ms") is not None:
        bits.append(f"首帧时延 {state['first_skew_ms']} ms")
    w = [str(x) for x in (state.get("errs") or [])[:2]]
    if w:
        bits.append("；".join(w))
    return msg + ("（" + "，".join(bits)[:140] + "）" if bits else "")


# ---------- 主流程 ----------

def render(stage_html: Path, audio_path: Path, out_mp4: Path, out_poster: Path,
           duration_ms: int, *, width: int = OUT_W, height: int = OUT_H,
           fps: int = OUT_FPS, on_progress=None) -> dict:
    """渲染一条成片。返回渲染报告 dict（含 skew / 帧数 / 重复帧占比）。

    失败一律抛 `RuntimeError`（带上可读原因），由调用方置 `failed`。
    """
    import av
    from PIL import Image
    from playwright.sync_api import sync_playwright

    dur_s = max(0.1, int(duration_ms or 0) / 1000.0)
    n_frames = int(round(dur_s * fps))
    timeout_s = dur_s * TIMEOUT_FACTOR + TIMEOUT_EXTRA_S

    exe = find_chromium()
    if not exe:
        raise RuntimeError("找不到可用的 Chromium（渲染器需要）")

    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    pending_sids: list = []        # 主循环里 ack 用（事件回调只入队，不发 CDP）
    ts_abs: list = []              # **成功入队**的帧的绝对时间戳（诊断与交叉验证用）
    state = {"armed": False,       # 只有 armed 之后的帧才算「驱动期」
             "pre": 0,             # 起驱动前捕获并被丢弃的帧数
             # 被丢弃的前驱帧里**最新**的那个时间戳：arm 之后任何不新于它的帧
             # 都是「驱动前捕获、现在才投递到」的滞留帧 —— 绝不能当锚点。
             "pre_ts_max": None,
             "anchor_ts": None, "clock_ms": 0.0, "done": False,
             "last_ts": None, "errs": [], "dropped": 0,
             "first_skew_ms": None}

    filler_holder: dict = {}
    # ⚠️ 帧回调**不一定**与主线程同拍（Playwright 的事件派发可能发生在 pump 之外）
    #    → 「arm / anchor / feed」这套共享状态必须上锁，否则会出现
    #    「主线程刚读到的帧数」与「回调里追加的帧」错位（实测就是靠它才暴露出来的）。
    frame_lock = threading.Lock()
    last_img = {"im": None, "k": None}
    vpackets: list = []
    vstream_holder: dict = {}

    def make_writer(container, vst):
        """帧投递器：JPEG → PIL → VideoFrame → encode。同一 JPEG 复用时不重复解码。"""
        def put(payload, k: int):
            data = payload
            if last_img["k"] != id(data):
                try:
                    im = Image.open(io.BytesIO(data))
                    im.load()
                except BaseException:
                    return
                last_img["im"] = im.convert("RGB")
                last_img["k"] = id(data)
            im = last_img["im"]
            if im is None:
                return
            fr = av.VideoFrame.from_image(im)
            fr.pts = k
            fr.time_base = Fraction(1, fps)
            for pkt in vst.encode(fr):
                vpackets.append(pkt)
        return put

    t_start = time.time()
    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=True, executable_path=exe,
                                args=list(CHROME_BASE_ARGS))
        try:
            ctx = br.new_context(viewport={"width": width, "height": height},
                                 device_scale_factor=1)
            page = ctx.new_page()
            page.on("pageerror", lambda e: state["errs"].append(str(e)))
            page.goto(stage_html.as_uri())
            # ⚠️ 两段等待必须**分别**给出人话原因：裸的 `wait_for_function` 超时
            #    对用户毫无信息（既不知道卡在哪一步，也不知道为什么），而这两段的
            #    失败原因完全不同 —— 前者是页面脚本自己炸了，后者是素材永远就绪不了。
            try:
                page.wait_for_function("window.__stage_ready === true", timeout=30000)
            except BaseException as e:
                raise RuntimeError(_with_notes(
                    "舞台页脚本没有执行完（页面自身报错了）", state)) from e
            # 资源就绪（图片解码 + 字体）——否则第一帧抓到的是白屏
            try:
                page.wait_for_function("window.__stage.ready && window.__stage.ready()",
                                       timeout=20000)
            except BaseException as e:
                raise RuntimeError(_with_notes(
                    "舞台页素材始终未就绪（原件画面或字体没加载完）", state)) from e
            page.wait_for_timeout(int(SETTLE_S * 1000))

            cdp = ctx.new_cdp_session(page)

            def on_frame(ev):
                # ⚠️⚠️ 事件回调里**任何**异常都**不会**冒泡到我们这里：Playwright 会把它
                #     交给监听器错误处理器，只往 stderr 打一行 "Error occurred in event listener"
                #     然后继续跑 —— 彻头彻尾的静默失败。
                #     实测踩到：回调里漏定义一个局部变量 `ts` → 整条 29 秒的片子只喂进 1 帧，
                #     而对外只说了一句「第 875 槽偏差 29167 ms」，跟真实原因毫无关系。
                #     所以**整个函数体都必须包在 try 里**，异常一律记进 `state["errs"]`
                #     （失败信息会带上它，见 `_with_notes`）。
                # ⚠️ 另外：回调里只做「入队 + 喂帧」，**不发任何 CDP 命令**（同步 API 在事件
                #     回调里会有死锁风险）。ack 统一在主循环里发。
                try:
                    pending_sids.append(ev.get("sessionId"))
                    meta = ev.get("metadata") or {}
                    ts = float(meta.get("timestamp") or 0.0)
                    with frame_lock:
                        if not state["armed"]:
                            state["pre"] += 1
                            state["pre_ts_max"] = max(
                                state["pre_ts_max"] or 0.0, ts)
                            return          # 驱动前的静止帧：丢弃（不参与成片）
                        # ⚠️ 滞留帧守卫：`SETTLE_S` 期间 Chrome 会主动送帧（**不需要 ack**），
                        #    其中一部分会晚到 —— 它们的时间戳不新于已丢弃的前驱帧。
                        #    实测：182 秒的口播片，锚点取到这种帧 → 首帧时延 **-249.9 ms**、
                        #    p99 179 ms → 整条 7 分钟的渲染被判「对齐异常」作废。
                        #    锚点一旦定错，**所有**槽位一起偏，是系统性问题，必须在这里拦住。
                        if (state["anchor_ts"] is None
                                and state["pre_ts_max"] is not None
                                and ts <= state["pre_ts_max"]):
                            state["pre"] += 1
                            state["pre_ts_max"] = max(state["pre_ts_max"], ts)
                            return
                        payload = base64.b64decode(ev.get("data"))
                        f = filler_holder["filler"]
                        # ⚠️ 锚点必须「先喂进去、喂成功了」才算认下：`state["anchor_ts"] = ts`
                        #    绝不能写在别处。踩过的坑：把锚点写在 try 外面，一旦这一帧喂失败，
                        #    锚点已写死而这一帧从没进过重采样器 → 第一帧真正入队的 rel 变成
                        #    几百 ms → 现象是「第 0 槽偏差 317 ms」（槽位 0 在数学上只会是 0）。
                        rel = 0.0 if state["anchor_ts"] is None else ts - state["anchor_ts"]
                        f.feed(rel, payload)
                        if state["anchor_ts"] is None:
                            state["anchor_ts"] = ts
                        ts_abs.append(ts)
                        state["last_ts"] = ts
                except BaseException as e:
                    state["dropped"] += 1
                    state["errs"].append(f"frame: {type(e).__name__}: {e}")

            cdp.on("Page.screencastFrame", on_frame)
            cdp.send("Page.startScreencast", {
                "format": "jpeg", "quality": JPEG_QUALITY,
                "maxWidth": width, "maxHeight": height,
                "everyNthFrame": 1,          # ⚠️ 绝不能用 2
            })

            # ---- 锚点：armed 之前的帧一律丢弃，armed 之后的第一帧即 t=0 ----
            # ⚠️⚠️ arm 必须在 `start()` **之后**（这里踩过一次，代价是整条渲染作废）：
            #    原先写成「先 arm 再 start」，中间那段窗口里投递的帧会被当成锚点 ——
            #    而它们的捕获时刻在**驱动开始之前**（settle 期的静止帧，Chrome 不等 ack
            #    也会送），于是锚点早了几百 ms：实测首帧时延 **-249.9 ms**、p99 179 ms，
            #    一条 182 秒的片子白渲染 7 分钟后被判「对齐异常」。
            #    ⚠️ 判据是「首帧时延为**负**」= 页钟落后于最新帧 = 锚点取早了；
            #    正面（正值，几十 ms）是投递时延，属正常。
            # ⚠️ 顺序仍不能换的部分：① 先等 settle 让初始静止帧出完；
            #    ② **先把 filler 建好再 arm** —— armed 之后回调会立刻用
            #    `filler_holder["filler"]`，晚一步就是「锚点丢了」的洞；
            #    ③ 最后才 arm（此时候选帧一定是驱动开始后捕获的）。
            page.wait_for_timeout(int(SETTLE_S * 1000))
            with frame_lock:
                filler_holder["filler"] = _GridFiller(n_frames, fps)

            # ---- 启动自驱动时钟（页面按真实时间播放） ----
            page.evaluate("() => window.__stage.start()")

            with frame_lock:
                state["armed"] = True

            # ---- 泵：等页面跑完 + 及时 ack + 上报进度 ----
            t0 = time.time()
            last_pct = -1
            # 断流看门狗：页面在连续播放，帧流不该长时间静默。
            # ⚠️ 加上它是因为「回调静默失败」会一路跑满整个时长（29 秒白等），
            #    最后只报一句跟真因无关的「对齐偏差」。
            last_n = len(ts_abs)
            last_frame_at = time.time()
            n_pump = 0
            st = {"done": False}
            while True:
                n_pump += 1
                page.wait_for_timeout(PUMP_MS)   # 让事件回调有机会跑
                while pending_sids:              # 及时回 ack，否则停流
                    sid = pending_sids.pop(0)
                    try:
                        cdp.send("Page.screencastFrameAck", {"sessionId": sid})
                    except BaseException:
                        pass
                n_now = len(ts_abs)
                if n_now != last_n:
                    last_n, last_frame_at = n_now, time.time()
                elif time.time() - last_frame_at > STALL_S:
                    raise RuntimeError(_with_notes(
                        f"渲染中断：{int(STALL_S)} 秒没有抓到任何画面帧，请重试", state))
                if n_pump % STATE_EVERY == 0:
                    try:
                        st = page.evaluate("() => window.__stage.state()")
                    except BaseException:
                        st = {"done": False}
                    # 诊断：第一次**新鲜**的状态查询时，「页钟 − 最新已入队帧的相对时刻」。
                    # 正常 ≈ 投递时延（几十 ms）；明显偏大 = 锚点取到了驱动前的帧。
                    if (state["first_skew_ms"] is None and ts_abs
                            and state["anchor_ts"] is not None):
                        with frame_lock:
                            newest = ts_abs[-1] - state["anchor_ts"]
                        state["first_skew_ms"] = round(
                            (float(st.get("clock") or 0.0) / 1000.0 - newest) * 1000.0, 1)
                if st.get("done"):
                    state["clock_ms"] = float(st.get("clock") or 0.0)
                    state["done"] = True
                    break
                if time.time() - t0 > timeout_s:
                    raise RuntimeError(
                        f"渲染超时（超过 {int(timeout_s)} 秒），请重试或缩短时长")
                if on_progress:
                    pct = int(min(99, (st.get("clock") or 0.0) / max(1.0, dur_s * 1000) * 100))
                    if pct != last_pct:
                        last_pct = pct
                        on_progress(pct)

            # ---- 收尾：等最后几帧投递完，再停流 ----
            page.wait_for_timeout(int(DRAIN_S * 1000))
            while pending_sids:
                sid = pending_sids.pop(0)
                try:
                    cdp.send("Page.screencastFrameAck", {"sessionId": sid})
                except BaseException:
                    pass
            try:
                cdp.send("Page.stopScreencast")
            except BaseException:
                pass

            filler = filler_holder["filler"]
            filler.finish()
            chosen = filler.chosen
            if not chosen or any(it is None or it[1] is None for it in chosen):
                raise RuntimeError("渲染失败：没有抓到任何画面帧，请重试")

            # ---- 帧时间戳乱序检查（PRD 3.8.7） ----
            # `ts_abs` 只收「成功入队」的帧 —— 与重采样器看到的完全是同一批，
            # 否则会出现「乱序检查过的帧」和「真正用上的帧」不是一批的自欺。
            rel_all = [t - state["anchor_ts"] for t in ts_abs] \
                if state["anchor_ts"] is not None else []
            # 秩位移：**p99 主判**（与 3.8.6 同口径）+ **峰值安全网**（长度无关）。
            # ⚠️ 为什么不用 max 当主判：见常量处的注释（单点卡顿不是缺陷，作品 6 就是被它判死的）。
            rd = reorder_disp_ms(rel_all)
            if rd["p99_ms"] > REORDER_P99_LIMIT_MS:
                raise RuntimeError(_with_notes(
                    f"渲染数据异常（帧时间戳乱序 99 分位 {rd['p99_ms']:.0f} ms "
                    f"> {int(REORDER_P99_LIMIT_MS)} ms），请重试", state))
            if rd["max_ms"] > REORDER_MAX_LIMIT_MS:
                raise RuntimeError(_with_notes(
                    f"渲染数据异常（帧时间戳乱序峰值 {rd['max_ms']:.0f} ms "
                    f"> {int(REORDER_MAX_LIMIT_MS)} ms），请重试", state))

            # ---- 逐槽位对齐自检（PRD 3.8.6，逐槽位形式） ----
            # 槽位 k 的画面必须取自页面时间 ≈ k/fps 的那一帧。锚点错、起跑点错、
            # 录制断尾，三种毛病都会在这里爆掉（只看末帧会被排水尾巴掩掉）。
            # ⚠️ 判据用 p99 + 平均 + 末槽，**不用最大值**（单点卡顿不是缺陷，见 slot_errors）。
            se = slot_errors(chosen, fps)
            skew_ms = se["p99_ms"]
            if abs(se["p99_ms"]) > SKEW_LIMIT_MS:
                raise RuntimeError(_with_notes(
                    f"渲染对齐异常（99 分位偏差 {se['p99_ms']:.0f} ms "
                    f"> {int(SKEW_LIMIT_MS)} ms），请重试", state))
            if abs(se["mean_ms"]) > MEAN_SKEW_LIMIT_MS:
                raise RuntimeError(_with_notes(
                    f"渲染对齐异常（平均偏差 {se['mean_ms']:.0f} ms "
                    f"> {int(MEAN_SKEW_LIMIT_MS)} ms），请重试", state))
            if abs(se["tail_ms"]) > SKEW_LIMIT_MS:
                raise RuntimeError(_with_notes(
                    f"渲染不完整（最后一个槽位偏差 {se['tail_ms']:.0f} ms），请重试", state))

            # 抓帧间隔统计：用来解释「为什么最大误差偏大」（页面自身卡顿留下的空洞）
            gaps = [(ts_abs[i] - ts_abs[i - 1]) * 1000.0 for i in range(1, len(ts_abs))]
            max_gap_ms = round(max(gaps), 1) if gaps else 0.0
            big_gaps = sum(1 for g in gaps if g > 100.0)

            # 以下两项**只作诊断**：排水帧会把它们推过 TOTAL，不能当判据用。
            recorded_s = rel_all[-1] if rel_all else 0.0
            tail_skew_ms = (recorded_s * 1000.0 - state["clock_ms"]) \
                if rel_all else 0.0

            # ---- 编码 ----
            container = av.open(str(out_mp4), mode="w",
                                options={"movflags": "+faststart"})
            vst = _video_stream(container, fps, width, height)
            put = make_writer(container, vst)
            n_dup = 0
            for k, (_rel, payload) in enumerate(chosen):
                if k > 0 and payload is chosen[k - 1][1]:
                    n_dup += 1
                put(payload, k)
            for pkt in vst.encode(None):
                vpackets.append(pkt)

            _, apackets, audio_s = encode_audio(container, audio_path)
            if audio_s < dur_s - 0.5:
                # 音轨比讲稿短：成片尾部会静音。这不是致命错，但必须可见。
                state["errs"].append(f"audio_short: {audio_s:.2f}s < {dur_s:.2f}s")

            # ---- 交错 mux（按**秒**排序，两个流的 time_base 不同，pts 不能直接比） ----
            mux_interleaved(container, vpackets, vst, apackets, container.streams.audio[0])
            container.close()

            # ---- 封面帧：取 8% 处（避开开场黑场/静止） ----
            poster_ok = False
            try:
                pk = max(0, min(len(chosen) - 1, int(n_frames * 0.08)))
                im = Image.open(io.BytesIO(chosen[pk][1]))
                im.load()
                poster_ok = save_poster(im.convert("RGB"), out_poster)
            except BaseException:
                poster_ok = False

        finally:
            try:
                br.close()
            except BaseException:
                pass

    size = out_mp4.stat().st_size if out_mp4.exists() else 0
    if size <= 0:
        raise RuntimeError("渲染失败：成片文件为空，请重试")
    return {
        "frame_count": len(chosen),
        "duration_ms": int(round(dur_s * 1000)),
        "audio_ms": int(round(audio_s * 1000)),
        "width": width, "height": height, "fps": fps,
        "skew_ms": skew_ms,                 # 逐槽位 99 分位误差（判据）
        "skew_worst_ms": se["worst_ms"],    # 最大误差（诊断：常被单点卡顿顶高）
        "skew_worst_k": se["worst_k"],
        "skew_mean_ms": se["mean_ms"],
        "skew_tail_ms": se["tail_ms"],
        "reorder_p99_ms": rd["p99_ms"],      # 帧时间戳秩位移 99 分位（判据，PRD 3.8.7）
        "reorder_max_ms": rd["max_ms"],      # 峰值秩位移（安全网 500 ms 兼诊断）
        "reorder_affected": rd["affected"],  # 位移非 0 的帧数（正常渲染是个位数）
        "gap_max_ms": max_gap_ms,           # 抓帧最大间隔（>100ms 说明页面卡过）
        "gap_big_count": big_gaps,
        "tail_skew_ms": round(tail_skew_ms, 1),   # 旧口径，仅诊断（含排水尾巴）
        "dup_ratio": round(n_dup / max(1, len(chosen)), 4),
        "captured": len(ts_abs),
        "pre_frames": state["pre"],               # 起驱动前丢弃的帧数
        "dropped": state["dropped"],              # 入队失败的帧数（>0 就该查 warnings）
        "first_skew_ms": state["first_skew_ms"],  # 首帧时延（锚点是否取到驱动前的帧）
        "recorded_s": round(recorded_s, 3),       # 仅诊断
        "bytes": size,
        "poster_ok": poster_ok,
        "elapsed_s": round(time.time() - t_start, 1),
        "warnings": state["errs"][:5],
    }


def _pkt_sec(pkt, stream) -> float:
    """包的**解码顺序时刻**（秒）—— 交错写入必须按它排。

    ⚠️ 三个坑叠在一起，缺一个就是 mp4 直接拒包（`av.error.ArgumentError: … returned 22`）：
      ① 用 **DTS** 不用 PTS：libx264 默认开 B 帧 → 发射序是 DTS 序、PTS 是乱序的
         （实测同一段视频的 pts = 0,4,2,1,3,8,6,5,7…）。按 PTS 交错会让 muxer
         收到**倒退的 dts**（实测 512 → 0）→ EINVAL。
      ② 用**包自己的 time_base**（`pkt.time_base`）而不是流的：`stream.time_base`
         在 PyAV 里可能是 `None`（实测就是），退回 1/1000 会把 1/30 的 pts 算成
         33 倍大的秒数 —— 而排序用错刻度是**静默**的，只表现为「交错顺序很怪」。
      ③ 音频包必须显式给 pts（见 `encode_audio`），否则 pts/dts 全是 None →
         全部排到 0 秒。
    """
    tb = pkt.time_base or getattr(stream, "time_base", None)
    t = pkt.dts if pkt.dts is not None else pkt.pts
    if t is None or tb is None:
        return 0.0
    try:
        return float(t * tb)
    except BaseException:
        return 0.0


def mux_interleaved(container, vpacks: list, vstream, apacks: list,
                    astream) -> int:
    """把视频包与音频包**按秒**交错写入容器，返回写入的包数。

    ⚠️ 必须按「秒」排，不能按 `pkt.pts` 数值排：两个流的 `time_base` 不同
       （视频 1/30、音频 1/48000），pts 值不在一个刻度上，直接比会得到荒谬的顺序。
    ⚠️ 抽成独立函数是为了**可离线测**：合流层的错误（EINVAL）与浏览器无关，
       不该为了复现它去跑一遍 50 秒的真渲染。
    """
    merged = []
    for pkt in vpacks:
        merged.append((_pkt_sec(pkt, vstream), 0, pkt))
    for pkt in apacks:
        merged.append((_pkt_sec(pkt, astream), 1, pkt))
    merged.sort(key=lambda x: (x[0], x[1]))
    for _, _, pkt in merged:
        container.mux(pkt)
    return len(merged)


def estimate_seconds(duration_ms: int) -> float:
    """预计渲染耗时 ≈ 视频时长 × 1.5（screencast 是实时抓帧，1× 起步）。"""
    return max(1.0, int(duration_ms or 0) / 1000.0 * 1.5)
