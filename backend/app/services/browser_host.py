"""AI 浏览器宿主（阶段 2 · WP12）：宿主定位 + 主线程投递 + 只读状态面。

本模块只回答三个问题，每个都对应一条**违反就静默出错**的硬约束：

1. **谁执行？**（主线程约束）
   pywebview 的 `after_start` 回调与 FastAPI 的 HTTP 处理都跑在**工作线程**
   （阶段 1 实测为 `Thread-2`），而一切 Qt 控件操作必须在主线程。
   在工作线程碰 Qt **不是报错，是没有任何输出地挂死**（阶段 0 阴性对照 rc=3）。
   ⇒ 所有 UI 动作必须经 `call_on_main()` 投递。

2. **投到哪里？**（宿主定位约束）
   `BrowserView.instances` 是 `uid → 实例` 的字典，**没有「主窗口」这一概念**。
   上游 `qt.py:969` 自己写 `list(instances.values())[0]` 并注释 `# arbitrary instance`
   —— 顺序一变就取错对象（可能取到侧栏或某个标签页，且症状是「投递成功但界面没反应」）。
   ⇒ 本项目一律按 `uid == "master"` 取（`qt.py:945` 主窗口的 uid）。

3. **投递有回执吗？**（超时约束）
   上游投递原语 `BrowserView.create_window_trigger`（`qt.py:100`，`QtCore.Signal(object)`）
   是**发射即忘**：没有回执、没有超时。主循环若已退出（窗口正在关闭），
   emit 之后回调永远不会执行 ⇒ 无超时就是**永久挂死**（阶段 1 踩过同款，故此处强制带超时）。

⚠️ 模块顶层**禁止** import `webview` / `qtpy`：
   后端必须能在浏览器模式、单元测试、验收脚本里正常 import（见 `_load_qt()` 惰性加载）。
"""
import threading
import time
from urllib.parse import urlsplit

__all__ = [
    "HostUnavailable",
    "MainThreadTimeout",
    "DEFAULT_TIMEOUT",
    "host_view",
    "is_available",
    "call_on_main",
    "state",
    "normalize_url",
    "set_sidebar_payload",
    "sidebar_payload",
    "clear_sidebar_payload",
    "selftest_mainthread",
    "selftest_timeout",
    "request_browser_open",
    "request_browser_close",
    "request_browser_navigate",
    "request_panel_selfcheck",
    "request_sidebar_show",
]

# 只允许这两种协议。**显式白名单**：`file:` / `javascript:` / `data:` 一律拒 ——
# 地址栏内容来自用户输入，允许 `file:` 等于把本地文件系统暴露给一个能执行 JS 的视图。
_ALLOWED_SCHEMES = ("http", "https")

DEFAULT_TIMEOUT = 5.0
"""主线程投递默认超时（秒）。取值依据：正常投递在毫秒级完成；5s 足够覆盖
「主线程正在处理一次页面导航」的极端情况，又不会让接口长时间无响应。"""


class HostUnavailable(RuntimeError):
    """没有可用的 Qt 宿主（浏览器模式 / 单元测试 / 窗口尚未创建）。

    ⚠️ 这是**正常状态**，不是故障：后端在无桌面环境必须照常起得来，
       只是没有浏览器能力。调用方应把它转成 503 + 明确错误码，而不是 500。
    """


class MainThreadTimeout(RuntimeError):
    """主线程投递超时：回调未在超时内执行完（主循环可能已退出）。"""


def _load_qt():
    """惰性取 `(BrowserView, QtCore)`。导入失败一律收敛成 `HostUnavailable`。"""
    try:
        from webview.platforms.qt import BrowserView
        from qtpy import QtCore
    except Exception as e:                      # ImportError / 无 Qt 运行库 / ABI 不匹配
        raise HostUnavailable("Qt 宿主不可用（%s: %s）" % (type(e).__name__, e))
    return BrowserView, QtCore


def host_view(uid="master"):
    """取宿主 `BrowserView`；取不到抛 `HostUnavailable`。

    ⚠️ 只认 `uid == "master"`，**不做 `values()[0]` 兜底** —— 兜底会把「取错对象」
       变成一个更难查的 bug（投递成功但没人执行）。宁可显式失败，并在错误里
       列出当前有哪些 uid 供定位。
    """
    BrowserView, _ = _load_qt()
    inst = getattr(BrowserView, "instances", None) or {}
    view = inst.get(uid)
    if view is None:
        raise HostUnavailable("未找到 uid=%r 的宿主窗口（现有：%s）" % (uid, sorted(inst)))
    return view


def is_available():
    """宿主窗口是否存在。**不碰 Qt 控件**，任意线程可调用。"""
    try:
        host_view()
        return True
    except HostUnavailable:
        return False


def call_on_main(fn, *args, timeout=DEFAULT_TIMEOUT, **kwargs):
    """把 `fn(*args, **kwargs)` 投到 Qt 主线程执行，等结果返回。

    实现复用 pywebview 自己的投递通道（`create_window_trigger`，上游用它从工作线程建窗口，
    是**上游验证过**的机制），只在其外包一层**超时 + 异常回传**。

    :raises HostUnavailable: 没有宿主窗口
    :raises MainThreadTimeout: 回调没在 `timeout` 秒内跑完（**不是挂死**）
    :returns: `fn` 的返回值；`fn` 抛的异常原样重抛给调用方
    """
    view = host_view()
    box = {"result": None, "error": None}
    ev = threading.Event()

    def _run():
        try:
            box["result"] = fn(*args, **kwargs)
        except BaseException as e:      # ⚠️ BaseException：槽里抛 SystemExit 也必须带回调用方
            box["error"] = e
        finally:
            ev.set()

    view.create_window_trigger.emit(_run)
    if not ev.wait(timeout):
        raise MainThreadTimeout(
            "主线程投递超时（%.1fs）：%r" % (timeout, getattr(fn, "__name__", fn)))
    if box["error"] is not None:
        raise box["error"]
    return box["result"]


def state():
    """宿主只读状态。空态是**确定结构**：`{"tabs": [], "active": None, "ready": False}`。

    ⚠️ 三条工程约束：
    1. 空态必须可**逐字比对**：返回 500 或空 body 都会让验收判据无法自动化
       （「不是 500」这种断言天生判不出东西）。
    2. `ready` 的语义是「有宿主窗口、可接受浏览器动作」，**不是**「页面已加载」。
       浏览器模式（fallback / 单元测试）下恒为 False —— 这是正确值，不是故障。
    3. 本函数**不碰 Qt 控件**（只查 `instances` 字典 + 面板句柄里的普通值）
       → 可在路由线程安全调用。
       ⚠️ 将来要读标签的实时 URL / 标题 / 加载进度时，那些值来自 Qt 对象，
          必须改走 `call_on_main()`；**不能**在这个函数里直接读，否则路由线程会挂死。
    """
    if not is_available():
        return {"tabs": [], "active": None, "ready": False}
    try:
        from . import browser_panel
        if browser_panel.is_assembled():
            return browser_panel.get_state()
    except Exception:
        pass
    return {"tabs": [], "active": None, "ready": True}


# ---------- URL 归一化（WP13 契约 · 遵守「身份用归一化、抓取用原始输入」） ----------

def normalize_url(raw):
    """把用户输入归一化成**可加载**的 URL；不可用时返回 `""`。

    ⚠️ 本项目的既有纪律（外部内容源接入时定下的，违反会静默出错）：
       **身份用归一化、抓取用原始输入**，且前后端必须**成对**。
       本函数只做「补协议」这类**让加载能成功**的转换，**不改写语义**
       （不补斜杠、不删查询串、不小写化路径）—— 否则同一页面会算出两个身份。

    拒绝规则（每条都有具体理由，不是保守）：
      · 协议不在 `http`/`https` 白名单 → 拒（`file:` 会暴露本地文件系统）；
      · host 为空 → 拒；
      · host 非 ASCII → 拒（本项目约定：host 校验要 ASCII + 含点号，
        否则 `http://中文` 这类输入会让 Qt 直接抛错，错误信息对用户毫无意义）。
    """
    s = (raw or "").strip()
    if not s:
        return ""
    # 补协议：`example.com/x` → `https://example.com/x`
    if "://" not in s:
        s = "https://" + s
    try:
        parts = urlsplit(s)
    except Exception:
        return ""
    scheme = (parts.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        return ""
    host = parts.hostname or ""
    if not host:
        return ""
    try:
        host.encode("ascii")
    except UnicodeEncodeError:
        return ""
    # ⚠️ 含点号校验：`localhost` 被**显式放行**（本地调试要能用），
    #    其余必须含点号 —— 否则 `https://abc` 这种输入会走到 DNS 失败，
    #    用户看到的是一条看不懂的网络错误，不如在这里就拒掉。
    if "." not in host and host not in ("localhost", "127.0.0.1"):
        return ""
    return s



def _probe_is_main():
    """投递探针：回报回调是否跑在 Qt 主线程（用 Qt 语义判断，不用线程名猜）。"""
    _, QtCore = _load_qt()
    app = QtCore.QCoreApplication.instance()
    if app is None:
        raise HostUnavailable("QApplication 尚未创建")
    return {
        "is_main": bool(QtCore.QThread.currentThread() == app.thread()),
        "ident": threading.get_ident(),
    }


def selftest_mainthread():
    """仅调试：证明「从工作线程投递 → 回调在主线程执行」。

    判据必须成对，缺一就是恒真断言：
      - `ran_on_main` 为真：回调确实在主线程；
      - `caller_is_main` 为假：**调用方不在主线程** —— 否则「同线程直接调用」会让上一条恒真。
      - `roundtrip_ms` 仅作观测，不作门禁（机器负载波动大）。
    """
    caller_ident = threading.get_ident()
    t0 = time.perf_counter()
    probe = call_on_main(_probe_is_main)
    elapsed = (time.perf_counter() - t0) * 1000.0
    return {
        "ok": True,
        "ran_on_main": probe["is_main"],
        "caller_is_main": caller_ident == probe["ident"],
        "roundtrip_ms": round(elapsed, 2),
        "caller_ident": caller_ident,
        "callback_ident": probe["ident"],
    }


def selftest_timeout(block_s=2.0, timeout=0.4):
    """仅调试：证明「投递超时会**明确报错**，而不是永久挂死」。

    判据是**计时断言**：接口必须在 `timeout + 余量` 内返回，而不是等 `block_s` 跑完。
    ⚠️ 副作用：会真实阻塞 Qt 主线程 `block_s` 秒（仅调试接口，勿在生产路径调用）。

    :returns: `{"ok": True, "timed_out": True, "elapsed_ms": float}`
    """
    def _block():
        time.sleep(block_s)

    t0 = time.perf_counter()
    timed_out = False
    try:
        call_on_main(_block, timeout=timeout)
    except MainThreadTimeout:
        timed_out = True
    elapsed = (time.perf_counter() - t0) * 1000.0
    return {
        "ok": True,
        "timed_out": timed_out,
        "elapsed_ms": round(elapsed, 2),
        "block_s": block_s,
        "timeout_s": timeout,
    }


# ---------- 侧栏数据面（WP12 契约 · WP16/WP17 消费） ----------
# ⚠️ 为什么侧栏内容要经后端内存、而不是「Qt 直接把字符串塞进侧栏页面」：
#    侧栏是一个加载本地页面的 `QWebEngineView`，Python 无法直接设它的 JS 变量
#    （只能 `evaluate_js`，而那只在 Qt 宿主下可用）。走数据面后，侧栏的显示逻辑
#    在**浏览器模式下也能完整验证** —— 否则这条链只能等真机才测得到。
# ⚠️ 进程内存、不落库：与 V3 §6.3 的 N04 口径一致（页面内容不上传、浏览历史不落库）。

_sidebar = {"url": "", "title": "", "selection": "", "ts": 0.0}
_sidebar_lock = threading.Lock()


def set_sidebar_payload(url="", title="", selection=""):
    """写入侧栏载荷，返回是否写入成功。

    ⚠️ 一律 `str()` 强转：调用方可能传进来非字符串（Qt 侧 JS 值回来常是各种形态），
       不转会让下游的 `.strip()` / 前端渲染在不该出错的地方出错。
    """
    with _sidebar_lock:
        _sidebar.update({
            "url": str(url or ""),
            "title": str(title or ""),
            "selection": str(selection or ""),
            "ts": time.time(),
        })
    return True


def sidebar_payload():
    """读侧栏载荷（**确定空态**：四个字段都在，值为空串 / 0.0，不是 404 也不是 null）。"""
    with _sidebar_lock:
        return dict(_sidebar)


def clear_sidebar_payload():
    """清空侧栏载荷，返回「清空前是否有内容」。"""
    with _sidebar_lock:
        had = bool(_sidebar["url"] or _sidebar["selection"])
        _sidebar.update({"url": "", "title": "", "selection": "", "ts": 0.0})
    return had


def _show_sidebar_on_main():
    """在 Qt 主线程里把浏览器面板显示出来。

    ⚠️ 为什么"显示面板"与"侧栏载荷"是两件事：
       侧栏载荷（选中内容）走 HTTP 数据面，**浏览器模式下也能验证**；
       而"把面板显示出来"必须碰 Qt 控件 → 只在有宿主时才有意义。
       合成一件事会让「没宿主」这种**正常情况**看起来像失败。
    """
    from . import browser_panel
    host = host_view()
    browser_panel.show(host)
    return True


def request_sidebar_show():
    """请求宿主显示浏览器面板。返回 `(shown, error_message)`。

    ⚠️ 宿主不可用**不算失败**：浏览器模式下没有面板可显示，但数据面照常工作。
       返回二元组而不是单个 bool，是为了让调用方能区分「没宿主」（正常）与
       「投递失败」（真问题）—— 合成一个布尔就再也分不开了。
    """
    if not is_available():
        return False, "host_unavailable"
    try:
        call_on_main(_show_sidebar_on_main)
    except Exception as e:          # HostUnavailable / MainThreadTimeout / 槽内异常
        return False, "%s: %s" % (type(e).__name__, e)
    return True, None


def _open_on_main(url, width):
    from . import browser_panel
    host = host_view()
    browser_panel.show(host, url=url, width=width)
    return True


def request_browser_open(url="", width=520):
    """打开浏览器面板（可同时导航）。返回 `(shown, error_message)`。"""
    if not is_available():
        return False, "host_unavailable"
    try:
        call_on_main(_open_on_main, url, width)
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e)
    return True, None


def _navigate_on_main(url):
    from . import browser_panel
    host = host_view()
    panel = browser_panel.assemble(host)      # 幂等；未装配时自动装配
    return browser_panel.navigate(panel, url)


def request_browser_navigate(url):
    """在当前浏览器视图里导航。返回 `(ok, error)`。

    ⚠️ **归一化必须先于宿主检查**：否则浏览器模式（无宿主）下传一个 `file://` 地址，
       会先返回 `host_unavailable` 而把 `invalid_url` 盖掉 ——
       验收脚本就再也分不清「用户输入非法」与「环境没宿主」，
       而这正是本函数把两者分开的**唯一理由**（实测踩到过：先查宿主时该判据恒为 false）。
    """
    final = normalize_url(url)
    if not final:
        return False, "invalid_url"
    if not is_available():
        return False, "host_unavailable"
    try:
        ok, err = call_on_main(_navigate_on_main, final)
        return bool(ok), err
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e)


def _close_on_main():
    from . import browser_panel
    return browser_panel.hide()


def request_browser_close():
    """隐藏浏览器面板（保留页面状态）。返回 `(hidden, error_message)`。"""
    if not is_available():
        return False, "host_unavailable"
    try:
        call_on_main(_close_on_main)
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e)
    return True, None


def _selfcheck_on_main():
    from . import browser_panel
    return browser_panel.selfcheck(host_view())


def request_panel_selfcheck():
    """仅调试/验收：在主线程回读面板**真实** Qt 状态。

    ⚠️ 必须经主线程：`dock.isVisible()` / `findChildren()` 都是 Qt 调用，
       在路由线程直接调会挂死。
    """
    if not is_available():
        return None, "host_unavailable"
    try:
        return call_on_main(_selfcheck_on_main), None
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, e)

