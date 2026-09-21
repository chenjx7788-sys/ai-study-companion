# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：把后端 + 前端 dist + BGE 模型打成免安装绿色版（Windows/macOS 通用）"""
import sys as _sys
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []

# 客户端图标：Windows 用 .ico，macOS 用 .icns（由 scripts/build_macos.sh 从 mascot_b64 生成）
_ICON = 'mascot.icns' if _sys.platform == 'darwin' else 'mascot.ico'

# 前端静态产物（单端口化，main.py 里 _MEIPASS/dist 定位）
datas += [('../frontend/dist', 'dist')]

# 收集依赖（动态库 + 数据文件 + 隐藏导入）
for pkg in ['faster_whisper', 'ctranslate2', 'onnxruntime', 'tokenizers',
            'chromadb', 'uvicorn', 'openai', 'pypdf', 'docx', 'pptx', 'webview',
            'lxml',   # EPUB 章节 XHTML 解析：此前靠依赖链偶然带入，显式收集保证两个平台都齐
            'rapidocr_onnxruntime', 'cv2', 'fitz', 'pymupdf', 'shapely', 'pyclipper', 'PIL',
            # AI 播客 TTS：edge-tts（免费音色）+ aiohttp（WebSocket 客户端，含 C 扩展，需显式收集）
            'edge_tts', 'aiohttp', 'certifi']:
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# macOS 额外收集 pyobjc：pywebview 原生窗口依赖 Cocoa/WebKit（PyInstaller 不自动收集其二进制）
if _sys.platform == 'darwin':
    for pkg in ['objc', 'Foundation', 'AppKit', 'WebKit', 'PyObjCTools']:
        try:
            d, b, h = collect_all(pkg)
            datas += d
            binaries += b
            hiddenimports += h
        except Exception:
            pass

# ⚠️ 阶段 1（GUI 外壳换 Qt）：必须让包里有 PySide6 / shiboken6 / qtpy。
#    launcher.py 只 `import webview`，而 pywebview 的 qt 后端是**运行期**按需 import
#    （webview/guilib.py 里 import webview.platforms.qt），PyInstaller 静态分析看不到 →
#    不显式收集就会打成「装了 PySide6 但包里缺 QtWebEngine」，且要到真机运行才暴露
#    （症状：静默回落到 winforms/cocoa，浏览器能力整体不见）。
#    上方 macOS 的 pyobjc 收集（if _sys.platform == 'darwin'）在阶段 1 后保留，不必删
#    —— cocoa 仍是回落候选（webview/guilib.py 的 Darwin 分支是 [cocoa, qt]）。
#
# ⚠️⚠️ 为什么**不再用** collect_all('PySide6')（阶段 1 · WP10 实测后改的）
#    collect_all 是**全量**收集，实测把包从 591.2 MB 撑到 1259.3 MB（PySide6 单项 632 MB），
#    因为它把 Qt3D / QtCharts / QtDesigner / QtMultimedia / QML 全套风格 /
#    VirtualKeyboard / 18 个 Qt 开发工具 exe（qmlls/assistant/designer/linguist…）全拖了进来。
#    PyInstaller **自带** hook-PySide6.QtWebEngineWidgets / QtWebEngineCore / QtWidgets …
#    （→ utils/hooks/qt/add_qt6_dependencies → qt_info.collect_module，**依赖驱动**）。
#    只要模块名出现在 hiddenimports 里，对应 hook 就会跑，并带上运行期必需的
#    QtWebEngineProcess 可执行文件、resources/*.pak、icudtl.dat
#    （由 hook-PySide6.QtWebEngineCore 的 collect_qtwebengine_files 负责）——
#    所以**不需要** collect_all。
#    ⚠️ 唯一必须 collect_all 的是 **qtpy**：纯 Python 包（很小），且是**动态**选绑定，
#       静态分析连 import 都看不见。shiboken6 是 PySide6 的绑定运行时，一并 collect_all。
#
# ⚠️ 关于「还能再删什么」：**不要**在这里手写 excludes 白名单。
#    实测（_fetch_probe/_probe_qt_imports.py 读 PE 导入表）表明 Qt6WebEngineCore 直接依赖
#    Qt6Quick / Qt6Qml / Qt6Positioning / Qt6WebChannel，Qt6WebEngineWidgets 还依赖
#    Qt6PrintSupport / Qt6QuickWidgets —— 这些「看起来用不到」的模块其实是硬依赖，
#    手写清单极容易自相矛盾，且错了只在运行期暴露（静默回落或启动即崩）。
#    可再裁的候选见报告 §WP10（标注为待实测），要裁必须先跑导入表探针。
_QT_HIDDEN = ['PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineCore', 'PySide6.QtWebChannel',
              'PySide6.QtWidgets', 'PySide6.QtGui', 'PySide6.QtNetwork', 'PySide6.QtCore',
              'qtpy']
hiddenimports += _QT_HIDDEN

# ⚠️ 阶段 2（AI 浏览器）：`app.services.browser_panel` 与 `app.services.browser_host`
#    在源码里是**函数内 import**（`from . import browser_panel`），
#    这是**有意**的（后端必须能在无 Qt 环境 import 这两个模块，见其 docstring）——
#    但代价是 **PyInstaller 静态分析看不到它们** → 不显式声明就会漏收 →
#    打包版表现为「点『AI 浏览器』没反应 / 接口 503」，而源码态一切正常
#    （与 pywebview 的 qt 后端是同一个坑，见上方注释）。
#    故此处显式加入。**新增同类「函数内 import」模块时必须同步加到这里。**
hiddenimports += ['app.services.browser_panel', 'app.services.browser_host']

for pkg in ['qtpy', 'shiboken6']:
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as _e:
        # ⚠️ 大声失败，绝不静默跳过 —— 静默跳过 = 打出一个「没有 QtWebEngine 的 Qt 客户端」
        print('[spec] ⚠️ collect_all(%s) 失败：%s: %s' % (pkg, type(_e).__name__, _e))
        print('[spec] ⚠️ 先装齐依赖再打包：pip install -r backend/requirements.txt'
              '（含 pywebview[pyside6] 拉来的 PySide6 + qtpy）')

# BGE 向量模型随包（启动器首次启动时预置到用户目录）
datas += [('data/models/bge-small-zh-v1.5', 'data/models/bge-small-zh-v1.5')]

a = Analysis(
    ['launcher.py'],
    pathex=['.'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'pytest', 'IPython', 'notebook'],
    noarchive=False,
)

pyz = PYZ(a.pure)

# onedir 模式：二进制与数据文件用 COLLECT 收集到输出目录，避免 onefile 每次启动解压 ~12s
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='AIStudyCompanion',
    icon=_ICON,                  # 客户端图标：猫头鹰「伴伴」（Windows=.ico / macOS=.icns）
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='AIStudyCompanion',
)

# macOS：把 onedir 目录包装成 .app bundle（pywebview 原生窗口依赖 .app 运行环境）
if _sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name='AIStudyCompanion.app',
        icon=_ICON,
        bundle_identifier='com.aistudy.companion',
        info_plist={
            'NSHighResolutionCapable': True,
        },
    )
