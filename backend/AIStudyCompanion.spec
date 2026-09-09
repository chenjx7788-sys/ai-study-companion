# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：把后端 + 前端 dist + BGE 模型打成免安装绿色版"""
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []

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
    icon='mascot.ico',          # 客户端图标：猫头鹰「伴伴」
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
