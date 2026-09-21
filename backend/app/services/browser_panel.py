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
import threading

__all__ = [
    "DOCK_OBJNAME", "VIEW_OBJNAME", "ADDR_OBJNAME", "STATUS_OBJNAME",
    "BTN_BACK_OBJNAME", "BTN_FORWARD_OBJNAME", "BTN_RELOAD_OBJNAME", "BTN_STOP_OBJNAME",
    "assemble", "show", "hide", "close", "navigate", "load_html",
    "is_assembled", "get_state", "get_nav_state", "nav_action",
    "classify_load_error", "selfcheck",
]

DOCK_TITLE = "AI 浏览器"
DOCK_OBJNAME = "asc_browser_dock"
PANEL_OBJNAME = "asc_browser_panel"
ADDR_OBJNAME = "asc_addr"
VIEW_OBJNAME = "asc_browser_view"
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

        # ---------- 浏览器内容 ----------
        view = QWebEngineView()
        view.setObjectName(VIEW_OBJNAME)

        # ⚠️ 复用应用界面所在的 profile（**不是**新建默认 profile）：
        #    V3 §6.1 要求「首次登录后免登录」→ cookie / storage 必须与主窗口同一 profile；
        #    新建 profile 会导致每次访问都要重新登录（症状像"登录不保存"）。
        shared_ok = True
        shared_err = None
        try:
            if app_view is not None and hasattr(app_view, "page") and app_view.page() is not None:
                prof = app_view.page().profile()
                view.setPage(QWebEnginePage(prof, view))
        except BaseException as e:
            shared_ok = False
            shared_err = "%s: %s" % (type(e).__name__, e)

        pl.addWidget(view, 1)

        # ---------- 状态栏 ----------
        status = QtWidgets.QLabel("就绪")
        status.setObjectName(STATUS_OBJNAME)
        pl.addWidget(status)

        dock.setWidget(panel)

        _panel = {
            "dock": dock, "panel": panel, "addr": addr, "view": view,
            "status": status, "app_view": app_view,
            "btn_go": btn_go, "btn_close": btn_close,
            "btn_back": btn_back, "btn_forward": btn_forward,
            "btn_reload": btn_reload, "btn_stop": btn_stop,
            "shared_profile": shared_ok, "shared_profile_error": shared_err,
            # WP13 导航状态：**只存普通值**（路由线程只读它，绝不直接读 Qt 对象 —— 会挂死）
            "nav": {"state": "idle", "url": "", "title": "", "progress": 0,
                    "can_back": False, "can_forward": False, "error": None},
        }

        # ---------- 接线 ----------
        def _do_navigate():
            navigate(_panel, addr.text())

        addr.returnPressed.connect(_do_navigate)
        btn_go.clicked.connect(_do_navigate)
        btn_close.clicked.connect(lambda: hide(host))
        # WP13 导航动作：**统一走 nav_action**（单一入口 → 可用性判定与错误回传只写一份）
        btn_back.clicked.connect(lambda: nav_action(_panel, "back"))
        btn_forward.clicked.connect(lambda: nav_action(_panel, "forward"))
        btn_reload.clicked.connect(lambda: nav_action(_panel, "reload"))
        btn_stop.clicked.connect(lambda: nav_action(_panel, "stop"))

        def _on_load_finished(ok):
            try:
                url = view.url().toString()
                if _panel is not None:
                    _panel["last_load_ok"] = bool(ok)
                    _panel["last_url"] = url
                    if not addr.hasFocus():
                        addr.setText(url)      # 回填（跟随页内跳转）
                status.setText("加载完成" if ok else "加载失败")
                _sync_nav(_panel)      # WP13：刷新 can_back / can_forward（必须在主线程读）
            except BaseException:
                pass

        view.loadFinished.connect(_on_load_finished)

        def _on_title_changed(title):
            try:
                if _panel is not None:
                    _panel["last_title"] = title
                if title:
                    status.setText(title)
            except BaseException:
                pass

        view.titleChanged.connect(_on_title_changed)

        def _on_load_progress(p):
            try:
                if _panel is not None:
                    _panel["progress"] = int(p)
            except BaseException:
                pass

        view.loadProgress.connect(_on_load_progress)

        def _on_loading_changed(info):
            """WP13 状态机：把加载事件分类成 nav 状态（**唯一**写 nav["state"] 的地方）。

            ⚠️ 必须用 `loadingChanged`（带 `QWebEngineLoadingInfo`）而不是只看
               `loadFinished`：后者给不出 `errorDomain` / `errorCode` / `isErrorPage`，
               而「服务器返回 404」与「域名解析失败」对用户是**两句完全不同的话**
               （实测：HTTP 404 时 `loadFinished=False`，但页面显示的是**服务端的** 404 页）。
            """
            try:
                if _panel is None:
                    return
                nav = _panel.setdefault("nav", {})
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
                    # ⚠️ 服务端自己的错误页（is_chromium_error_page=False）仍**显示服务端内容**
                    #    → 归 http_error，文案要说「服务器返回 404」，不能说「网页打不开」。
                    nav["state"] = "http_error" if err.get("kind") == "http" else "net_error"
                    nav["error"] = err
                _sync_nav(_panel)
            except BaseException:
                pass

        try:
            view.page().loadingChanged.connect(_on_loading_changed)
            _panel["loading_hooked"] = True
        except BaseException as e:
            _panel["loading_hooked"] = "%s: %s" % (type(e).__name__, e)

        # ⚠️ `target=_blank` / `window.open` 必须**留在本视图内**打开：
        #    否则 Qt 会尝试开新窗口，而 pywebview 没接管它 → 表现成「点了链接没反应」；
        #    更糟的是若导航到应用自身文档，会把应用界面顶掉（`md.js::createMd` 那条铁律的同类风险）。
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
            _panel["new_window_hooked"] = True
        except BaseException as e:
            _panel["new_window_hooked"] = "%s: %s" % (type(e).__name__, e)

        host.addDockWidget(QtCore.Qt.RightDockWidgetArea, dock)
        host.resizeDocks([dock], [520], QtCore.Qt.Horizontal)
        dock.show()

        if initial_url:
            navigate(_panel, initial_url)

        return _panel


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
        panel["view"].setUrl(QtCore.QUrl(final))
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
        if base_url:
            panel["view"].setHtml(html, QtCore.QUrl(base_url))
        else:
            panel["view"].setHtml(html)
        return True, None
    except BaseException as e:
        return False, "%s: %s" % (type(e).__name__, e)


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
        view = panel.get("view")
        if view is not None:
            nav["url"] = view.url().toString()
            hist = view.history()
            nav["can_back"] = bool(hist.canGoBack())
            nav["can_forward"] = bool(hist.canGoForward())
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
    view = panel.get("view")
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
        return {"state": "idle", "url": "", "title": "", "progress": 0,
                "can_back": False, "can_forward": False, "error": None}
    nav = _panel.get("nav") or {}
    return {
        "state": nav.get("state", "idle"),
        "url": nav.get("url", "") or _panel.get("last_url", "") or "",
        "title": _panel.get("last_title", "") or "",
        "progress": int(nav.get("progress", 0) or 0),
        "can_back": bool(nav.get("can_back", False)),
        "can_forward": bool(nav.get("can_forward", False)),
        "error": nav.get("error", None),
    }


def get_state():
    """面板只读状态（**不碰 Qt 控件**，只读句柄里的普通值）→ 任意线程可调用。

    ⚠️ 只有**已被主线程写入的普通 Python 值**能在这里读；
       任何 `view.url()` / `dock.isVisible()` 这类 Qt 调用都必须经 call_on_main，
       否则路由线程会挂死。
    """
    if _panel is None:
        return {"tabs": [], "active": None, "ready": False, "assembled": False}
    return {
        "tabs": [{
            "id": "main",
            "url": _panel.get("last_url", ""),
            "title": _panel.get("last_title", ""),
            "loading": _panel.get("progress", 100) < 100,
            "progress": _panel.get("progress", 0),
            "load_ok": _panel.get("last_load_ok"),
        }],
        "active": "main",
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
    view = _panel.get("view")
    addr = _panel.get("addr")
    host_docks = host.findChildren(QtWidgets.QDockWidget)
    matching = [d for d in host_docks if d.objectName() == DOCK_OBJNAME]
    out.update({
        "dock_found_by_findChildren": len(matching),
        "dock_objname": matching[0].objectName() if matching else None,
        "dock_visible": bool(dock.isVisible()) if dock else None,
        "dock_floating": bool(dock.isFloating()) if dock else None,
        "dock_size": [dock.width(), dock.height()] if dock else None,
        "view_visible": bool(view.isVisible()) if view else None,
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
