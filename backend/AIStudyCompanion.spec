# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：把后端 + 前端 dist + BGE 模型打成免安装绿色版（Windows/macOS 通用）"""
import sys as _sys
from PyInstaller.utils.hooks import collect_all, collect_data_files

datas = []
binaries = []
hiddenimports = []

# 客户端图标：Windows 用 .ico，macOS 用 .icns（由 scripts/build_macos.sh 从 mascot_b64 生成）
_ICON = 'mascot.icns' if _sys.platform == 'darwin' else 'mascot.ico'

# 前端静态产物（单端口化，main.py 里 _MEIPASS/dist 定位）
datas += [('../frontend/dist', 'dist')]

# 浏览器扩展源码（设置页「下载扩展」现场打包 zip 用，ext.py 里 _MEIPASS/extension 定位）
datas += [('../extension', 'extension')]

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

# macOS 额外收集 pyobjc：pywebview 原生窗口依赖 Cocoa/WebKit（PyInstaller 不自动收集其二进制）。
# ⚠️ 2026-09-22（AI 浏览器下线）后，**cocoa 是 macOS 的首要后端**而非回落候选：
#    `webview/guilib.py::initialize` 的 Darwin 分支是 `[import_cocoa, import_qt]`，
#    此前靠 `PYWEBVIEW_GUI=qt` 把它顶到第二位；该强制设置已移除（见 launcher.py）。
#    ⇒ 这一段收集**必须存在**，否则 macOS 上会走到 `WebViewException` 或回落失败。
if _sys.platform == 'darwin':
    for pkg in ['objc', 'Foundation', 'AppKit', 'WebKit', 'PyObjCTools']:
        try:
            d, b, h = collect_all(pkg)
            datas += d
            binaries += b
            hiddenimports += h
        except Exception:
            pass

# ⚠️ 2026-09-22：**本 spec 不再收集任何 Qt 运行时**（原「阶段 1」整段已删）。
#    阶段 1 曾把两平台外壳从 WebView2 / WKWebView 换到 Qt(QtWebEngine)，为此需要显式
#    `collect_all('qtpy', 'shiboken6')` + `_QT_HIDDEN`（PySide6 子模块）。换 Qt 的**唯一**
#    动因是「应用内 AI 浏览器」要在侧栏塞一个 `QWebEngineView`；该功能下线后 launcher
#    不再设 `PYWEBVIEW_GUI=qt`，各平台回到默认后端：
#        Windows  `[import_winforms]`（系统 WebView2，缺运行时由 launcher 预检拦住 → 不再静默白屏）
#        macOS    `[import_cocoa, import_qt]`
#    ⇒ 连带收益：安装包 -187.6 MB（压缩）/ -324.5 MB（解压），启动 22.8s → 5.7s。
#
#    ⚠️ 历史坑记在这里，别重蹈：
#      · Qt 的 pywebview 后端是**运行期**按需 import（`webview/guilib.py`），PyInstaller
#        静态分析看不见 → 当年必须显式收集，漏了只在真机暴露（症状：静默回落旧后端）。
#      · 当年**不要**用 `collect_all('PySide6')`：它是全量收集，实测把包从 591.2 MB 撑到
#        1259.3 MB（Qt3D / QtCharts / QML 全套 / 18 个开发工具 exe 全被拖进来）。
#      · 若哪天某个「函数内 import」的新模块又漏收，症状与上面第一条**完全一样**：
#        源码态正常、打包版功能不见。新增同类模块时必须同步加进 hiddenimports。

# BGE 向量模型随包（启动器首次启动时预置到用户目录）
datas += [('data/models/bge-small-zh-v1.5', 'data/models/bge-small-zh-v1.5')]

# ⚠️⚠️ 数据文件必须单独收（2026-09-23 实测，R6 时抓到）：
#   `collect_all()` **只收模块与二进制，不收数据文件** —— 上面那些 `collect_all('webview')`
#   之类的调用都拿不到 `*.cfg` / `*.txt`。漏收的后果是**静默失效**：
#     · 缺 `trafilatura/settings.cfg` → `trafilatura.extract()` 抛
#       `NoOptionError: No option 'min_extracted_size' in section: 'DEFAULT'`
#       → 被 `_extract` 的 `except Exception` 吞掉 → 正文为空 →
#       对外报成**误导性的 SPA_EMPTY**（「这个页面是动态渲染的，请粘贴正文」）。
#   实测对照（同一 URL、同一版本）：源码态 `chars=6660`，漏收的包里 `chars=0`。
#   ⚠️ 这个差别**在源码态永远看不见**，只有打包版才暴露 —— 与「Qt 溜进包」是同一类陷阱。
datas += collect_data_files('trafilatura')     # settings.cfg + data/tei_corpus.dtd
datas += collect_data_files('justext')         # stoplists/*.txt（100 个，兜底抽取用）

a = Analysis(
    ['launcher.py'],
    pathex=['.'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # ⚠️⚠️ 2026-09-22：这里的 Qt 三项是**必须的**，而且不只是为了瘦身 ——
    #    光删掉 spec 里原来的显式收集**不足以**让 Qt 离开包。实测（下线后首次出包日志）：
    #      · 照样出现 hook-PySide6.QtQuick / QtQml / QtPositioning / QtQuickWidgets
    #        与 run-time hook `pyi_rth_pyside6.py`；
    #      · 真凶是**上面那行 `collect_all('webview')`** —— 它把 `webview.platforms.qt`
    #        当 hiddenimport 收了进来，而那个模块 `import qtpy / PySide6` →
    #        整棵 Qt 又进了模块图（PyInstaller 的 PySide6 hook 随之触发）。
    #    ⇒ 排除 PySide6 / shiboken6 / qtpy 才是**结构性**保证：包不再依赖
    #      「构建机上恰好没装 PySide6」这个偶然前提。
    #      ⚠️ 这个差别在 CI 上**看不见**（干净 runner 本来就没装 PySide6），
    #         只在开发机上静默多出 187 MB —— 属于「必须在源头堵死」的隐性环境差异。
    #    ⚠️ 排除后 macOS 不受影响：`webview/guilib.py` 的 Darwin 分支是
    #       `[import_cocoa, import_qt]`，cocoa 在前；`import_qt` 的 ImportError 被吞掉。
    #    ⚠️ 这条契约由 `_fetch_probe/verify_pack_no_qt.py` 的 S6 守着（带分辨力证明）。
    excludes=['tkinter', 'matplotlib', 'pytest', 'IPython', 'notebook',
              'PySide6', 'shiboken6', 'qtpy'],
    noarchive=False,
)

# =============================================================================
# 第 1 档瘦身：按 asc_pack_trim 的清单过滤 COLLECT 的输入（2026-09-21）
#
# ⚠️⚠️ 必须在 COLLECT(...) **之前**替换 a.datas / a.binaries：
#    COLLECT 捕获的是这两个列表**对象**，构造之后再换一个新列表**不会生效** ——
#    而且不报错、产物照旧 484 MB，属于「静默失效」。
# ⚠️ 过滤按 dest 名匹配，dest 形态（反斜杠 vs 斜杠、是否有 `_internal/` 前缀）由清单模块归一化。
#    清单一条都没命中时**不会报错**，所以下面打印计数，并对「全 0」硬失败。
# ⚠️ 保留集：babel 包**本身**不能删（courlan/filters.py:11 是硬 import），只删 babel/locale-data/。
# ⚠️⚠️ 2026-09-22：AI 浏览器下线后，第 1 档只剩 babel 这一组规则 —— 原先
#    P1/P2/P3/P5 四条**全部**按 `PySide6/` 前缀匹配，包内已无该目录（见 asc_pack_trim.py 头注）。
# =============================================================================
import os as _os
_sys.path.insert(0, SPECPATH)          # SPECPATH 由 PyInstaller 注入 = 本 spec 所在目录
import asc_pack_trim as _trim

_toc_before = len(a.datas) + len(a.binaries)
_kept_d, _drop_d, _stats_d = _trim.filter_toc(a.datas)
_kept_b, _drop_b, _stats_b = _trim.filter_toc(a.binaries)
a.datas = _kept_d
a.binaries = _kept_b

_merged = {}
for _g in _trim.GROUP_ORDER:
    _merged[_g] = {"files": _stats_d[_g]["files"] + _stats_b[_g]["files"],
                   "bytes": _stats_d[_g]["bytes"] + _stats_b[_g]["bytes"]}
_drop_n = len(_drop_d) + len(_drop_b)
_toc_after = len(a.datas) + len(a.binaries)
print('[spec] ===== 第 1 档瘦身：COLLECT 输入过滤 =====')
print('[spec] TOC 条目 %d -> %d（裁掉 %d 项）'
      % (_toc_before, _toc_after, _drop_n))
print(_trim.format_stats(_merged))
if _drop_n == 0:
    print('[spec] ❌ 裁剪清单一条都没命中 —— dest 名形态与清单不匹配，产物不会变瘦！')
    raise SystemExit('[spec] 第 1 档瘦身失效：检查 asc_pack_trim.normalize_dest / 分组规则')
_empty = [_g for _g in _trim.EXPECT_NONZERO if _merged[_g]["files"] == 0]
if _empty:
    print('[spec] ⚠️ 以下预期非空的分组命中 0（可能是依赖版本变化，请人工确认）：%s' % _empty)

# 落盘统计：把「spec 自己说裁了多少」变成打包后可复核的证据
try:
    import json as _json
    with open(_os.path.join(SPECPATH, 'build_trim_stats.json'), 'w', encoding='utf-8') as _f:
        _json.dump({'groups': _merged, 'dropped_files': _drop_n,
                    'toc_before': _toc_before, 'toc_after': _toc_after}, _f,
                   ensure_ascii=False, indent=2)
except Exception as _e:
    print('[spec] ⚠️ 写 build_trim_stats.json 失败：%s: %s' % (type(_e).__name__, _e))

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
