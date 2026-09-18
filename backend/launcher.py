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

PORT = int(os.environ.get("ASC_PORT", "8000"))

# 启动 loading 页：内联 HTML，不依赖后端。窗口先显示它，前端轮询 /api/health 就绪后自动跳转主页。
LOADING_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI 伴学助手</title>
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
    <div class="text">正在启动 AI 伴学助手…</div>
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
            print("[launcher] 已预置 BGE 向量模型")
    except Exception as e:
        print(f"[launcher] 预置模型失败（将走自动下载）：{e}")


def _open_browser():
    time.sleep(2.5)  # 等服务器就绪
    webbrowser.open(f"http://127.0.0.1:{PORT}")


def _ensure_qt_backend():
    """显式要求 Qt 后端，并在启动前预检依赖（阶段 1：外壳 WebView2/WKWebView → QtWebEngine）

    ⚠️ 为什么不能只设环境变量就算切换 —— webview/guilib.py 的后端候选是**列表回退**：
        Windows：PYWEBVIEW_GUI=qt → [import_qt, import_winforms]；未设 → [import_winforms]
        Darwin ：PYWEBVIEW_GUI=qt → [import_qt, import_cocoa]；  未设 → [import_cocoa, import_qt]
      即 qt 起不来时会**静默回落到旧后端**：不报错、不警告，表现成「应用一切正常，
      只是浏览器相关能力不见」。所以「设了变量」不等于「切换成功」。
    本函数只负责「表达意图 + 启动前预检」；真正的判据是启动后的
    _report_webview_backend()（读 webview.guilib 的**实际**值）。
    """
    if "PYWEBVIEW_GUI" not in os.environ:
        os.environ["PYWEBVIEW_GUI"] = "qt"      # setdefault 语义：尊重调用方已设的值
    want = os.environ["PYWEBVIEW_GUI"].strip().lower()
    if want != "qt":
        print(f"[launcher] 后端由 PYWEBVIEW_GUI={want} 指定（非默认 qt），跳过 Qt 预检")
        return
    try:
        import qtpy  # noqa: F401          # pywebview 的 qt 后端走 qtpy 抽象层（只随 extras 安装）
        from qtpy.QtWebEngineWidgets import QWebEngineView  # noqa: F401
        print("[launcher] Qt 后端预检通过（qtpy + QtWebEngine 可导入）")
    except Exception as e:
        print(f"[launcher] ⚠️ Qt 后端预检失败：{type(e).__name__}: {e}")
        print("[launcher] ⚠️ pywebview 将静默回落到旧后端（Windows=WebView2 / macOS=WKWebView）——"
              "打包版出现此警告即表示 spec 漏收 PySide6，见 AIStudyCompanion.spec")


def _report_webview_backend():
    """webview.start() 确定后端后回调：把**实际**后端写进日志，供事后核对（防静默回落）。

    ⚠️ 只读 webview.guilib 的模块属性，不碰任何 Qt 控件 —— 本回调运行在工作线程
    （实测 Thread-2）；跨线程操作 Qt 控件会挂死（阶段 0 阴性对照 rc=3）。
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


_BAD_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _patch_qt_download_handler():
    """修正 pywebview 6.2.1 Qt 后端下载回调的**两个上游缺陷**（阶段 1 换 Qt 外壳后暴露）。

    ⚠️ 缺陷在 site-packages 里（webview/platforms/qt.py:739-747），不能改库，只能在此打补丁。
    两个错误各自独立、叠加后必然导致下载不可用：

      ① 把 `download.url().path()` 当「默认文件名」喂给 QFileDialog.getSaveFileName。
         该函数第 3 参 dir 允许「目录 + 预填文件名」，于是对话框预填的是
         `/api/materials/7/file` 这类含 `/` 的串 → Windows 原生「另存为」拒绝关闭
         并提示**「文件名无效」**。产品的 4 个下载入口（材料文件 / 笔记导出 /
         播客文稿导出 / 设置页备份）URL 全部命中。

      ② 调 `download.setPath(path)` —— Qt6 的 QWebEngineDownloadRequest **没有**
         setPath，只有 setDownloadDirectory() / setDownloadFileName()。
         即便用户手改成合法名点「确定」，也会在 Qt 信号回调里抛 AttributeError，
         界面无任何提示 → 表现为「点了没反应」。

    ⚠️ **升级 pywebview 解决不了**：上游 master 分支仍逐字写着 `download.setPath(path)`
       （2026-09-18 核对 raw.githubusercontent.com/r0x0r/pywebview/master）。
       ⇒ 本机打补丁恢复可用；上游 issue 另行跟进。

    ⚠️⚠️ **必须在 `webview.create_window()` / `webview.start()` 之前调用**，两个原因：
      ① `webview.start()` 是**阻塞**的 GUI 主循环，写在它后面的代码要等窗口关闭才执行
         → 补丁在程序存活期间**永远不会生效**（本函数第一版就错在这一点）。
      ② `qt.py:450` 在 `BrowserView.__init__` 里
         `downloadRequested.connect(self.on_download_requested)` —— `self.xxx` 在**那一刻**
         已求值成绑定方法。实测：先构造后替换类属性 = **无效**；先替换后构造 = 有效。
         而 BrowserView 由 `webview.start()` 内部构造 → 补丁必须早于它。

    ⚠️ **提前 import 的代价必须一并处理**：`qt.py:71-73` 的模块级
       `_profile_storage_path = _state['storage_path'] or ~/.pywebview`
       在**导入期**求值，而 `_state['storage_path']` 要到 `webview.start()` 内部
       （webview/__init__.py:225）才设置 → 提前导入时它拿到 None → 落到 `~/.pywebview`
       → `setPersistentStoragePath` 指向错目录 → **localStorage 静默换目录**
       （新手引导、侧边栏折叠等本地记忆丢失）。故此处**显式覆盖**该模块变量。
       实测：覆盖后再次 import 不会被重置（命中 sys.modules 缓存）。
    """
    # 只在「确实要走 Qt 后端」时装补丁；否则（如回落 winforms/cocoa）导入 qt.py 无意义
    if os.environ.get("PYWEBVIEW_GUI", "").strip().lower() != "qt":
        print("[launcher] 非 Qt 后端，跳过下载回调补丁")
        return False

    try:
        from webview.platforms import qt as _qt
        from qtpy import QtCore
        from qtpy.QtWidgets import QFileDialog
    except Exception as e:
        print(f"[launcher] ⚠️ 下载回调补丁跳过（无法导入 Qt 后端）：{type(e).__name__}: {e}")
        return False

    # ⚠️ 修正「提前 import」导致被过早抓取的持久化存储路径（见上方 docstring）。
    #    用同一个 _webview_storage_path()，保证与 webview.start(storage_path=...) 一致。
    try:
        _want_storage = _webview_storage_path()
        if getattr(_qt, "_profile_storage_path", None) != _want_storage:
            print("[launcher] 修正 Qt 持久化存储路径：%r → %r"
                  % (getattr(_qt, "_profile_storage_path", None), _want_storage))
            _qt._profile_storage_path = _want_storage
    except Exception as e:
        print(f"[launcher] ⚠️ 修正 Qt 存储路径失败（localStorage 可能落错目录）：{type(e).__name__}: {e}")

    if getattr(_qt.BrowserView.on_download_requested, "_asc_patched", False):
        return True     # 幂等：重复调用不叠加包装

    def _safe_filename(name):
        """清掉 Windows 文件名非法字符与控制字符；空则给兜底名。

        先按路径分隔符取末段（`a/b.txt` → `b.txt`，比替换成 `a_b.txt` 更自然），
        再用正则清掉剩余非法字符。
        """
        name = (name or "").replace("\\", "/").split("/")[-1]
        name = _BAD_FILENAME_CHARS.sub("_", name).strip().strip(".")
        return name or "download"

    def on_download_requested(self, download):
        # 优先用 Qt 从 Content-Disposition 解析出的建议名；拿不到才退回 URL 末段。
        suggested = ""
        try:
            suggested = download.suggestedFileName() or ""
        except Exception:
            pass
        if not suggested:
            suggested = QtCore.QFileInfo(download.url().path()).fileName()
        suggested = _safe_filename(suggested)

        suffix = QtCore.QFileInfo(suggested).suffix()
        downloads = os.path.join(os.path.expanduser("~"), "Downloads")
        start = os.path.join(downloads, suggested) if os.path.isdir(downloads) else suggested

        path, _ = QFileDialog.getSaveFileName(
            self, self.localization["global.saveFile"], start,
            ("*." + suffix) if suffix else "*")
        if not path:
            return      # 用户取消：不 accept（与原逻辑一致）
        directory, filename = os.path.split(path)
        if directory:
            download.setDownloadDirectory(directory)
        download.setDownloadFileName(filename or suggested)
        download.accept()

    on_download_requested._asc_patched = True
    _qt.BrowserView.on_download_requested = on_download_requested
    print("[launcher] 已修正 pywebview Qt 下载回调"
          "（默认名改用 suggestedFileName + 清洗；setPath → setDownloadDirectory/FileName）")
    return True


def _webview_storage_path():
    """WebView 持久化存储目录：localStorage（新手引导/侧边栏折叠等本地记忆）

    ⚠️ 后端差异（阶段 1 换 Qt 后走第二支，别按 WebView2 的结构去推断路径）：
      - WebView2：整个用户数据目录，缓存与 local storage 都在其下；
      - Qt(QtWebEngine)：pywebview 只把它传给 QWebEngineProfile.setPersistentStoragePath()，
        即**只管持久化存储**；HTTP 缓存另有位置（pywebview 未调 setCachePath），
        故本目录下**不存在** Cache/Code Cache —— 见 _purge_webview_cache_on_upgrade 的边界说明。
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

    ⚠️ 生效范围：本函数**只对 WebView2（Windows 当前形态）有效** —— 清的是
    webview/EBWebView/Default/{Cache,Code Cache,Service Worker}。
    阶段 1 外壳换成 Qt(QtWebEngine) 后这里是**空操作**：pywebview 的 qt 后端只调用
    setPersistentStoragePath()（webview/platforms/qt.py），**没有 setCachePath()**，
    Qt 的 HTTP 缓存因此不在 storage_path 下（在 Qt 的 QStandardPaths::CacheLocation）。
    ⚠️ 不要在这里补一个「猜出来的 Qt 缓存路径」—— 路径未实测，写错就是这个函数现在的样子
    （看着在做事、实际什么都没清）。Qt 侧改由下面两道更靠前的机制保证，本函数保留仅为 WebView2 兜底：
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
        try:
            import webview
            from mascot_b64 import MASCOT_B64
            _ensure_qt_backend()   # 阶段 1：显式要求 Qt 后端（失败会被静默回落，见该函数注释）
            # 允许下载（导出笔记/备份等），否则 WebView2 默认静默取消下载，界面无任何反馈
            webview.settings['ALLOW_DOWNLOADS'] = True
            # ⚠️⚠️ 必须在此处装（早于 create_window/start）：start() 会阻塞，写在它之后等于没装；
            #     且 BrowserView 在 start() 内部构造、构造时就 connect 了绑定方法。详见该函数 docstring。
            _patch_qt_download_handler()
            # 先显示 loading 窗口（内联 HTML，不依赖后端），后端在后台线程启动
            webview.create_window(
                "AI 伴学助手",
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
