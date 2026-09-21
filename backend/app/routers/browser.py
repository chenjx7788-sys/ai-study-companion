"""应用内 AI 浏览器（阶段 2）的状态与调试接口。

⚠️ 本路由必须在 `main.py` 的 SPA catch-all **之前**注册（与 `/api/assets` 同款约束）：
   catch-all 对 `api/` 前缀一律 404，晚注册会被它吞掉 —— 且症状是 **404**（看起来像
   「路由没写对」）而不是「注册顺序错了」，极难定位。

⚠️ 设计取舍：本模块**只做数据面**（状态 / 调试），不做 UI 动作编排。
   原因：数据面在浏览器模式下也能完整验证；UI 动作要碰 Qt 控件，必须经
   `browser_host.call_on_main()` 投递到主线程，那部分（WP13/WP14）单独落。
"""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..services import browser_host

router = APIRouter(prefix="/browser", tags=["browser"])


def _host_unavailable(e):
    """把「没有宿主」表达成 503 + 明确错误码（**不是 500**）。

    判据上必须能区分「环境不支持」（503，正常）与「代码出错」（500，故障）——
    混在一起会让验收脚本无法归因，只能靠人读日志。
    """
    return JSONResponse(
        status_code=503,
        content={"ok": False, "error": "host_unavailable", "detail": str(e)},
    )


@router.get("/state")
def get_state():
    """宿主只读状态。

    空态**确定结构**（可逐字比对）：`{"tabs": [], "active": null, "ready": false}`。
    浏览器模式下 `ready` 为 `false` 是**正确值**，不是故障。
    """
    return browser_host.state()


@router.post("/selftest/mainthread")
def selftest_mainthread():
    """仅调试：从工作线程投递 → 回报是否在主线程执行（WP12 判据 2）。"""
    try:
        return browser_host.selftest_mainthread()
    except browser_host.HostUnavailable as e:
        return _host_unavailable(e)


@router.post("/selftest/timeout")
def selftest_timeout(block_s: float = 2.0, timeout: float = 0.4):
    """仅调试：证明投递超时是**明确异常**而非挂死（WP12 判据 2 的第二半）。

    ⚠️ 会真实阻塞主线程 `block_s` 秒 —— 只在调试/验收时调用。
    """
    try:
        return browser_host.selftest_timeout(block_s=block_s, timeout=timeout)
    except browser_host.HostUnavailable as e:
        return _host_unavailable(e)


# ---------- 侧栏数据面（WP12 契约，WP16/WP17 消费） ----------
# ⚠️ 为什么侧栏内容要走后端内存、而不是「Qt 直接把字符串塞进侧栏页面」：
#    侧栏是一个加载本地页面的 `QWebEngineView`，Python 无法直接设它的 JS 变量
#    （必须经 `evaluate_js`，而那只在 Qt 宿主下可用）。走 HTTP 数据面后，
#    侧栏的显示逻辑在**浏览器模式下也能完整验证** —— 否则这条链只能等真机才能测。

@router.post("/sidebar/open")
def open_sidebar(payload: dict | None = None):
    """把「选中的内容」投递给侧栏，并（若有宿主）请求把侧栏显示出来。

    ⚠️ 返回值把两件事分开：`payload_saved`（数据面，任何环境都应为真）与
       `host_shown`（显示面，浏览器模式下为假是**正确值**）。
       合成一个布尔会让验收脚本无法区分「没保存」和「没宿主」。
    """
    p = payload or {}
    saved = browser_host.set_sidebar_payload(
        url=p.get("url", ""), title=p.get("title", ""), selection=p.get("selection", ""))
    shown, err = browser_host.request_sidebar_show()
    return {"ok": True, "payload_saved": saved, "host_shown": shown, "host_error": err}


@router.get("/sidebar/payload")
def get_sidebar_payload():
    """读取当前侧栏载荷（无载荷时是**确定空态**，不是 404）。"""
    return browser_host.sidebar_payload()


@router.post("/sidebar/clear")
def clear_sidebar_payload():
    """清空侧栏载荷（侧栏「关闭 / 重新开始」用）。"""
    return {"ok": True, "cleared": browser_host.clear_sidebar_payload()}


# ---------- 浏览器面板控制（WP12/WP13） ----------
# ⚠️ 全部经 `browser_host.request_*()` → `call_on_main()` 投递到 Qt 主线程。
#    在路由线程（FastAPI 工作线程）直接碰 Qt 控件**不会报错，会挂死**
#    （实测：`setParent` / `takeCentralWidget` 都是静默卡死，连日志都不打）。

@router.post("/open")
def open_browser(payload: dict | None = None):
    """打开浏览器面板（可选同时导航到 url）。

    ⚠️ 返回值把「数据面」与「显示面」分开，与 `/sidebar/open` 同款：
       浏览器模式下 `opened=false` 是**正确值**（没有原生窗口可放面板），不是故障。
    """
    p = payload or {}
    url = p.get("url", "") or ""
    width = int(p.get("width", 520) or 520)
    opened, err = browser_host.request_browser_open(url=url, width=width)
    return {
        "ok": True,
        "opened": opened,
        "host_error": err,
        "normalized_url": browser_host.normalize_url(url) if url else "",
    }


@router.post("/navigate")
def navigate(payload: dict | None = None):
    """让浏览器面板导航到指定 URL。

    ⚠️ `invalid_url` 与 `host_unavailable` 必须能区分：
       前者是用户输入问题（要提示），后者是环境问题（静默即可）。
       合成一个 `ok:false` 会让界面无法决定要不要弹错误。
       ⚠️ 因此 `browser_host.request_browser_navigate` 里**校验先于宿主检查**。
    """
    p = payload or {}
    ok, err = browser_host.request_browser_navigate(p.get("url", "") or "")
    return {"ok": bool(ok), "error": err}


@router.post("/close")
def close_browser():
    """隐藏浏览器面板（保留页面状态，不销毁）。"""
    hidden, err = browser_host.request_browser_close()
    return {"ok": True, "hidden": hidden, "host_error": err}


@router.get("/panel/selfcheck")
def panel_selfcheck():
    """仅调试/验收：在主线程回读面板**真实** Qt 状态（WP12 判据 1）。

    ⚠️ 判据里必须有 `dock_found_by_findChildren >= 1` 这一条：
       只断言「dock 对象存在」在「对象建了但没挂上宿主树」时也会通过 ——
       而那正是**在 Thread-2 里装配**时的实际症状（Qt 只打一行 setParent 警告）。
    """
    data, err = browser_host.request_panel_selfcheck()
    if err == "host_unavailable":
        return _host_unavailable(browser_host.HostUnavailable("无宿主窗口（浏览器模式）"))
    if err:
        return JSONResponse(status_code=500,
                            content={"ok": False, "error": "selfcheck_failed", "detail": err})
    return {"ok": bool(data and data.get("ok")), "detail": data}


@router.get("/url/normalize")
def url_normalize(url: str = ""):
    """URL 归一化（前端**必须用同一套规则**，否则「预览能成、入库必失败」）。

    ⚠️ 这条接口存在的意义是**让归一化规则可被验收**：
       前端拿它做「身份」，抓取用原始输入 —— 两侧成对。
    """
    n = browser_host.normalize_url(url)
    return {"input": url, "normalized": n, "valid": bool(n)}

