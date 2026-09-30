#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打包产物「原生外壳」冒烟测试：断言原生外壳**没有静默回落**

【为什么需要它 —— 普通的 /api/health 冒烟结构上测不到外壳】

`build_macos.sh` / `build_windows.sh` 的健康冒烟都带 `ASC_BROWSER=1`：
它会让 `launcher.py` **整段跳过 pywebview 原生窗口**（直接走浏览器 fallback）
→ 那一步只证明了「后端能起来、原生库能 import」，**一个字节都没碰 pywebview**。

后果：一旦原生外壳静默回落，这种包在上述冒烟下**照样全绿**，发现不了。
（`webview/guilib.py::try_import` 是**列表回退**，回落时不抛异常、不报警，
只写一行 logger。）

【各平台的「期望后端」与回落会落到哪】

    Windows  guis = [import_winforms]                ← **只有一项**，没有并列候选
             winforms 内部再分叉：有 WebView2 运行时 → Chromium(edgechromium)；
             **没有则静默回落 mshtml(IE 引擎)**。那是本产品最危险的形态：
             前端产物是现代 ES module，IE 执行不了 → **窗口打开、白屏、零报错**
             （故 `mshtml` 在本脚本里单独判红并给出这条解释）。
             ⇒ 期望 `webview.platforms.winforms`
    macOS    guis = [import_cocoa, import_qt]         ← cocoa 优先
             ⇒ 期望 `webview.platforms.cocoa`
    Linux    guis = [import_gtk, import_qt]           （本产品未在 Linux 发布，仅兜底）
             依据：`webview/guilib.py::initialize` 的分支表（本机已核对 pywebview 6.2.1）。

⚠️ 2026-09-22（AI 浏览器下线）前，这里硬编码期望 `webview.platforms.qt` —— 那时
launcher 用 `PYWEBVIEW_GUI=qt` 把两平台都强制顶到 Qt。那次下线移除了该强制设置，
各平台的**默认首选项**才重新成为正确期望值（见 `default_expect_backend()`）。

【判据】
    不设 ASC_BROWSER → 起原生窗口 → 读 `data_dir/launcher_backend.log`
    → 断言其中**最后一个** `backend=<期望后端>`。
这条信号是阶段 1 的 WP0 专门做出来的（`_report_webview_backend()` 读
`webview.guilib.__name__` 写盘），所以成本几乎为零。

【诚实边界】
本脚本证明的是「**原生外壳后端模块**符合本平台预期」。
它**不**证明页面渲染成功 —— 渲染进程若起不来（沙箱注入、显卡/权限问题），
`guilib` 的名字照旧。逐页渲染验证属 `_fetch_probe/verify_stage1_page_equiv.py` 的职责。

【用法】
    python3 smoke_native_shell.py --app-bin <可执行文件>   # 期望后端按平台自动取
    python3 smoke_native_shell.py --app-bin <exe> --expect-backend webview.platforms.cocoa
    python3 smoke_native_shell.py --self-test             # 任意平台可跑，验本脚本自身逻辑
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

LOG_NAME = "launcher_backend.log"
# 只认「backend=<值>」这一处，避免日志里别处的字样被误当判据
_BACKEND_RE = re.compile(r"backend=([A-Za-z0-9_.]+)")


def parse_backend(text: str):
    """从 launcher_backend.log 内容里取**最后一个** backend 值（None = 还没写）。"""
    hits = _BACKEND_RE.findall(text or "")
    return hits[-1] if hits else None


def default_expect_backend() -> str:
    """按当前平台取「期望后端」。

    ⚠️ 2026-09-22 前这里（调用方）硬编码 `webview.platforms.qt` —— 那时 launcher 用
    `PYWEBVIEW_GUI=qt` 把两平台都强制顶到 Qt。AI 浏览器下线后不再强制，
    **各平台自己的默认首选项**才是正确期望值（依据见模块 docstring 的 guilib 分支表）。
    """
    if sys.platform == "darwin":
        return "webview.platforms.cocoa"
    if os.name == "nt":
        return "webview.platforms.winforms"
    return "webview.platforms.gtk"


def isolate_env(data_dir: Path, port: int, base_env=None):
    """构造隔离环境变量。

    ⚠️ 必须**四个一起设**：`config.py` 里 `files_dir` / `db_url` / `chroma_dir` 的默认值
    是在**类定义时**用 `BASE_DIR/data` 算好的，**不是**从 `data_dir` 派生
    → 只设 ASC_DATA_DIR 会让材料文件仍落进用户目录。
    """
    env = dict(base_env if base_env is not None else os.environ)
    env.pop("ASC_BROWSER", None)          # 关键：不设 → 走原生窗口
    env["ASC_DATA_DIR"] = str(data_dir)
    env["ASC_FILES_DIR"] = str(data_dir / "files")
    env["ASC_DB_URL"] = "sqlite:///%s" % (data_dir / "app.db").as_posix()
    env["ASC_CHROMA_DIR"] = str(data_dir / "chroma")
    env["ASC_PORT"] = str(port)
    return env


def kill_tree(proc):
    """只杀**本次自己启动**的进程树。

    ⚠️ 绝不按端口反查 PID —— 本机 8000/5173 上可能跑着用户自己的实例。
    """
    if proc is None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def run_smoke(app_cmd, expect_backend: str, port: int, timeout: float,
              report_path: Path, log=print, data_dir: Path | None = None):
    """app_cmd: 可执行文件 Path，**或** 完整 argv 列表。

    接受列表是为了自测：本机 `sys.executable` 路径含中文，写成 `.cmd` 包装会被
    Windows 按 OEM 代码页读成乱码（实测「系统找不到指定的路径」）→ 只能直接给 argv。
    """
    t0 = time.time()
    if isinstance(app_cmd, (str, os.PathLike)):
        cmd = [str(app_cmd)]
        app_bin = Path(app_cmd)
        if not app_bin.exists():
            raise SystemExit("[smoke] 找不到可执行文件：%s" % app_bin)
    else:
        cmd = [str(x) for x in app_cmd]
        app_bin = Path(cmd[0])

    tmp_root = Path(tempfile.mkdtemp(prefix="asc_smoke_"))
    ddir = data_dir or (tmp_root / "data")
    ddir.mkdir(parents=True, exist_ok=True)
    log_file = ddir / LOG_NAME
    stdout_file = tmp_root / "app_stdout.txt"
    env = isolate_env(ddir, port)

    log("[smoke] 可执行  = %s" % app_bin)
    log("[smoke] 数据目录 = %s（隔离）" % ddir)
    log("[smoke] 期望后端 = %s" % expect_backend)
    log("[smoke] ASC_BROWSER = %r  ← 必须为 None，否则不会起原生窗口"
        % env.get("ASC_BROWSER"))

    proc = None
    found = None
    alive_after = None
    try:
        with open(stdout_file, "wb") as fo:
            proc = subprocess.Popen(cmd, env=env, stdout=fo,
                                    stderr=subprocess.STDOUT,
                                    cwd=str(app_bin.parent))
            deadline = time.time() + timeout
            while time.time() < deadline:
                if log_file.exists():
                    found = parse_backend(log_file.read_text(encoding="utf-8", errors="replace"))
                    if found:
                        break
                if proc.poll() is not None:
                    # 进程自己退了：等一小会儿再读一次日志（可能已写）
                    time.sleep(0.5)
                    if log_file.exists():
                        found = parse_backend(log_file.read_text(encoding="utf-8",
                                                                errors="replace"))
                    break
                time.sleep(0.5)
            # 关键：**先确认拿到日志，再看进程是否还活着**
            alive_after = (proc.poll() is None)
    finally:
        kill_tree(proc)
        try:
            proc.wait(timeout=10)
        except Exception:
            pass

    app_out = ""
    if stdout_file.exists():
        app_out = stdout_file.read_text(encoding="utf-8", errors="replace")[-4000:]

    ok = (found == expect_backend)
    # 失败时的分叉诊断：是「没起窗口」还是「回落了旧后端」
    if ok:
        diag = "后端确为 %s" % found
    elif found is None:
        diag = ("日志里没有任何 backend= 记录 → 原生窗口**没走到** pywebview.start()"
                "（ASC_BROWSER 被设了？或 create_window 抛异常回落浏览器模式）")
    elif found.endswith("mshtml"):
        diag = ("后端是 %s → **这台机器缺 WebView2 运行时**，pywebview 静默用了 IE 引擎。"
                "本产品前端产物是现代 ES module，IE 执行不了 ⇒ 用户看到的是"
                "**白屏、零报错**。请确认 app/core/webview2_check.py 的预检是否真的跑过"
                "（预检结果应写进 launcher_backend.log 的 webview2= 行）"
                % found)
    else:
        diag = ("后端为 %s（期望 %s）→ 原生外壳落到了非预期后端。"
                "各平台可能值：Windows=winforms|mshtml，macOS=cocoa|qt，Linux=gtk|qt"
                % (found, expect_backend))

    report = {
        "ok": ok,
        "app_bin": str(app_bin),
        "cmd": cmd,
        "expect_backend": expect_backend,
        "actual_backend": found,
        "diagnosis": diag,
        "process_alive_after_probe": alive_after,
        "duration_s": round(time.time() - t0, 1),
        "data_dir": str(ddir),
        "app_stdout_tail": app_out,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log("[smoke] 实际后端 = %r" % found)
    log("[smoke] %s" % diag)
    log("[smoke] 报告：%s" % report_path)
    if not ok:
        log("[smoke] --- 应用输出尾部 ---\n%s" % app_out)
    # 清理隔离目录（删不掉要大声，但不影响结论）
    try:
        shutil.rmtree(tmp_root, ignore_errors=True)
    except Exception as e:
        log("[smoke] ⚠️ 清理隔离目录失败：%s" % e)
    return report


# --------------------------------------------------------------------------
# 自测：用「假可执行文件」验证判据的分辨力
# --------------------------------------------------------------------------
def run_self_test() -> int:
    """五条判据，覆盖「该绿时绿、该红时红」：

      T1 日志写 backend=<本平台期望值>   → 必须 PASS（基线）
      T2 日志写 backend=webview.platforms.qt（非期望）→ 必须 FAIL
      T3 日志里**没有** backend= 行      → 必须 FAIL（没起窗口不能被当成过）
      T4 日志里含期望串、但不在 backend= 位
         （fallback=<期望值>）            → 必须 FAIL（正则必须锚在 backend= 上，
                                            "全文搜期望串" 这种松判据会假绿）
      T5 日志写 backend=webview.platforms.mshtml → 必须 FAIL
                                        （缺 WebView2 的最危险形态：白屏无提示）

    基线守卫：T1 未过 → T2..T5 记 SKIP 而不是 PASS。
      （假进程若根本没跑起来，负向自检会**以错误的理由**全绿 —— 实测过。）

    ⚠️ 期望值不是硬编码的：假进程按 FAKE_EXPECT 环境变量写 `backend=`，
       T1 用它、T2/T4 拿它做「差一点」的对照 —— 这样本自测在任何平台上都成立，
       且顺带验证了 `default_expect_backend()` 本身。"""
    results = []
    baseline_failed = []
    expect_default = default_expect_backend()
    assert expect_default != "webview.platforms.qt", (
        "平台默认期望值不应是 qt —— 那是被 PYWEBVIEW_GUI 强制切换的后端，"
        "会掩盖「launcher 是否真的不再强制」这件事（见 default_expect_backend 注释）")

    def chk(name, cond, detail="", gated=False):
        if gated and baseline_failed:
            results.append((name, None, "基线 T1 未通过，本项不具分辨力 → SKIP"))
            return
        results.append((name, bool(cond), detail))

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        # 假可执行：一个 python 脚本，按 FAKE_MODE 往 data_dir 写日志
        fake = tmp / "fake_app.py"
        fake.write_text(
            "import os, sys, time\n"
            "from pathlib import Path\n"
            "mode = os.environ.get('FAKE_MODE', 'expect')\n"
            "exp = os.environ.get('FAKE_EXPECT', 'webview.platforms.winforms')\n"
            "d = Path(os.environ['ASC_DATA_DIR']); d.mkdir(parents=True, exist_ok=True)\n"
            "assert not os.environ.get('ASC_BROWSER'), 'ASC_BROWSER 不该被设'\n"
            "txt = {\n"
            "  'expect': '2026-01-01 00:00:00  backend=' + exp + '  want=PYWEBVIEW_GUI=<unset>\\n',\n"
            "  'other': '2026-01-01 00:00:00  backend=webview.platforms.qt  want=PYWEBVIEW_GUI=<unset>\\n',\n"
            "  'mshtml': '2026-01-01 00:00:00  backend=webview.platforms.mshtml  want=PYWEBVIEW_GUI=<unset>\\n',\n"
            "  'empty': '',\n"
            "  'decoy': '2026-01-01 00:00:00  want=PYWEBVIEW_GUI=<unset>  fallback=' + exp + '\\n',\n"
            "}[mode]\n"
            "(d / 'launcher_backend.log').write_text(txt, encoding='utf-8')\n"
            "time.sleep(30)\n",
            encoding="utf-8")
        # ⚠️ 绝不写 .cmd/.sh 包装脚本：本机解释器路径含中文，.cmd 被按 OEM 代码页读
        #    → 路径乱码 → 假进程根本没跑（实测「系统找不到指定的路径」，poll()=1）。
        #    直接给 argv 列表，跨平台行为一致。
        fake_cmd = [sys.executable, str(fake)]

        cases = [
            ("T1 后端=平台期望值 → PASS", "expect", True),
            ("T2 后端=qt（非期望）→ 必须 FAIL", "other", False),
            ("T3 无 backend= 行 → 必须 FAIL", "empty", False),
            ("T4 仅别处字样（fallback=期望值）→ 必须 FAIL", "decoy", False),
            ("T5 后端=mshtml → 必须 FAIL（缺 WebView2 的最危险形态）", "mshtml", False),
        ]
        for name, mode, want_ok in cases:
            os.environ["FAKE_MODE"] = mode
            os.environ["FAKE_EXPECT"] = expect_default
            rep = tmp / ("report_%s.json" % mode)
            try:
                r = run_smoke(fake_cmd, expect_default, 8231, timeout=12, report_path=rep,
                              log=lambda *_: None)
                got_ok = r["ok"]
                detail = "actual=%r" % r["actual_backend"]
            except SystemExit as e:
                got_ok, detail = False, str(e)
            finally:
                os.environ.pop("FAKE_MODE", None)
                os.environ.pop("FAKE_EXPECT", None)
            if mode == "expect" and got_ok != want_ok:
                baseline_failed.append(name)
            chk(name, got_ok == want_ok, "want_ok=%s got_ok=%s %s" % (want_ok, got_ok, detail),
                gated=(mode != "expect"))

    print("=" * 74)
    print("smoke_native_shell.py 自测（假可执行文件，任意平台可跑）")
    print("=" * 74)
    npass = nskip = 0
    for name, ok, detail in results:
        tag = "SKIP" if ok is None else ("PASS" if ok else "FAIL")
        print("  [%s] %s%s" % (tag, name, "" if ok else "  <- %s" % detail))
        npass += 1 if ok is True else 0
        nskip += 1 if ok is None else 0
    nfail = len(results) - npass - nskip
    print("-" * 74)
    print("合计 %d 项，通过 %d，失败 %d，跳过 %d" % (len(results), npass, nfail, nskip))
    if baseline_failed:
        print("⚠️ 基线未通过（%s）→ 负向自检已记 SKIP，本次**不能**视为验证通过"
              % ", ".join(baseline_failed))
    return 0 if (nfail == 0 and nskip == 0) else 1


def main() -> int:
    ap = argparse.ArgumentParser(
        description="打包产物原生外壳冒烟：断言后端符合平台预期（Win=winforms / macOS=cocoa）")
    ap.add_argument("--app-bin", help="打包后的可执行文件")
    ap.add_argument("--expect-backend", default=None,
                    help="留空则按当前平台自动取（Windows=winforms / macOS=cocoa）")
    ap.add_argument("--port", type=int, default=8123)
    ap.add_argument("--timeout", type=float, default=120)
    ap.add_argument("--report", default=None)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return run_self_test()
    if not args.app_bin:
        ap.error("需要 --app-bin（或用 --self-test）")
    app_bin = Path(args.app_bin)
    rep = Path(args.report) if args.report else app_bin.parent / "smoke_native_shell.json"
    expect = args.expect_backend or default_expect_backend()
    r = run_smoke(app_bin, expect, args.port, args.timeout, rep)
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
