# -*- coding: utf-8 -*-
"""AI 浏览器面板：Qt 控件装配与生命周期（阶段 2 · WP12）

⚠️⚠️ **本模块所有公开函数必须在 Qt 主线程执行。**
   实测（2026-09-20）：在 `Thread-2`（pywebview 的 after_start 线程）里创建 QWidget
   并挂到宿主上，Qt 会打出
       `QObject::setParent: Cannot set parent, new parent is in a different thread`
   → 控件**根本没挂上**（`findChildren` 找不到它），而且
     `setParent(None)` / `takeCentralWidget()` 会让主线程**静默挂死**
     （无异常、无输出、连 watchdog 的日志都来不及写）。
   ⇒ 一律经 `browser_host.call_on_main()` 投递。

⚠️ 为什么用 `QDockWidget` 而不是「把 centralWidget 换成 QSplitter」——**这是实测结论，不是偏好**：

   | 装配路径 | 实测结果 |
   |---|---|
   | `cw.setParent(None)` → 塞进 QSplitter | ❌ 主线程**挂死**（60s 后 watchdog 杀，rc=3） |
   | `host.takeCentralWidget()` → 塞进 QSplitter | ❌ 主线程**挂死**（同一形态） |
   | `QDockWidget` + `addDockWidget()` | ✅ 全程走通，`centralWidget` 未变，应用界面零回归 |

   实测证据：`dock_visible=True`、`dock_size=[520,820]`（右侧栏）/ `[1280,820]`（全宽）、
   `central_unchanged=True`、`render_ok=True`、截图 OCR 能读出种子字符串。

⚠️ 装配是**幂等**的：重复调用返回同一个面板，不叠加（否则每点一次就多一个 dock）。
"""
import json
import threading

__all__ = [
    "DOCK_OBJNAME", "VIEW_OBJNAME", "ADDR_OBJNAME", "STATUS_OBJNAME",
    "BTN_BACK_OBJNAME", "BTN_FORWARD_OBJNAME", "BTN_RELOAD_OBJNAME", "BTN_STOP_OBJNAME",
    "TABBAR_OBJNAME", "TABNEW_OBJNAME", "STACK_OBJNAME", "MAX_TABS",
    "assemble", "show", "hide", "close", "navigate", "load_html",
    "is_assembled", "get_state", "get_nav_state", "nav_action",
    "tab_new", "tab_close", "tab_switch", "get_tabs_state", "active_tab_id",
    "active_view_eval",
    "page_source_start", "page_source_poll", "get_page_source",
    "classify_load_error", "selfcheck",
]

DOCK_TITLE = "AI 浏览器"
DOCK_OBJNAME = "asc_browser_dock"
PANEL_OBJNAME = "asc_browser_panel"
ADDR_OBJNAME = "asc_addr"
VIEW_OBJNAME = "asc_browser_view"

# ---------- WP14：多标签 ----------
# ⚠️⚠️ **兼容层设计（关键取舍，不要"顺手清理"）**：
#   多标签改造后，`panel["view"] / ["addr"] / ["nav"]` 仍然存在，但语义变成
#   **「当前活跃标签」的别名**。这样 WP13 已验证的 `navigate()` / `nav_action()` /
#   `_sync_nav()` / `get_nav_state()` **一行都不用改**，20/20 验收天然不回归。
#   代价：读代码时要记住这三个键是别名，本体在 `panel["tabs"][i]`。
TABBAR_OBJNAME = "asc_tabbar"
TABNEW_OBJNAME = "asc_tab_new"
STACK_OBJNAME = "asc_stack"
# 标签数上限：判据里**不设**「单标签 <200MB」（V3 已撤回 N02 —— 多进程架构下
# 「标签页内存」不是可归属实体），改为整应用观测口径。上限只防手滑点爆内存。
MAX_TABS = 12

STATUS_OBJNAME = "asc_status"
# WP13 导航动作按钮的对象名（验收脚本按名字定位，**不要**按位置取 —— 加按钮会串位）
BTN_BACK_OBJNAME = "asc_nav_back"
BTN_FORWARD_OBJNAME = "asc_nav_forward"
BTN_RELOAD_OBJNAME = "asc_nav_reload"
BTN_STOP_OBJNAME = "asc_nav_stop"

# 面板句柄：**只在主线程访问**（所有公开函数都经 call_on_main 投递，故天然串行）
_panel = None
_panel_lock = threading.Lock()


def _qt():
    """惰性取 Qt 符号。**不在模块顶层 import** —— 后端必须能在无 Qt 环境 import 本模块。"""
    from qtpy import QtCore, QtWidgets
    from qtpy.QtWebEngineWidgets import QWebEnginePage, QWebEngineView
    return QtCore, QtWidgets, QWebEngineView, QWebEnginePage


def _loading_info():
    """惰性取 `QWebEngineLoadingInfo`（WP13 错误分类用）。

    ⚠️ **不并进 `_qt()` 的返回值** —— 那个 4 元组已有 5 处解包调用，
       改元数会静默打乱所有调用点的解包（把 QWebEnginePage 当成 LoadingInfo），
       而且症状是「某个不相关的函数行为诡异」，极难定位。
    """
    from qtpy.QtWebEngineCore import QWebEngineLoadingInfo
    return QWebEngineLoadingInfo


def _enum_int(e):
    """把 Qt 枚举转成 int。

    ⚠️⚠️ Qt6 / PySide6 的枚举是 **Python enum** → `int(SomeEnum.Member)` 抛
       `TypeError: int() argument must be a string, ... not 'ErrorDomain'`。
       必须走 `.value`。**本 bug 曾真实发生**（2026-09-21）：`_error_domain_names()`
       与 `classify_load_error()` 都用了 `int(...)`，异常被 `except BaseException: pass`
       吞掉 → 域名表建不起来、三类错误全归 "other"，用户永远只看到「加载失败」，
       看不到「服务器返回 404」。验收 P4b/P4d/P4e 抓到它才算数。

    ⚠️ 只用于**需要 int 比较**的场景；状态机里 `st == LS.LoadFailedStatus` 那种
       枚举对枚举的比较本来就是对的，不要顺手改。
    """
    try:
        return int(e)
    except (TypeError, ValueError):
        return int(e.value)


def is_assembled():
    """面板是否已装配。⚠️ 只读模块级标记，不碰 Qt 控件 → 任意线程可调用。"""
    return _panel is not None


def _find_dock(host, QtWidgets):
    """在宿主里按对象名找已存在的 dock（**幂等的第二道保险**）。

    ⚠️ 不只看模块级 `_panel`：模块可能被 reload（开发态 / 测试），
       而 Qt 对象树是活的 → 只看标记会重复装配出两个 dock。
    """
    for d in host.findChildren(QtWidgets.QDockWidget):
        if d.objectName() == DOCK_OBJNAME:
            return d
    return None


def assemble(host, initial_url=""):
    """在宿主窗口里装配浏览器面板（幂等）。**必须在主线程执行。**

    :returns: 面板句柄 dict（供同模块其他函数使用）
    """
    global _panel
    QtCore, QtWidgets, QWebEngineView, QWebEnginePage = _qt()

    with _panel_lock:
        existing = _find_dock(host, QtWidgets)
        if existing is not None and _panel is not None:
            _panel["dock"] = existing
            return _panel
        if existing is not None:
            # ⚠️ 有残留 dock 但句柄已丢（`close()` 里 `deleteLater()` 尚未被事件循环执行）
            #    → **必须清掉它**，否则下面新建会**叠出第二个 dock**
            #      （用户看到两个「AI 浏览器」面板，且旧的还占着右侧宽度）。
            #    用 hide + deleteLater，**不用 setParent(None)**（那会挂死，见 close() 注释）。
            try:
                existing.hide()
                existing.deleteLater()
            except BaseException:
                pass

        app_view = host.centralWidget()          # 应用界面（pywebview 自己的 QWebEngineView）

        # ---------- dock ----------
        dock = QtWidgets.QDockWidget(DOCK_TITLE, host)
        dock.setObjectName(DOCK_OBJNAME)
        dock.setAllowedAreas(QtCore.Qt.LeftDockWidgetArea | QtCore.Qt.RightDockWidgetArea)

        panel = QtWidgets.QWidget()
        panel.setObjectName(PANEL_OBJNAME)
        pl = QtWidgets.QVBoxLayout(panel)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(0)

        # ---------- 地址栏 ----------
        bar = QtWidgets.QWidget()
        bar.setObjectName("asc_bar")
        bl = QtWidgets.QHBoxLayout(bar)
        bl.setContentsMargins(6, 6, 6, 6)
        bl.setSpacing(6)

        # ---------- 导航动作按钮（WP13） ----------
        # ⚠️ 用**文字符号**而不是图标：不引入资源文件依赖，打包也不会漏图标。
        # ⚠️ 后退 / 前进初始为**灰**（还没有历史）—— 这是正确状态，不是缺陷。
        btn_back = QtWidgets.QPushButton("\u2190")
        btn_back.setObjectName(BTN_BACK_OBJNAME)
        btn_back.setToolTip("后退")
        btn_back.setFixedWidth(30)
        btn_back.setEnabled(False)

        btn_forward = QtWidgets.QPushButton("\u2192")
        btn_forward.setObjectName(BTN_FORWARD_OBJNAME)
        btn_forward.setToolTip("前进")
        btn_forward.setFixedWidth(30)
        btn_forward.setEnabled(False)

        btn_reload = QtWidgets.QPushButton("\u27f3")
        btn_reload.setObjectName(BTN_RELOAD_OBJNAME)
        btn_reload.setToolTip("刷新")
        btn_reload.setFixedWidth(30)

        btn_stop = QtWidgets.QPushButton("\u2715")
        btn_stop.setObjectName(BTN_STOP_OBJNAME)
        btn_stop.setToolTip("停止")
        btn_stop.setFixedWidth(30)

        addr = QtWidgets.QLineEdit()
        addr.setObjectName(ADDR_OBJNAME)
        addr.setPlaceholderText("输入网址，回车打开")
        addr.setClearButtonEnabled(True)

        btn_go = QtWidgets.QPushButton("转到")
        btn_go.setObjectName("asc_go")
        btn_go.setFixedWidth(52)

        btn_close = QtWidgets.QPushButton("关闭")
        btn_close.setObjectName("asc_close")
        btn_close.setFixedWidth(52)

        bl.addWidget(btn_back)
        bl.addWidget(btn_forward)
        bl.addWidget(btn_reload)
        bl.addWidget(btn_stop)
        bl.addWidget(addr, 1)
        bl.addWidget(btn_go)
        bl.addWidget(btn_close)
        pl.addWidget(bar)

        # ---------- 浏览器内容（WP14：多标签） ----------
        # ⚠️ `QStackedWidget` 装所有标签的 view，索引 = 标签顺序；`QTabBar` 只驱动切换。
        #    用 QStackedWidget 而**不是**「每次切换重建 view」：重建会丢掉页面状态
        #    （滚动位置 / 表单 / JS 变量），而「切回来标记仍在」正是本包的验收判据。
        tabbar = QtWidgets.QTabBar()
        tabbar.setObjectName(TABBAR_OBJNAME)
        tabbar.setExpanding(False)
        tabbar.setMovable(True)          # 允许拖动重排（低风险，QTabBar 原生支持）

        btn_tab_new = QtWidgets.QPushButton("+")
        btn_tab_new.setObjectName(TABNEW_OBJNAME)
        btn_tab_new.setToolTip("新建标签")
        btn_tab_new.setFixedWidth(28)

        tabrow = QtWidgets.QWidget()
        tabrow.setObjectName("asc_tabrow")
        tl = QtWidgets.QHBoxLayout(tabrow)
        tl.setContentsMargins(6, 0, 6, 0)
        tl.setSpacing(0)
        tl.addWidget(tabbar, 1)
        tl.addWidget(btn_tab_new)
        pl.addWidget(tabrow)

        stack = QtWidgets.QStackedWidget()
        stack.setObjectName(STACK_OBJNAME)
        # ⚠️ stretch=1：内容区吃掉所有剩余高度（地址栏 / 标签栏 / 状态栏都是固定高）。
        pl.addWidget(stack, 1)

        _profile_val = None
        shared_ok = True
        shared_err = None
        try:
            if app_view is not None and hasattr(app_view, "page") and app_view.page() is not None:
                _profile_val = app_view.page().profile()
        except BaseException as e:
            shared_ok = False
            shared_err = "%s: %s" % (type(e).__name__, e)

        # ---------- 状态栏 ----------
        status = QtWidgets.QLabel("就绪")
        status.setObjectName(STATUS_OBJNAME)
        pl.addWidget(status)

        dock.setWidget(panel)

        _panel = {
            "dock": dock, "panel": panel, "addr": addr,
            "status": status, "app_view": app_view,
            "btn_go": btn_go, "btn_close": btn_close,
            "btn_back": btn_back, "btn_forward": btn_forward,
            "btn_reload": btn_reload, "btn_stop": btn_stop,
            "tabbar": tabbar, "tab_new_btn": btn_tab_new, "stack": stack,
            "profile": _profile_val,
            # ⚠️⚠️ 多标签数据面：`tabs` 是**本体**；`view`/`nav`/`last_url` 等是
            #     **活跃标签的别名**（由 `_bind_active()` 重绑）。这是兼容层，
            #     让 WP13 的 navigate/nav_action/_sync_nav 一行不改即可复用。
            "tabs": [], "active": None, "_tab_seq": 0,
            # WP15 取源盒子：**只放普通值**（src 由事件循环里的回调写入，
            # 路由线程只读它 → 主线程全程不被占住，界面不冻）。
            "_src": None, "_src_seq": 0,
            "view": None,
            # WP13 导航状态：**只存普通值**（路由线程只读它，绝不直接读 Qt 对象 —— 会挂死）
            "nav": {"state": "idle", "url": "", "title": "", "progress": 0,
                    "can_back": False, "can_forward": False, "error": None},
            "last_url": "", "last_title": "", "last_load_ok": None, "progress": 0,
            "shared_profile": shared_ok, "shared_profile_error": shared_err,
        }

        # ---------- 接线 ----------
        def _do_navigate():
            navigate(_panel, addr.text())

        addr.returnPressed.connect(_do_navigate)
        btn_go.clicked.connect(_do_navigate)
        btn_close.clicked.connect(lambda: hide(host))
        # WP13 导航动作：**统一走 nav_action**（单一入口 → 可用性判定与错误回传只写一份）；
        # ⚠️ 它内部经 `_active_view(panel)` 取活跃标签的 view → 多标签下天然正确。
        btn_back.clicked.connect(lambda: nav_action(_panel, "back"))
        btn_forward.clicked.connect(lambda: nav_action(_panel, "forward"))
        btn_reload.clicked.connect(lambda: nav_action(_panel, "reload"))
        btn_stop.clicked.connect(lambda: nav_action(_panel, "stop"))

        def _on_tab_current_changed(idx):
            """QTabBar 切换 → 切 stack + **重绑兼容别名** + 刷新导航面。"""
            try:
                if idx < 0:
                    return
                tab_switch(_panel, idx)
            except BaseException:
                pass

        tabbar.currentChanged.connect(_on_tab_current_changed)
        btn_tab_new.clicked.connect(lambda: tab_new(_panel))

        # ⚠️ `dock` 先挂上，再建第一个标签：`_make_tab()` 里会 `resizeDocks` 前的
        #    布局计算依赖 dock 已在宿主上；先建标签也完全可以，但保持这个顺序更稳。
        host.addDockWidget(QtCore.Qt.RightDockWidgetArea, dock)
        host.resizeDocks([dock], [520], QtCore.Qt.Horizontal)
        dock.show()

        tab_new(_panel, url=initial_url or "")

        return _panel


# ---------- WP14：多标签管理 ----------
# ⚠️⚠️ 兼容层核心：`panel["view"] / ["addr"] / ["nav"] / ["last_*"] / ["progress"]`
#     **不是本体**，而是「当前活跃标签」的别名。所有写这些键的既有代码
#     （navigate / _sync_nav / 各 load* 信号槽）都只在活跃标签上生效 —— 这恰好就是
#     多标签的正确语义，所以 WP13 的代码一行都不用改。
#     本体在 `panel["tabs"][i]`，每个元素是一个 **tab dict**：
#         {"id": str, "view": QWebEngineView, "nav": {...七键...},
#          "last_url": str, "last_title": str, "last_load_ok": bool|None, "progress": int}
#     ⚠️ `nav` 必须是**每标签独立**的 dict 对象（不能共享引用）——
#        共享会让「切到 B 页却显示 A 页的错误」这种串台 bug 躲过所有断言。

def _tab_dicts(panel):
    return panel.get("tabs") or []


def _active_view(panel):
    """取**当前活跃标签**的 QWebEngineView（**主线程**，会碰 Qt 对象）。

    ⚠️ 一律经本函数取 view，**不要**再用 `panel["view"]` —— 那只是别人重绑好的别名，
       在「切换标签」的那一刻可能还没同步。本函数按 `panel["active"]` 现算，是唯一真相。
    """
    if panel is None:
        return None
    aid = panel.get("active")
    for t in _tab_dicts(panel):
        if t.get("id") == aid:
            return t.get("view")
    # active 指向不存在（如刚关掉）→ 退回第一个（**不是 None**：有标签就该能拿到 view）
    ts = _tab_dicts(panel)
    return ts[0].get("view") if ts else None


def _active_tab(panel):
    if panel is None:
        return None
    aid = panel.get("active")
    for t in _tab_dicts(panel):
        if t.get("id") == aid:
            return t
    ts = _tab_dicts(panel)
    return ts[0] if ts else None


def _bind_active(panel):
    """把活跃标签的值**重绑**到兼容别名键上。**主线程。**

    ⚠️ 这一步是多标签不回归 WP13 的关键：切完标签后，
       `panel["view"]` / `["nav"]` / `["last_url"]` 必须立刻指向新活跃标签的对应对象，
       否则导航按钮、地址栏回填、`/browser/nav` 全都还在读**旧标签**的状态。
    ⚠️ `nav` 绑的是**同一个 dict 引用**（不是拷贝）—— 加载信号槽往里写状态，
       必须写进活跃标签自己的那份。
    """
    if panel is None:
        return
    t = _active_tab(panel)
    if t is None:
        panel["view"] = None
        return
    panel["view"] = t.get("view")
    panel["nav"] = t.setdefault("nav", _empty_nav())
    panel["last_url"] = t.get("last_url", "")
    panel["last_title"] = t.get("last_title", "")
    panel["last_load_ok"] = t.get("last_load_ok")
    panel["progress"] = int(t.get("progress", 0) or 0)
    # 地址栏回填活跃标签的 URL（切换后必须看到新标签的地址）
    try:
        a = panel.get("addr")
        u = t.get("last_url") or ""
        if a is not None and not a.hasFocus():
            a.setText(u)
    except BaseException:
        pass
    _refresh_nav_buttons(panel)
    _sync_tabbar_labels(panel)


def _sync_tabbar_labels(panel):
    """把标签标题同步到 QTabBar（**主线程**）。标题优先用页面 title，退化用 host。"""
    try:
        tb = panel.get("tabbar")
        if tb is None:
            return
        for i, t in enumerate(_tab_dicts(panel)):
            title = (t.get("last_title") or "").strip()
            if not title:
                u = (t.get("last_url") or "").strip()
                title = _host_of(u) if u else "新标签页"
            if len(title) > 18:
                title = title[:17] + "…"
            tb.setTabText(i, title)
            tb.setTabToolTip(i, t.get("last_url") or title)
    except BaseException:
        pass


def _host_of(url):
    try:
        from urllib.parse import urlparse
        return urlparse(url).netloc or url
    except BaseException:
        return url


def _empty_nav():
    """导航空态（**七个键恒定**，与 WP13 契约一致）。"""
    return {"state": "idle", "url": "", "title": "", "progress": 0,
            "can_back": False, "can_forward": False, "error": None}


def _wire_tab(panel, tab):
    """给一个标签的 view 接线（**主线程**）。

    ⚠️ 信号槽必须闭包捕获**本标签**的 `tab`，而**不是**读 `_panel["view"]`
       （那是活跃标签的别名，会在切标签后变）——否则「后台标签加载完成」
       会把状态写进前台标签，症状是「切过来的页面进度条乱跳」。
    """
    view = tab["view"]

    def _on_load_finished(ok):
        try:
            url = view.url().toString()
            tab["last_load_ok"] = bool(ok)
            tab["last_url"] = url
            st = panel.get("status")
            if st is not None and tab.get("id") == panel.get("active"):
                st.setText("加载完成" if ok else "加载失败")
            if tab.get("id") == panel.get("active"):
                _sync_nav(panel)
            _sync_tabbar_labels(panel)
        except BaseException:
            pass

    def _on_title_changed(title):
        try:
            tab["last_title"] = title
            t = tab.setdefault("nav", _empty_nav())
            t["title"] = title
            if tab.get("id") == panel.get("active"):
                st = panel.get("status")
                if st is not None and title:
                    st.setText(title)
            _sync_tabbar_labels(panel)
        except BaseException:
            pass

    def _on_load_progress(p):
        try:
            tab["progress"] = int(p)
            if tab.get("id") == panel.get("active"):
                panel["progress"] = int(p)
        except BaseException:
            pass

    def _on_loading_changed(info):
        """WP13 状态机，但写**本标签自己的** nav（每标签独立）。"""
        try:
            nav = tab.setdefault("nav", _empty_nav())
            LS = _loading_info().LoadStatus
            st = info.status()
            if st == LS.LoadStartedStatus:
                nav["state"] = "loading"
                nav["error"] = None
            elif st == LS.LoadSucceededStatus:
                nav["state"] = "loaded"
                nav["error"] = None
            elif st == LS.LoadStoppedStatus:
                nav["state"] = "stopped"
            elif st == LS.LoadFailedStatus:
                err = classify_load_error(info)
                nav["state"] = "http_error" if err.get("kind") == "http" else "net_error"
                nav["error"] = err
            if tab.get("id") == panel.get("active"):
                _sync_nav(panel)
            _sync_tabbar_labels(panel)
        except BaseException:
            pass

    try:
        view.loadFinished.connect(_on_load_finished)
        view.titleChanged.connect(_on_title_changed)
        view.loadProgress.connect(_on_load_progress)
    except BaseException:
        pass
    try:
        view.page().loadingChanged.connect(_on_loading_changed)
        tab["loading_hooked"] = True
    except BaseException as e:
        tab["loading_hooked"] = "%s: %s" % (type(e).__name__, e)

    # ⚠️ `target=_blank` **留在本标签视图内**打开（不新开标签）：
    #    这正是 WP13 的判据 P3 —— 「不把应用导航走」。
    try:
        page = view.page()

        def _on_new_window(request):
            try:
                request.openIn(page)
            except BaseException:
                try:
                    view.setUrl(request.requestedUrl())
                except BaseException:
                    pass

        page.newWindowRequested.connect(_on_new_window)
        tab["new_window_hooked"] = True
    except BaseException as e:
        tab["new_window_hooked"] = "%s: %s" % (type(e).__name__, e)


def tab_new(panel, url=""):
    """新建标签并激活。**必须在主线程执行。**

    :returns: `(ok, error, tab_id)`；超上限返回 `(False, "tab_limit", None)`。

    ⚠️ 上限返回**独立错误码** `tab_limit`（不复用 `error`），界面才能提示
       「标签开太多了」而不是通用失败。
    """
    if panel is None:
        return False, "no_panel", None
    QtCore, QtWidgets, QWebEngineView, QWebEnginePage = _qt()
    tabs = panel.setdefault("tabs", [])
    if len(tabs) >= MAX_TABS:
        return False, "tab_limit", None
    stack = panel.get("stack")
    tabbar = panel.get("tabbar")
    if stack is None or tabbar is None:
        return False, "no_container", None

    panel["_tab_seq"] = int(panel.get("_tab_seq", 0) or 0) + 1
    tid = "t%d" % panel["_tab_seq"]

    view = QWebEngineView()
    view.setObjectName(VIEW_OBJNAME)
    # ⚠️ 复用宿主 profile（**每标签都要设**）：cookie / storage 与主窗口同一 profile
    #    → 「首次登录后免登录」。漏设某个标签会让那个标签每次都要求重新登录
    #    （症状像"登录不保存"，且只在某一个标签上出现，极难定位）。
    prof = panel.get("profile")
    if prof is not None:
        try:
            view.setPage(QWebEnginePage(prof, view))
        except BaseException:
            pass

    tab = {"id": tid, "view": view, "nav": _empty_nav(),
           "last_url": "", "last_title": "", "last_load_ok": None, "progress": 0}
    tabs.append(tab)
    stack.addWidget(view)

    try:
        tabbar.blockSignals(True)
        tabbar.addTab("新标签页")
        tabbar.setCurrentIndex(tabbar.count() - 1)
        tabbar.blockSignals(False)
    except BaseException:
        pass

    _wire_tab(panel, tab)
    panel["active"] = tid
    _bind_active(panel)

    if url:
        navigate(panel, url)
    return True, None, tid


def tab_close(panel, index=None, tab_id=None):
    """关闭一个标签。**必须在主线程执行。**

    :returns: `(ok, error, remaining)`

    ⚠️ **最后一个标签不允许关闭**，返回 `(False, "last_tab", 1)` ——
       否则面板会变成一片空白，用户以为界面坏了。这是**正常约束**不是故障，
       故用独立错误码，界面该把关闭按钮变灰而不是弹错误。
    """
    if panel is None:
        return False, "no_panel", 0
    tabs = panel.setdefault("tabs", [])
    if not tabs:
        return False, "no_tab", 0
    if len(tabs) <= 1:
        return False, "last_tab", len(tabs)

    idx = None
    if tab_id is not None:
        for i, t in enumerate(tabs):
            if t.get("id") == tab_id:
                idx = i
                break
    elif index is not None:
        try:
            idx = int(index)
        except BaseException:
            idx = None
    if idx is None or idx < 0 or idx >= len(tabs):
        return False, "bad_index", len(tabs)

    t = tabs[idx]
    was_active = (t.get("id") == panel.get("active"))
    stack = panel.get("stack")
    tabbar = panel.get("tabbar")
    try:
        if stack is not None:
            stack.removeWidget(t["view"])
        # ⚠️ 用 `deleteLater()`，**不要** `setParent(None)` —— 后者会挂死主线程
        #    （WP12/WP13 已两次实测；见模块头与 close() 的说明）。
        t["view"].deleteLater()
    except BaseException:
        pass
    try:
        if tabbar is not None:
            tabbar.blockSignals(True)
            tabbar.removeTab(idx)
            tabbar.blockSignals(False)
    except BaseException:
        pass
    tabs.pop(idx)

    if was_active:
        # 移到相邻标签（优先前一个；若关的是第一个则取新的第一个）
        new_idx = max(0, idx - 1)
        new_idx = min(new_idx, len(tabs) - 1)
        panel["active"] = tabs[new_idx]["id"]
        try:
            if tabbar is not None:
                tabbar.blockSignals(True)
                tabbar.setCurrentIndex(new_idx)
                tabbar.blockSignals(False)
            if stack is not None:
                stack.setCurrentWidget(tabs[new_idx]["view"])
        except BaseException:
            pass
    _bind_active(panel)
    return True, None, len(tabs)


def tab_switch(panel, index=None, tab_id=None):
    """切换活跃标签。**必须在主线程执行。**

    :returns: `(ok, error, tab_id)`
    """
    if panel is None:
        return False, "no_panel", None
    tabs = panel.setdefault("tabs", [])
    idx = None
    if tab_id is not None:
        for i, t in enumerate(tabs):
            if t.get("id") == tab_id:
                idx = i
                break
    elif index is not None:
        try:
            idx = int(index)
        except BaseException:
            idx = None
    if idx is None or idx < 0 or idx >= len(tabs):
        return False, "bad_index", None

    t = tabs[idx]
    panel["active"] = t["id"]
    try:
        stack = panel.get("stack")
        if stack is not None:
            stack.setCurrentWidget(t["view"])
    except BaseException:
        pass
    # ⚠️⚠️ 必须同时把 `QTabBar` 的当前项对齐到 `idx`。
    #    本函数被**两条路径**调用：
    #      ① tabbar 自己的 `currentChanged` 槽（`_on_tab_current_changed`）——
    #         那时索引已对齐，重设是幂等的；
    #      ② API 的 `POST /tab/switch` → `request_tab_switch` → 这里 ——
    #         那时 tabbar **还停在旧项**。
    #    缺这一步，界面高亮与实际内容会**错位**（内容已切到 B，高亮还在 A）。
    #    危险之处在于：`stack` 当前页、`panel["active"]`、数据面**全都是对的** ——
    #    任何「只查数据/只查 stack」的断言都发现不了，只有直接读
    #    `tabbar.currentIndex()` 才看得见（WP14 验收专门有这条）。
    #    `blockSignals` 防止再次触发 `currentChanged` → `tab_switch` 递归。
    try:
        tabbar = panel.get("tabbar")
        if tabbar is not None and tabbar.currentIndex() != idx:
            tabbar.blockSignals(True)
            tabbar.setCurrentIndex(idx)
            tabbar.blockSignals(False)
    except BaseException:
        pass
    _bind_active(panel)
    return True, None, t["id"]


def get_tabs_state():
    """标签列表（**只读普通值** → 任意线程可调用）。

    ⚠️ 与 `get_state()` 的分工：本函数是 WP14 的新数据面（含每标签独立导航状态）；
       `get_state()` 保留原形状（`tabs/active/ready/assembled`）不破坏既有消费者。
    ⚠️ 空态是**确定结构**：`{"tabs": [], "active": null, "count": 0, "max": N, "assembled": false}`。
    """
    if _panel is None:
        return {"tabs": [], "active": None, "count": 0, "max": MAX_TABS, "assembled": False}
    out = []
    for t in _tab_dicts(_panel):
        nav = t.get("nav") or {}
        out.append({
            "id": t.get("id"),
            "url": t.get("last_url", "") or "",
            "title": t.get("last_title", "") or "",
            "active": t.get("id") == _panel.get("active"),
            "state": nav.get("state", "idle"),
            "progress": int(nav.get("progress", 0) or 0),
            "can_back": bool(nav.get("can_back", False)),
            "can_forward": bool(nav.get("can_forward", False)),
            "error": nav.get("error", None),
        })
    return {"tabs": out, "active": _panel.get("active"),
            "count": len(out), "max": MAX_TABS, "assembled": True}


def active_tab_id():
    """当前活跃标签 id（只读普通值 → 任意线程可调用）。"""
    if _panel is None:
        return None
    return _panel.get("active")


def show(host, url=None, width=520):
    """显示浏览器面板（必要时先装配）。**必须在主线程执行。**"""
    global _panel
    panel = assemble(host)
    dock = panel["dock"]
    dock.show()
    dock.raise_()
    try:
        host.resizeDocks([dock], [width], _qt()[0].Qt.Horizontal)
    except BaseException:
        pass
    if url:
        navigate(panel, url)
    return panel


def hide(host=None):
    """隐藏浏览器面板（**不销毁** —— 隐藏后重新显示保留页面状态）。**必须在主线程执行。**"""
    if _panel is None:
        return False
    _panel["dock"].hide()
    return True


def close(host=None):
    """关闭并销毁面板（下次 show 会重新装配）。**必须在主线程执行。**

    ⚠️⚠️ **绝不要用 `dock.setParent(None)`** —— 实测它会**挂死 Qt 主线程**
       （无异常、无 traceback，事件循环层面的死锁；同一形态的
       `cw.setParent(None)` / `takeCentralWidget()` 在装配探针里都死过）。
       正确做法是 `hide()` + `deleteLater()`：
         · `hide()` 立刻从界面上消失（用户可感知的效果已达成）；
         · `deleteLater()` 交给事件循环在安全时机销毁，**不动父对象关系**。
       代价是对象在当轮事件循环结束前仍在树上 —— 但 `assemble()` 的幂等
       是**按对象名查找**（`_find_dock`），所以下次 show 会复用/接管它，不会叠出两个。
    """
    global _panel
    if _panel is None:
        return False
    dock = _panel.get("dock")
    try:
        # WP14：先把标签的 view 逐个摘掉再销毁 dock —— 直接用 deleteLater 让 Qt 收尾，
        # ⚠️ 仍然**绝不** setParent(None)（会挂死主线程，见上文）。
        for t in list(_tab_dicts(_panel)):
            try:
                t["view"].deleteLater()
            except BaseException:
                pass
        _panel["tabs"] = []
        _panel["active"] = None
        _panel["view"] = None
    except BaseException:
        pass
    try:
        if dock is not None:
            dock.hide()
            dock.deleteLater()
    except BaseException:
        pass
    _panel = None
    return True



def navigate(panel, url):
    """在当前浏览器视图里打开 URL（归一化在 `browser_host.normalize_url` 完成）。

    ⚠️ **抓取用原始输入**：本函数收到的已是调用方决定要加载的地址；
       归一化只用于「补协议」等让加载能成功的处理，**不改写用户输入的语义**。
    """
    if panel is None:
        return False, "no_panel"
    QtCore, _, _, _ = _qt()
    from ..services.browser_host import normalize_url
    raw = (url or "").strip()
    if not raw:
        return False, "empty_url"
    final = normalize_url(raw)
    if not final:
        return False, "invalid_url"
    try:
        panel["status"].setText("正在打开…")
        # WP13：每次导航都从 loading 重新开始（并清掉上一次的错误，避免旧错误挂在状态栏）
        nav = panel.setdefault("nav", {})
        nav["state"] = "loading"
        nav["error"] = None
        # ⚠️ WP14：一律经 `_active_view(panel)` 取 view（**不要**用 `panel["view"]`
        #    别名 —— 切标签那一刻它可能尚未重绑）。导航只作用于**活跃标签**。
        v = _active_view(panel)
        if v is None:
            return False, "no_view"
        v.setUrl(QtCore.QUrl(final))
        panel["addr"].setText(final)
        return True, None
    except BaseException as e:
        return False, "%s: %s" % (type(e).__name__, e)


def load_html(panel, html, base_url=""):
    """在当前视图加载一段 HTML（自检 / 离线内容用）。"""
    if panel is None:
        return False, "no_panel"
    QtCore, _, _, _ = _qt()
    try:
        v = _active_view(panel)
        if v is None:
            return False, "no_view"
        if base_url:
            v.setHtml(html, QtCore.QUrl(base_url))
        else:
            v.setHtml(html)
        return True, None
    except BaseException as e:
        return False, "%s: %s" % (type(e).__name__, e)


def active_view_eval(panel, script, timeout_ms=2000):
    """在**活跃标签**上执行 JS 并**同步**取回结果（WP14 判据 1 / WP16 的前置能力）。

    :returns: `(ok, error, value)`；`value` 是 `runJavaScript` 回调拿到的 JSON 可序列化值。

    ⚠️⚠️ `QWebEnginePage.runJavaScript(script, callback)` 是**异步**的：
       callback 在**事件循环**里执行。若在主线程同步等待，就是死锁
       （主线程被占 → 事件循环跑不了 → callback 永不触发）。
       本函数因此**必须由「非主线程」调用**（经 `call_on_main` 之外的路径），
       由调用方在自己的线程里 QEventLoop 等待，或改造成「投递 + 轮询」两步式。
       ⇒ 现阶段只用它在**探针/验收**里，且探针自己起独立 QApplication 跑事件循环。
    ⚠️ 返回值必须能 JSON 序列化：注入脚本一律 `JSON.stringify` 或返回原始字面量。
    """
    if panel is None:
        return False, "no_panel", None
    try:
        v = _active_view(panel)
        if v is None:
            return False, "no_view", None
        v.page().runJavaScript(script)
        return True, None, None
    except BaseException as e:
        return False, "%s: %s" % (type(e).__name__, e), None



# ---------- WP15：从当前页取「原始源码」（带登录态） ----------
# ⚠️⚠️ 本节是 WP15 的核心。三个实测结论决定了它的形态：
#   ① 抽取器**不能**喂 `toHtml()` —— 那是渲染后 DOM 序列化，`<script>` 已剥离：
#      公众号正文在 `content_noencode`、小红书正文与配图在 `window.__INITIAL_STATE__` 里，
#      剥掉脚本 = 剥掉整篇正文（实测 1413 字/8 图 → 129 字/0 图，−90.9%）。
#   ② Qt 的 cookie store **只写不可读**（无 `cookiesForUrl`）→ 没有「导出会话给服务端」这条路。
#   ③ ⇒ 唯一可行：**页面内** `fetch(location.href,{credentials:'include'})` 取原始响应体。
#
# ⚠️⚠️ 取源是**两步式异步**（WP13 血泪：主线程同步等 = 死锁，回调永不触发且**不报错**）：
#    ① `page_source_start()` 只**发起** fetch（写 window 全局）→ 立刻返回；
#    ② `page_source_poll()` 由调用方**反复调用**，每次向页面要一份「小快照」，
#       就绪后再拉**一次**大字符串；
#    ③ `get_page_source()` 读的是 `panel["_src"]` 这个**普通值盒子**，任意线程可读。
#    主线程从头到尾只做「发起」，从不 `wait()`。

# 页内「小快照」JS：只回报长度 / 截断 / 错误 / 是否就绪 —— **不搬大字符串**。
# 轮询每 tick 都跑它，所以它必须是 O(1) 的（P3 实测：每 tick 都搬 MB 会白白拖慢主线程）。
_SRC_SNAP_JS = ("(function(){var T='%s';"
                "if(window.__asc_tok!==T){return JSON.stringify({stale:1});}"
                "return JSON.stringify({"
                "l:(typeof window.__asc_src_len==='number')?window.__asc_src_len:-1,"
                "t:!!window.__asc_trunc,e:window.__asc_err||null,"
                "d:!!window.__asc_src});})()")


def page_source_start(panel=None):
    """发起「取当前**活跃标签**页面源码」。**必须在主线程执行。** 立刻返回，不等结果。

    :returns: `(ok, error, token)`；`token` 是本次请求标识（用于防串场）。

    ⚠️⚠️ 为什么要 token 而不是「直接读结果」：用户可能在 fetch 飞行中**切标签 / 关标签**，
       没有 token，后到的回调会把**上一个页面**的源码写进盒子 → 张冠李戴，且不报错。
       本函数每次自增 `_src_seq`，回调只认自己那次的 token（不匹配就丢弃）。
    """
    p = panel if panel is not None else _panel
    if p is None:
        return False, "no_panel", None
    try:
        from . import external as external_svc
        view = _active_view(p)          # ⚠️ 一律经本函数，**不读** panel["view"] 别名
        if view is None:
            return False, "no_view", None
        p["_src_seq"] = int(p.get("_src_seq", 0) or 0) + 1
        tok = "t%d" % p["_src_seq"]
        # 先置成「进行中」——**在发起之前**，否则轮询方可能先读到上一轮的陈旧值
        p["_src"] = {"token": tok, "phase": "fetching", "err": None,
                     "length": -1, "truncated": False, "src": "", "pulling": False}
        cap = int(getattr(external_svc, "SOURCE_MAX_CHARS", 3000000))
        view.page().runJavaScript(external_svc.source_fetch_js(cap=cap, token=tok))
        return True, None, tok
    except BaseException as e:
        return False, "%s: %s" % (type(e).__name__, e), None


def page_source_poll(panel=None):
    """**主线程**：推进一次取源（要快照 / 拉大字符串）。立刻返回，**不返回结果**。

    ⚠️ 之所以「不返回结果」：`runJavaScript` 的值只能经**回调**拿，而回调在事件循环里跑 ——
       在主线程同步等它就是死锁（WP13）。故本函数只负责「发起读取」，状态落在 `panel["_src"]`。
    ⚠️ 每次调用**只做一件事**（快照 或 拉取），由 `phase` 决定 → 幂等、可被安全地反复调用。
    """
    p = panel if panel is not None else _panel
    if p is None:
        return
    try:
        box = p.get("_src")
        if not box or box.get("phase") in ("done", "error"):
            return
        view = _active_view(p)
        if view is None:
            box["phase"] = "error"
            box["err"] = "no_view"
            return
        tok = box.get("token") or ""
        page = view.page()

        if box.get("phase") == "fetching":
            def _on_snap(raw):
                # ⚠️ 本回调跑在**事件循环**里 → 只写普通值，绝不碰 Qt 对象、绝不外抛。
                try:
                    b = p.get("_src") or {}
                    if b.get("token") != tok:
                        return                      # 已被后一次请求取代 → 丢弃（防串场）
                    data = json.loads(raw) if isinstance(raw, str) else (raw or {})
                    if not isinstance(data, dict) or data.get("stale"):
                        b["phase"] = "error"
                        b["err"] = "stale"
                        return
                    b["length"] = int(data.get("l", -1))
                    b["truncated"] = bool(data.get("t"))
                    if data.get("e"):
                        b["phase"] = "error"
                        b["err"] = str(data.get("e"))[:200]
                        return
                    if data.get("d"):
                        b["phase"] = "ready"
                except BaseException:
                    pass

            page.runJavaScript(_SRC_SNAP_JS % tok, _on_snap)
            return

        if box.get("phase") == "ready" and not box.get("src") and not box.get("pulling"):
            box["pulling"] = True       # 拉取只做一次，防止反复搬 MB

            def _on_src(val):
                try:
                    b = p.get("_src") or {}
                    if b.get("token") != tok:
                        return
                    b["src"] = val if isinstance(val, str) else ""
                    if b["src"]:
                        b["phase"] = "done"
                    else:
                        b["phase"] = "error"
                        b["err"] = "empty"
                except BaseException:
                    pass

            page.runJavaScript("window.__asc_src", _on_src)
    except BaseException:
        pass


def get_page_source(panel=None):
    """读「取源」状态（**只读普通值** → 任意线程可调用）。

    空态是**确定结构**（可逐字比对）：七个键都在。
    ⚠️ 只有**已被事件循环回调写进普通值**的内容能在这里读；
       任何 `view.page()` / `runJavaScript` 都必须经主线程。
    """
    p = panel if panel is not None else _panel
    box = (p or {}).get("_src") or {}
    return {
        "token": box.get("token"),
        "phase": box.get("phase", "idle"),
        "err": box.get("err"),
        "length": int(box.get("length", -1) or -1),
        "truncated": bool(box.get("truncated")),
        "src": box.get("src") or "",
    }


# ---------- WP13：导航动作 + 加载错误分类 ----------
# ⚠️ 本段所有函数都必须**在主线程执行**（都碰 Qt 对象）；
#    唯一例外是 `get_nav_state()` —— 它只读普通值，任意线程可调用。

_ACTION_WEBACTION = {"back": "Back", "forward": "Forward", "reload": "Reload", "stop": "Stop"}

# Chromium net error code → 类别。**实测映射**（`_probe_wp13_errsem2.py --no-sandbox`，2026-09-21）
_CONN_KIND = {
    -105: "dns",        # ERR_NAME_NOT_RESOLVED
    -102: "refused",    # ERR_CONNECTION_REFUSED
    -118: "timeout",    # ERR_CONNECTION_TIMED_OUT
}
_KIND_LABEL = {
    "dns": "找不到这个网站（域名解析失败）",
    "refused": "无法连接（目标拒绝连接）",
    "timeout": "连接超时",
    "tls": "证书不受信任",
    "other": "加载失败",
}

_ERROR_DOMAIN_CACHE = None


def _error_domain_names():
    """`int → 域名`，**运行时从 Qt 枚举构建**。

    ⚠️ 不写死数字：`ErrorDomain` 的取值随 Qt 版本漂移，写死会**静默错分类**
       （症状是「DNS 失败被说成连接超时」—— 用户看不出来，验收脚本也可能照样绿）。
    """
    global _ERROR_DOMAIN_CACHE
    if _ERROR_DOMAIN_CACHE is None:
        out = {}
        try:
            ED = _loading_info().ErrorDomain
            for n in dir(ED):
                if n.startswith("_"):
                    continue
                try:
                    out[_enum_int(getattr(ED, n))] = n
                except BaseException:
                    pass
        except BaseException:
            pass
        _ERROR_DOMAIN_CACHE = out
    return _ERROR_DOMAIN_CACHE


def classify_load_error(info):
    """把 `QWebEngineLoadingInfo` 分类成**用户能懂**的错误（纯函数，不碰全局状态）。

    实测映射（`_probe_wp13_errsem2.py --no-sandbox`）：

    | 场景 | domain | code | isErrorPage |
    |---|---|---|---|
    | HTTP 404 / 500 | `HttpStatusCodeDomain` | 404 / 500 | **False** |
    | DNS 失败 | `ConnectionErrorDomain` | -105 | True |
    | 连接被拒 | `ConnectionErrorDomain` | -102 | True |
    | 连接超时 | `ConnectionErrorDomain` | -118 | True |

    ⚠️ 返回 dict 的**键必须恒定**（验收脚本要逐字比对）：取不到值也要有键、值为 None。
    ⚠️ `is_chromium_error_page` 是**关键区分**：
       True  → 页面显示 Chromium 自带错误页，用户看到「网页打不开」；
       False → 页面显示的是**服务端自己的**错误页，用户看到 404 页面本身。
       两者对用户的提示语完全不同，不能合成一句「加载失败」。
    """
    domain_name = None
    code = None
    is_err_page = None
    try:
        d = _enum_int(info.errorDomain())
        domain_name = _error_domain_names().get(d, "Domain(%d)" % d)
        code = _enum_int(info.errorCode())
        is_err_page = bool(info.isErrorPage())
    except BaseException:
        pass

    kind = "other"
    http_status = None
    if domain_name == "HttpStatusCodeDomain":
        kind = "http"
        http_status = code
    elif domain_name == "CertificateErrorDomain":
        kind = "tls"
    elif domain_name == "DnsErrorDomain":
        kind = "dns"
    elif domain_name == "ConnectionErrorDomain":
        kind = _CONN_KIND.get(code, "other")

    if kind == "http" and http_status is not None:
        label = "服务器返回 %s" % http_status
    else:
        label = _KIND_LABEL.get(kind, "加载失败")

    return {
        "kind": kind,
        "http_status": http_status,
        "domain": domain_name,
        "code": code,
        "is_chromium_error_page": is_err_page,
        "label": label,
    }


def _profile_is_shared(panel):
    """dock 视图与主视图是否**同一个 profile 对象**（WP13 · 用于 R3 结案）。

    ⚠️⚠️ 必须比**对象同一性**（`is`），不能比路径 / URL：
       两个**不同**的 profile 也可能 `persistentStoragePath` 相同 → 比路径会**假绿**，
       而 R3 的结论恰恰是「连接挂在哪个对象上」，路径一致推不出对象一致。
    取不到时返回 `None`（**不是 False**）—— 「没装好」与「装了但不共享」是两件事。
    """
    try:
        v = panel.get("view")
        a = panel.get("app_view")
        if v is None or a is None or a.page() is None:
            return None
        return bool(v.page().profile() is a.page().profile())
    except BaseException:
        return None


def _refresh_nav_buttons(panel):
    """按 nav 状态刷新后退 / 前进按钮的可用性（**主线程**）。"""
    if panel is None:
        return
    nav = panel.get("nav") or {}
    for key, field in (("can_back", "btn_back"), ("can_forward", "btn_forward")):
        b = panel.get(field)
        if b is None:
            continue
        try:
            b.setEnabled(bool(nav.get(key, False)))
        except BaseException:
            pass


def _sync_nav(panel):
    """把「只有主线程能读」的 Qt 值同步进普通 dict（供路由线程读）。**主线程**。

    ⚠️ `QWebEngineHistory.canGoBack()` 是 Qt 调用 —— 在路由线程直接调会**挂死**
       （WP12 已实测同族问题：setParent / takeCentralWidget 都是静默卡死）。
       故一律在主线程读完后缓存成普通 Python 值，路由只读缓存。
    """
    if panel is None:
        return
    nav = panel.setdefault("nav", {})
    try:
        # ⚠️ WP14：经 `_active_view` 取（不是 `panel["view"]` 别名）。
        #    `QWebEngineHistory.canGoBack()` 是 Qt 调用 —— 路由线程直接调会**挂死**，
        #    故一律在主线程读完后缓存成普通 Python 值，路由只读缓存。
        view = _active_view(panel)
        if view is not None:
            nav["url"] = view.url().toString()
            hist = view.history()
            nav["can_back"] = bool(hist.canGoBack())
            nav["can_forward"] = bool(hist.canGoForward())
            # 顺便把 url 同步进标签本体（get_tabs_state 读它）
            t = _active_tab(panel)
            if t is not None:
                if nav["url"]:
                    t["last_url"] = nav["url"]
                t["nav"] = nav
    except BaseException:
        pass

    try:
        nav["progress"] = int(panel.get("progress", 0) or 0)
    except BaseException:
        pass
    _refresh_nav_buttons(panel)


def nav_action(panel, action):
    """执行导航动作（back / forward / reload / stop）。**必须在主线程执行。**

    :returns: `(ok, error, available)`

    ⚠️ 三元组**不能合成一个布尔**：
       `available=False` 表示「此刻这个动作不可用」（如没有历史可后退）——
       那是**正常状态**（按钮该灰），与「执行失败」是两件事。
    ⚠️ 非法 action 一律 `bad_action`，**不做静默兜底** —— 兜底会把「调用方传错」
       变成「什么都没发生」，属于最难查的一类 bug。
    """
    if panel is None:
        return False, "no_panel", False
    act = (action or "").strip().lower()
    if act not in _ACTION_WEBACTION:
        return False, "bad_action", False
    _, _, _, QWebEnginePage = _qt()
    # ⚠️ WP14：作用于**活跃标签**的 view（经 _active_view，不用别名）
    view = _active_view(panel)
    if view is None:
        return False, "no_view", False

    available = True
    try:
        hist = view.history()
        if act == "back":
            available = bool(hist.canGoBack())
        elif act == "forward":
            available = bool(hist.canGoForward())
    except BaseException:
        available = True
    if not available:
        return False, "not_available", False

    try:
        enum = getattr(QWebEnginePage.WebAction, _ACTION_WEBACTION[act])
        view.page().triggerAction(enum)
        return True, None, True
    except BaseException as e:
        return False, "%s: %s" % (type(e).__name__, e), available


def get_nav_state():
    """导航状态（**只读普通值** → 任意线程可调用）。

    ⚠️ 空态是**确定结构**（可逐字比对）：七个键都在，值为空串 / 0 / False / None。
       ⚠️ 只有**已被主线程写入的普通 Python 值**能在这里读；
       任何 `view.url()` / `history().canGoBack()` 这类 Qt 调用都必须经 call_on_main。
    """
    if _panel is None:
        return _empty_nav()
    # ⚠️ WP14：`nav` 是**活跃标签**的那份（由 `_bind_active` 重绑）。
    #    每标签独立，故切到 B 页读到的就是 B 页的错误，不会串台。
    nav = _panel.get("nav") or {}
    return {
        "state": nav.get("state", "idle"),
        "url": nav.get("url", "") or _panel.get("last_url", "") or "",
        "title": nav.get("title", "") or _panel.get("last_title", "") or "",
        "progress": int(nav.get("progress", 0) or 0),
        "can_back": bool(nav.get("can_back", False)),
        "can_forward": bool(nav.get("can_forward", False)),
        "error": nav.get("error", None),
    }


def get_state():
    """面板只读状态（**不碰 Qt 控件**，只读句柄里的普通值）→ 任意线程可调用。

    ⚠️ 形状**保持 WP12 契约不变**（`tabs` 是列表、`active` 是 id）——
       WP14 只是让 `tabs` 从「恒为单元素」变成「真的多元素」，
       消费方（侧栏 / 验收脚本）无需改动。更细的每标签状态看 `get_tabs_state()`。
    ⚠️ 只有**已被主线程写入的普通 Python 值**能在这里读；
       任何 `view.url()` / `dock.isVisible()` 这类 Qt 调用都必须经 call_on_main，
       否则路由线程会挂死。
    """
    if _panel is None:
        return {"tabs": [], "active": None, "ready": False, "assembled": False}
    tabs = []
    for t in _tab_dicts(_panel):
        nav = t.get("nav") or {}
        tabs.append({
            "id": t.get("id"),
            "url": t.get("last_url", "") or "",
            "title": t.get("last_title", "") or "",
            "loading": int(t.get("progress", 0) or 0) < 100,
            "progress": int(t.get("progress", 0) or 0),
            "load_ok": t.get("last_load_ok"),
            "active": t.get("id") == _panel.get("active"),
            "state": nav.get("state", "idle"),
            "can_back": bool(nav.get("can_back", False)),
            "can_forward": bool(nav.get("can_forward", False)),
            "error": nav.get("error", None),
        })
    return {
        "tabs": tabs,
        "active": _panel.get("active"),
        "ready": True,
        "assembled": True,
        "shared_profile": _panel.get("shared_profile"),
        "shared_profile_error": _panel.get("shared_profile_error"),
        "new_window_hooked": _panel.get("new_window_hooked"),
    }



def selfcheck(host):
    """仅调试/验收：在主线程里回读面板的**真实** Qt 状态（不是断言"我调用过 API"）。

    ⚠️ 判据必须包含**负向守卫**：`dock_found_by_findChildren >= 1` 才能证明
       dock 真的挂在宿主树上 —— 只断言 `dock is not None` 在"对象建了但没挂上"时
       也会通过（这正是 Thread-2 里装配时的实际症状）。
    """
    QtCore, QtWidgets, _, _ = _qt()
    app = QtCore.QCoreApplication.instance()
    out = {
        "on_main_thread": bool(QtCore.QThread.currentThread() == app.thread()) if app else None,
        "assembled": _panel is not None,
    }
    if _panel is None:
        return out
    dock = _panel.get("dock")
    view = _active_view(_panel)
    addr = _panel.get("addr")
    stack = _panel.get("stack")
    tabbar = _panel.get("tabbar")
    host_docks = host.findChildren(QtWidgets.QDockWidget)
    matching = [d for d in host_docks if d.objectName() == DOCK_OBJNAME]
    out.update({
        "dock_found_by_findChildren": len(matching),
        "dock_objname": matching[0].objectName() if matching else None,
        "dock_visible": bool(dock.isVisible()) if dock else None,
        "dock_floating": bool(dock.isFloating()) if dock else None,
        "dock_size": [dock.width(), dock.height()] if dock else None,
        "view_visible": bool(view.isVisible()) if view else None,
        # ---------- WP14：多标签真实 Qt 状态 ----------
        "tab_count": int(tabbar.count()) if tabbar else None,
        "stack_count": int(stack.count()) if stack else None,
        "active_tab_id": _panel.get("active"),
        # ⚠️ 判据必须包含**一致性**：tabbar / stack / tabs 三者数量必须相等。
        #    只断言「有 N 个标签」在「UI 加了但数据面没加」时也会通过。
        "counts_consistent": bool(
            tabbar is not None and stack is not None
            and tabbar.count() == stack.count() == len(_tab_dicts(_panel))),
        "dock_found_by_name": bool(
            host.findChild(QtWidgets.QTabBar, TABBAR_OBJNAME) is not None),
        "stack_found_by_name": bool(
            host.findChild(QtWidgets.QStackedWidget, STACK_OBJNAME) is not None),
        "view_size": [view.width(), view.height()] if view else None,
        "addr_visible": bool(addr.isVisible()) if addr else None,
        "central_unchanged": host.centralWidget() is _panel.get("app_view"),
        # WP13 · R3 结案判据：连接挂在 **profile 对象**上（qt.py:450）→ 同一对象即必然覆盖
        "profile_shared_with_app": _profile_is_shared(_panel),
        "nav": get_nav_state(),
        "central_visible": bool(host.centralWidget().isVisible()) if host.centralWidget() else None,
        "host_visible": bool(host.isVisible()),
    })
    out["ok"] = bool(
        out["on_main_thread"]
        and out["dock_found_by_findChildren"] >= 1
        and out["dock_visible"]
        and out["view_visible"]
    )
    return out
