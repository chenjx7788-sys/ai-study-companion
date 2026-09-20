#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""macOS .app 代码签名：自内向外逐层签 + 签名有效性验证（阶段 1 · WP8）

【为什么单独一个脚本，而不是在 build_macos.sh 里写几行 codesign】

1) **顺序**是这个任务的全部难点。
   .app 里的代码必须**先签最内层**（嵌套 .app / .framework / dylib / 可执行），
   再签它们的容器，最后签外层 bundle。顺序错了 codesign 会直接失败，
   或者留下一个「外层签了、内层没签」的坏包（用户那边表现为 `killed: 9`）。
   在 bash 里用 find 排出这个序既难读、又没法验证。

2) **PyInstaller 6.x 其实已经签过一遍，但它的失败是静默的**。
   `PyInstaller/building/osx.py` 在 BUNDLE 阶段调
   `osxutils.sign_binary(name, identity, entitlements, deep=True)`，
   而异常只在 `PYINSTALLER_STRICT_BUNDLE_CODESIGN_ERROR=0`（默认）时打一条 warning：
       logger.warning("Error while signing the bundle: %s", e)
       logger.warning("You will need to sign the bundle manually!")
   → 一个「签名失败但构建成功」的包会照常被打进 zip 发出去。
   本脚本把这一步变成**有证据、会失败**。

3) **本脚本的排序 / 校验逻辑可以在 Windows 上自测**（`--self-test` 用假 codesign），
   不必等 macOS 机器才能确认代码本身是对的 —— 见 `run_self_test()`。

【用法】
    python3 macos_sign.py --app dist/AIStudyCompanion.app
    python3 macos_sign.py --app dist/AIStudyCompanion.app --identity "Developer ID Application: X (TEAMID)" \
        --entitlements scripts/macos_entitlements.plist
    python3 macos_sign.py --self-test          # 任意平台可跑，验的是本脚本自己的逻辑

【关于 ad-hoc 签名的诚实边界】
    默认 identity 是 `-`（ad-hoc）。ad-hoc 签名**不能**让 Gatekeeper 放行 ——
    用户从网上下载的 zip 仍带 quarantine 属性，首次打开仍需「右键 → 打开」。
    它真正解决的是**内部一致性**：嵌套 bundle 签名完整，避免 `killed: 9`
    （macOS 对签名损坏的 QtWebEngineProcess 会直接杀掉）。
    要真正免右键，需要 Developer ID + 公证（notarization），本脚本已为其预留
    `--identity` 与 `--entitlements` 两个入口。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Mach-O magic（含 fat / 大小端各种形态）。用读前 4 字节判类型，
# 而不是 shell 调 `file` —— 这样判定逻辑跨平台、可自测。
MACHO_MAGICS = frozenset([
    b"\xfe\xed\xfa\xce",  # MH_MAGIC     32-bit
    b"\xce\xfa\xed\xfe",  # MH_CIGAM
    b"\xfe\xed\xfa\xcf",  # MH_MAGIC_64
    b"\xcf\xfa\xed\xfe",  # MH_CIGAM_64
    b"\xca\xfe\xba\xbe",  # FAT_MAGIC
    b"\xbe\xba\xfe\xca",  # FAT_CIGAM
    b"\xca\xfe\xba\xbf",  # FAT_MAGIC_64
    b"\xbf\xba\xfe\xca",  # FAT_CIGAM_64
])

BUNDLE_SUFFIXES = (".app", ".framework")

# ⚠️ fat 容器 magic（32/64 位、两种字节序）—— **与 fat 静态库共用同一个 magic**，
#    所以只读前 4 字节**必然**把 `*.a` 误判成 Mach-O。详见 is_macho()。
#    值 = (字节序, fat_arch 条目大小, offset 字段大小)
_FAT_MAGICS = {
    b"\xca\xfe\xba\xbe": ("big", 20, 4),     # FAT_MAGIC     · fat_arch
    b"\xbe\xba\xfe\xca": ("little", 20, 4),  # FAT_CIGAM
    b"\xca\xfe\xba\xbf": ("big", 32, 8),     # FAT_MAGIC_64  · fat_arch_64
    b"\xbf\xba\xfe\xca": ("little", 32, 8),  # FAT_CIGAM_64
}
# ar(1) 归档 magic —— 静态库/目标文件的真身
_AR_MAGIC = b"!<arch>\n"

# 「签名信息被写进扩展属性」的关键字（全小写后做子串比较）。
# 这套 xattr 是 Apple **旧式**签名方案，codesign 只在「无法内嵌签名」的文件上才会用它。
XATTR_SIGN_KEYS = ("com.apple.cs.", "com.apple.codesign")

# 最小自测树：够覆盖「嵌套 .app / .framework / dylib / 可执行」四种形态与三层深度
_SELFTEST_LAYOUT = [
    # (相对路径, 内容头 4 字节)
    ("Contents/MacOS/AIStudyCompanion", b"\xcf\xfa\xed\xfe"),
    ("Contents/Frameworks/libpython3.11.dylib", b"\xca\xfe\xba\xbe"),
    ("Contents/Frameworks/PySide6/Qt/lib/QtCore.framework/Versions/A/QtCore",
     b"\xcf\xfa\xed\xfe"),
    ("Contents/Frameworks/PySide6/Qt/lib/QtCore.framework/Versions/A/Resources/Info.plist",
     b"<?xm"),
    ("Contents/Frameworks/PySide6/Qt/lib/QtWebEngineCore.framework/Versions/A/Helpers/"
     "QtWebEngineProcess.app/Contents/MacOS/QtWebEngineProcess", b"\xcf\xfa\xed\xfe"),
    ("Contents/Resources/dist/index.html", b"<!DO"),
]


def _fat_slices_are_archives(f) -> bool:
    """f 是**已打开、定位在 0** 的 fat 容器；返回「它的每个切片都是 ar 归档」。

    ⚠️⚠️ 这一条是 2026-09-20 run #12 的根因所在。
    PySide6 的 macOS wheel 里带一个 **fat 静态库**：

        PySide6/Qt/qml/Qt/labs/assetdownloader/libqmlassetdownloaderprivateplugin.a

    它的文件头是 **FAT_MAGIC（0xcafebabe）—— 和 fat Mach-O 完全同一个 magic**，
    所以「只读前 4 字节」的判定必然把它当成 Mach-O 去签名。而 codesign 对归档
    **无法内嵌签名**，只能退回写 **xattr 型旧式签名**
    （`com.apple.cs.CodeDirectory` / `CodeRequirements` / `CodeSignature`）——
    恰好撞上本脚本自己的 xattr 检查（那条检查的本意是「签名若只存在于 xattr，
    zip 一传就丢」）。于是脚本**自己制造了问题，再把它判成致命**，两条 job 全红。

    实测证据（`diag-arm64` / `diag-x86_64` 的 `sign_report.json`）：
    `xattr_hits = 4`，且 4 条**全落在同一个 `.a` 上**，两架构完全一致；
    同一次运行 `sign_failed = 0`、`verify_failed = 0`、`order_problems = []`。

    判法：解析 fat_header 的 nfat_arch，逐个 fat_arch 取 `offset`，看该偏移处
    8 字节是不是 `!<arch>\\n`。**全部**切片都是归档才判定为归档。
    切片里只要有任何一个像 Mach-O，就仍然按 Mach-O 签名（**失败安全**：
    宁可不排除，也不要漏签真的代码）。
    """
    f.seek(0)
    magic = f.read(4)
    spec = _FAT_MAGICS.get(magic)
    if not spec:
        return False
    endian, ent_size, off_size = spec
    n = int.from_bytes(f.read(4), endian)
    if n <= 0 or n > 64:
        return False
    for i in range(n):
        f.seek(8 + i * ent_size + 8)          # cputype(4) + cpusubtype(4) 之后是 offset
        off = int.from_bytes(f.read(off_size), endian)
        if off < 8:
            return False
        f.seek(off)
        if f.read(8) != _AR_MAGIC:
            return False
    return True


def _classify_macho(p: Path) -> str:
    """返回 'macho' | 'fat_archive' | 'no'。

    只开一次文件就把「是不是可签名代码」和「为什么不是」都判出来，
    供 collect_targets 一遍走完（避免为统计再开一遍全部文件）。
    """
    try:
        with open(p, "rb") as f:
            magic = f.read(4)
            if magic not in MACHO_MAGICS:
                return "no"
            if magic in _FAT_MAGICS:
                return "fat_archive" if _fat_slices_are_archives(f) else "macho"
            return "macho"                     # thin Mach-O
    except OSError:
        return "no"


def is_macho(p: Path) -> bool:
    """按 Mach-O magic 判定，不依赖 `file` 命令（跨平台可测）。

    fat 容器要**再往里看一眼**：fat Mach-O 与 fat 静态库共用 FAT_MAGIC，
    只有确认「所有切片都是 ar 归档」才排除（见 `_fat_slices_are_archives`）。
    thin 归档（直接以 `!<arch>\\n` 开头）本就不在 MACHO_MAGICS 里，无此问题。
    """
    return _classify_macho(p) == "macho"


def collect_targets(app: Path, skipped: list | None = None):
    """返回 (leaves, bundles)，均为 Path，且**已按由深到浅排好序**。

    leaves  = 所有 Mach-O 文件（dylib / so / 可执行 / 嵌套 .app 内的主可执行）
    bundles = 所有嵌套 .app / .framework 目录（**不含**最外层 app 自身）

    排序规则：路径层数降序（深的先签）。同层按字符串排序保证确定性。

    传入 `skipped`（list）时，会把「因是 fat 静态库而被排除」的路径记进去 ——
    用于写进报告并在日志里明示「本轮没签哪些、为什么」（run #12 的教训：
    一个不做记录的隐式排除，会让人在下一次红的时候无从下手）。
    """
    root = app.resolve()
    leaves, bundles = [], []
    for dirpath, dirnames, filenames in os.walk(root):
        d = Path(dirpath)
        for name in dirnames:
            if name.endswith(BUNDLE_SUFFIXES):
                bundles.append(d / name)
        for name in filenames:
            f = d / name
            kind = _classify_macho(f)
            if kind == "macho":
                leaves.append(f)
            elif kind == "fat_archive" and skipped is not None:
                skipped.append(f)
    key = lambda p: (-len(p.parts), str(p))  # noqa: E731
    leaves.sort(key=key)
    bundles.sort(key=key)
    if skipped is not None:
        skipped.sort(key=str)
    return leaves, bundles


def check_self_inward(order, root: Path) -> list:
    """校验签名顺序确实是「自内向外」：任何对象都不得早于它内部的签名对象。

    `order` 是完整的签名序列（含最后的外层 app）。返回违规描述列表（空 = 合规）。
    这条判据是纯函数，可在 Windows 上单测 —— 是本脚本最该被验的部分。
    """
    problems = []
    seen = set()
    for idx, item in enumerate(order):
        item = Path(item)
        # 找出「应该排在我前面」的内部对象：所有路径以我为父的项
        for other_idx, other in enumerate(order):
            other = Path(other)
            if other == item:
                continue
            if item in other.parents and other_idx > idx:
                problems.append(
                    "顺序违规：%s 在第 %d 位，但它在 %s 内部（应在第 %d 位之前）"
                    % (item.name, idx, item.name, other_idx)
                )
        seen.add(item)
    if not order or Path(order[-1]).resolve() != root.resolve():
        problems.append("顺序违规：外层 bundle 必须**最后**签（实际最后是 %s）"
                        % (Path(order[-1]).name if order else "<空>"))
    return problems


def classify_xattr_hits(lines, signed_paths) -> tuple[list, list]:
    """把 `xattr -lr` 的输出行分成 (致命, 良性)。**纯函数，任意平台可测。**

    - **致命**：命中落在**我们自己签过的对象**上（`signed_paths` 里）。
      那种签名确实**只存在于 xattr**，而 `zip -y` 不保存 xattr → 用户解压出来签名就没了。
      这正是这条检查当初要防的事，必须继续判死。
    - **良性**：命中落在签名序列之外。典型案例是上游 Qt wheel 自带的 `*.a` 静态库
      遗留的旧式 xattr 签名（它不是运行期可加载的代码，丢了不影响包的有效性）。
      只作提示，不阻断构建。

    ⚠️ 2026-09-20 run #12 的教训：这条检查**原来一行日志都不打**，
    `ok=False` 时只说「详见报告」——在 CI 里等于「红了但不说什么原因」，
    定位只能靠读 JSON 排除法。所以两类的命中项都必须完整打出来。
    """
    fatal, benign = [], []
    for line in lines:
        low = line.lower()
        if not any(k in low for k in XATTR_SIGN_KEYS):
            continue
        # `xattr -l` 的行格式是 `<路径>: <属性名>: <值>`；路径几乎不含冒号，
        # 故取第一个冒号之前即为路径（值与属性名里都可能还有冒号）。
        path = line.split(":", 1)[0].strip()
        (fatal if path in signed_paths else benign).append(line.strip())
    return fatal, benign


def _codesign_prefix(bin_path: str):
    """codesign 命令前缀。

    macOS 上就是 ['/usr/bin/codesign']。自测时传的是一个 .py 假实现，
    Windows 不能直接 exec 脚本文件（WinError 193），故显式用当前解释器拉起
    —— 这样同一套代码在任意平台都能自测。
    """
    if bin_path.endswith(".py"):
        return [sys.executable, bin_path]
    return [bin_path]


def codesign_cmd(bin_path: str, identity: str, entitlements, target: Path, verify: bool):
    """拼 codesign 命令。

    ⚠️ 不用 `--deep`：Apple 已弃用，且对 Qt 的嵌套 .app / .framework 会漏签。
       本脚本改为自己按「自内向外」显式逐层签 —— 顺序由 check_self_inward 兜底。
    """
    if verify:
        return _codesign_prefix(bin_path) + ["--verify", "--strict", "--verbose=2", str(target)]
    cmd = _codesign_prefix(bin_path) + ["--force", "--all-architectures"]
    if identity and identity != "-":
        # 真身份：启用 hardened runtime + 时间戳（公证要求）
        cmd += ["--timestamp", "--options=runtime"]
    cmd += ["--sign", identity or "-"]
    if entitlements:
        cmd += ["--entitlements", str(entitlements)]
    cmd.append(str(target))
    return cmd


def run(cmd, timeout=600):
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return p.returncode, (p.stdout or "").strip()


def codesign_available(codesign_bin: str):
    """判定 codesign 是否可用。返回 (可用?, 说明)。**不调用任何命令行选项。**

    ⚠️⚠️ 绝不要用 `codesign --version` 当探针。
    macOS 自带的 codesign **没有 `--version` 选项**（只有 -s / -v / -d / -h /
    --validate-constraint），调它只会打印 usage 并返回**非零** ——
    于是「探针失败」被读成「这里不是 macOS」，**在真正的 macOS 上也会误判**。

    2026-09-20 的 macOS 跑批就是这样挂的（run #10）：arm64 与 x86_64 **两条 job**
    都一路走过 [1/6] 模型下载 → [4/6] PyInstaller（产物已生成）→ [5/7] 后端健康冒烟
    → [5b/7] 原生外壳冒烟（`actual_backend = webview.platforms.qt`，R4 被证伪），
    唯独 [5c/7] 被这一行判成「本步骤只能在 macOS 上运行」而整步中止。

    它之所以躲过了 `--self-test`：自测用的假 codesign **自己实现了 `--version`**
    （旧 `_FAKE_CODESIGN` 的 `if "--version" in args: ... sys.exit(0)`）——
    测试替身把被测代码的错误假设**一起复制了**，所以这个 bug 在自测里结构性地
    不可能被发现。现在假实现改成**和真身一样拒绝 `--version`**，并由自测 T5/T5b
    做「同一个二进制、老探针判否 / 新探针判是」的分辨力证明。

    改用「存在且可执行」判定：对真 codesign（/usr/bin/codesign 恒存在）与自测用的
    .py 假实现都成立，且不依赖任何选项语义。
    """
    if Path(codesign_bin).is_absolute() or "/" in codesign_bin or "\\" in codesign_bin:
        p = Path(codesign_bin)
        if not p.exists():
            return False, "路径不存在：%s" % codesign_bin
        if not os.access(p, os.X_OK):
            return False, "文件不可执行（缺 +x）：%s" % codesign_bin
        return True, str(p)
    found = shutil.which(codesign_bin)
    if not found:
        return False, "PATH 里找不到 %s" % codesign_bin
    return True, found


def sign_and_verify(app: Path, identity: str, entitlements, codesign_bin: str,
                    report_path: Path, log=print):
    t0 = time.time()
    root = app.resolve()
    if not root.is_dir():
        raise SystemExit("[sign] 找不到 .app：%s" % root)

    ok_bin, bin_detail = codesign_available(codesign_bin)
    if not ok_bin:
        raise SystemExit("[sign] 调不到 codesign（%s）：%s\n"
                         "        本步骤只能在装有 Xcode Command Line Tools 的 macOS 上运行。"
                         % (codesign_bin, bin_detail))
    log("[sign] codesign = %s" % bin_detail)

    skipped_archives: list = []
    leaves, bundles = collect_targets(root, skipped=skipped_archives)
    order = leaves + bundles + [root]
    problems = check_self_inward(order, root)

    log("[sign] 待签对象：Mach-O 文件 %d 个 · 嵌套 bundle %d 个 · 外层 1 个 = %d"
        % (len(leaves), len(bundles), len(order)))
    if skipped_archives:
        # ⚠️ 必须明示：隐式排除会让人在下次红的时候无从下手（run #12 的教训）
        log("[sign] 已排除 fat 静态库 %d 个（ar 归档不可内嵌签名，签它只会写成 xattr → zip 丢）:"
            % len(skipped_archives))
        for f in skipped_archives[:10]:
            log("[sign]   - %s" % f.relative_to(root))
    log("[sign] identity=%s%s" % (identity, "（ad-hoc）" if identity in ("", "-") else ""))
    if problems:
        for p in problems:
            log("[sign] ✗ %s" % p)
        raise SystemExit("[sign] 签名顺序自检未通过，已中止（不产出一个「外层签了、内层没签」的包）")

    failures = []
    for idx, target in enumerate(order):
        cmd = codesign_cmd(codesign_bin, identity, entitlements, target, verify=False)
        rc, out = run(cmd)
        if rc != 0:
            failures.append({"stage": "sign", "target": str(target), "rc": rc, "out": out[:2000]})
            log("[sign] ✗ 签名失败 %s\n      %s" % (target.relative_to(root), out[:400]))
    signed_ok = len(order) - len(failures)
    log("[sign] 签名完成：成功 %d / 失败 %d" % (signed_ok, len(failures)))

    # 逐项验证（含外层 bundle）。注意这里**逐项**验，不用 --deep ——
    # --deep 的验证会被外层签名「代表」内层，正是我们要避免的盲区。
    verify_failures = []
    if not failures:
        for target in order:
            cmd = codesign_cmd(codesign_bin, identity, entitlements, target, verify=True)
            rc, out = run(cmd)
            if rc != 0:
                verify_failures.append({"stage": "verify", "target": str(target),
                                        "rc": rc, "out": out[:2000]})
                log("[sign] ✗ 验证失败 %s\n      %s" % (target.relative_to(root), out[:400]))
    else:
        log("[sign] 有签名失败，跳过验证（先修签名）")

    # xattr 检查：签名信息若**只**被写进扩展属性，zip 一传就丢 → 包整体失效。
    # 关键：只有落在我们签过的对象上的命中才致命；其余只提示（见 classify_xattr_hits）。
    # 两类的命中项**都要打日志** —— 这道门以前完全静默，run #12 因此只能靠排除法定位。
    xattr_hits, xattr_benign = [], []
    if sys.platform == "darwin" and not failures:
        signed_paths = {str(t) for t in order}
        rc, out = run(["xattr", "-lr", str(root)])
        xattr_hits, xattr_benign = classify_xattr_hits(out.splitlines(), signed_paths)
        log("[sign] xattr 检查：致命 %d 项 · 良性 %d 项（xattr rc=%d）"
            % (len(xattr_hits), len(xattr_benign), rc))
        for line in xattr_hits[:10]:
            log("[sign] ✗ 已签对象上存在 xattr 型签名（zip 会丢）%s" % line)
        for line in xattr_benign[:10]:
            log("[sign] · 非签名对象上的 xattr 签名（不影响包有效性）%s" % line)

    ok = not failures and not verify_failures and not xattr_hits and not problems
    report = {
        "ok": ok,
        "app": str(root),
        "identity": identity,
        "duration_s": round(time.time() - t0, 1),
        "counts": {
            "leaves": len(leaves), "bundles": len(bundles), "total": len(order),
            "signed_ok": signed_ok, "sign_failed": len(failures),
            "verify_failed": len(verify_failures), "xattr_hits": len(xattr_hits),
            "xattr_benign": len(xattr_benign),
            "skipped_fat_archives": len(skipped_archives),
        },
        "order_ok": not problems,
        "order_problems": problems,
        "sign_order": [str(t.relative_to(root)) if root in t.parents else "." for t in order],
        "skipped_fat_archives": [str(f.relative_to(root)) for f in skipped_archives],
        "sign_failures": failures,
        "verify_failures": verify_failures,
        "xattr_hits": xattr_hits,
        "xattr_benign": xattr_benign,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log("[sign] 报告：%s" % report_path)
    if not ok:
        # ⚠️ 失败信息必须**分项**说明是哪道门 —— 原来只说「详见报告」，
        #    在 CI 里等于「红了但不说什么原因」（run #12 只能靠排除法定位）。
        raise SystemExit(
            "[sign] 签名/验证未全部通过 —— 中止，不产出发不出去的包\n"
            "        分项：sign_failed=%d  verify_failed=%d  xattr_fatal=%d  "
            "order_problems=%d\n"
            "        报告：%s"
            % (len(failures), len(verify_failures), len(xattr_hits),
               len(problems), report_path))
    log("[sign] ✅ 全部通过：%d 项已签且验证有效（含嵌套 QtWebEngineProcess.app）" % len(order))
    return report


# --------------------------------------------------------------------------
# 自测：用「假 codesign」在任意平台验证本脚本的排序与校验逻辑
# --------------------------------------------------------------------------
_FAKE_CODESIGN = r'''#!/usr/bin/env python3
"""假 codesign：把「签名」实现成写一个 <target>.fakesig（内含内容指纹），
「验证」则重算指纹比对。这样篡改文件后验证必然失败 —— 足以证明
macos_sign.py 的验证逻辑真的能发现问题（而不是恒真）。"""
import hashlib, sys, os
from pathlib import Path

MAGICS = [bytes.fromhex(h) for h in
          ("feedface", "cefaedfe", "feedfacf", "cffaedfe",
           "cafebabe", "bebafeca", "cafebabf", "bfbafeca")]

def is_macho(p):
    try:
        with open(p, "rb") as f:
            return f.read(4) in MAGICS
    except OSError:
        return False

def digest(p):
    p = Path(p)
    if p.is_file():
        return hashlib.sha256(p.read_bytes()).hexdigest()
    parts = []
    for dp, dn, fn in os.walk(p):
        for n in sorted(fn):
            f = Path(dp) / n
            if n.endswith(".fakesig"):
                continue
            parts.append(n + ":" + hashlib.sha256(f.read_bytes()).hexdigest())
    return hashlib.sha256("|".join(sorted(parts)).encode()).hexdigest()

args = sys.argv[1:]
# ⚠️ 与真身保持一致：macOS 的 codesign **拒绝** `--version`（打印 usage、返回非零）。
#    这里**故意不实现**它 —— 旧版本实现了，结果把「拿 --version 当可用性探针」
#    这个错误假设一起复制进了测试替身，导致真机上的误判在自测里永远看不见。
#    改掉之后，谁再把 --version 探针写回去，自测会当场红灯（T3 / T5）。
if "--version" in args:
    print("codesign: unrecognized option --version")
    print("Usage: codesign -s identity [-fv*] [-o flags] [-r reqs] [-i ident] path ...")
    sys.exit(1)
verify = "--verify" in args
target = args[-1]
if verify:
    sig = Path(str(target) + ".fakesig")
    if not sig.exists():
        print("%s: code object is not signed at all" % target); sys.exit(1)
    if sig.read_text() != digest(target):
        print("%s: invalid signature (resource envelope mismatch)" % target); sys.exit(1)
    sys.exit(0)
Path(str(target) + ".fakesig").write_text(digest(target))
sys.exit(0)
'''


def run_self_test() -> int:
    """在临时目录里造一棵假 .app，跑「签名 → 验证 → 篡改 → 必须验证失败」。

    四条判据（全部必须为真，否则本脚本的逻辑不可信）：
      T1 排序自检：完整顺序是严格自内向外的，外层 bundle 在最后
      T2 顺序**反了必须被抓到**（把 root 提到最前，check_self_inward 必须报违规）
      T3 正常签完后，逐项验证全部通过
      T4 篡改一个**嵌套**文件后，验证必须失败（否则说明验证是恒真的）
      T5 探针不与替身共谋：同一个**拒绝 --version** 的 codesign，新探针判「可用」
      T5b（负向自检）同一码上老探针（--version）判「不可用」—— 结论必须相反
      T6/T6b/T6c xattr 命中分类（用 run #12 的真实 4 行为合成数据 + 噪声对照 + 分辨力）
      T7a..e fat 容器：fat Mach-O 仍签、**fat 静态库必须排除**、旧判据会误判（分辨力）
      T7f..h 集成：树里带着 fat 静态库时，排除被记录、且整轮 ok=True（不再自伤）
    """
    results = []

    def chk(name, cond, detail=""):
        results.append((name, bool(cond), detail))

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        fake = tmp / "fake_codesign.py"
        fake.write_text(_FAKE_CODESIGN, encoding="utf-8")
        os.chmod(fake, 0o755)

        app = tmp / "AIStudyCompanion.app"
        for rel, magic in _SELFTEST_LAYOUT:
            f = app / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(magic + b"\x00" * 32)

        root = app.resolve()
        leaves, bundles = collect_targets(root)
        order = leaves + bundles + [root]

        # T1
        probs = check_self_inward(order, root)
        chk("T1 顺序为自内向外且外层最后", not probs, str(probs))
        # 形态覆盖：必须真的发现了嵌套 .app 与 .framework，否则这棵树太浅、测不出东西
        chk("T1b 树上确实有嵌套 .app 与 .framework",
            any(b.name.endswith(".app") for b in bundles)
            and any(b.name.endswith(".framework") for b in bundles),
            "bundles=%s" % [b.name for b in bundles])

        # T2 分辨力：把 root 提到最前，必须报违规
        bad = [root] + [t for t in order if t != root]
        chk("T2 顺序反了能被抓到", bool(check_self_inward(bad, root)),
            str(check_self_inward(bad, root)))

        # T3 / T4
        rep = tmp / "report.json"
        try:
            r = sign_and_verify(app, "-", None, str(fake), rep, log=lambda *_: None)
            chk("T3 签名 + 逐项验证全部通过", r["ok"] and r["counts"]["verify_failed"] == 0,
                json.dumps(r["counts"], ensure_ascii=False))
        except SystemExit as e:
            chk("T3 签名 + 逐项验证全部通过", False, str(e))

        # T5 / T5b 分辨力证明：找**同一个**二进制练两种探针，结论必须相反。
        #   假的 codesign 现在和真身一样拒绝 --version，所以：
        #     老探针 → 判「不可用」（这正是真机上发生过的误判）；新探针 → 判「可用」。
        #   两者结论相反 = T3 的通过不是因为替身迁就了探针。
        rc_v, out_v = run(_codesign_prefix(str(fake)) + ["--version"])
        avail, avail_detail = codesign_available(str(fake))
        chk("T5 新探针认可这个 codesign（不依赖 --version）", avail, avail_detail)
        chk("T5b 负向自检：老探针（--version）在同一码上判「不可用」",
            rc_v != 0, "rc=%s out=%s" % (rc_v, out_v[:120]))

        # T4：篡改一个**嵌套**的 Mach-O，验证必须失败
        victim = app / ("Contents/Frameworks/PySide6/Qt/lib/QtWebEngineCore.framework/"
                        "Versions/A/Helpers/QtWebEngineProcess.app/Contents/MacOS/QtWebEngineProcess")
        chk("T4a 篡改目标存在", victim.is_file(), str(victim))
        if victim.is_file():
            victim.write_bytes(b"\xcf\xfa\xed\xfe" + b"\xff" * 32)
            rc, out = run(_codesign_prefix(str(fake)) + ["--verify", "--strict",
                                                         "--verbose=2", str(victim)])
            chk("T4 篡改嵌套可执行后验证必须失败", rc != 0, out[:200])

        # ---- T7 fat 容器：与 fat 静态库共用 magic，只能靠**切片内容**区分 ----
        def _fat(entry_head: bytes) -> bytes:
            """造一个 fat 容器：fat_header(8) + 1×fat_arch(20) + 切片数据。"""
            head = bytes.fromhex("cafebabe") + (1).to_bytes(4, "big")
            head += (0x0100000C).to_bytes(4, "big") + (0).to_bytes(4, "big")
            head += (28).to_bytes(4, "big") + (4096).to_bytes(4, "big")
            head += (12).to_bytes(4, "big")
            return head + entry_head + b"\x00" * 64

        p_fat_macho = tmp / "fat_macho.bin"
        p_fat_macho.write_bytes(_fat(b"\xcf\xfa\xed\xfe"))
        p_fat_ar = tmp / "fat_archive.a"
        p_fat_ar.write_bytes(_fat(_AR_MAGIC))
        p_thin_ar = tmp / "thin_archive.a"
        p_thin_ar.write_bytes(_AR_MAGIC + b"\x00" * 64)
        p_thin_macho = tmp / "thin_macho.bin"
        p_thin_macho.write_bytes(b"\xcf\xfa\xed\xfe" + b"\x00" * 64)

        chk("T7a thin Mach-O 仍判为可签名", is_macho(p_thin_macho))
        chk("T7b fat Mach-O 仍判为可签名", is_macho(p_fat_macho))
        chk("T7c fat 静态库判为**不可**签名（run #12 的根因）", not is_macho(p_fat_ar))
        chk("T7d thin 静态库判为不可签名", not is_macho(p_thin_ar))
        chk("T7e 分辨力：旧判据（只看前 4 字节）在同一 fat 归档上会判 True",
            p_fat_ar.read_bytes()[:4] in MACHO_MAGICS,
            "前4字节=%r" % p_fat_ar.read_bytes()[:4])

        # T7f/T7g/T7h 集成：把 fat 静态库放进假 .app 里（复现 run #12 的现场）
        nested_ar = app / "Contents/Resources/libqmlassetdownloaderprivateplugin.a"
        nested_ar.parent.mkdir(parents=True, exist_ok=True)
        nested_ar.write_bytes(_fat(_AR_MAGIC))
        _sk = []
        _lv, _bd = collect_targets(root, skipped=_sk)
        chk("T7f 树里的 fat 静态库被排除**且被记录**", nested_ar in _sk,
            "skipped=%s" % [str(x) for x in _sk])
        chk("T7g 被排除的对象不在 leaves 里", nested_ar not in _lv,
            "leaves=%d" % len(_lv))
        rep2 = tmp / "report2.json"
        try:
            r2 = sign_and_verify(app, "-", None, str(fake), rep2, log=lambda *_: None)
            chk("T7h 树里带着 fat 静态库时，整轮仍然 ok=True（不再自伤）",
                bool(r2["ok"]) and r2["counts"]["skipped_fat_archives"] == 1,
                json.dumps(r2["counts"], ensure_ascii=False))
        except SystemExit as e:
            chk("T7h 树里带着 fat 静态库时，整轮仍然 ok=True（不再自伤）", False, str(e))

        # ---- T6 xattr 分类：直接用 run #12 命中的那 4 行真实现场当合成数据 ----
        xa_path = ("/tmp/app/AIStudyCompanion.app/Contents/Resources/PySide6/Qt/qml/"
                   "Qt/labs/assetdownloader/libqmlassetdownloaderprivateplugin.a")
        xa_lines = [xa_path + ": com.apple.cs." + n + ": \x00\x01"
                    for n in ("CodeDirectory", "CodeRequirements",
                              "CodeRequirements-1", "CodeSignature")]
        # 噪声对照：不含关键字的 xattr 必须被忽略，否则分类会把整份输出都算进来
        noise = ["/tmp/app/foo: com.apple.quarantine: 0081",
                 "/tmp/app/bar: com.apple.provenance: ",
                 "/tmp/app/baz: user.custom: 1"]
        f1, b1 = classify_xattr_hits(xa_lines + noise, set())
        chk("T6 非签名对象上的 xattr 签名 → 良性，不阻断", (not f1) and len(b1) == 4,
            "fatal=%d benign=%d" % (len(f1), len(b1)))
        f2, b2 = classify_xattr_hits(xa_lines + noise, {xa_path})
        chk("T6b 分辨力：同一批行放进「已签」集合 → 全部转致命（结论相反）",
            len(f2) == 4 and not b2, "fatal=%d benign=%d" % (len(f2), len(b2)))
        # 噪声对照：不含关键字的 xattr 行**不得**出现在任何一类结果里。
        # ⚠️ 注意别写成「b1 里的行不含关键字」—— b1 装的就是含关键字的行，那样是恒假。
        _hay = "\n".join(b1 + f2 + b2)
        _noise_paths = ("/tmp/app/foo", "/tmp/app/bar", "/tmp/app/baz")
        chk("T6c 不含关键字的 xattr 被忽略（quarantine / provenance / 自定义都不算）",
            (not any(p in _hay for p in _noise_paths)) and len(b1) == 4,
            "混进=%r  b1=%d" % ([p for p in _noise_paths if p in _hay], len(b1)))

    print("=" * 74)
    print("macos_sign.py 自测（假 codesign，任意平台可跑）")
    print("=" * 74)
    npass = 0
    for name, ok, detail in results:
        print("  [%s] %s%s" % ("PASS" if ok else "FAIL", name,
                               "" if ok else "  <- %s" % detail))
        npass += 1 if ok else 0
    print("-" * 74)
    print("合计 %d 项，通过 %d，失败 %d" % (len(results), npass, len(results) - npass))
    return 0 if npass == len(results) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="macOS .app 自内向外逐层签名 + 验证")
    ap.add_argument("--app", help=".app 路径")
    ap.add_argument("--identity", default=os.environ.get("SIGN_IDENTITY", "-"),
                    help="签名身份，默认 '-'（ad-hoc）；正式分发传 Developer ID")
    ap.add_argument("--entitlements", default=os.environ.get("SIGN_ENTITLEMENTS") or None)
    ap.add_argument("--codesign-bin", default="/usr/bin/codesign")
    ap.add_argument("--report", default=None, help="JSON 证据文件路径")
    ap.add_argument("--self-test", action="store_true", help="跑本脚本自身的逻辑自测")
    args = ap.parse_args()

    if args.self_test:
        return run_self_test()
    if not args.app:
        ap.error("需要 --app（或用 --self-test）")
    app = Path(args.app)
    rep = Path(args.report) if args.report else app.parent / "sign_report.json"
    sign_and_verify(app, args.identity, args.entitlements, args.codesign_bin, rep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
