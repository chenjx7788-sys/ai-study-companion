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


def is_macho(p: Path) -> bool:
    """按 Mach-O magic 判定，不依赖 `file` 命令（跨平台可测）。"""
    try:
        with open(p, "rb") as f:
            return f.read(4) in MACHO_MAGICS
    except OSError:
        return False


def collect_targets(app: Path):
    """返回 (leaves, bundles)，均为 Path，且**已按由深到浅排好序**。

    leaves  = 所有 Mach-O 文件（dylib / so / 可执行 / 嵌套 .app 内的主可执行）
    bundles = 所有嵌套 .app / .framework 目录（**不含**最外层 app 自身）

    排序规则：路径层数降序（深的先签）。同层按字符串排序保证确定性。
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
            if is_macho(f):
                leaves.append(f)
    key = lambda p: (-len(p.parts), str(p))  # noqa: E731
    leaves.sort(key=key)
    bundles.sort(key=key)
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


def sign_and_verify(app: Path, identity: str, entitlements, codesign_bin: str,
                    report_path: Path, log=print):
    t0 = time.time()
    root = app.resolve()
    if not root.is_dir():
        raise SystemExit("[sign] 找不到 .app：%s" % root)

    rc, out = run(_codesign_prefix(codesign_bin) + ["--version"])
    if rc != 0:
        raise SystemExit("[sign] 调不到 codesign（%s）：%s\n"
                         "        本步骤只能在 macOS 上运行。" % (codesign_bin, out))
    log("[sign] %s" % out)

    leaves, bundles = collect_targets(root)
    order = leaves + bundles + [root]
    problems = check_self_inward(order, root)

    log("[sign] 待签对象：Mach-O 文件 %d 个 · 嵌套 bundle %d 个 · 外层 1 个 = %d"
        % (len(leaves), len(bundles), len(order)))
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

    # xattr 检查：签名信息若被写进扩展属性，zip 一传就丢 → 包整体失效
    xattr_hits = []
    if sys.platform == "darwin" and not failures:
        rc, out = run(["xattr", "-lr", str(root)])
        for line in out.splitlines():
            low = line.lower()
            if any(k in low for k in ("com.apple.cs.", "com.apple.codesign")):
                xattr_hits.append(line.strip())

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
        },
        "order_ok": not problems,
        "order_problems": problems,
        "sign_order": [str(t.relative_to(root)) if root in t.parents else "." for t in order],
        "sign_failures": failures,
        "verify_failures": verify_failures,
        "xattr_hits": xattr_hits,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log("[sign] 报告：%s" % report_path)
    if not ok:
        raise SystemExit("[sign] 签名/验证未全部通过（详见报告）—— 中止，不产出发不出去的包")
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
if "--version" in args:
    print("fake codesign 1.0"); sys.exit(0)
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

        # T4：篡改一个**嵌套**的 Mach-O，验证必须失败
        victim = app / ("Contents/Frameworks/PySide6/Qt/lib/QtWebEngineCore.framework/"
                        "Versions/A/Helpers/QtWebEngineProcess.app/Contents/MacOS/QtWebEngineProcess")
        chk("T4a 篡改目标存在", victim.is_file(), str(victim))
        if victim.is_file():
            victim.write_bytes(b"\xcf\xfa\xed\xfe" + b"\xff" * 32)
            rc, out = run(_codesign_prefix(str(fake)) + ["--verify", "--strict",
                                                         "--verbose=2", str(victim)])
            chk("T4 篡改嵌套可执行后验证必须失败", rc != 0, out[:200])

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
