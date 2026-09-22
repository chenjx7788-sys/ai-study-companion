# -*- coding: utf-8 -*-
"""WP16：选区动作编排 —— 「页面上点了什么」→「侧栏里显示什么」。

=================== 分工（**不要跨层**） ===================
  · `browser_inject`   ：页面侧（浮动工具栏 / QWebChannel 桥 / 载荷序列化）
  · `browser_panel`    ：Qt 侧（注入、桥接线、普通值盒子 `_sel`）
  · `browser_actions`  ：**本模块** —— 唯一把「选区事件」接到 LLM 的地方
  · `browser_host`     ：数据面（侧栏盒子 + 动作代号 `gen`）
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

⚠️ 本模块**顶层不 import Qt**（后端必须能在无 Qt 环境 import 它，验收脚本才能跑数据面）。
"""
from __future__ import annotations

import threading

from . import browser_host
from . import llm

__all__ = [
    "ACTION_LABELS", "ACTION_ORDER", "MAX_SELECTION_CHARS",
    "action_label", "is_action", "on_selection_event",
    "record_selection", "start_action",
]

ACTION_LABELS = {"explain": "解释", "summarize": "总结", "quiz": "出题"}
ACTION_ORDER = ("explain", "summarize", "quiz")

MAX_SELECTION_CHARS = 6000
"""送去 LLM 的选区上限。

依据：选区是「一段划线」，不是整篇文档。但用户**可以**全选整页 ——
那种情况下必须截断，且**如实告诉模型/用户**（见 `_worker` 的尾部提示），
不能悄悄截了却让答案看起来像覆盖了全文。
"""


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
    payload, truncated = _clip_for_llm(sel)
    th = threading.Thread(
        target=_worker, args=(gen, action, payload, url, title, truncated),
        name="asc-wp16-act-%s" % action, daemon=True)
    th.start()
    return {"ok": True, "error": None, "action": action, "gen": gen,
            "status": "running", "truncated": truncated}


def _worker(gen, action, selection, url, title, truncated):
    """后台线程：调 LLM → 写回侧栏盒子。**本模块唯一允许做慢活的地方。**

    ⚠️ 失败也要写盒子（`status="error"`）：只把异常记进日志的话，
       界面会永远停在「正在生成…」—— 用户无法区分「慢」和「已经死了」。
    ⚠️ 写回一律带 `gen`；`browser_host.set_sidebar_result()` 内部会丢弃过期的那些。
    """
    try:
        text = llm.browser_selection_action(action, selection, title=title, url=url)
    except BaseException as e:
        browser_host.set_sidebar_result(
            gen, "error", error="%s: %s" % (type(e).__name__, e))
        return
    body = str(text or "").strip()
    if not body:
        browser_host.set_sidebar_result(gen, "error", error="empty_answer")
        return
    if truncated:
        body += ("\n\n> 选中的文字较长，本次%s只用了前 %d 字。"
                 % (action_label(action), MAX_SELECTION_CHARS))
    browser_host.set_sidebar_result(gen, "done", result=body)
