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
    "assemble", "show", "hide", "close", "navigate", "load_html",
    "is_assembled", "get_state", "selfcheck",
]

DOCK_TITLE = "AI 浏览器"
DOCK_OBJNAME = "asc_browser_dock"
PANEL_OBJNAME = "asc_browser_panel"
ADDR_OBJNAME = "asc_addr"
VIEW_OBJNAME = "asc_browser_view"
STATUS_OBJNAME = "asc_status"

# 面板句柄：**只在主线程访问**（所有公开函数都经 call_on_main 投递，故天然串行）
_panel = None
_panel_lock = threading.Lock()


def _qt():
    """惰性取 Qt 符号。**不在模块顶层 import** —— 后端必须能在无 Qt 环境 import 本模块。"""
    from qtpy import QtCore, QtWidgets
    from qtpy.QtWebEngineWidgets import QWebEnginePage, QWebEngineView
    return QtCore, QtWidgets, QWebEngineView, QWebEnginePage


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
            "shared_profile": shared_ok, "shared_profile_error": shared_err,
        }

        # ---------- 接线 ----------
        def _do_navigate():
            navigate(_panel, addr.text())

        addr.returnPressed.connect(_do_navigate)
        btn_go.clicked.connect(_do_navigate)
        btn_close.clicked.connect(lambda: hide(host))

        def _on_load_finished(ok):
            try:
                url = view.url().toString()
                if _panel is not None:
                    _panel["last_load_ok"] = bool(ok)
                    _panel["last_url"] = url
                    if not addr.hasFocus():
                        addr.setText(url)      # 回填（跟随页内跳转）
                status.setText("加载完成" if ok else "加载失败")
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
