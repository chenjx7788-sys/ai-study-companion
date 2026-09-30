"""客户端启动器：内嵌 uvicorn 启动后端 + pywebview 原生窗口（fallback 浏览器）+ 首次预置 BGE 模型

开发态用 start.bat（前后端分离）；本启动器供 PyInstaller 打包的客户端版使用。
环境变量 ASC_BROWSER=1 可强制走浏览器模式（调试用）。
"""
import os
import re
import sys
import threading
import time
import webbrowser

from app_version import __version__ as APP_VERSION

# ⚠️⚠️ 控制台编码兜底 —— 必须早于本文件**任何**一句中文 print（也早于 import app.main）。
#
# 打包版是窗口模式（AIStudyCompanion.spec 的 console=False）。当 stdout/stderr 被重定向、
# 而目标编码不是 UTF-8 时（英文 Windows 的 ANSI 代码页是 cp1252），第一句中文 print
# 就抛 UnicodeEncodeError。2026-09-30 CI 实测（Build Windows #6，注解原文）：
#
#   File "launcher.py", line 79, in _ensure_models
#   UnicodeEncodeError: 'charmap' codec can't encode characters in position 11-13
#   During handling of the above exception, another exception occurred:
#   File "launcher.py", line 81, in _ensure_models    ← except 分支里**还有一句**中文 print
#
# 于是：第一句崩 → 被 except 接住 → except 里那句又崩 → 异常逃出 main()，
# 应用**永远起不来**，外部只看到「90s 没响应」。中文 Windows（cp936）编得出中文、
# macOS 是 UTF-8，所以本机与 mac 一直是绿的 —— 这是只在英文环境下才现形的缺陷。
#
# 这里是**单点修复**：一次 reconfigure 覆盖本进程后续所有输出，包括
# `from app.main import app` 之后 app/ 里那些中文 print（如 main.py 的 chroma 重建日志、
# webview_js.py 的 emoji）。只改**输出编码**，不动任何业务行为；
# errors="replace" 保证即便目标仍不支持 UTF-8，也只会显示成问号而不是把应用打死。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PORT = int(os.environ.get("ASC_PORT", "8000"))


def _safe_print(msg: str) -> None:
    """打印**绝不允许**打死应用。

    兜底一：窗口模式且没有重定向时 sys.stdout 可能是 None（print 本就是 no-op）。
    兜底二：万一上面的 reconfigure 没生效（stdout 不是可 reconfig 的 TextIOWrapper），
            print 仍可能抛 UnicodeEncodeError —— 而这里处在启动关键路径上，
            抛出去就等于「应用起不来」。见 _ensure_models 里 2026-09-30 的那次事故。
    """
    try:
        print(msg, flush=True)
    except Exception:
        pass


# 启动 loading 页：内联 HTML，不依赖后端。窗口先显示它，前端轮询 /api/health 就绪后自动跳转主页。
LOADING_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>知萤 - AI知识中枢</title>
<style>
  * { box-sizing: border-box; }
  body { margin: 0; height: 100vh; display: flex; align-items: center; justify-content: center;
         background: #f5f5f6; font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif; }
  .wrap { text-align: center; }
  .mascot { width: 92px; height: 92px; margin: 0 auto 22px; display: block;
            filter: drop-shadow(0 8px 18px rgba(124, 92, 252, .35)); }
  .spinner { width: 30px; height: 30px; margin: 0 auto 16px; border-radius: 50%;
             border: 3px solid #e3e3e6; border-top-color: #7c5cfc;
             animation: spin .8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .text { color: #6e6e6e; font-size: 14px; }
  .tip { color: #a0a0a0; font-size: 12px; margin-top: 8px; }
</style>
</head>
<body>
  <div class="wrap">
    <img class="mascot" src="data:image/png;base64,__MASCOT_B64__" alt="伴伴" />
    <div class="spinner"></div>
    <div class="text">正在启动知萤 - AI知识中枢…</div>
    <div class="tip">首次启动需加载本地模型，请稍候</div>
  </div>
<script>
  var PORT = '__PORT__';
  var VER = '__VER__';
  // 入口 URL 带版本号：与 WebView 后端无关地保证「升级后不复用旧 index.html」
  //（query 只影响缓存键，不影响 SPA 路由：前端用的是 createWebHistory）
  function jump() { window.location.href = 'http://127.0.0.1:' + PORT + '/?v=' + VER; }
  function poll() {
    try {
      fetch('http://127.0.0.1:' + PORT + '/api/health')
        .then(function (r) { r.ok ? jump() : setTimeout(poll, 300); })
        .catch(function () { setTimeout(poll, 300); });
    } catch (e) { setTimeout(poll, 300); }
  }
  poll();
</script>
</body>
</html>"""


def _ensure_models():
    """把随包分发的 BGE 向量模型复制到用户数据目录（首次启动）"""
    if not getattr(sys, "frozen", False):
        return
    from pathlib import Path
    import shutil
    from app.core.config import settings

    src = Path(sys._MEIPASS) / "data" / "models" / "bge-small-zh-v1.5"
    dst = settings.data_dir / "models" / "bge-small-zh-v1.5"
    try:
        if src.exists() and not (dst / "model.onnx").exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, dst)
            _safe_print("[launcher] 已预置 BGE 向量模型")
    except Exception as e:
        # ⚠️ 这里**必须**用 _safe_print：本分支原本是 print，而当年触发它的那次异常
        #    正是「上一句 print 编不出中文」—— 于是 except 里的 print 二次崩溃，
        #    异常逃出 main()，应用永远起不来（Build Windows #6）。
        _safe_print("[launcher] 预置模型失败（将走自动下载）：%s" % e)


def _open_browser():
    time.sleep(2.5)  # 等服务器就绪
    webbrowser.open(f"http://127.0.0.1:{PORT}")


def _log_shell(line):
    """把外壳选型/预检结果同时写 stdout **和** data_dir 下的 `launcher_backend.log`。

    ⚠️⚠️ 必须落盘：打包版是窗口化运行（`runw` 无 stdout），只 print 的话事后**无法判断**
       预检到底跑没跑 —— `_report_webview_backend()` 落盘 `backend=` 行是同一个道理。
    ⚠️ `flush=True` 也必须有：本进程是长驻 GUI，被强杀时未 flush 的输出会整段丢失
       （实测：预检的 print 在干净进程树的日志里一行都没留下）。
    """
    line = "%s  %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), line)
    print("[launcher] " + line, flush=True)
    try:
        from app.core.config import settings
        with open(settings.data_dir / "launcher_backend.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        print("[launcher] 外壳日志写入失败：%s" % e, flush=True)


def _webview2_preflight():
    """进 pywebview 之前的最后一道闸：确认这台机器的 WebView2 可用。

    返回 True = 可以走原生外壳；False = 用户选择退出（调用方应**直接 return**）。

    ⚠️⚠️ 为什么不能省掉这一检：pywebview 探测不到 WebView2 时**不抛异常**，而是静默回落
       到 `mshtml`（IE 引擎），只打一行 logger.warning
       （webview/platforms/winforms.py:147-155）。而前端产物是现代 ES module
       （vite 未加 legacy 插件）→ IE 引擎执行不了 → **白屏且界面上零提示**，
       launcher 现有的 fallback 也抓不到（没有异常可抓）。
    ⚠️ 检测口径与 pywebview 的 `_is_chromium()` 逐条一致，见 `app/core/webview2_check.py`。
    """
    from app.core import webview2_check

    res = webview2_check.detect()
    if res["ok"]:
        if res.get("reason") != "not_windows":
            _log_shell("webview2=ok  version=%s  source=%s"
                       % (res.get("version") or "?", res.get("source") or "?"))
        return True
    _log_shell("webview2=UNAVAILABLE  reason=%s  version=%r"
               % (res.get("reason"), res.get("version") or ""))
    return _ask_continue_without_shell(webview2_check.user_hint(res))


def _ask_continue_without_shell(hint):
    """原生弹窗（**不依赖 Qt / pywebview**，只用 ctypes）。True = 用浏览器模式继续。

    ⚠️ 这就是「外壳依赖系统运行时」的兜底：运行时可能缺失，缺了必须给**人话提示**，
       绝不能白屏。同一个提示也解释了为什么退回浏览器模式仍然可用。
    """
    if sys.platform != "win32":
        print(hint)
        return True          # 非 Windows 不该走到这里（detect() 已放行）
    try:
        import ctypes

        MB_OKCANCEL, MB_ICONWARNING, MB_TOPMOST = 0x1, 0x30, 0x40000
        r = ctypes.windll.user32.MessageBoxW(
            None, hint, "知萤 · 需要安装 WebView2 运行时",
            MB_OKCANCEL | MB_ICONWARNING | MB_TOPMOST)
        return r == 1        # IDOK = 用系统浏览器继续
    except Exception as e:
        print("[launcher] 弹窗失败（%s），默认走浏览器模式" % type(e).__name__)
        return True


def _report_webview_backend():
    """webview.start() 确定后端后回调：把**实际**后端写进日志，供事后核对（防静默回落）。

    ⚠️ 只读 webview.guilib 的模块属性，不碰任何控件 —— 本回调运行在工作线程
    （实测 Thread-2）；跨线程操作控件会挂死（阶段 0 阴性对照 rc=3）。
    ⚠️ 期望值是 `webview.platforms.winforms`（Windows，renderer=edgechromium）/`webview.platforms.cocoa`（macOS）；
    出现 `mshtml` 即说明这台机器缺 WebView2 运行时（后果是白屏），出现 `qt` 表示被 PYWEBVIEW_GUI 强制切走。
    """
    import webview
    name = getattr(webview.guilib, "__name__", "unknown")
    line = "%s  backend=%s  want=PYWEBVIEW_GUI=%s" % (
        time.strftime("%Y-%m-%d %H:%M:%S"), name, os.environ.get("PYWEBVIEW_GUI", "<unset>"))
    print(f"[launcher] webview {line}")
    try:
        from app.core.config import settings
        with open(settings.data_dir / "launcher_backend.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        print(f"[launcher] 后端日志写入失败：{e}")


def _webview_storage_path():
    """WebView 持久化存储目录：localStorage（新手引导/侧边栏折叠等本地记忆）

    ⚠️ 当前后端是 WebView2：这是一个**完整的用户数据目录**，HTTP 缓存与 local storage
      都在其下（`Cache/` `Code Cache/` `Service Worker/` 见 _purge_webview_cache_on_upgrade）。
    ⚠️ 若将来又被切到 Qt(QtWebEngine)，本条不再成立：那时它只被传给
      `QWebEngineProfile.setPersistentStoragePath()`（只管持久化存储），HTTP 缓存另有位置。
    """
    from app.core.config import settings
    p = settings.data_dir / "webview"
    p.mkdir(parents=True, exist_ok=True)
    return str(p)


def _purge_webview_cache_on_upgrade():
    """版本变化时清掉 WebView2 的 HTTP/JS 缓存（保留 Local Storage）。

    背景：前端静态资源此前无 Cache-Control，浏览器对 index.html 走「启发式缓存」，
    升级后仍复用旧入口 → 去请求新包里已不存在的旧分片（404）→ 路由懒加载静默失败，
    表现为「点菜单/按钮没反应」。此处在启动窗口创建前清理缓存，从根上避免。

    ⚠️ 生效范围：本函数**只对 WebView2 有效**（Windows 当前形态）—— 清的是
    `webview/EBWebView/Default/{Cache, Code Cache, Service Worker}`。
    （曾有一段时间外壳是 Qt(QtWebEngine)，那时本函数是空操作；2026-09-22 换回 WebView2 后重新生效。）
    另有两道更靠前的机制兜底，与本函数**互补而非替代**：
      ① 后端 main.py 给 index.html 发 `Cache-Control: no-cache, must-revalidate`
         （哈希分片走 `immutable`）→ 入口每次回源校验，与新分片天然配套；
      ② 入口 URL 自带版本号（见 LOADING_HTML 的 ?v=）→ 版本一变即全新缓存键，且与后端无关。
    """
    import json
    import shutil
    from app.core.config import settings

    state_path = settings.data_dir / "launcher_state.json"
    try:
        old = json.loads(state_path.read_text(encoding="utf-8")).get("version", "")
    except Exception:
        old = ""   # 无状态文件（含首次从旧版升级）→ 同样清理一次，代价只是首次冷启动
    if old == APP_VERSION:
        return

    default_dir = settings.data_dir / "webview" / "EBWebView" / "Default"
    for name in ("Cache", "Code Cache", "Service Worker"):
        target = default_dir / name
        if not target.exists():
            continue
        shutil.rmtree(target, ignore_errors=True)
        if target.exists():   # 删除失败时改名（等效失效，且不影响运行）
            try:
                target.rename(target.with_name(f"{name}.old-{int(time.time())}"))
            except Exception:
                pass
    try:
        state_path.write_text(json.dumps({"version": APP_VERSION}), encoding="utf-8")
        print(f"[launcher] 版本 {old or '(首次)'} → {APP_VERSION}，已清理 WebView 旧缓存")
    except Exception as e:
        print(f"[launcher] 写入版本状态失败：{e}")


class _Api:
    """pywebview js_api：暴露原生文件/文件夹选择，前端拿到真实路径后做「本地文件直引」"""

    def pick_files(self):
        import webview
        if not webview.windows:
            return []
        r = webview.windows[0].create_file_dialog(webview.OPEN_DIALOG, allow_multiple=True)
        if not r:
            return []
        return [r] if isinstance(r, str) else list(r)

    def pick_folder(self):
        import webview
        if not webview.windows:
            return []
        r = webview.windows[0].create_file_dialog(webview.FOLDER_DIALOG)
        if not r:
            return []
        return [r] if isinstance(r, str) else list(r)


def main():
    _ensure_models()
    _purge_webview_cache_on_upgrade()   # 升级后清 WebView 旧缓存（须在窗口创建前）

    import uvicorn

    def start_backend():
        from app.main import app  # 延迟导入：约 6s（重依赖），放到后台线程，让 loading 窗口先出现
        uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")

    # 客户端默认 pywebview 原生窗口（ASC_BROWSER=1 强制浏览器）
    if os.environ.get("ASC_BROWSER", "") != "1":
        # ⚠️⚠️ WebView2 预检必须在 `import webview` / `create_window` **之前**：
        #    缺运行时的话 pywebview **不抛异常**、静默回落 mshtml(IE 引擎) → 白屏且零提示。
        if not _webview2_preflight():
            return
        try:
            import webview
            from mascot_b64 import MASCOT_B64
            # 允许下载（导出笔记/备份等），否则 WebView2 默认静默取消下载，界面无任何反馈
            webview.settings['ALLOW_DOWNLOADS'] = True
            # 先显示 loading 窗口（内联 HTML，不依赖后端），后端在后台线程启动
            webview.create_window(
                "知萤 - AI知识中枢",
                html=LOADING_HTML.replace("__PORT__", str(PORT))
                                 .replace("__VER__", APP_VERSION)
                                 .replace("__MASCOT_B64__", MASCOT_B64),
                width=1200, height=800,
                min_size=(960, 640),
                text_select=True,   # 必须显式开启，否则客户端无法选中文字 → 划线/AI解读/转笔记/复制全部失效
                js_api=_Api(),      # 本地文件直引：原生文件/文件夹选择对话框
            )
            threading.Thread(target=start_backend, daemon=True).start()
            # private_mode=False + 持久化 storage_path：否则 localStorage 每次退出清空，
            # 新手引导、侧边栏折叠等本地记忆会失效（每次启动都重新弹出）
            webview.start(_report_webview_backend, private_mode=False,
                          storage_path=_webview_storage_path())
            return
        except Exception as e:
            print(f"[launcher] 原生窗口启动失败，回退浏览器模式：{e}")

    # 浏览器模式（fallback）
    threading.Thread(target=_open_browser, daemon=True).start()
    start_backend()


if __name__ == "__main__":
    main()
