#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""macOS QtWebEngine 可行性探针（阶段 0 验证门）

用途
----
在 macOS（真机或 GitHub Actions 的 macos-14 / macos-15-intel runner）上回答
「应用内 AI 浏览器」方案是否成立，输出**逐条可判定**的结论。

背景
----
项目当前两平台用的是不同 webview 后端（Windows=WebView2、macOS=Cocoa/WKWebView），
方案要把两平台统一切到 pywebview 的 qt 后端（QtWebEngine）。
Windows 侧已在本机实测通过；macOS 侧有三条高风险项必须前置验证：

  R1  未签名 .app + QtWebEngine 被 Gatekeeper 拦截（本脚本不覆盖，属打包阶段）
  R7  macOS 上 pywebview qt 后端的成熟度——**失败会静默回落 cocoa**（本脚本 D4 专门覆盖）

判据（D1-D5 全绿 → 放行阶段 1）
----
  D1  PySide6 + QtPy 可装、QtWebEngineWidgets 可 import
  D2  QApplication 能起、QWebEngineView 能创建、内嵌 Chromium 版本可读
  D3  顶层加载目标站点成功（loadFinished=True 且正文长度超阈值）
      失败时先做**网络对照**（Python 直连同一 URL）再归因：网络不通记 SKIP、网络通记 FAIL
      —— 缺这一层归因时，CI runner 的网络抖动会产出假红并误判成 no-go
      必需站点只留**全球稳定可达的一个**：CI 机房 IP 被某个站点反爬挡住 ≠ 引擎不可用，
      把多个站点都设成必需 = 把噪声升格成 no-go；其余站点照常加载并进报告（信息不减，只是不 gate）。
      归因三态：必需站点加载成功 → PASS；网络对照「可达」但引擎读不到 → FAIL（真 no-go 证据）；
      网络对照「不可达」→ 证据不足（退出码 3，提示重跑），**绝不写成 no-go**
  D4  PYWEBVIEW_GUI=qt 生效且**未静默回落 cocoa**
  D5  runJavaScript 注入 DOM 并二次回读成功（往第三方页面插元素）

用法
----
  python3 probe_macos_qtwebengine.py                  # 正式判定，退出码 0=全绿
  python3 probe_macos_qtwebengine.py --out report.json
  python3 probe_macos_qtwebengine.py --keep-open      # 保留窗口（本地肉眼观察）

环境变量（一般不用设，脚本有保守默认）
----
  PROBE_TIMEOUT      单页加载超时秒数，默认 45
  QT_QPA_PLATFORM    无显示环境可设 offscreen
  QTWEBENGINE_CHROMIUM_FLAGS   默认追加 --disable-gpu（CI/无 GPU 环境必需）

退出码：0 = 全部必需判据通过；1 = 有必需判据失败；2 = 脚本自身异常；
        3 = 证据不足（外网不可达等环境问题 —— **不是** no-go，请重跑）

沙箱自愈（实测：受限环境里 Chromium 会把渲染进程秒杀）
----
  症状：所有站点（含 file:// 本地页）在 <1s 内 LOADFAIL，页面 URL 为空，
        QWebEnginePage.renderProcessTerminated 报 KilledTerminationStatus。
  实测结论：与 --disable-gpu 等无关；加 `--no-sandbox` 即可恢复。
  处理：探针**自动用 --no-sandbox 重跑自身一次**（Chromium flags 在引擎初始化时固定，
        只能换进程），并在报告里记 `artifacts.sandbox_retry=true`。
  为什么不在 CI 里直接写死 --no-sandbox：那样就抹掉了「沙箱能否工作」这个事实，
        而它是阶段 1（.app 打包 / 签名 / entitlements）的一条风险输入。
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
import traceback

# CI / 无 GPU 环境的保守默认；已被外部显式设置则不覆盖
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--disable-gpu --disable-software-rasterizer --disable-dev-shm-usage",
)

TIMEOUT = int(os.environ.get("PROBE_TIMEOUT", "45"))

# 探针站点：
#   required=True  → 计入判定（选用全球可达的站点，避免地域性网络差异造成假红）
#   required=False → 仅记录（已知存在登录墙/地域敏感，失败记 SKIP 而非 FAIL）
SITES = [
    {
        "name": "mdn",
        "url": "https://developer.mozilla.org/zh-CN/docs/Web/JavaScript",
        "required": True,
        "min_chars": 500,
        "why": "响应头带 X-Frame-Options: DENY —— 顶层导航不受 XFO 限制的证据",
    },
    {
        "name": "cnblogs",
        "url": "https://www.cnblogs.com/",
        # 不作必需项：CI 机房 IP 下可能被反爬挡住，那是站点策略问题，
        # 不能升格成「引擎不可用」。结果照样进报告，只是不 gate。
        "required": False,
        "min_chars": 300,
        "why": "常规站点对照组（不作判定，仅记录）",
    },
    {
        "name": "baike-baidu",
        "url": "https://baike.baidu.com/item/%E4%BA%BA%E5%B7%A5%E6%99%BA%E8%83%BD",
        "required": False,
        "min_chars": 1000,
        "why": "服务端抓取返 403 的站点 —— 浏览器视图能读，是剪藏链路修复点的样本",
    },
    {
        "name": "zhihu",
        "url": "https://www.zhihu.com/question/19550224",
        "required": False,
        "min_chars": 200,
        "why": "登录墙已由产品决策接受：未登录表现与真实浏览器一致即可，不计入判定",
    },
]

results = []       # [(judge_id, name, status, detail)]  status: PASS/FAIL/SKIP
_artifacts = {}    # 额外证据
_needs_rerun = []  # 「证据不足」的原因（非 no-go）；非空 → 退出码 3

# 沙箱自愈：渲染进程被秒杀时，用 --no-sandbox 重跑子进程（一次性）
SANDBOX_RETRY_ENV = "ASC_PROBE_SANDBOX_RETRY"
_sandbox_retry_request = []   # 非空 → 本次不产出结论，改由 --no-sandbox 子进程重跑


def record(judge_id, name, status, detail=""):
    results.append((judge_id, name, status, detail))
    line = "[%s] %s %s" % (status, judge_id, name)
    if detail:
        line += "  —— " + detail
    print(line, flush=True)


def note(key, value):
    _artifacts[key] = value


# ---------------------------------------------------------------------------
# D1：依赖与 import
# ---------------------------------------------------------------------------
def check_d1():
    ver = {}
    ok = True

    try:
        import PySide6
        from PySide6 import QtCore
        ver["PySide6"] = QtCore.__version__
    except Exception as e:
        record("D1", "PySide6 可 import", "FAIL", "%s: %s" % (type(e).__name__, e))
        return False
    record("D1", "PySide6 可 import", "PASS", "版本 %s" % ver["PySide6"])

    try:
        import shiboken6  # noqa: F401
        record("D1", "shiboken6 可 import", "PASS")
    except Exception as e:
        ok = False
        record("D1", "shiboken6 可 import", "FAIL", "%s: %s" % (type(e).__name__, e))

    # qtpy 是 pywebview qt 后端的硬依赖（pywebview 仅在 extras 中声明，易漏）
    try:
        import qtpy
        ver["qtpy"] = getattr(qtpy, "__version__", "?")
        record("D1", "qtpy 可 import（pywebview qt 后端硬依赖）", "PASS", "版本 %s" % ver["qtpy"])
    except Exception as e:
        ok = False
        record("D1", "qtpy 可 import（pywebview qt 后端硬依赖）", "FAIL",
               "%s: %s —— 需 pip install QtPy" % (type(e).__name__, e))

    for mod in ("PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
                "PySide6.QtWebChannel", "PySide6.QtNetwork"):
        try:
            __import__(mod)
            record("D1", "%s 可 import" % mod, "PASS")
        except Exception as e:
            ok = False
            record("D1", "%s 可 import" % mod, "FAIL", "%s: %s" % (type(e).__name__, e))

    note("versions", ver)
    return ok


# ---------------------------------------------------------------------------
# D2：引擎可创建
# ---------------------------------------------------------------------------
def check_d2(app):
    try:
        from PySide6.QtWebEngineCore import QWebEngineProfile
        from PySide6.QtWebEngineWidgets import QWebEngineView
    except Exception as e:
        record("D2", "创建 QWebEngineView", "FAIL", "%s: %s" % (type(e).__name__, e))
        return False, None, None

    try:
        view = QWebEngineView()
    except Exception as e:
        record("D2", "创建 QWebEngineView", "FAIL", "%s: %s" % (type(e).__name__, e))
        return False, None, None
    record("D2", "创建 QWebEngineView", "PASS")

    ua = ""
    try:
        ua = view.page().profile().httpUserAgent()
        note("user_agent", ua)
        chrome_ver = ""
        if "Chrome/" in ua:
            chrome_ver = ua.split("Chrome/", 1)[1].split()[0]
        record("D2", "读取内嵌 Chromium 版本", "PASS" if chrome_ver else "SKIP",
               "Chrome/%s" % chrome_ver if chrome_ver else "UA 中未找到 Chrome 标记")
    except Exception as e:
        record("D2", "读取内嵌 Chromium 版本", "SKIP", "%s: %s" % (type(e).__name__, e))

    # 持久化 profile：登录态保持（知乎登录墙决策依赖此项）
    try:
        p = QWebEngineProfile("probe")
        sp = p.persistentStoragePath()
        note("persistent_storage_path", sp)
        has_cookie_store = p.cookieStore() is not None
        record("D2", "QWebEngineProfile 持久化存储可用", "PASS" if (sp and has_cookie_store) else "FAIL",
               "storage=%s / cookieStore=%s" % (sp, has_cookie_store))
    except Exception as e:
        record("D2", "QWebEngineProfile 持久化存储可用", "SKIP", "%s: %s" % (type(e).__name__, e))

    return True, view, ua


# ---------------------------------------------------------------------------
# D3 + D5：加载站点 与 注入回读
# ---------------------------------------------------------------------------
INJECT_JS = """
(function () {
  try {
    var id = 'asc-probe-injected';
    var old = document.getElementById(id);
    if (old) { old.remove(); }
    var d = document.createElement('div');
    d.id = id;
    d.textContent = '解释';
    d.setAttribute('data-asc-probe', 'marker-ok');
    d.style.cssText = 'position:fixed;left:8px;top:8px;z-index:2147483647;' +
                      'padding:6px 10px;background:#7c5cfc;color:#fff;border-radius:6px;' +
                      'font-size:13px;font-family:sans-serif;';
    document.body.appendChild(d);
    return 'injected';
  } catch (e) { return 'ERR:' + String(e); }
})();
"""

READBACK_JS = """
(function () {
  var el = document.getElementById('asc-probe-injected');
  if (!el) { return JSON.stringify({stillThere: false}); }
  return JSON.stringify({
    stillThere: true,
    text: el.textContent,
    marker: el.getAttribute('data-asc-probe'),
    bodyChars: (document.body ? document.body.innerText.length : 0),
    title: document.title
  });
})();
"""


def _run_js(view, script, timeout=15):
    """同步等待 runJavaScript 回调结果。"""
    box = {}
    done = []

    def cb(val):
        box["v"] = val
        done.append(1)

    view.page().runJavaScript(script, cb)
    t0 = time.time()
    while not done and time.time() - t0 < timeout:
        _pump(0.05)
    return box.get("v")


def _pump(seconds):
    """驱动 Qt 事件循环（不阻塞 GUI 线程）。"""
    from PySide6.QtWidgets import QApplication
    t0 = time.time()
    while time.time() - t0 < seconds:
        QApplication.processEvents()
        time.sleep(0.005)


_NET_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def _net_reachable(url, timeout=12):
    """独立于 QtWebEngine 的网络对照：用 Python 直连同一 URL，判断「网络是否可达」。

    为什么必须有它：不加对照时，CI runner 的网络抖动会让要求站点 LOADFAIL → 判 FAIL
    → 看板变红 → 误判成「Qt 后端不可用」即 no-go。这是本探针最可能的假红来源。
    归因规则：网络不通 → SKIP（外网可达性不属方案可行性）；网络通但引擎读不到 → FAIL。

    返回 (reachable, detail)。
    """
    import urllib.error
    import urllib.request

    try:
        req = urllib.request.Request(url, headers={"User-Agent": _NET_UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return (200 <= r.status < 400), "http %s" % r.status
    except urllib.error.HTTPError as e:
        # 4xx/5xx 说明「网络通、站点可达」，只是拒绝了我们的裸请求（反爬）。
        # 不能算网络不通，否则百度百科那类返 403 的站点会被误判成 SKIP。
        return True, "http %s（站点可达，拒绝裸请求）" % e.code
    except BaseException as e:
        return False, "%s: %s" % (type(e).__name__, str(e)[:90])


def should_retry_no_sandbox(req_ok, terminated, already_retried):
    """沙箱自愈闸门（纯函数，便于做负向自检）。三个条件缺一不可：

      · req_ok == 0      —— 必需站点一个都没成功（有成功就不必怀疑环境）
      · terminated 非空  —— 有「渲染进程被秒杀」这条**特征证据**（没有它就可能
                             是真的站点/网络故障，当成环境问题会把 no-go 洗白）
      · 未重跑过         —— 一次性闸门，防死循环
    """
    return req_ok == 0 and bool(terminated) and not already_retried


def check_sites(view):
    """D3：逐站加载；首个 required 站点额外做 D5 注入回读。"""
    d3_ok = True
    d5_done = False
    detail_rows = []
    req_ok = 0         # 必需站点加载成功数
    req_fail = 0       # 必需站点「网络可达但引擎读不到」= 真 no-go 证据
    req_net_skip = 0   # 必需站点因网络不可达未加载 = 证据不足

    # 「渲染进程被秒杀」是 sandbox 故障的特征信号：没有它，一次沙箱故障会被
    # 写成「网络可达但引擎读不到」→ 误判成 no-go。必须单独接住。
    terminated = []
    try:
        view.page().renderProcessTerminated.connect(
            lambda st, code: terminated.append("%s/%s" % (st, code)))
    except BaseException:
        pass

    for site in SITES:
        loaded = {"ok": None}
        # 先清掉上一轮遗留的槽，否则旧 lambda 仍指向旧的 store，
        # 会把新站点的加载结果写进上一轮的字典（表现为某个站点永远等不到结果）
        try:
            view.loadFinished.disconnect()
        except (RuntimeError, TypeError):
            pass
        view.loadFinished.connect(lambda ok, store=loaded: store.__setitem__("ok", bool(ok)))

        t0 = time.time()
        view.load(site["url"])
        while loaded["ok"] is None and time.time() - t0 < TIMEOUT:
            _pump(0.1)
        elapsed = round(time.time() - t0, 2)

        if loaded["ok"] is None:
            status, chars, title = "TIMEOUT", 0, ""
        elif loaded["ok"] is False:
            status, chars, title = "LOADFAIL", 0, ""
        else:
            # 读取页面自身信息（不复用 READBACK_JS：那条查询的是注入元素，与此处无关）
            info = _run_js(
                view,
                "(function(){return JSON.stringify({"
                "bodyChars:(document.body?document.body.innerText.length:0),"
                "title:document.title});})();",
            )
            if isinstance(info, str):
                try:
                    j = json.loads(info)
                    chars = j.get("bodyChars", 0)
                    title = j.get("title", "")[:60]
                except Exception:
                    chars, title = 0, ""
            else:
                chars, title = 0, ""
            status = "OK" if chars >= site["min_chars"] else "SHORT"

        need = site["required"]
        net_note = ""
        if status == "OK":
            judge = "PASS"
        elif need:
            # 失败先归因，再定红绿：网络不通 → SKIP（证据不足）；网络通而引擎读不到 → FAIL
            net_ok, net_note = _net_reachable(site["url"])
            judge = "FAIL" if net_ok else "SKIP"
        else:
            judge = "SKIP" if status != "OK" else "PASS"

        if need:
            if status == "OK":
                req_ok += 1
            elif judge == "FAIL":
                req_fail += 1
            else:
                req_net_skip += 1

        record("D3", "加载 %s" % site["name"], judge,
               "%s / %s / %ss / %d 字 / %s%s" % (
                   site["url"], status, elapsed, chars, title,
                   ("  [网络对照: %s]" % net_note) if net_note else ""))
        detail_rows.append({"site": site["name"], "url": site["url"], "status": status,
                            "elapsed_s": elapsed, "chars": chars, "title": title,
                            "required": need, "net_control": net_note, "note": site["why"]})

        # D5：在最靠前的加载成功站点上验证注入（SITES 里必需项排在最前）
        if status == "OK" and not d5_done:
            inj = _run_js(view, INJECT_JS)
            _pump(0.4)
            rb = _run_js(view, READBACK_JS)
            try:
                j = json.loads(rb) if isinstance(rb, str) else {}
            except Exception:
                j = {}
            ok5 = (inj == "injected" and j.get("stillThere") is True
                   and j.get("marker") == "marker-ok" and j.get("text") == "解释")
            record("D5", "往第三方页面注入 UI 并二次回读", "PASS" if ok5 else "FAIL",
                   "inject=%s / readback=%s" % (inj, rb))
            note("d5_target", site["name"])
            d5_done = ok5

    note("render_terminated", list(terminated))
    note("sandbox_retry", os.environ.get(SANDBOX_RETRY_ENV) == "1")

    # 沙箱故障自愈：必需站点一个都没加载成功、但出现了「渲染进程被秒杀」的证据，
    # 且本次不是重跑 → 本次不产出结论，改由 --no-sandbox 的子进程重跑
    # （flags 在引擎初始化时固定，只能在另一个进程里改）。
    if should_retry_no_sandbox(req_ok, terminated,
                               os.environ.get(SANDBOX_RETRY_ENV) == "1"):
        _sandbox_retry_request.append(
            "渲染进程被秒杀（%s）—— 疑为 Chromium sandbox 在受限环境下不可用" % terminated[0])
        return False

    # D3 汇总口径：按「必需站点里至少一个成功」判，不按单站点下结论（理由见文件头 D3 段）
    if req_ok > 0:
        record("D3", "必需站点加载汇总", "PASS", "%d 个必需站点加载成功" % req_ok)
    elif req_fail == 0 and req_net_skip > 0:
        msg = "必需站点均因网络不可达未加载（网络对照判定）"
        record("D3", "必需站点加载汇总", "SKIP", msg)
        _needs_rerun.append(msg)
    else:
        record("D3", "必需站点加载汇总", "FAIL",
               "网络可达但引擎读不到（%d 个必需站点）" % req_fail)
        d3_ok = False

    if not d5_done:
        if req_fail == 0 and req_net_skip > 0:
            msg = "无可注入站点：必需站点因网络不可达未加载"
            record("D5", "往第三方页面注入 UI 并二次回读", "SKIP", msg)
            _needs_rerun.append(msg)
        else:
            record("D5", "往第三方页面注入 UI 并二次回读", "FAIL", "无可用站点完成注入验证")
            d3_ok = False

    note("sites", detail_rows)
    return d3_ok


def _rerun_with_no_sandbox(args):
    """用 --no-sandbox 把探针作为**子进程**重跑一次；返回子进程退出码，失败返回 None。

    为什么是子进程而不是 os.execve 替换自身：
      · Chromium flags 在引擎初始化时固定，改不了 → 只能换进程；
      · Windows 上 os.execve 实为「起新进程 + 退旧进程」，父进程一退，调用方
        （shell / CI 步骤）就以为整步结束，孤儿子进程可能被顺带回收 —— 本机实测：
        重跑跑到 D2 之后被杀，`--out` 报告一个字节都没写；
      · 子进程 + wait 在三个平台行为一致，退出码也能如实带出去。
    子进程自己写 --out 报告，父进程据此**不再覆盖**。
    """
    import subprocess
    flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    env = dict(os.environ)
    env["QTWEBENGINE_CHROMIUM_FLAGS"] = (flags + " --no-sandbox").strip()
    env[SANDBOX_RETRY_ENV] = "1"
    reason = _sandbox_retry_request[0] if _sandbox_retry_request else "渲染进程被秒杀"
    print("\n[自愈] %s" % reason)
    print("[自愈] 用 --no-sandbox 重跑子进程：QTWEBENGINE_CHROMIUM_FLAGS=%s"
          % env["QTWEBENGINE_CHROMIUM_FLAGS"])
    print("[自愈] 该字段会记进报告的 artifacts.sandbox_retry（阶段 1 的风险输入）")
    sys.stdout.flush()
    try:
        return subprocess.run([sys.executable] + sys.argv, env=env).returncode
    except BaseException as e:
        print("[自愈] 重跑失败：%s: %s —— 保留本次结果继续" % (type(e).__name__, e))
        sys.stdout.flush()
        return None


# ---------------------------------------------------------------------------
# D4：PYWEBVIEW_GUI=qt 生效且未静默回落 cocoa
# ---------------------------------------------------------------------------
EXPECTED_GUILIB = "webview.platforms.qt"

# 生产代码（launcher.py / 未来的浏览器视图）实际依赖的 pywebview Window API
REQUIRED_WEBVIEW_APIS = ("has_create_file_dialog", "has_evaluate_js", "has_load_url")


def d4_verdict(data):
    """把 D4 子进程的原始数据判成结论。**独立纯函数**，便于做负向自检。

    返回 (ok, guilib_name, reason)。

    判据为什么必须精确到「后端名 == qt」而不能放宽成「能跑就算过」：
    macOS 上 qt 起不来时 pywebview 会**静默回落 cocoa**（guilib.py:103-107 是列表回退），
    不抛异常、不警告，Window 的 API 也一样齐备 —— 唯一的可观测差异就是后端名。
    若判据写成「窗口创建成功即通过」，这个最危险的失败形态会被判成绿。
    """
    if not isinstance(data, dict):
        return False, "", "子进程未返回可解析的 JSON：%r" % (data,)

    if "fatal" in data or "probe_err" in data:
        return False, str(data.get("guilib", "")), "pywebview 启动失败：%s" % data

    # 看门狗触发 = 载荷挂住（start() 未返回）。**不能当通过**：
    # 挂住说明窗口没被正常关闭，这个缺陷本身就该修，而不是被"后端名对了"掩盖。
    if data.get("watchdog"):
        return False, str(data.get("guilib", "")), "载荷挂死：%s" % data["watchdog"]

    if data.get("destroy_err"):
        return False, str(data.get("guilib", "")), "窗口未能销毁：%s" % data["destroy_err"]

    name = data.get("guilib", "")
    if name != EXPECTED_GUILIB:
        return False, name, ("实际后端 = %s ← 不是 qt（已静默回落），浏览器视图将无法工作"
                             % (name or "(空)"))

    missing = [a for a in REQUIRED_WEBVIEW_APIS if not data.get(a)]
    if missing:
        return False, name, "qt 后端已生效，但缺少生产代码依赖的 API：%s" % missing

    return True, name, "实际后端 = %s，依赖 API 齐备" % name


def check_d4():
    """必须在**独立子进程**里做：pywebview 的后端选择发生在 start() 且为进程级全局。"""
    code = r"""
import json, os, sys, tempfile, threading, time
os.environ['PYWEBVIEW_GUI'] = 'qt'
out = {}

# 看门狗：载荷挂住时不能靠外层 subprocess 的 timeout 兜底 —— 那样拿到的是一句
# "子进程无结果"，完全无法归因（是被杀？是卡在哪？）。这里自带硬退出，把"挂住"
# 变成一条可读结论。同时保证总时长远小于外层 timeout（120s）。
def _watchdog():
    time.sleep(45)
    out['watchdog'] = 'webview.start() 45s 未返回（事件循环未退出）'
    sys.stdout.write('@@D4@@' + json.dumps(out, ensure_ascii=False))
    sys.stdout.flush()
    os._exit(9)

threading.Thread(target=_watchdog, daemon=True).start()

try:
    import webview
    w = webview.create_window('probe', html='<html><body>probe</body></html>',
                              width=320, height=200)
    def after_start():
        try:
            out['guilib'] = getattr(webview.guilib, '__name__', repr(webview.guilib))
            # 生产代码依赖的 API 在同后端下是否齐备
            out['has_create_file_dialog'] = hasattr(w, 'create_file_dialog')
            out['has_evaluate_js'] = hasattr(w, 'evaluate_js')
            out['has_load_url'] = hasattr(w, 'load_url')
            # ⚠️ webview.settings 是 ImmutableDict，**不是** dict 子类
            # （曾用 isinstance(..., dict) 判定 → 恒为 False，把「可设置下载」错记成 false）。
            # 用成员判定，不要用类型判定。
            try:
                out['allow_downloads_settable'] = 'ALLOW_DOWNLOADS' in webview.settings
            except BaseException as e:
                out['allow_downloads_settable'] = 'ERR: %s' % e
        except Exception as e:
            out['probe_err'] = '%s: %s' % (type(e).__name__, e)
        time.sleep(0.3)
        # ⚠️ pywebview 6.x **没有** webview.destroy_window()，只有 Window.destroy()。
        # 用错 API 时窗口关不掉 → webview.start() 永不返回 → 载荷挂死；
        # 若这里再 except: pass 把异常吞掉，症状就是"卡死且零线索"（本机实测踩过）。
        try:
            w.destroy()
        except BaseException as e:
            out['destroy_err'] = '%s: %s' % (type(e).__name__, e)
    webview.start(after_start, private_mode=False,
                  storage_path=os.path.join(tempfile.gettempdir(),
                                            'asc_probe_webview_storage'))
except BaseException as e:
    out['fatal'] = '%s: %s' % (type(e).__name__, e)
sys.stdout.write('@@D4@@' + json.dumps(out, ensure_ascii=False) + '\n')
sys.stdout.flush()
"""
    import subprocess
    try:
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                              text=True, timeout=120)
        stdout = proc.stdout or ""
        marker = stdout.find("@@D4@@")
        if marker < 0:
            record("D4", "PYWEBVIEW_GUI=qt 生效", "FAIL",
                   "子进程无结果 rc=%s stderr=%s" % (proc.returncode, (proc.stderr or "")[-300:]))
            return False
        data = json.loads(stdout[marker + 6:])
    except subprocess.TimeoutExpired:
        # 载荷自带 45s 看门狗，正常绝不该走到这里；走到这里说明看门狗线程也没起来
        record("D4", "PYWEBVIEW_GUI=qt 生效", "FAIL",
               "子进程超时 120s（载荷自带 45s 看门狗未生效，疑为 import 阶段就卡住）")
        return False
    except Exception as e:
        record("D4", "PYWEBVIEW_GUI=qt 生效", "FAIL", "%s: %s" % (type(e).__name__, e))
        return False

    ok, guilib_name, reason = d4_verdict(data)
    record("D4", "PYWEBVIEW_GUI=qt 生效（未静默回落 cocoa）", "PASS" if ok else "FAIL", reason)
    note("d4", data)
    note("d4_verdict", reason)

    if guilib_name == EXPECTED_GUILIB:
        for api in REQUIRED_WEBVIEW_APIS:
            record("D4", "qt 后端 API %s" % api.replace("has_", ""),
                   "PASS" if data.get(api) else "FAIL")
    return ok


# ---------------------------------------------------------------------------
def _write_report(path):
    """把逐条判据写成机器可读证据。**不管走到哪一步都要写**。

    为什么不能只在成功路径写：D1 失败（依赖装不上）是最可能的失败形态之一，
    而它走的正是早退分支 —— 旧写法在那里 `return 1`，报告一个字都不写，
    CI 只剩日志、拿不到逐条判据，归因成本全落到人身上。
    """
    report = {
        "platform": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": sys.version.split()[0],
        "qpa": os.environ.get("QT_QPA_PLATFORM", "(default)"),
        "results": [{"judge": r[0], "name": r[1], "status": r[2], "detail": r[3]}
                    for r in results],
        "artifacts": _artifacts,
        "needs_rerun": list(_needs_rerun),
    }
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print("JSON 报告：%s" % path)
        return True
    except Exception as e:
        print("报告写入失败：%s" % e)
        return False


def _run_checks(args):
    is_target = platform.system() == "Darwin"
    print("=" * 74)
    print("QtWebEngine 可行性探针（阶段 0 验证门）")
    print("  目标平台 : macOS (Darwin)")
    print("  当前运行 : %s%s" % (platform.system(),
                                "" if is_target else "  ← 非目标平台，本次结果仅作 dry-run 参考，不可当 macOS 结论"))
    print("  platform : %s %s" % (platform.system(), platform.release()))
    print("  machine  : %s" % platform.machine())
    print("  python   : %s" % sys.version.split()[0])
    print("  QPA      : %s" % os.environ.get("QT_QPA_PLATFORM", "(default)"))
    print("  chromium flags: %s" % os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", ""))
    print("=" * 74)

    if not check_d1():
        _summary()
        return 1

    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv[:1])

    ok2, view, _ua = check_d2(app)
    ok3 = False
    if ok2 and view is not None:
        try:
            ok3 = check_sites(view)
        except Exception:
            record("D3", "站点加载批处理", "FAIL", traceback.format_exc(limit=3))
            ok3 = False

    if _sandbox_retry_request:
        # 本次不产出结论：D4/D5 一并跳过，改由 --no-sandbox 子进程给出
        print("\n[自愈] 本次不作结论，改由 --no-sandbox 子进程重跑")
        sys.stdout.flush()
        return 4

    ok4 = check_d4()

    if args.keep_open and view is not None:
        print("\n--keep-open：窗口保留 30s 供肉眼观察…")
        view.resize(900, 640)
        view.show()
        _pump(30)

    return _summary()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="", help="JSON 报告输出路径")
    ap.add_argument("--keep-open", action="store_true", help="保留窗口（本地肉眼观察）")
    args = ap.parse_args()

    rc = 2
    child_rc = None
    try:
        rc = _run_checks(args)
        if _sandbox_retry_request:
            child_rc = _rerun_with_no_sandbox(args)
    except BaseException:
        traceback.print_exc()
        record("SELF", "探针自身异常", "FAIL", traceback.format_exc(limit=4)[-400:])
        try:
            _summary()
        except BaseException:
            pass
        rc = 2

    # 子进程（--no-sandbox）已经把报告写好了 → 父进程不要用「沙箱故障版」覆盖它
    if child_rc is not None:
        return child_rc

    # 证据优先：成功 / 失败 / 自身异常三条路径都落盘
    if args.out:
        _write_report(args.out)
    return rc


def _summary():
    req = [r for r in results if r[0] in ("D1", "D2", "D3", "D4", "D5")]
    n_pass = sum(1 for r in req if r[2] == "PASS")
    n_fail = sum(1 for r in req if r[2] == "FAIL")
    n_skip = sum(1 for r in req if r[2] == "SKIP")
    print("\n" + "=" * 74)
    print("合计 %d 项，通过 %d，失败 %d（跳过 %d）" % (len(req), n_pass, n_fail, n_skip))
    if n_fail:
        print("结论：存在失败判据 → 不放行阶段 1；评估降级路线 M2 / M3")
        rc = 1
    elif _needs_rerun:
        # 「没跑到」必须和「跑过了」给出不同退出码，否则判据缺失会被当成放行
        print("结论：**证据不足**（不是 no-go）：%s" % _needs_rerun[0])
        print("      → 本次不作结论，请重跑该 job（外网可达性不属方案可行性）")
        rc = 3
    else:
        print("结论：D1-D5 通过 → 放行阶段 1（换 GUI 外壳）")
        rc = 0
    print("=" * 74)
    return rc


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:
        traceback.print_exc()
        sys.exit(2)
