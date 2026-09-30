# -*- coding: utf-8 -*-
"""WebView2 运行时检测（Windows）：在**创建窗口之前**判断这台机器能不能跑原生外壳。

为什么必须有这个模块（实测，见 `AI伴学助手_AI浏览器下线方案-20260922.md` §2.6）：
  pywebview 在探测不到 WebView2 时**不抛异常**，而是静默回落到 `mshtml`（IE 引擎），
  只打一行 `logger.warning`（`webview/platforms/winforms.py:147-155`）。
  而前端产物是**现代 ES module**（`vite.config.js` 无 `plugin-legacy`、`build.target` 未设）
  → IE 引擎执行不了 → **白屏、界面上零提示**；launcher 现有的 fallback 也抓不到
  （没有异常可抓）。⇒ 必须在进 pywebview 之前自己检一次、并给出人话提示。

检测口径与 pywebview 的 `_is_chromium()` **逐条一致**（照抄，不做"改良"）：
  ① .NET Framework `Release >= 394802`（即 4.6.2）
  ② 四个注册表键（Runtime / Beta / Developer / Canary），HKCU 与 HKLM **各查一遍**
     （HKLM 下走 `WOW6432Node`）
  ③ 版本 `>= 86.0.622.0`

⚠️ 本模块**只依赖标准库**：它要在 `import webview` 之前运行，不能引入任何重依赖。
"""
from __future__ import annotations

import sys

MIN_VERSION = "86.0.622.0"
NET_RELEASE_MIN = 394802          # .NET Framework 4.6.2
DOWNLOAD_URL = "https://developer.microsoft.com/microsoft-edge/webview2/"

_CLIENTS = (
    ("{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}", "WebView2 Runtime"),
    ("{2CD8A007-E189-409D-A2C8-9AF4EF3C72AA}", "WebView2 Beta"),
    ("{0D50BFEC-CD6A-4F9A-964C-C7416E3ACB10}", "WebView2 Developer"),
    ("{65C35B14-6C1D-4122-AC46-7148CC9D6497}", "WebView2 Canary"),
)


def _newer_or_equal(want: str, got: str) -> bool:
    """与 pywebview `_is_new_version` **同口径**：按段比，遇到第一段 got >= want 即真。

    ⚠️ 不改成 `tuple(int(...))` 逐段比较 —— 口径一旦与 pywebview 不同，
       就会出现「我们判定能跑、它实际回落」的分叉（那正是本模块要防的事）。
    """
    try:
        for i, seg in enumerate(want.split(".")):
            g = got.split(".")
            if len(g) > i:
                return int(g[i]) >= int(seg)
    except (TypeError, ValueError):
        return False
    return False


def _read_webview2_version() -> tuple[str, str]:
    """返回 `(版本号, 来源描述)`；查不到返回 `("", "")`。"""
    import winreg

    for key, desc in _CLIENTS:
        for hive, hive_name in ((winreg.HKEY_CURRENT_USER, "HKCU"),
                                (winreg.HKEY_LOCAL_MACHINE, "HKLM")):
            try:
                if hive_name == "HKCU":
                    path = r"Microsoft\EdgeUpdate\Clients\%s" % key
                else:
                    path = r"WOW6432Node\Microsoft\EdgeUpdate\Clients\%s" % key
                with winreg.OpenKey(hive, r"SOFTWARE\%s" % path) as k:
                    build, _ = winreg.QueryValueEx(k, "pv")
                if build:
                    return str(build), "%s @ %s" % (desc, hive_name)
            except Exception:
                continue
    return "", ""


def detect() -> dict:
    """检测系统 WebView2 运行时。

    返回 dict，四个键恒定存在：
      `ok`      —— 是否可用
      `version` —— 命中的版本号（无则空串）
      `source`  —— 在哪找到的（如 `WebView2 Runtime @ HKLM`）
      `reason`  —— 不可用时的原因码；可用时为 `""`

    ⚠️⚠️ 非 Windows 返回 `ok=True` + `reason="not_windows"`（**不是 False**）——
       macOS 走 cocoa(WKWebView)，是系统自带能力；调用方不该因为「不是 Windows」就拦用户。
    """
    if sys.platform != "win32":
        return {"ok": True, "version": "", "source": "", "reason": "not_windows"}

    # ① .NET Framework 门槛（pywebview 同样先查它）
    try:
        import winreg
        with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full") as k:
            release, _ = winreg.QueryValueEx(k, "Release")
        if int(release) < NET_RELEASE_MIN:
            return {"ok": False, "version": "", "source": "",
                    "reason": "dotnet_too_old(Release=%s)" % release}
    except Exception as e:
        return {"ok": False, "version": "", "source": "",
                "reason": "dotnet_unreadable(%s)" % type(e).__name__}

    # ② + ③ 注册表里的 WebView2 客户端与版本
    ver, src = _read_webview2_version()
    if not ver:
        return {"ok": False, "version": "", "source": "", "reason": "not_installed"}
    if not _newer_or_equal(MIN_VERSION, ver):
        return {"ok": False, "version": ver, "source": src,
                "reason": "too_old(need>=%s)" % MIN_VERSION}
    return {"ok": True, "version": ver, "source": src, "reason": ""}


def user_hint(res: dict) -> str:
    """把检测结果写成**用户能懂**的一段话（用于原生弹窗）。"""
    reason = res.get("reason", "")
    if reason == "not_installed":
        head = "这台电脑上没有找到「Microsoft Edge WebView2 运行时」。"
    elif reason.startswith("too_old"):
        head = ("这台电脑的 WebView2 运行时版本过旧（当前 %s，需要 %s 或更高）。"
                % (res.get("version") or "未知", MIN_VERSION))
    elif reason.startswith("dotnet"):
        head = "这台电脑的 .NET Framework 版本过旧（WebView2 需要 4.6.2 或更高）。"
    else:
        head = "无法确认这台电脑的 WebView2 运行时是否可用。"

    return (
        "%s\n\n"
        "它是 Windows 自带的浏览器内核组件，通常在更新 Windows 或安装 Microsoft Edge 时一并装好。\n"
        "手动安装地址：%s\n\n"
        "点「确定」= 改用系统浏览器继续使用（功能完整，只是没有独立窗口）\n"
        "点「取消」= 退出程序\n" % (head, DOWNLOAD_URL)
    )
