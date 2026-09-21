# -*- coding: utf-8 -*-
"""打包裁剪清单：PyInstaller COLLECT 的**输入过滤器**（Windows / macOS 共用）。

为什么放在 spec 外面单独一个模块
--------------------------------
`AIStudyCompanion.spec` 会 import 本模块，`_fetch_probe/_t_pack_trim.py` 也会 import 本模块
—— **同一份清单**。若把规则写死在 spec 里，测试就只能复制一份，两边一旦漂移就会出现
「测试绿、产物没瘦」这种最难发现的错。

为什么过滤 `a.datas` 而不是打包完再删文件
----------------------------------------
COLLECT 之前过滤 ⇒ 文件**根本不会被复制**到 dist。事后删文件既慢、又要二次判断，
而且会撞上本机的批量删除护栏（阈值 50/回合，超了抛 SystemExit）。

⚠️ 三条「静默失效」的坑（都踩过或差点踩）
1. **dest 名形态不固定**：Windows 上可能带 `\\`、也可能带 `_internal/` 前缀。
   本模块统一 normalize 后再匹配；匹配不上就**不会**裁掉任何东西 ——
   而「没裁掉」在体积报告里看起来只像「收益比预期小」，不会报错。
   ⇒ 故 spec 里必须打印各组命中计数，且**预期非零的组命中 0 时大声失败**。
2. **保留集必须显式**：`qtwebengine_locales` 里 `en-US.pak` 是 Chromium 的兜底语言包，
   `zh-CN.pak` 是本产品的界面语言。不能「只留 zh-CN」。
3. **`babel` 包本身不能删**：`courlan/filters.py:11` 是 `from babel import Locale`
   → 硬 import。只可删 `babel/locale-data/`（数据子目录）。
   实测（`_fetch_probe/_probe_babel_langpath.py`）：删掉后 courlan 判语言的 20 项行为
   与基线**逐项完全一致**（`Locale.parse` 的 `.language` 不读 locale-data）。
"""

import os
import posixpath

# --- 分组标识（报告与日志里按这个口径统计）---
G_DEBUG_PAK = "P1-debug-pak"
G_QT_QM = "P2-qt-qm"
G_WEBENGINE_LOCALE = "P3-webengine-locale"
G_BABEL_LOCALE_DATA = "P4-babel-locale-data"
G_DEVTOOLS_PAK = "P5-devtools-pak"

GROUP_ORDER = [G_DEBUG_PAK, G_QT_QM, G_WEBENGINE_LOCALE, G_BABEL_LOCALE_DATA, G_DEVTOOLS_PAK]

GROUP_DESC = {
    G_DEBUG_PAK: "Chromium 调试版资源包（*.debug.pak）—— release 版不加载",
    G_QT_QM: "Qt 翻译目录（*.qm）—— 本应用**从不**安装 QTranslator，全部不会被读",
    G_WEBENGINE_LOCALE: "Chromium 语言包 —— 只保留 en-US（兜底）与 zh-CN（界面语言）",
    G_BABEL_LOCALE_DATA: "babel 的 locale-data 数据（babel 包本身保留，courlan 要 import）",
    G_DEVTOOLS_PAK: "Chromium DevTools 前端资源 —— 本应用未启用 F12/远程调试",
}

# 保留集：Chromium 必需兜底 + 本产品界面语言。**只写文件名，比较前统一小写。**
KEEP_WEBENGINE_LOCALES = frozenset({"en-us.pak", "zh-cn.pak"})

# P5 单独开关：它是第 1 档里唯一「有潜在功能取舍」的一项（失去 F12 / 远程调试的逃生口）。
# 2026-09-21 实测（阶段1-B）：删掉后 WP12 端到端验收 12/12 通过（含真实导航 load_ok=True、
# A0 后端仍为 qt）；且前端/后端**没有任何**启用 DevTools 的代码路径
# （无 DeveloperExtras / 无 QTWEBENGINE_REMOTE_DEBUGGING）⇒ 对产品零回归。
# 若将来要恢复 F12 能力，把这里改成 False 重新打包即可。
DROP_DEVTOOLS = True
DEFAULT_DROP_DEVTOOLS = DROP_DEVTOOLS

# 打包正常时应命中的分组（命中 0 说明依赖版本变了或 dest 形态不匹配 —— spec 会大声提示）
EXPECT_NONZERO = [G_DEBUG_PAK, G_QT_QM, G_WEBENGINE_LOCALE, G_BABEL_LOCALE_DATA]


def normalize_dest(dest):
    """把 COLLECT 条目的 dest 名归一化：`\\`→`/`、去掉 `_internal/` 前缀与 `./`、转小写。

    归一化只用于**匹配**，不用于写回，所以不影响产物路径大小写。
    """
    if dest is None:
        return ""
    d = str(dest).replace("\\", "/")
    # PyInstaller 6 的 onedir 会把内容放进 _internal/；TOC 里的 dest 一般不带，
    # 但若带（或带 './'）就剥掉，保证两种形态都能匹配。
    while d.startswith("./"):
        d = d[2:]
    if d.startswith("_internal/"):
        d = d[len("_internal/"):]
    if d.startswith("/"):
        d = d.lstrip("/")
    return d.lower()


def drop_reason(dest, drop_devtools=None):
    """返回该 dest 应被裁掉的**分组名**；不该裁则返回 None。

    drop_devtools=None ⇒ 取 DEFAULT_DROP_DEVTOOLS。
    """
    if drop_devtools is None:
        drop_devtools = DEFAULT_DROP_DEVTOOLS
    d = normalize_dest(dest)
    if not d:
        return None
    name = posixpath.basename(d)

    # P1：PySide6/resources/*.debug.pak
    if d.startswith("pyside6/resources/") and name.endswith(".debug.pak"):
        return G_DEBUG_PAK

    # P5：PySide6/resources/qtwebengine_devtools_resources.pak（**非** debug 那个）
    if drop_devtools and d.startswith("pyside6/resources/") and \
            name == "qtwebengine_devtools_resources.pak":
        return G_DEVTOOLS_PAK

    # P2：PySide6/translations/**/*.qm（含子目录，虽然目前只有顶层有 .qm）
    if d.startswith("pyside6/translations/") and name.endswith(".qm"):
        return G_QT_QM

    # P3：PySide6/translations/qtwebengine_locales/*.pak（保留 KEEP_WEBENGINE_LOCALES）
    if d.startswith("pyside6/translations/qtwebengine_locales/") and name.endswith(".pak"):
        if name in KEEP_WEBENGINE_LOCALES:
            return None
        return G_WEBENGINE_LOCALE

    # P4：babel/locale-data/**
    if d.startswith("babel/locale-data/"):
        return G_BABEL_LOCALE_DATA

    return None


def filter_toc(entries, drop_devtools=None):
    """过滤 TOC（list of (dest, src, typecode)）。

    返回 (kept, dropped, stats)：
      kept    —— 保留的条目（保持原顺序与原始字段，不做任何改写）
      dropped —— 被裁的条目
      stats   —— {group: {"files": n, "bytes": n}}（bytes 取 src 的真实大小，取不到记 0）
    """
    kept = []
    dropped = []
    stats = {g: {"files": 0, "bytes": 0} for g in GROUP_ORDER}
    for e in entries:
        try:
            dest = e[0]
            src = e[1] if len(e) > 1 else None
        except Exception:
            kept.append(e)
            continue
        g = drop_reason(dest, drop_devtools=drop_devtools)
        if g is None:
            kept.append(e)
            continue
        dropped.append(e)
        stats[g]["files"] += 1
        try:
            if src and os.path.isfile(src):
                stats[g]["bytes"] += os.path.getsize(src)
        except Exception:
            pass
    return kept, dropped, stats


def format_stats(stats):
    """把 stats 渲染成多行可读文本（spec 里打印用）。"""
    lines = []
    tot_f = tot_b = 0
    for g in GROUP_ORDER:
        s = stats.get(g) or {"files": 0, "bytes": 0}
        tot_f += s["files"]
        tot_b += s["bytes"]
        lines.append("    %-22s %6d 个文件  %8.2f MB" % (g, s["files"], s["bytes"] / 1048576.0))
    lines.append("    %-22s %6d 个文件  %8.2f MB" % ("合计", tot_f, tot_b / 1048576.0))
    return "\n".join(lines)
