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

⚠️ 两条「静默失效」的坑（都踩过或差点踩）
1. **dest 名形态不固定**：Windows 上可能带 `\\`、也可能带 `_internal/` 前缀。
   本模块统一 normalize 后再匹配；匹配不上就**不会**裁掉任何东西 ——
   而「没裁掉」在体积报告里看起来只像「收益比预期小」，不会报错。
   ⇒ 故 spec 里必须打印各组命中计数，且**预期非零的组命中 0 时大声失败**。
2. **`babel` 包本身不能删**：`courlan/filters.py:11` 是 `from babel import Locale`
   → 硬 import。只可删 `babel/locale-data/`（数据子目录）。
   实测（`_fetch_probe/_probe_babel_langpath.py`）：删掉后 courlan 判语言的 20 项行为
   与基线**逐项完全一致**（`Locale.parse` 的 `.language` 不读 locale-data）。

⚠️⚠️ 2026-09-22：AI 浏览器下线的连带改动 —— 5 组规则缩到 1 组
------------------------------------------------------------
已删除的四条规则，其匹配前缀**全是 `PySide6/`**，各自的实测收益（v0.1.4 产物）：

    P1  pyside6/resources/*.debug.pak                        4 个文件 / 74.8 MB
    P2  pyside6/translations/**/*.qm                       255 个文件 / 13.4 MB
    P3  pyside6/translations/qtwebengine_locales/*.pak      51 个文件 / 42.6 MB
    P4  babel/locale-data/**                             1084 个文件 / 28.5 MB   ← **保留**
    P5  pyside6/resources/qtwebengine_devtools_resources.pak 1 个文件 / 11.1 MB

PySide6 是随「应用内 AI 浏览器」进包的（要 `QWebEngineView` 当容器），浏览器于
2026-09-22 下线、`AIStudyCompanion.spec` 也不再收集它 ⇒ **包内已无 `PySide6/` 目录**，
P1/P2/P3/P5 恒不命中。留着它们的代价不是空间而是**误导**：
spec 的 `EXPECT_NONZERO` 会每次打包打印「预期非空的分组命中 0」，把一条真告警
（依赖版本变了 / dest 形态不匹配）稀释成噪声。故整组删除。

P5 还附过一个模块级布尔开关（「要不要保留 F12 / 远程调试的逃生口」，spec 由它取值）——
随 P5 一并删除；spec 的落盘统计里也不再写该字段。
"""

import os

# --- 分组标识（报告与日志里按这个口径统计）---
G_BABEL_LOCALE_DATA = "P4-babel-locale-data"

GROUP_ORDER = [G_BABEL_LOCALE_DATA]

GROUP_DESC = {
    G_BABEL_LOCALE_DATA: "babel 的 locale-data 数据（babel 包本身保留，courlan 要 import）",
}

# 打包正常时应命中的分组（命中 0 说明依赖版本变了或 dest 形态不匹配 —— spec 会大声提示）
EXPECT_NONZERO = [G_BABEL_LOCALE_DATA]


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


def drop_reason(dest):
    """返回该 dest 应被裁掉的**分组名**；不该裁则返回 None。

    ⚠️ 任何新增规则都要同步更新 `_fetch_probe/_t_pack_trim.py` 的期望值与
    `GROUP_ORDER` / `EXPECT_NONZERO`，否则测试会与产物口径脱节。
    """
    d = normalize_dest(dest)
    if not d:
        return None

    # P4：babel/locale-data/**
    if d.startswith("babel/locale-data/"):
        return G_BABEL_LOCALE_DATA

    return None


def filter_toc(entries):
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
        g = drop_reason(dest)
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
