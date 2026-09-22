# -*- coding: utf-8 -*-
"""WP17：侧栏流式订阅（SSE 扇出）—— 「侧栏盒子在变」这件事的**唯一广播点**。

=================== 为什么是独立模块（而不是塞进 browser_host） ===================
⚠️⚠️ 设计上扇出应该落在**数据面**（`browser_host`）里：那样任何一处写盒子都自动带上广播。
   但本机 `browser_host.py` 被一个**外部只读句柄**长期占用（实测：写 / 改名 / 覆盖一律
   `ERROR_ACCESS_DENIED`，而同目录同 ACL 的邻居可写；占用者在进程表里表现为
   百度网盘同步 / 沙箱 GC 一类，**不是**本项目的进程）→ 该文件本轮改不了。
   于是把扇出**外移**到本模块，由「写盒子的那几个调用点」显式调用。
   代价与收益必须写清楚，不许含糊：
     · 收益：不动一个被锁的文件；发布点与写入点**同屏可见**（谁写盒子、谁广播，一眼对齐）。
     · 代价：将来若有人**新增**一处直接写盒子却忘了广播，流式会漏事件 ——
       但 1.5s 轮询**仍在跑**，所以退化形态是「慢一点」而**不是**「内容错」。
       为守住这条，验收探针里有一条断言：back-end 里 `set_sidebar_payload` /
       `begin_sidebar_action` / `set_sidebar_result` / `clear_sidebar_payload` 的调用点
       必须**恰好**落在登记过的那两个文件里（新增调用点会让探针变红，逼人回来登记）。

=================== 事件词表（**只有四个**，改词表要同步前端） ===================
  token ：盒子正文在增长（一段 delta）→ 客户端**拉一次盒子**
  done  ：盒子状态发生**权威变更**（选区重置 / 开始生成 / 正常结束）→ 客户端拉一次盒子
  error ：本轮以错误结束 → 客户端拉一次盒子
  meta  ：连接级（**只在订阅成功时**发给该订阅者）→ 客户端重置重连退避

⚠️⚠️ `token` 的 `t` 字段只是**给人读 / 给探针断言用**，客户端**绝不许**拿它自己拼答案：
   一拼就出现第二份真相，「看着流出来的」与「最后停住的」可能不是同一段文字，且无从复现。
   正文的唯一真相永远是**盒子**（`GET /browser/sidebar/payload`）。
⚠️ `done` 与 `error` 都**不带正文**，同理。

=================== 线程模型 ===================
  · `publish_*` 可能跑在 **Qt 事件循环**里（页面推送 → `browser_actions.record_selection`
    那条路）→ **一律非阻塞**：队列满就**丢事件 + 计数**，绝不 `put()` 等待。
    丢一条 token 只是少刷一帧（轮询会补），阻塞一下则是整个界面卡住。
  · `append_sidebar_delta` 跑在**动作 worker 线程**里（`browser_actions._worker`）。
"""
from __future__ import annotations

import queue
import threading

from . import browser_host

__all__ = [
    "EVENTS", "SUB_QUEUE_MAX", "MAX_SUBS", "STREAM_HEARTBEAT_S",
    "subscribe_sidebar_stream", "unsubscribe_sidebar_stream", "subscriber_count",
    "stream_stats", "publish_sidebar_event", "publish_round", "append_sidebar_delta",
]

EVENTS = ("token", "done", "error", "meta")
"""合法事件名。词表外的名字一律**收敛成 `done`** —— 宁可让客户端多拉一次盒子，也不许漏。"""

SUB_QUEUE_MAX = 256
"""单个订阅者的队列上限。取值依据：一次动作的 token 数是百量级、而消费者是「取一次盒子」，
   所以客户端消费速率远高于生产；256 是给「客户端短暂卡住」留的余量，不是为了缓冲正常流。"""

MAX_SUBS = 8
"""同时允许的订阅者上限（= 同时打开的侧栏数 × 重连重叠）。

⚠️ 每个订阅者是一个队列 + 一条 SSE 长连接（路由侧用的是 **async** 生成器，不占线程池线程）。
   不设上限时，「客户端反复重连但每次都注销不掉」会把名额吃满：
   之后所有新订阅都拿 503 → 流式**静默退化**成 1.5s 轮询（没有报错、没有日志，最难归因）。
   设了上限，退化是**有界**的：最多 8 条，且 `/sidebar/stream/stats` 的 `subs` 一眼可见。
"""

STREAM_HEARTBEAT_S = 15.0
"""服务端心跳间隔。客户端（`utils/sse.js`）不解析注释行，故心跳只用于「尽快发现对端已死」：
   写失败会抛异常 → 生成器结束 → `finally` 注销订阅者。不设心跳时，半死的连接会一直占着订阅名额。"""

# ---------------------------------------------------------------------------
# 订阅者注册表
# ---------------------------------------------------------------------------
_subs_lock = threading.Lock()
_subs: list[queue.Queue] = []       # 保持插入序（探针要按序断言）
_rev = 0
_published = 0
_dropped = 0


def subscribe_sidebar_stream() -> queue.Queue:
    """注册一个订阅者，返回它的队列（已预置一条 `meta`）。

    ⚠️ `meta` **只发给新订阅者**（不是广播）：它的语义是「你连上了，当前 rev 是 X」,
       客户端据此重置重连退避。广播出去会让其它订阅者误以为自己也重连了一次。
    ⚠️ 超上限抛 `RuntimeError("too_many_streams")`：路由层转成 **503**（环境/资源问题，
       不是调用方问题）—— 与 `host_unavailable` 同款语义，客户端只需退避重连。
    """
    q: queue.Queue = queue.Queue(maxsize=SUB_QUEUE_MAX)
    with _subs_lock:
        if len(_subs) >= MAX_SUBS:
            raise RuntimeError("too_many_streams")
        _subs.append(q)
        n, rev = len(_subs), _rev
    _put(q, {"event": "meta", "rev": rev, "subs": n})
    return q


def unsubscribe_sidebar_stream(q) -> int:
    """注销订阅者，返回**剩余订阅者数**（探针靠它断言「注销归零」，不要另写一个计数器）。"""
    with _subs_lock:
        try:
            _subs.remove(q)
        except ValueError:
            pass            # 重复注销是正常路径（路由的 finally + 客户端断开可能同时发生）
        return len(_subs)


def subscriber_count() -> int:
    with _subs_lock:
        return len(_subs)


def stream_stats() -> dict:
    """订阅面只读状态（**键恒定**，可逐字比对）。`dropped > 0` 说明客户端消费不过来。"""
    with _subs_lock:
        return {"subs": len(_subs), "rev": _rev, "published": _published,
                "dropped": _dropped, "max_subs": MAX_SUBS, "queue_max": SUB_QUEUE_MAX}


def _put(q, evt) -> bool:
    """向一个订阅者投递（**非阻塞**）。返回是否投递成功。"""
    global _dropped
    try:
        q.put_nowait(evt)
        return True
    except queue.Full:
        # ⚠️ 见模块头「线程模型」：这里绝不能阻塞（可能跑在 Qt 事件循环里）
        with _subs_lock:
            _dropped += 1
        return False
    except BaseException:                                   # noqa: BLE001
        return False


def publish_sidebar_event(event, **fields) -> int:
    """把一条事件广播给所有订阅者，返回**成功投递数**。

    ⚠️ 本函数**永不抛异常**：调用它的地方包括 Qt 事件循环（选区推送）与 worker 线程，
       抛出去会变成「动作跑了但界面停住」这类无从定位的现象。
    ⚠️ 没有订阅者时**也要**推进 `rev`：否则「rev 递增」这条判据会在「先跑动作再订阅」
       的场景下失去分辨力（探针会先订阅再动作，但两类顺序都必须自洽）。
    """
    global _rev, _published
    ev = str(event or "")
    if ev not in EVENTS:
        ev = "done"
    try:
        with _subs_lock:
            _rev += 1
            _published += 1
            rev = _rev
            targets = list(_subs)
        if not targets:
            return 0
        evt = {"event": ev, "rev": rev}
        evt.update(fields)
        n = 0
        for q in targets:
            if _put(q, dict(evt)):      # 每个订阅者拿到**独立副本**：消费方丢弃字段不许影响别人
                n += 1
        return n
    except BaseException:                                   # noqa: BLE001
        return 0


def publish_round(gen=0, status="", reason="") -> int:
    """发布一条**状态变更**事件（选区重置 / 清空 / 开始 / 一轮结束）。

    :param gen:   与本次动作配对的代号；`0` = 与某个具体动作无关（选区变更 / 清空）。
    :param status: 盒子此刻的 `status`（`"running"` / `"done"` / `"error"` / `"idle"`）。
    :param reason: 归因（`"selection"` / `"clear"` / `"begin"` / `"result"` / `"ask"`），
                   只为**人读与探针断言**，客户端不得据此分支。

    ⚠️ `status == "error"` → 发 `error` 事件；其余一律 `done`。
       不新造第三种「状态事件」：词表越短，客户端越难写错。
    """
    ev = "error" if str(status or "") == "error" else "done"
    return publish_sidebar_event(ev, gen=int(gen or 0), status=str(status or ""),
                                 reason=str(reason or ""))


def append_sidebar_delta(gen, text) -> bool:
    """把一段增量**写进盒子**并发一条 `token`。返回是否写入成功。

    ⚠️⚠️ `gen` 的裁决权**只在数据面**（`browser_host.set_sidebar_result()` 的返回值）。
        本函数**不另判一次** —— 两处判 gen 迟早分叉，而分叉的症状是
        「旧答案盖在新选区上」，不报错、只错内容，是本项目最贵的一类 bug。
    ⚠️ 读—改—写不是原子操作，但同一个 `gen` **只有一个** worker 线程在写
        （`gen` 本身就是「作废在飞回调」的那把锁的代号），故不会互相覆盖。
    ⚠️ 过期 `gen` → **不写、不发**（连 token 都不发）：发了会让客户端白拉一次，
        更糟的是让探针的「事件序」看起来像有新一轮开始了。
    """
    t = str(text or "")
    if not t:
        return False
    cur = browser_host.sidebar_payload()
    total = str(cur.get("result") or "") + t
    if not browser_host.set_sidebar_result(gen, "running", result=total):
        return False
    publish_sidebar_event("token", gen=int(gen or 0), t=t, chars=len(total))
    return True
