"""客户端启动器：内嵌 uvicorn 启动后端 + pywebview 原生窗口（fallback 浏览器）+ 首次预置 BGE 模型

开发态用 start.bat（前后端分离）；本启动器供 PyInstaller 打包的客户端版使用。
环境变量 ASC_BROWSER=1 可强制走浏览器模式（调试用）。
"""
import os
import sys
import threading
import time
import webbrowser

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
  function jump() { window.location.href = 'http://127.0.0.1:' + PORT + '/'; }
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


def _webview_storage_path():
    """WebView2 用户数据目录：持久化 localStorage（新手引导/侧边栏折叠等本地记忆）"""
    from app.core.config import settings
    p = settings.data_dir / "webview"
    p.mkdir(parents=True, exist_ok=True)
    return str(p)


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

    import uvicorn

    def start_backend():
        from app.main import app  # 延迟导入：约 6s（重依赖），放到后台线程，让 loading 窗口先出现
        uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")

    # 客户端默认 pywebview 原生窗口（ASC_BROWSER=1 强制浏览器）
    if os.environ.get("ASC_BROWSER", "") != "1":
        try:
            import webview
            from mascot_b64 import MASCOT_B64
            # 允许下载（导出笔记/备份等），否则 WebView2 默认静默取消下载，界面无任何反馈
            webview.settings['ALLOW_DOWNLOADS'] = True
            # 先显示 loading 窗口（内联 HTML，不依赖后端），后端在后台线程启动
            webview.create_window(
                "AI 伴学助手",
                html=LOADING_HTML.replace("__PORT__", str(PORT)).replace("__MASCOT_B64__", MASCOT_B64),
                width=1200, height=800,
                min_size=(960, 640),
                text_select=True,   # 必须显式开启，否则客户端无法选中文字 → 划线/AI解读/转笔记/复制全部失效
                js_api=_Api(),      # 本地文件直引：原生文件/文件夹选择对话框
            )
            threading.Thread(target=start_backend, daemon=True).start()
            # private_mode=False + 持久化 storage_path：否则 localStorage 每次退出清空，
            # 新手引导、侧边栏折叠等本地记忆会失效（每次启动都重新弹出）
            webview.start(private_mode=False, storage_path=_webview_storage_path())
            return
        except Exception as e:
            print(f"[launcher] 原生窗口启动失败，回退浏览器模式：{e}")

    # 浏览器模式（fallback）
    threading.Thread(target=_open_browser, daemon=True).start()
    start_backend()


if __name__ == "__main__":
    main()
