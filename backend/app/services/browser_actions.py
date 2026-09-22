# -*- coding: utf-8 -*-
"""WP16：选区动作编排 —— 「页面上点了什么」→「侧栏里显示什么」。

=================== 分工（**不要跨层**） ===================
  · `browser_inject`   ：页面侧（浮动工具栏 / QWebChannel 桥 / 载荷序列化）
  · `browser_panel`    ：Qt 侧（注入、桥接线、普通值盒子 `_sel`）
  · `browser_actions`  ：**本模块** —— 唯一把「选区事件」接到 LLM 的地方
  · `browser_host`     ：数据面（侧栏盒子 + 动作代号 `gen`）
  · `sidebar_stream`   ：侧栏盒子的**流式扇出**（订阅者注册表 + 事件广播）——
                         它本该长在 `browser_host` 里，被外部文件锁逼出来成了独立模块
  · `routers/browser`  ：HTTP 形状

=================== 三条硬约束 ===================
① ⚠️⚠️ `on_selection_event()` 跑在 **Qt 事件循环**里（桥回调 → `_on_live_payload`）。
   它只许做两件事：**写普通值**、**起线程**。绝不许调 LLM / 写库 / 任何阻塞 IO ——
   否则用户一选中文字整个界面就卡住，而症状完全不像「这里的问题」。
   （`_worker` 是唯一允许做慢活的地方，它在**普通线程**里。）

② ⚠️⚠️ 每次动作都要先 `begin_sidebar_action()` 拿 `gen`，回来只认自己那次的 `gen`。
   缺了它，用户连点两次时「先发后到」的回调会把旧答案盖在新选区上 ——
   **不报错、只错内容**，是本项目最贵的一类 bug。

③ ⚠️ 空选区不许发起动作：页面侧 `__asc_fire` 已经拦过一次，但那只是**界面便利**；
   数据面必须自己再拦一次 —— HTTP 接口可以被直接调用，不能依赖页面脚本还在。

④ ⚠️⚠️ 追问（`start_ask`）与三个动作**共用同一个 `gen`**，但**绝不进** `ACTION_ORDER`。
   两件事都不是「顺手」的区别：
     · 共用 gen：分两套代号的话，「动作 + 追问」并发时旧答案会盖住新答案（不报错、只错内容）。
     · 不进 ACTION_ORDER：页面侧浮动工具栏是**按 ACTION_ORDER 渲染按钮**的，
       加一个 key 就会在**网页里**多出一个「追问」按钮（网页上看不到答案，追问毫无意义），
       同时直接打红 WP16「工具栏三按钮 / acts=3」那条判据。

⚠️ 本模块**顶层不 import Qt**（后端必须能在无 Qt 环境 import 它，验收脚本才能跑数据面）。
"""
from __future__ import annotations

import threading

from . import browser_host
from . import llm
from . import sidebar_stream

__all__ = [
    "ACTION_LABELS", "ACTION_ORDER", "MAX_SELECTION_CHARS",
    "action_label", "is_action", "on_selection_event",
    "record_selection", "start_action",
    "ASK_ACTION", "ASK_QUESTION_MAX", "MAX_HISTORY_MSGS", "MAX_HISTORY_CHARS",
    "clip_history", "start_ask",
]

ACTION_LABELS = {"explain": "解释", "summarize": "总结", "quiz": "出题"}
ACTION_ORDER = ("explain", "summarize", "quiz")

MAX_SELECTION_CHARS = 6000
"""送去 LLM 的选区上限。

依据：选区是「一段划线」，不是整篇文档。但用户**可以**全选整页 ——
那种情况下必须截断，且**如实告诉模型/用户**（见 `_worker` 的尾部提示），
不能悄悄截了却让答案看起来像覆盖了全文。
"""

ASK_ACTION = "ask"
"""追问在**盒子**里的 `action` 值。**不是** `ACTION_LABELS` 的一员（见模块头 ④）。

⚠️ 它会被写进 `_sidebar["action"]`（`begin_sidebar_action` 的入参），作用是让侧栏那三个
   动作按钮**都不高亮** —— 追问进行中没有任何一个动作是「当前动作」，这比高亮错的那个好。
"""

MAX_HISTORY_MSGS = 12
"""追问历史保留的**条数**上限。

⚠️ 前端 `BrowserSidebarView.vue` 的 `MAX_HISTORY` 必须与本值一致：历史由前端携带，
   两边上限不同会出现「前端留着 12 条、后端只用最后 8 条」这种查不出来的偏差。
"""

MAX_HISTORY_CHARS = 4000
"""追问历史**单条**字符上限（一条助手答案可能上千字，多轮之后会撑爆上下文）。"""

ASK_QUESTION_MAX = 1000
"""单次追问的问题长度上限（与 `ephemeral` 的 `ASK_QUESTION_MAX` 同量级）。"""


def action_label(action):
    """动作 → 中文名。未知动作返回空串（**不猜**）。"""
    return ACTION_LABELS.get(str(action or ""), "")


def is_action(action):
    return str(action or "") in ACTION_LABELS


def _clip_for_llm(text):
    """返回 `(要送去的文本, 是否截断)`。"""
    t = str(text or "")
    if len(t) <= MAX_SELECTION_CHARS:
        return t, False
    return t[:MAX_SELECTION_CHARS], True


# ---------- 面板钩子入口（Qt 事件循环） ----------

def on_selection_event(tab_id, box):
    """面板钩子（**Qt 主线程 / 事件循环**）。返回 `'ignored' | 'same' | 'recorded' | 'started'`。

    :param tab_id: 事件来自哪个标签（只用于归因；动作一律作用于「当前」选区）
    :param box: `browser_panel._on_live_payload()` 产出的普通值 dict

    ⚠️ 本函数**绝不抛异常**：它在事件循环里被调，抛出去会变成无从定位的 Qt 堆栈，
       而调用点（`_on_live_payload`）本来就把异常吞了 —— 结果是「什么都没发生」。
    """
    try:
        b = box or {}
        kind = str(b.get("kind") or "")
        sel = str(b.get("sel") or "")
        url = str(b.get("url") or "")
        title = str(b.get("title") or "")
        if kind == "act":
            act = b.get("act")
            if not is_action(act) or not sel.strip():
                return "ignored"
            start_action(act, sel, url=url, title=title)
            return "started"
        if kind == "sel":
            return record_selection(sel, url=url, title=title)
        return "ignored"
    except BaseException:
        return "ignored"


def record_selection(selection, url="", title=""):
    """只记「选中了什么」（**预览路**，不调 LLM）。返回 `'same' | 'recorded'`。

    ⚠️ 同一次选区**重复推送不动**：页面侧的 `selectionchange` 会反复触发，
       每次都重置盒子会把「已经跑完的答案」清掉 ——
       用户看到的现象是「答案一闪就没了」，且完全指向不到这里。
    """
    cur = browser_host.sidebar_payload()
    sel = str(selection or "")
    if sel and cur.get("selection") == sel and cur.get("url") == str(url or ""):
        return "same"
    browser_host.set_sidebar_payload(url=url, title=title, selection=sel)
    # WP17：选区一变，盒子里的结果就被作废了 → 必须**立刻**告诉订阅者，
    # 否则侧栏会拿上一段的答案配新文字，直到 1.5s 轮询才纠正。
    sidebar_stream.publish_round(status="idle", reason="selection")
    return "recorded"


# ---------- 动作 ----------

def start_action(action, selection, url="", title=""):
    """发起一次动作：**记录选区 → 取 gen → 起后台线程**。返回 dict（可直接当 HTTP 响应体）。

    ⚠️ 「记录选区」与「取 gen」必须**成对**（先 `set` 后 `begin`）：
       `set` 把上一次的结果作废、`begin` 拿到严格递增的新代号。
       只做一半就会出现「新选区配旧答案」或「旧回调盖新结果」。
    ⚠️ 返回值里**不要**放 `result`：动作是异步的，此刻还没有结果 ——
       放一个空串进去，前端会当成「跑完了但没内容」。
    """
    if not is_action(action):
        return {"ok": False, "error": "bad_action", "action": str(action or ""),
                "gen": 0, "status": "idle", "truncated": False}
    sel = str(selection or "")
    if not sel.strip():
        return {"ok": False, "error": "empty_selection", "action": str(action or ""),
                "gen": 0, "status": "idle", "truncated": False}

    browser_host.set_sidebar_payload(url=url, title=title, selection=sel, action=action)
    gen = browser_host.begin_sidebar_action(action)
    # WP17：让订阅者立刻去拉一次盒子（「正在生成…」要马上出现，不等轮询）
    sidebar_stream.publish_round(gen=gen, status="running", reason="begin")
    payload, truncated = _clip_for_llm(sel)
    th = threading.Thread(
        target=_worker, args=(gen, action, payload, url, title, truncated),
        name="asc-wp16-act-%s" % action, daemon=True)
    th.start()
    return {"ok": True, "error": None, "action": action, "gen": gen,
            "status": "running", "truncated": truncated}


def _fail_sidebar(gen, error, reason):
    """把一次失败如实写进盒子**并广播**（两件事必须成对，缺一个界面就会一直「正在生成…」）。

    ⚠️ 只写盒子不广播 = 界面上要再等一轮轮询才发现失败；只广播不写 = 订阅者拉回来还是旧态，
       白拉一次且看起来像「事件丢了」。
    """
    browser_host.set_sidebar_result(gen, "error", error=error)
    sidebar_stream.publish_round(gen=gen, status="error", reason=reason)


def _finish_sidebar(gen, body, reason):
    """把最终正文写进盒子并广播「本轮结束」。"""
    browser_host.set_sidebar_result(gen, "done", result=body)
    sidebar_stream.publish_round(gen=gen, status="done", reason=reason)


def _stream_into_sidebar(gen, chunks):
    """边读增量边写盒子（+广播 `token`），返回拼起来的总文本（**未 strip**）。

    ⚠️ 写的是 `sidebar_stream.append_sidebar_delta()`：它内部**一次**同时做两件事
       （写盒子 + 发 token），所以「盒子里已有多少」与「订阅者看到多少」结构上不会分叉。
    ⚠️ 返回值**不做 strip / 不加后缀**：加工口径留给调用方，否则会出现
       「最后流出来的」与「最终落定的」差几个字符（且这种差异只在长答案尾部才看得见）。
    """
    acc = []
    for c in chunks:
        t = str(c or "")
        if not t:
            continue
        acc.append(t)
        sidebar_stream.append_sidebar_delta(gen, t)
    return "".join(acc)


def _worker(gen, action, selection, url, title, truncated):
    """后台线程：调 LLM（**流式**）→ 边出边写侧栏盒子。**本模块唯一允许做慢活的地方。**

    ⚠️ 失败也要写盒子（`status="error"`）：只把异常记进日志的话，
       界面会永远停在「正在生成…」—— 用户无法区分「慢」和「已经死了」。
    ⚠️ 写回一律带 `gen`；`browser_host.set_sidebar_result()` 内部会丢弃过期的那些。
    ⚠️ 流式**一个 content 都没拿到**时退回一次性调用：某些上游配置下流式会静默只回
       reasoning（`llm.browser_selection_stream` 只 yield content）→ 不退回的话，
       侧栏会从「能用」变成「永远 empty_answer」，而症状完全指向不到流式这一层。
       正常路径不可能走到这里（有内容就不退），故不会偷偷多花一次调用。
    """
    try:
        raw = _stream_into_sidebar(
            gen, llm.browser_selection_stream(action, selection, title=title, url=url))
        if not raw:
            raw = str(llm.browser_selection_action(
                action, selection, title=title, url=url) or "")
    except BaseException as e:
        _fail_sidebar(gen, "%s: %s" % (type(e).__name__, e), "result")
        return
    body = raw.strip()
    if not body:
        _fail_sidebar(gen, "empty_answer", "result")
        return
    if truncated:
        body += ("\n\n> 选中的文字较长，本次%s只用了前 %d 字。"
                 % (action_label(action), MAX_SELECTION_CHARS))
    _finish_sidebar(gen, body, "result")


# ---------- WP17：追问（**不是第四个动作**） ----------
# ⚠️⚠️ 三条与三个动作**刻意不同**的地方，每一处都有代价，别顺手「统一」掉：
#   ① 不进 `ACTION_ORDER` / `ACTION_LABELS`：见模块头 ④（页面工具栏是按 ORDER 渲染的）。
#   ② **不调 `set_sidebar_payload()`**：追问的调用方只带 `question` 与 `history`，
#      走 set 会把 `url / title / selection` 一起重写成空串 —— 症状是「问完之后侧栏
#      头部的标题和地址全没了、选中内容也空了」，而那看起来完全不像追问的问题。
#      只调 `begin_sidebar_action()`：它正好只动 `gen / action / status / result / error`。
#   ③ 共用同一个 `gen`（同一把防串场锁）：动作与追问互相作废在飞的回调 —— 这是**对的**。
#      「解释」跑到一半又追问，用户要看的是追答，不是那个已经过时的解释。


def clip_history(history):
    """把前端送来的追问历史裁到上限；返回**新列表**（不改调用方的对象）。

    ⚠️ 只做两件事：**条数上限**、**单条长度上限**。
       **角色白名单在 `llm.browser_ask_messages()`** —— 那是这条链上唯一的清洗点，
       这里再筛一遍角色就是第二套口径，迟早分叉（而分叉的症状是「有的轮次被静默丢掉」）。
    ⚠️ 超限**从最旧的一端丢**：追问是「接着上文」，丢最近的会答非所问。
    """
    out = []
    for m in (history or []):
        if not isinstance(m, dict):
            continue
        content = str(m.get("content") or "")
        if not content.strip():
            continue
        out.append({"role": str(m.get("role") or ""), "content": content[:MAX_HISTORY_CHARS]})
    return out[-MAX_HISTORY_MSGS:]


def _worker_ask(gen, question, history, selection, url, title, truncated):
    """后台线程：流式追问 → 边出边写侧栏盒子。与 `_worker` 同构，只是入口与 kind 不同。"""
    try:
        raw = _stream_into_sidebar(
            gen, llm.browser_ask_stream(selection, history, question, title=title, url=url))
        if not raw:
            # 同 `_worker`：流式静默无输出 → 退回一次性调用（否则会变成「永远空答案」）
            raw = str(llm.chat(
                llm.browser_ask_messages(selection, history, question,
                                         title=title, url=url),
                kind="browser_ask") or "")
    except BaseException as e:
        _fail_sidebar(gen, "%s: %s" % (type(e).__name__, e), "ask")
        return
    body = raw.strip()
    if not body:
        _fail_sidebar(gen, "empty_answer", "ask")
        return
    if truncated:
        # ⚠️ 与三个动作同款：截断了就必须说出来（不许让答案看起来像覆盖了整段选区）
        body += "\n\n> 选中的文字较长，本次追问只用了前 %d 字。" % MAX_SELECTION_CHARS
    _finish_sidebar(gen, body, "ask")


def start_ask(question, history=None, selection=None, url="", title=""):
    """发起一次追问：**取 gen → 起后台线程**。返回 dict（可直接当 HTTP 响应体）。

    ⚠️ `error` 三种必须分开：`empty_selection`（没选中东西）/ `empty_question`（没问）/
       `bad_question`（问得太长被截）—— 前两种界面提示完全不同，合成一个 `invalid` 就没法提示。
    ⚠️ 返回值里**不放 `result`**：追问是异步的，放个空串进去前端会当成「跑完了但没内容」。
    """
    cur = browser_host.sidebar_payload()
    sel = str(selection if selection is not None else (cur.get("selection") or ""))
    if not sel.strip():
        return {"ok": False, "error": "empty_selection", "action": ASK_ACTION,
                "gen": 0, "status": "idle", "truncated": False}
    q = str(question or "").strip()
    if not q:
        return {"ok": False, "error": "empty_question", "action": ASK_ACTION,
                "gen": 0, "status": "idle", "truncated": False}
    q = q[:ASK_QUESTION_MAX]
    url = str(url or cur.get("url") or "")
    title = str(title or cur.get("title") or "")
    payload, truncated = _clip_for_llm(sel)
    hist = clip_history(history)
    gen = browser_host.begin_sidebar_action(ASK_ACTION)
    sidebar_stream.publish_round(gen=gen, status="running", reason="ask")
    th = threading.Thread(
        target=_worker_ask, args=(gen, q, hist, payload, url, title, truncated),
        name="asc-wp17-ask", daemon=True)
    th.start()
    return {"ok": True, "error": None, "action": ASK_ACTION, "question": q,
            "gen": gen, "status": "running", "history_len": len(hist),
            "truncated": truncated}
