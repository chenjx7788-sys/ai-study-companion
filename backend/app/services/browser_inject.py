# -*- coding: utf-8 -*-
"""WP16：选区注入 —— 浮动工具栏 + QWebChannel 桥（页面侧）。

本模块只负责**页面侧**的事：把工具栏与桥送进第三方页面、把页面上发生的事
序列化成一段 JSON 交回 Python。**不碰 Qt 控件、不碰路由、不碰 LLM。**

=================== 四条设计依据（都有实测） ===================

① **桥选 `QWebChannel`，不是偏好，是实测结论**（2026-09-21 · `_probe_qwebchannel.py` 10/10）：
   · `:/qtwebchannel/qwebchannel.js` **就在 Qt 资源里**（16510 B）→ 可离线，不必走 CDN
     （走 CDN 等于「没网就没工具栏」，且会给第三方站点一个外部请求 —— 与 N04 口径冲突）；
   · 打包 spec 的 `_QT_HIDDEN`（`backend/AIStudyCompanion.spec`）**早就收了**
     `PySide6.QtWebChannel` → 不需要改打包配置；
   · 瘦身脚本不裁它。

② **两条采集路同时存在，且**「采集失败」与「采集成功但桥不通」**必须可区分**：
   页面脚本先试 `window.__asc_bridge.report(...)`；桥不在就落 `window.__asc_sel`，
   由 Python 按 WP15 那套「发起 → 轮询」两步式拉取。
   ⇒ 只留一条路的话，用户看到的现象都是「点了没反应」，而真因完全不同。

③ **注入必须幂等 + 每次导航后重注**：整页导航是**新的 JS 执行上下文**，
   上一次注入的 `window.__asc_tb` 与节点都不在了。`loadFinished` 后重注是**必需**，
   不是优化。幂等靠 `window.__asc_tb.v === INJECT_VER` 与「先删旧节点」。

④ **页面脚本不读任何 Python/Qt 状态**：它只认全局名（`ascBridge` / `__asc_sel` /
   `data-asc-act`）。这样验收脚本可以在**任何页面**上独立复现，不必起 Qt。

⚠️ 本模块**顶层不 import Qt**（后端必须能在无 Qt 环境 import 它）；
   所有 Qt 符号都走惰性函数。
"""
from __future__ import annotations

import json

__all__ = [
    "INJECT_VER", "BRIDGE_NAME", "TB_NODE_ID",
    "inject_js", "snapshot_js", "probe_js", "make_bridge", "webchannel_cls",
    "parse_payload", "empty_selection",
]

# ⚠️ 3：`push()` 改为「桥成功时同时写 W[TB].path」（此前只写 W[BS].path，
#    导致 probe_js 的 path 在桥成功时恒为 null）。JS 本体变了 → 必须 bump，
#    否则已注入的长活页面会因版本守卫提前返回、继续跑旧副本。
INJECT_VER = 3
BRIDGE_NAME = "ascBridge"
TB_NODE_ID = "asc-tb-x"

# 与既有验收/探针共用的全局名（**不要改名** —— 探针 `asc_wp16_site_probe.py` 与
# WP16 验收脚本都按这三个名字取状态）。
G_TB = "__asc_tb"          # 工具栏状态对象
G_SEL = "__asc_sel"        # 兜底投放盒（JSON 字符串）
G_BSTATE = "__asc_bstate"  # 桥初始化状态：init / ok / no_qwc / no_transport / no_obj / throw

_ACTIONS = (("explain", "解释"), ("summarize", "总结"), ("quiz", "出题"))

# ---------------- 页面侧脚本（原样字符串，不做 % 格式化 —— 避免与 JS 里的 % 撞车） ----


def _toolbar_js() -> str:
    """浮动工具栏（**候选生产形态**，在真机上 6/6 站点注入成功）。

    逐条对应需求：
      A01 选中即浮出，三个动作（解释 / 总结 / 出题）；
      U02 可拖动（拖过之后不再跟着选区跳，否则用户刚拖好就被拽回去）；
      选中文本变化 → 经桥**顺带**报一次 `kind:"sel"`（防抖 350ms）→ 侧栏可即时预览；
      ⚠️ 拖动后位置**记住**（`pos` 非空即不再自动定位），这是 U02 的判据要点。
    """
    return r"""
(function(){
  var V = __ASC_VER__, TB = '__ASC_TB__', BS = '__ASC_BS__', SELB = '__ASC_SEL__', NID = '__ASC_NID__';
  var W = window;
  if (W[TB] && W[TB].v === V) { return 'asc-tb-ok'; }
  var old = document.getElementById(NID);
  if (old && old.parentNode) { old.parentNode.removeChild(old); }
  W[TB] = {v:V, sel:'', vis:false, path:null, fired:null, err:null, pos:null, pushed:''};

  var box = document.createElement('div');
  box.id = NID;
  box.setAttribute('data-asc','1');
  box.style.cssText = 'position:fixed;z-index:2147483647;left:0;top:0;display:none;' +
    'background:#fff;border:1px solid #d8d8d8;border-radius:8px;' +
    'box-shadow:0 4px 16px rgba(0,0,0,.16);padding:3px;' +
    'font:13px/1.4 system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;' +
    'user-select:none;-webkit-user-select:none;';
  var ACTS = __ASC_ACTS__;
  for (var i = 0; i < ACTS.length; i++) {
    (function(p){
      var b = document.createElement('button');
      b.type = 'button';
      b.textContent = p.t;
      b.setAttribute('data-asc-act', p.a);
      b.style.cssText = 'margin:0 2px;padding:3px 9px;cursor:pointer;' +
        'border:1px solid #e3e3e3;background:#fafafa;border-radius:6px;color:#1f1f1f;font-size:12.5px;';
      b.onmousedown = function(ev){ if (ev && ev.stopPropagation) ev.stopPropagation(); };
      b.onclick = function(ev){
        if (ev && ev.stopPropagation) ev.stopPropagation();
        W['__asc_fire'](p.a);
      };
      box.appendChild(b);
    })(ACTS[i]);
  }
  (document.body || document.documentElement).appendChild(box);

  function sel_text(){
    try { return W.getSelection ? String(W.getSelection().toString()) : ''; }
    catch (e) { return ''; }
  }
  function push(kind, act){
    var payload = JSON.stringify({
      kind: kind, act: act || null, sel: W[TB].sel,
      url: String(location.href || ''), title: String(document.title || ''),
      ts: Date.now()
    });
    W[SELB] = payload;                       // 兜底：Python 可来拉
    if (W[BS]) {
      // ⚠️⚠️ **两处都要写**：`W[BS].path` 是桥对象上的记录，而 `probe_js()` 回读的是
      //    `W[TB].path`。只写前者 → 页面侧「走了哪条路」在**桥成功时恒为 null**，
      //    取值集合只剩 'poll' 与 null（'bridge' 永不出现），与 probe_js 的契约不符。
      //    后果：用 path 区分两条路的判据会得出**相反结论**（实测 verify_wp16_inject I5/I9）。
      W[BS].path = 'bridge';
      W[TB].path = 'bridge';
      try { W[BS].report(payload); return 'bridge'; } catch (e) { W[BS].err = String(e); }
    }
    W[TB].path = 'poll';
    return 'poll';
  }
  W['__asc_fire'] = function(action){
    var t = (W[TB].sel || '').trim();
    if (!t) { W[TB].err = 'empty_selection'; return 'empty'; }
    W[TB].fired = action;
    push('act', action);
    return W[TB].path;
  };
  W['__asc_push_sel'] = function(){ return push('sel', null); };

  var drag = null;
  box.onmousedown = function(ev){
    if (ev.button !== 0) { return; }
    drag = {x: ev.clientX, y: ev.clientY, l: box.offsetLeft, t: box.offsetTop};
    if (ev.preventDefault) ev.preventDefault();
  };
  document.addEventListener('mousemove', function(ev){
    if (!drag) { return; }
    var nx = drag.l + (ev.clientX - drag.x), ny = drag.t + (ev.clientY - drag.y);
    W[TB].pos = {x: Math.max(2, nx), y: Math.max(2, ny)};
    box.style.left = W[TB].pos.x + 'px';
    box.style.top = W[TB].pos.y + 'px';
  }, true);
  document.addEventListener('mouseup', function(){ drag = null; }, true);

  function place(r){
    if (W[TB].pos) { box.style.left = W[TB].pos.x + 'px'; box.style.top = W[TB].pos.y + 'px'; return; }
    if (!r) { return; }
    var vw = W.innerWidth || 800;
    box.style.left = Math.max(4, Math.min(r.left, vw - 220)) + 'px';
    box.style.top = Math.max(4, r.top - 42) + 'px';
  }
  function refresh(){
    var t = sel_text().trim();
    W[TB].sel = t;
    if (!t) { box.style.display = 'none'; W[TB].vis = false; W[TB].pushed = ''; return; }
    var r = null;
    try {
      var s = W.getSelection();
      if (s && s.rangeCount) { r = s.getRangeAt(0).getBoundingClientRect(); }
    } catch (e) { r = null; }
    box.style.display = 'block';
    W[TB].vis = true;
    place(r);
    if (t !== W[TB].pushed) {
      W[TB].pushed = t;
      W[TB].tmr && clearTimeout(W[TB].tmr);
      W[TB].tmr = setTimeout(function(){ if (W[TB].sel === t) { W['__asc_push_sel'](); } }, 350);
    }
  }
  document.addEventListener('mouseup', refresh, true);
  document.addEventListener('selectionchange', refresh, true);
  // 首帧就跑一次：某些页面（PDF 查看器 / 阅读模式）不发 mouseup
  setTimeout(refresh, 0);
  return 'asc-tb-ok';
})()
"""


def _bridge_js() -> str:
    """桥初始化。**必须在工具栏之前跑**（工具栏的 `push()` 直接看 `window.__asc_bridge`）。

    ⚠️ `new QWebChannel(...)` 的回调是**异步**的 → 回调返回前 `__asc_bridge` 还不存在，
       此时点击会走轮询兜底。这是**设计允许**的（两条路都通），不是 bug。
    """
    return r"""
(function(){
  var BS = '__ASC_BS__', SELB = '__ASC_SEL__', NAME = '__ASC_BN__';
  if (typeof QWebChannel === 'undefined') { window[BS] = null; window['__asc_bstate'] = 'no_qwc'; return 'no_qwc'; }
  if (!window.qt || !window.qt.webChannelTransport) { window[BS] = null; window['__asc_bstate'] = 'no_transport'; return 'no_transport'; }
  try {
    new QWebChannel(qt.webChannelTransport, function(ch){
      window[BS] = (ch && ch.objects) ? (ch.objects[NAME] || null) : null;
      window['__asc_bstate'] = window[BS] ? 'ok' : 'no_obj';
    });
    window['__asc_bstate'] = 'init';
    return 'init';
  } catch (e) {
    window[BS] = null;
    window['__asc_bstate'] = 'throw:' + e;
    return 'throw';
  }
})()
"""


def _qwc_source() -> str:
    """读 `:/qtwebchannel/qwebchannel.js`（Qt 资源，**离线可用**）。读不到返回空串。

    ⚠️ 读不到时**不抛**：调用方会把「无 qwebchannel.js」记进 `bridge_error`，
       工具栏照旧注入（走轮询兜底）—— 「桥不通」不该让整个功能不可用。
    """
    try:
        from qtpy.QtCore import QFile, QIODevice
    except BaseException:
        return ""
    try:
        f = QFile(":/qtwebchannel/qwebchannel.js")
        if not f.open(QIODevice.ReadOnly):
            return ""
        try:
            data = bytes(f.readAll().data())
        finally:
            f.close()
        return data.decode("utf-8", "replace")
    except BaseException:
        return ""


def _subst(src: str) -> str:
    """把占位符替换成运行期常量（**不用 `%` 格式化**：JS 里有 `%` 与 `{}`，会撞）。"""
    return (src
            .replace("__ASC_VER__", str(INJECT_VER))
            .replace("__ASC_TB__", G_TB)
            .replace("__ASC_BS__", "__asc_bridge")
            .replace("__ASC_SEL__", G_SEL)
            .replace("__ASC_NID__", TB_NODE_ID)
            .replace("__ASC_BN__", BRIDGE_NAME)
            .replace("__ASC_ACTS__", "[" + ",".join(
                "{%s}" % ",".join(['a:"%s"' % a, 't:"%s"' % t]) for a, t in _ACTIONS) + "]"))


def inject_js() -> str:
    """一次注入的全部脚本：qwebchannel.js → 桥初始化 → 工具栏。

    ⚠️ 三段的**顺序不能换**：桥初始化依赖 `QWebChannel` 构造函数（第一段定义），
       工具栏虽然不依赖桥（内部判空），但放最后能让「首屏就有桥」的概率最大。
    ⚠️ 用 `;` 分段 + 末尾不依赖返回值：`runJavaScript` 只取最后一个表达式的值，
       本函数返回整段字符串，调用方**不读**返回值（要看状态用 `probe_js()`）。
    """
    parts = []
    qwc = _qwc_source()
    if qwc:
        parts.append(qwc)
    parts.append(_subst(_bridge_js()))
    parts.append(_subst(_toolbar_js()))
    return "\n;\n".join(parts)


def snapshot_js(token: str = "") -> str:
    """兜底拉取：把 `window.__asc_sel` 原样取回（空则返回 null）。

    `token` 仅用于**让两次拉取可区分**（值里带上，便于 stage 日志归因）。
    """
    return ("(function(){var v=window['%s']||null;return v;})()" % G_SEL)


def probe_js() -> str:
    """回读注入状态（**双向断言的「回读」那一半**；探针与验收脚本共用）。

    返回 JSON 字符串，字段恒定：
      ok          工具栏节点存在
      installed   全局状态对象存在
      ver         注入版本
      has_el      节点在 DOM 里
      sel         当前选区长度
      vis         工具栏是否浮出
      bstate      桥状态
      path        最近一次投放走的哪条路（bridge / poll / null）
      fired       最近一次点是哪个动作
      err         页面侧记录的错误
    """
    return r"""
(function(){
  var TB='__ASC_TB__', NID='__ASC_NID__';
  var t = window[TB] || null;
  var el = document.getElementById(NID);
  return JSON.stringify({
    ok: !!(t && el),
    installed: !!t,
    ver: t ? t.v : null,
    has_el: !!el,
    acts: el ? el.querySelectorAll('[data-asc-act]').length : 0,
    sel: t ? String(t.sel || '').length : 0,
    vis: !!(t && t.vis),
    bstate: window['__asc_bstate'] || null,
    path: t ? (t.path || null) : null,
    fired: t ? (t.fired || null) : null,
    err: t ? (t.err || null) : null
  });
})()
""".replace("__ASC_TB__", G_TB).replace("__ASC_NID__", TB_NODE_ID)


# ---------------- Python 侧：桥对象 ----------------

_BRIDGE_FACTORY = None


def webchannel_cls():
    """惰性取 `QWebChannel` 类（找不到返回 `None` → 上层降级为「只有轮询」）。

    ⚠️ 三条 import 路径都试一遍：`qtpy.QtWebChannel` 在部分 qtpy 版本里没有；
       `qtpy.QtWebEngineWidgets` 在 PyQt5 下**不导出** `QWebChannel`（它在独立模块里）。
       写死单一路径会得到「打包版没有桥」这种只在发行包里才出现的病。
    """
    for mod, name in (("qtpy.QtWebChannel", "QWebChannel"),
                      ("qtpy.QtWebEngineWidgets", "QWebChannel"),
                      ("PySide6.QtWebChannel", "QWebChannel"),
                      ("PyQt6.QtWebChannel", "QWebChannel")):
        try:
            m = __import__(mod, fromlist=[name])
            c = getattr(m, name, None)
            if c is not None:
                return c
        except BaseException:
            continue
    return None


def make_bridge(on_payload):
    """造一个桥对象（**必须在 Qt 主线程**）。返回 `(bridge, error)`。

    :param on_payload: `callable(raw_str, payload_dict_or_None)`；在**主线程**被调用，
        所以它**只许写普通值**，绝不许做 IO / 调 LLM（那会把界面冻住）。

    ⚠️⚠️ 桥对象必须由**调用方持有引用**（存进 tab dict）：Python 侧一旦没有引用，
       `QObject` 被 GC 回收 → 页面里的 `ascBridge` 变成悬空对象 → 点击**静默无反应**。
       这是本项目「Qt 对象生命周期」类 bug 的同一形态，且症状最像「注入没生效」。
    ⚠️ **不抛异常**：造不出桥只该让「兜底轮询」成为唯一通路，不该让面板装配失败。
    """
    if _BRIDGE_FACTORY is None:
        ok, err = build_bridge_factory()
        if not ok:
            return None, err
    try:
        return _BRIDGE_FACTORY(on_payload), None
    except BaseException as e:
        return None, "%s: %s" % (type(e).__name__, e)


def build_bridge_factory():
    """在**主线程**构造桥工厂（把 QtCore 绑进来，避免每次 import）。返回 `(ok, err)`。"""
    global _BRIDGE_FACTORY
    if _BRIDGE_FACTORY is not None:
        return True, None
    try:
        from qtpy.QtCore import QObject, Slot
    except BaseException as e:
        return False, "%s: %s" % (type(e).__name__, e)

    class _AscBridge(QObject):
        """页面 → Python 的单向通道。**只有一个方法**，签名恒定（str → void）。"""

        @Slot(str)
        def report(self, payload):
            cb = getattr(self, "_asc_cb", None)
            if cb is None:
                return
            try:
                cb(str(payload))
            except BaseException:
                # 回调跑在事件循环里 → 绝不能把异常抛回 Qt（那会打出无从定位的堆栈，
                # 且下一次点击行为不定）。吞掉，状态由回调自己写进盒子。
                pass

    def _factory(cb):
        # ⚠️⚠️ 必须**只返回桥对象**，并把回调挂到实例上（`report()` 从 `self._asc_cb` 取）。
        #    旧实现 `return _AscBridge(), cb` 返回的是 **tuple**，而 `make_bridge` 会
        #    再包一层 `(…, None)` → `QWebChannel.registerObject(id, tuple)` 抛 TypeError：
        #      "called with wrong argument types … registerObject(id: str, object: QObject)"
        #    该异常被 `_attach_inject` 的宽 except 吞掉、只写进 `inject_err` →
        #    **桥静默失效**（页面侧 `bstate='no_transport'`，点击全走轮询兜底）。
        #    实测：verify_wp16_inject.py 的 I0b / I3 / I5（修复前 8/16，全红都指向这里）。
        b = _AscBridge()
        b._asc_cb = cb
        return b

    _BRIDGE_FACTORY = _factory
    return True, None


# ---------------- 载荷解析（**唯一实现**，路由与验收都用它） ----------------

_ACT_SET = {a for a, _t in _ACTIONS}


def empty_selection():
    """选区盒子的**确定空态**（键恒定、可逐字比对）。"""
    return {"seq": 0, "kind": "", "act": None, "sel": "", "url": "",
            "title": "", "ts": 0, "path": None, "err": None}


def parse_payload(raw):
    """把页面回传的 JSON 解析成规范 dict；坏输入返回 `(None, 原因)`。

    ⚠️ 不抛异常：坏载荷（第三方页面里什么都可能）只该被记为 `bad_payload`，
       不该把路由打成 500 —— 500 表示「代码出错」，而这里是**外部输入**。
    """
    if not isinstance(raw, str) or not raw:
        return None, "empty_payload"
    try:
        d = json.loads(raw)
    except BaseException:
        return None, "bad_json"
    if not isinstance(d, dict):
        return None, "not_object"
    kind = str(d.get("kind") or "")
    if kind not in ("sel", "act"):
        return None, "bad_kind"
    act = d.get("act")
    if kind == "act":
        if act not in _ACT_SET:
            return None, "bad_action"
    else:
        act = None
    return {
        "kind": kind,
        "act": act,
        "sel": str(d.get("sel") or ""),
        "url": str(d.get("url") or ""),
        "title": str(d.get("title") or ""),
        "ts": int(d.get("ts") or 0),
    }, None
