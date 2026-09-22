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

from ..services import browser_actions
from ..services import browser_host
from ..services import external as external_svc

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


@router.post("/action")
def browser_action(payload: dict | None = None):
    """执行导航动作：`back` / `forward` / `reload` / `stop`（WP13）。

    ⚠️ 三个字段必须**分开**返回（`ok` / `error` / `available`）：
       `available=false` = 「此刻不可用」（没有历史可后退）→ 界面该把按钮变灰；
       `error` 非空       = 「执行失败」→ 才是真问题。
       合成一个 `ok:false` 后，前端再也分不清「按钮该灰」与「出错了」——
       这正是本项目反复出现的「两个独立语义被压成一个布尔」形态。
    ⚠️ 动作名非法返回 `error="bad_action"`，**不是** 503：
       那是调用方的问题（要提示），而 503 是环境问题（静默即可）。
    """
    p = payload or {}
    ok, err, avail = browser_host.request_browser_action(p.get("action", "") or "")
    return {"ok": bool(ok), "error": err, "available": bool(avail)}


@router.get("/nav")
def browser_nav():
    """导航状态（WP13）。空态是**确定结构**，可逐字比对：

    `{"state":"idle","url":"","title":"","progress":0,
      "can_back":false,"can_forward":false,"error":null}`

    ⚠️ 与 `/state` 的分工：`/state` 是**面板级**状态（是否装配、profile 是否共享），
       本接口是**导航级**状态（当前页、能否后退、错误分类）。
       合成一个接口会让「面板没装配」与「页面加载失败」在同一个字段里打架。
    ⚠️ 出错时 `error` 的形状（`kind` / `http_status` / `domain` / `code` /
       `is_chromium_error_page` / `label`）由 `browser_panel.classify_load_error` 产出，
       六个键**恒定存在**（取不到值时为 null）—— 前端可无条件读 `error.label`。
    """
    return browser_host.nav_state()


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


# ---------- 多标签（WP14） ----------
# ⚠️ 与 `/action` 同款契约：三个字段**分开**返回，不许压成一个布尔。
#    `error="tab_limit"` / `"last_tab"` 是**正常约束**（界面该禁用按钮），
#    与「执行失败」是两件事；`host_unavailable` 仍然只表示环境不支持。

@router.get("/tabs")
def list_tabs():
    """标签列表。空态是**确定结构**，可逐字比对：

    `{"tabs":[],"active":null,"count":0,"max":12,"assembled":false}`

    ⚠️ 与 `/state` 的分工：`/state` 是**面板级**（是否装配、profile 是否共享）；
       本接口是**标签级**（每标签独立的 url / 标题 / can_back / error）。
       合成一个会让「面板没装配」与「某标签加载失败」在同一字段里打架。
    """
    return browser_host.tabs_state()


@router.post("/tab/new")
def tab_new(payload: dict | None = None):
    """新建标签（可选 `url`）。返回 `ok` / `error` / `tab_id` / `count`。

    ⚠️ `error="tab_limit"`（超上限）是**正常约束**，不是故障 ——
       界面该把「新建」变灰，而不是弹错误框。
    """
    p = payload or {}
    ok, err, tid = browser_host.request_tab_new(p.get("url", "") or "")
    st = browser_host.tabs_state()
    return {"ok": bool(ok), "error": err, "tab_id": tid,
            "count": st.get("count", 0), "max": st.get("max")}


@router.post("/tab/close")
def tab_close(payload: dict | None = None):
    """关闭标签（按 `index` 或 `tab_id`）。返回 `ok` / `error` / `remaining`。

    ⚠️ `error="last_tab"` = 「只剩一个，不许关」→ 界面该禁用关闭按钮。
       `error="bad_index"` = 「调用方传的位置不存在」→ 才是调用方的问题。
       两者必须能区分，否则界面无法决定「变灰」还是「报错」。
    """
    p = payload or {}
    idx = p.get("index", None)
    ok, err, remaining = browser_host.request_tab_close(index=idx, tab_id=p.get("tab_id"))
    return {"ok": bool(ok), "error": err, "remaining": int(remaining or 0)}


@router.post("/tab/switch")
def tab_switch(payload: dict | None = None):
    """切换活跃标签（按 `index` 或 `tab_id`）。返回 `ok` / `error` / `tab_id`。"""
    p = payload or {}
    idx = p.get("index", None)
    ok, err, tid = browser_host.request_tab_switch(index=idx, tab_id=p.get("tab_id"))
    return {"ok": bool(ok), "error": err, "tab_id": tid}


# ---------- WP15：带登录态取源（「内容抽取 → 一键入库」的第一步） ----------
# ⚠️⚠️ 本接口只做「取源 + 抽取」，**不做入库**。入库仍走 `POST /materials/clip/save`
#    （把 `source` 一起传过去）。理由：
#      · 抽取链的唯一实现是 `external_svc.extract_from_source`；
#      · 入库链的唯一实现是 `materials.clip_save`（幂等 / safe_stem / 图片本地化 / 索引）。
#    在这里顺手也落一次库 = 复制第二套口径，正是本项目反复禁止的形态。
# ⚠️ 本接口返回的 `preview` 由 `external_svc.preview_payload()` 产出 —— 与
#    `/materials/clip/preview` **同一个函数**，故两侧字段天然一致（不会「预览能成、入库必失败」）。

@router.post("/extract")
def browser_extract():
    """取当前**活跃标签**页面的**原始源码**并抽取正文（WP15 · 带登录态）。

    为什么需要它：服务端裸抓被反爬拒绝的站点（知乎 403 等），在当前浏览器视图里
    带着**用户自己的登录态**跑，不存在反爬问题。

    ⚠️⚠️ 返回的是**原始源码**（`source`），不是 DOM 序列化 —— 实测 DOM 会丢 −90.9% 正文。
       前端把 `source` 原样回传给 `/materials/clip/save`（那边的 `source` 字段会**先抽取**）。

    ⚠️ `error` 与 `ok:false` 必须分开（同 `/action`、`/tab/*`）：
       `host_unavailable` → **503**（环境不支持，静默）；
       其余（`no_url` / `no_view` / `timeout` / `superseded` / 抽取 `reason`）→ 200 + `ok:false`，
       界面要提示。压成一个 503 会让界面分不清「该不该弹提示」。
    """
    if not browser_host.is_available():
        return _host_unavailable(browser_host.HostUnavailable("无宿主窗口（浏览器模式）"))
    url = browser_host.current_url()
    if not url:
        return {"ok": False, "error": "no_url", "url": "", "title": "", "source": "",
                "source_len": -1, "truncated": False, "preview": None}
    ok, box, err = browser_host.request_page_source()
    if not ok:
        return {"ok": False, "error": err or "fetch_failed", "url": url, "title": "",
                "source": "", "source_len": int((box or {}).get("length", -1) or -1),
                "truncated": bool((box or {}).get("truncated")), "preview": None}
    src = (box or {}).get("src") or ""
    if not src:
        return {"ok": False, "error": "empty_source", "url": url, "title": "",
                "source": "", "source_len": 0, "truncated": False, "preview": None}
    r = external_svc.extract_from_source(url, src)
    pv = external_svc.preview_payload(r)
    return {
        "ok": bool(r.get("ok")),
        "error": None if r.get("ok") else (r.get("reason") or "extract_failed"),
        # ⚠️ 回传的 url 用**抽取后的归一化身份**：前端拿它入库，与服务端抓取路径同一身份
        "url": r.get("url") or url,
        "input_url": url,
        "title": r.get("title") or "",
        # 源码：`source_len` 是**截断前**的真实长度，`len(source)` 是实际搬回来的
        "source": src,
        "source_len": int((box or {}).get("length", -1) or -1),
        "truncated": bool((box or {}).get("truncated")),
        "preview": pv,
    }




# ---------- WP16：选区三动作（解释 / 总结 / 出题） ----------
# ⚠️ 与 `/extract` 同款契约：`host_unavailable` → **503**（环境不支持，静默）；
#    其余（`bad_action` / `empty_selection`）→ 200 + `ok:false`（调用方问题，要提示）。
# ⚠️ 本段的「选区 → 动作 → 结果」三段全部走**内存盒子 + 普通线程**，不碰 Qt ——
#    所以整条链在**无宿主环境**（浏览器模式）下也能被完整验收，不必等真机。
#    必须在宿主里执行的只有「重新注入 + 回读页面」那一个接口。

@router.get("/selection")
def get_selection():
    """当前选区盒子（页面推送的最新一次）。

    空态**键恒定**（`seq=0` / `kind=""` / `act=None` / 字符串字段为空串），
    由 `browser_inject.empty_selection()` 保证 —— 不是 404、也不是 null。

    ⚠️ 与 `/sidebar/payload` 的分工：本接口是「**页面上选了什么**」（含 `seq` / `path` /
       `err`，用来区分「没选」与「选了但桥不通」）；`/sidebar/payload` 是
       「**侧栏要显示什么**」（含动作与其结果）。合成一个会让这两类语义在同一字段里打架。
    """
    return {"ok": True, "selection": browser_host.selection_state(),
            "actions": list(browser_actions.ACTION_ORDER),
            "labels": dict(browser_actions.ACTION_LABELS)}


@router.post("/selection/action")
def selection_action(payload: dict | None = None):
    """对选区执行一个动作：`explain` / `summarize` / `quiz`。

    ⚠️ **两种调用方式都要支持**（缺一不可）：
       ① 页面桥推送触发（用户点浮动工具栏）→ 不带 `selection`，取当前选区；
       ② 带 `selection` 直接调用（前端 / 验收脚本）→ 显式指定选区。
       只支持 ① 的话整条链只能靠真机验证；只支持 ② 的话用户点按钮没反应。

    ⚠️ **异步**：立刻返回 `status="running"`，结果去 `/sidebar/payload` 读（靠 `gen` 配对）。
       同步等 LLM 会让 HTTP 线程挂住几十秒，前端也无法显示「正在生成…」。
    """
    p = payload or {}
    sel = p.get("selection", None)
    if sel is None:
        sel = browser_host.selection_state().get("sel") or ""
    return browser_actions.start_action(
        p.get("action", "") or "", sel,
        url=p.get("url", "") or "", title=p.get("title", "") or "")


@router.post("/inject")
def browser_inject_now():
    """让**活跃标签**重新注入工具栏与桥，并回读页面侧真实状态（真机验收用）。

    ⚠️ 「我发起了注入」（`ok`）与「页面上真有节点」（`probe.has_el`）是**两件事**。
       只回 `ok` 的接口在「注入没生效」时会给出全绿 —— 这正是探针最常犯的错。
       故本接口一次返回两者：`ok` 是发起结果，`probe` 是**回读**结果。

    ⚠️ `probe_error` 与 `probe.has_el == false` 必须分开看：前者是「回读本身失败」
       （超时 / 无标签），后者是「回读成功、页面确实没有」——
       压成一个假值之后就再也分不清「没注上」与「没读到」。
    """
    if not browser_host.is_available():
        return _host_unavailable(browser_host.HostUnavailable("无宿主窗口（浏览器模式）"))
    ok, err = browser_host.request_inject()
    pok, probe, perr = browser_host.request_selection_probe()
    return {"ok": bool(ok), "error": err,
            "probe_ok": bool(pok), "probe": probe, "probe_error": perr}


# ---------- WP16：侧栏宿主（显隐开关） ----------
# ⚠️ 本段**必须在宿主里执行**（碰 Qt 控件），故与 `/selection/*` 不同：浏览器模式下
#    一律 `host_unavailable` → **503**（环境不支持，静默）。这与 C5 的契约一致。

@router.get("/sidebar/pane")
def sidebar_pane():
    """侧栏宿主状态（**只读普通值**）。空态**键恒定**：

    `{"available":false,"visible":false,"requested_url":"","splitter":false,"error":null}`

    ⚠️ 与 `/sidebar/payload` 的分工：本接口说「侧栏这个**视图**怎么样」
       （建没建起来 / 收起来了没 / 请求加载的是哪个地址）；
       `/sidebar/payload` 说「侧栏要**显示什么内容**」。
       合成一个会让"侧栏被收起"与"侧栏没内容"在同一字段里打架。

    ⚠️ 这里**不回页面真实地址**（那要碰 Qt，会挂死路由线程）——
       要真实地址用 `/panel/selfcheck` 的 `side_url`。
    """
    return {"ok": True, "pane": browser_host.side_state()}


@router.post("/sidebar/toggle")
def sidebar_toggle(payload: dict | None = None):
    """显示 / 收起 / 切换侧栏：`{"visible": true|false}` 或省略表示**切换**。

    ⚠️ 返回值三键分开（`ok` / `visible` / `error`）：
       `visible` 是**操作之后的真实状态**，界面据此对齐开关按钮 ——
       只回 `ok` 的话，切换语义下按钮状态只能靠前端自己猜，猜错就是"按钮与侧栏相反"。
    """
    p = payload or {}
    vis = p.get("visible", None)
    ok, visible, err = browser_host.request_side_toggle(None if vis is None else bool(vis))
    return {"ok": bool(ok), "visible": bool(visible), "error": err,
            "pane": browser_host.side_state()}
