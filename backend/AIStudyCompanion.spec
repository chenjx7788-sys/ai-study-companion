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
            'rapidocr_onnxruntime', 'cv2', 'fitz', 'pymupdf', 'shapely', 'pyclipper', 'PIL']:
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
