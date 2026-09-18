#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打包产物「原生外壳」冒烟测试：断言实际后端是 Qt（阶段 1 · WP8b）

【为什么需要它 —— 原来的冒烟测试结构上测不到 Qt】

`build_macos.sh` 原第 [5/6] 步是：
    ASC_BROWSER=1 ASC_PORT=... "$APP_BIN" &
    curl -sf http://127.0.0.1:$PORT/api/health
`ASC_BROWSER=1` 会让 `launcher.py` **整段跳过 pywebview 原生窗口**（直接走浏览器 fallback）
→ 这一步只证明了「后端能起来」，**一个字节都没碰 Qt 路径**。

后果：R4 的失败形态（spec 漏收 PySide6/QtWebEngine → 运行期**静默回落**旧后端）
在这个冒烟测试下**照样通过** —— 打出一个「装了 Qt 但实际没在用」的包也发现不了。
而 `guilib.py` 的后端候选是**列表回退**（Win `[qt, winforms]` / Darwin `[qt, cocoa]`），
回落时**不报错、不警告**。

【判据】
    不设 ASC_BROWSER → 起原生窗口 → 读 `data_dir/launcher_backend.log`
    → 断言其中出现 `backend=<期望后端>`。
这条信号正是阶段 1 的 WP0 专门做出来的（`_report_webview_backend()` 读
`webview.guilib.__name__` 写盘），所以成本几乎为零。

【诚实边界】
本脚本证明的是「**后端模块**确为 Qt」，即 R4 的失败形态。
它**不**证明页面渲染成功 —— 渲染进程若起不来（如 R9 的命令沙箱注入），
`guilib` 仍然是 qt。渲染验证属阶段 0 探针（`probe_macos_qtwebengine.py`）的职责。

【用法】
    python3 smoke_native_shell.py --app-bin <可执行文件> --expect-backend webview.platforms.qt
    python3 smoke_native_shell.py --self-test        # 任意平台可跑，验本脚本自身逻辑
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
    else:
        diag = ("后端回落成 %s（期望 %s）→ 典型 R4：包里缺 PySide6/QtWebEngine，"
                "pywebview 静默用了旧后端" % (found, expect_backend))

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
    """四条判据，覆盖「该绿时绿、该红时红」：

      T1 日志写 backend=webview.platforms.qt      → 必须 PASS
      T2 日志写 backend=webview.platforms.cocoa   → 必须 FAIL（回落被抓住 = R4 的分辨力）
      T3 日志里**没有** backend= 行              → 必须 FAIL（没起窗口不能被当成过）
      T4 日志里**含期望字符串、但不在 backend= 位**
         （fallback=webview.platforms.qt）        → 必须 FAIL（正则必须锚在 backend= 上，
                                                     "全文搜期望串" 这种松判据会假绿）

    基线守卫：T1 未过 → T2/T3/T4 记 SKIP 而不是 PASS。
      （假进程若根本没跑起来，三条负向自检会**以错误的理由**全绿 —— 实测过。）
    """
    results = []
    baseline_failed = []

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
            "mode = os.environ.get('FAKE_MODE', 'qt')\n"
            "d = Path(os.environ['ASC_DATA_DIR']); d.mkdir(parents=True, exist_ok=True)\n"
            "assert not os.environ.get('ASC_BROWSER'), 'ASC_BROWSER 不该被设'\n"
            "txt = {\n"
            "  'qt': '2026-01-01 00:00:00  backend=webview.platforms.qt  want=PYWEBVIEW_GUI=qt\\n',\n"
            "  'cocoa': '2026-01-01 00:00:00  backend=webview.platforms.cocoa  want=PYWEBVIEW_GUI=qt\\n',\n"
            "  'empty': '',\n"
            "  'decoy': '2026-01-01 00:00:00  want=PYWEBVIEW_GUI=qt  fallback=webview.platforms.qt\\n',\n"
            "}[mode]\n"
            "(d / 'launcher_backend.log').write_text(txt, encoding='utf-8')\n"
            "time.sleep(30)\n",
            encoding="utf-8")
        # ⚠️ 绝不写 .cmd/.sh 包装脚本：本机解释器路径含中文，.cmd 被按 OEM 代码页读
        #    → 路径乱码 → 假进程根本没跑（实测「系统找不到指定的路径」，poll()=1）。
        #    直接给 argv 列表，跨平台行为一致。
        fake_cmd = [sys.executable, str(fake)]

        cases = [
            ("T1 后端=qt → PASS", "qt", "webview.platforms.qt", True),
            ("T2 回落 cocoa → 必须 FAIL", "cocoa", "webview.platforms.qt", False),
            ("T3 无 backend= 行 → 必须 FAIL", "empty", "webview.platforms.qt", False),
            ("T4 仅别处字样 → 必须 FAIL", "decoy", "webview.platforms.qt", False),
        ]
        for name, mode, expect, want_ok in cases:
            os.environ["FAKE_MODE"] = mode
            rep = tmp / ("report_%s.json" % mode)
            try:
                r = run_smoke(fake_cmd, expect, 8231, timeout=12, report_path=rep,
                              log=lambda *_: None)
                got_ok = r["ok"]
                detail = "actual=%r" % r["actual_backend"]
            except SystemExit as e:
                got_ok, detail = False, str(e)
            finally:
                os.environ.pop("FAKE_MODE", None)
            if mode == "qt" and got_ok != want_ok:
                baseline_failed.append(name)
            chk(name, got_ok == want_ok, "want_ok=%s got_ok=%s %s" % (want_ok, got_ok, detail),
                gated=(mode != "qt"))

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
    ap = argparse.ArgumentParser(description="打包产物原生外壳冒烟：断言实际后端为 Qt")
    ap.add_argument("--app-bin", help="打包后的可执行文件")
    ap.add_argument("--expect-backend", default="webview.platforms.qt")
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
    r = run_smoke(app_bin, args.expect_backend, args.port, args.timeout, rep)
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
