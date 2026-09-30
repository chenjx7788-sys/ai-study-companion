#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Windows CI 打包脚本（GitHub Actions · windows-latest）

与本地 build_windows.sh 的关系：同一套流程（模型 → 前端 → PyInstaller → zip），
但有三处**故意**不同 —— 都是为了 CI 这个新环境：

  1. **全程用 Windows 原生路径**，不经过 MSYS 的 /d/... 转换。
     build_windows.sh 里 `OUT_ROOT=/d/...` 与 `OUT_ROOT_WIN=D:/...` 必须成对出现，
     漏一个就静默落到 C 盘；CI runner 上压根没有 D 盘，直接改用脚本自身位置推算。
  2. **产物落在 backend/dist/**，与 build-macos.yml 的上传路径保持对称；
     并把 zip 文件名写进 backend/dist/win_asset_name.txt 供 workflow 读取 ——
     版本号只有 backend/app_version.py 一个来源，workflow 里**不重复硬编码版本号**。
  3. **冒烟失败即中止**（本地脚本只给 WARN）。CI 上「包出来但起不来」如果放行，
     发出去的 Release 资产就是坏的，而流水线还是绿的 —— 这是最坏的一种失败。

步骤：
  [1/5] 下载 BGE 向量模型（backend/data/ 被 .gitignore，仓库内无此模型）
  [2/5] 前端构建（npm ci + npm run build）
  [3/5] PyInstaller 打包（onedir）
  [4/5] 冒烟：ASC_BROWSER=1 起后端，轮询 /api/health
  [5/5] 打包 zip + 内容守卫

失败时留下什么（2026-09-30 追加，起因见下）：
  ⚠️ job 日志要仓库 admin 权限才拉得到（`GET /actions/runs/{id}/logs` → 403
     Must have admin rights），于是这一步一红就又回到「零证据」：只知道
     「Process completed with exit code 1」，不知道哪一步、为什么。
  所以本脚本自己产出三份证据，全部落在 backend/dist/（由 artifact 收走）：
     build_log.txt          全量控制台输出（Python 层 Tee）
     step_logs/NN-*.log     每个外部命令（npm ci / vite / PyInstaller）的原始输出
     failure.txt            失败报告：异常 + 环境 + 失败步骤输出的头尾
  并把失败摘要写成 GitHub **annotation**（`::error::`）—— 公开仓库的
  check-run annotations 无需鉴权即可读取，是拿不到 job 日志时的唯一可读通道。

用法：
  python backend/scripts/build_windows_ci.py

可选环境变量：
  VERSION      覆盖版本号（默认读 backend/app_version.py）
  HF_ENDPOINT  BGE 模型下载源，默认 https://huggingface.co（国内可设 https://hf-mirror.com）
  SMOKE_PORT   冒烟端口，默认 8127
  SKIP_SMOKE   设 1 跳过冒烟（**仅本地调试用**；CI 不要设）
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.request
import zipfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent
FRONTEND_DIR = REPO_ROOT / "frontend"
DIST_DIR = BACKEND_DIR / "dist"
BUILD_DIR = BACKEND_DIR / "build"
STEP_LOG_DIR = DIST_DIR / "step_logs"
BUILD_LOG = DIST_DIR / "build_log.txt"
FAILURE_REPORT = DIST_DIR / "failure.txt"
MODEL_REL = Path("data") / "models" / "bge-small-zh-v1.5"
MODEL_DIR = BACKEND_DIR / MODEL_REL
APP_NAME = "AIStudyCompanion"
APP_DIR = DIST_DIR / APP_NAME

# 模型文件体积下限：真实值 24,010,842 bytes（quantized ONNX）。
# 只做「量级守卫」而非等值断言 —— 上游换量化档位时不该让 CI 红。
MODEL_MIN_BYTES = 20_000_000
# 整包体积下限：本地实测 257 MB。低于 100 MB 一定是漏收了运行库。
ZIP_MIN_BYTES = 100 * 1024 * 1024
# 失败报告/注解里保留的「输出尾部」行数（GitHub 单条注解上限 64KB）
TAIL_LINES = 40
HEAD_LINES = 20


# ── 控制台编码：GitHub Actions 的 windows runner 上 stdout 可能是 cp1252，
#    直接 print 中文会抛 UnicodeEncodeError 把整个构建打断（而且报错信息本身也是中文）。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


class _Tee:
    """把 Python 层的 stdout/stderr 同时写到控制台与 build_log.txt。

    只覆盖 Python 自己写的东西；外部命令的输出由 run() 捕获后 print 出来，
    因此最终都会汇进同一份日志。
    """

    def __init__(self, stream, fh):
        object.__setattr__(self, "_stream", stream)
        object.__setattr__(self, "_fh", fh)

    def write(self, s):
        try:
            self._stream.write(s)
        except Exception:
            pass
        try:
            self._fh.write(s)
            self._fh.flush()
        except Exception:
            pass
        return len(s)

    def flush(self):
        for target in (object.__getattribute__(self, "_stream"),
                       object.__getattribute__(self, "_fh")):
            try:
                target.flush()
            except Exception:
                pass

    def __getattr__(self, name):
        # 转发 isatty / encoding / fileno 等（第三方库会问），但别碰私有名，
        # 否则 __init__ 之前的取值会递归回自己。
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(object.__getattribute__(self, "_stream"), name)


def log(msg: str = "") -> None:
    print(msg, flush=True)


def step(title: str) -> None:
    log("")
    log("=" * 72)
    log(title)
    log("=" * 72)


# ── 失败证据 ────────────────────────────────────────────────────────────────
def _gha_escape(text: str) -> str:
    """GitHub 注解消息的转义：% 要最先处理，换行用 %0A。"""
    return text.replace("%", "%25").replace("\r", "").replace("\n", "%0A")


def emit_annotation(title: str, lines) -> None:
    """写一条 ::error:: 注解。无鉴权读 check-run annotations 是拿不到 job 日志时的兜底通道。"""
    body = _gha_escape("\n".join(lines))
    if len(body) > 60_000:
        body = body[-60_000:]
    print("::error title=%s::%s" % (title, body), flush=True)


def _try_ver(argv) -> str:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=60)
        out = (p.stdout or "").strip() or (p.stderr or "").strip()
        return out.splitlines()[0] if out else "(空输出 rc=%s)" % p.returncode
    except Exception as exc:  # noqa: BLE001
        return "(失败 %s: %s)" % (type(exc).__name__, exc)


def _count_items(p: Path) -> str:
    try:
        if not p.exists():
            return "不存在"
        return "%d 项" % sum(1 for _ in p.rglob("*"))
    except Exception as exc:  # noqa: BLE001
        return "(统计失败 %s)" % exc


def collect_env() -> str:
    """环境快照。不用猜「runner 上装没装 npm」，直接打出来。"""
    out = []
    out.append("python          = %s" % sys.version.replace("\n", " "))
    out.append("sys.executable  = %s" % sys.executable)
    out.append("cwd             = %s" % os.getcwd())
    out.append("sys.platform    = %s" % sys.platform)
    for name in ("npm.cmd", "npm", "node", "git", "taskkill"):
        try:
            out.append("which %-9s = %s" % (name, shutil.which(name)))
        except Exception as exc:  # noqa: BLE001
            out.append("which %-9s = (异常 %s)" % (name, exc))
    out.append("node --version  = %s" % _try_ver(["node", "--version"]))
    out.append("npm  --version  = %s" % _try_ver(["npm", "--version"]))
    out.append("PATH 长度       = %d" % len(os.environ.get("PATH", "")))
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
                "HF_ENDPOINT", "SKIP_SMOKE", "SMOKE_PORT", "VERSION"):
        out.append("%-15s = %r" % (key, os.environ.get(key)))
    for label, p in (("frontend", FRONTEND_DIR),
                     ("frontend/package.json", FRONTEND_DIR / "package.json"),
                     ("frontend/package-lock.json", FRONTEND_DIR / "package-lock.json"),
                     ("frontend/node_modules", FRONTEND_DIR / "node_modules"),
                     ("frontend/node_modules/vditor/dist", FRONTEND_DIR / "node_modules" / "vditor" / "dist"),
                     ("frontend/dist", FRONTEND_DIR / "dist"),
                     ("AIStudyCompanion.spec", BACKEND_DIR / "AIStudyCompanion.spec"),
                     ("backend/data/models", MODEL_DIR)):
        out.append("exists %-30s = %s" % (label, p.exists()))
    try:
        probe = DIST_DIR if DIST_DIR.exists() else BACKEND_DIR
        free = shutil.disk_usage(str(probe)).free
        out.append("磁盘可用        = %.2f GB (%s)" % (free / 1024 ** 3, probe))
    except Exception as exc:  # noqa: BLE001
        out.append("磁盘可用        = (读取失败 %s)" % exc)
    return "\n".join(out)


class StepFailure(SystemExit):
    """带上下文的步骤失败：把子进程原始输出一并带走，供失败报告使用。"""

    def __init__(self, desc: str, rc, output: str):
        self.desc = desc
        self.rc = rc
        self.output = output or ""
        super().__init__("[ERROR] %s 失败（退出码 %s）" % (desc, rc))


def report_failure(exc: BaseException) -> None:
    """把失败写成文件 + 注解。**绝不外抛** —— 它自己失败不该盖掉原始失败。"""
    try:
        lines = []
        lines.append("=" * 72)
        lines.append("Windows CI 构建失败报告")
        lines.append("=" * 72)
        lines.append("时间     = %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
        lines.append("异常类型 = %s" % type(exc).__name__)
        lines.append("异常内容 = %s" % exc)
        lines.append("")
        lines.append("—— 环境 ——")
        lines.append(collect_env())
        lines.append("")
        lines.append("—— 现场（半成品目录）——")
        for label, p in (("frontend/node_modules", FRONTEND_DIR / "node_modules"),
                         ("frontend/public/vditor", FRONTEND_DIR / "public" / "vditor"),
                         ("frontend/dist", FRONTEND_DIR / "dist"),
                         ("backend/data/models", MODEL_DIR),
                         ("backend/dist", DIST_DIR),
                         ("backend/build", BUILD_DIR)):
            lines.append("%-26s = %s" % (label, _count_items(p)))

        brief = ["ASC-CI-Windows 失败：%s" % type(exc).__name__,
                 "%s" % exc]
        if isinstance(exc, StepFailure):
            lines.append("")
            lines.append("—— 失败步骤 ——")
            lines.append("步骤   = %s" % exc.desc)
            lines.append("退出码 = %s" % exc.rc)
            lines.append("输出共 = %d 字节（完整见 backend/dist/step_logs/）" % len(exc.output))
            o_lines = exc.output.splitlines()
            lines.append("")
            lines.append("—— 该步骤输出 · 头部 %d 行 ——" % min(HEAD_LINES, len(o_lines)))
            lines.extend(o_lines[:HEAD_LINES])
            lines.append("")
            lines.append("—— 该步骤输出 · 尾部 %d 行 ——" % min(TAIL_LINES, len(o_lines)))
            lines.extend(o_lines[-TAIL_LINES:])
            brief.append("失败步骤：%s（退出码 %s）" % (exc.desc, exc.rc))
            brief.append("—— 该步骤输出 · 尾部 ——")
            brief.extend(o_lines[-TAIL_LINES:])
        else:
            tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
            lines.append("")
            lines.append("—— 回溯 ——")
            lines.extend("".join(tb).splitlines())
            brief.append("—— 回溯 · 尾部 ——")
            brief.extend("".join(tb).splitlines()[-TAIL_LINES:])

        body = "\n".join(lines)
        try:
            FAILURE_REPORT.write_text(body, encoding="utf-8")
        except Exception:
            pass
        log("")
        log(body)
        log("")
        log("失败报告已写入：%s" % FAILURE_REPORT)
        emit_annotation("ASC-CI-Windows", brief)
    except BaseException:  # noqa: BLE001 — 报告自身失败绝不外抛
        pass


def read_version() -> str:
    """版本号唯一来源：backend/app_version.py（可用 VERSION 环境变量覆盖）。"""
    env = os.environ.get("VERSION", "").strip()
    if env:
        return env
    text = (BACKEND_DIR / "app_version.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', text)
    if not m:
        raise SystemExit("[ERROR] 无法从 backend/app_version.py 读到 __version__")
    return m.group(1)


def which_npm() -> str:
    """Windows 上 npm 是 npm.cmd；shutil.which 靠 PATHEXT 能命中，但两个都试一遍更稳。"""
    for name in ("npm.cmd", "npm"):
        p = shutil.which(name)
        if p:
            return p
    raise SystemExit("[ERROR] 找不到 npm（Node 未安装或不在 PATH）")


_step_counter = [0]


def run(cmd, cwd: Path, desc: str, env=None) -> None:
    """执行一步，失败即中止。

    ⚠️ 这里**捕获**子进程输出（而非让它继承控制台）：CI 的 job 日志要 admin 权限，
       失败时必须自带证据 —— 输出同时进 ① 控制台（build_log.txt）
       ② backend/dist/step_logs/NN-<desc>.log ③ 失败报告。
    """
    _step_counter[0] += 1
    idx = _step_counter[0]
    log(">>> %s" % desc)
    log("    cwd=%s" % cwd)
    log("    cmd=%s" % " ".join(str(c) for c in cmd))
    t0 = time.time()
    try:
        proc = subprocess.run(list(cmd), cwd=str(cwd), env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        raw = proc.stdout or b""
        rc = proc.returncode
    except Exception as exc:  # noqa: BLE001 — 文件不存在 / 不可执行等
        raw = ("%s: %s" % (type(exc).__name__, exc)).encode("utf-8", "replace")
        rc = -1
    text = raw.decode("utf-8", errors="replace")

    try:
        STEP_LOG_DIR.mkdir(parents=True, exist_ok=True)
        # 文件名走 ASCII slug（Windows 上非 ASCII 文件名容易被编码搞坏），
        # 所以中文描述会被清成下划线 —— 「这是哪一步」靠写入文件头的那几行还原。
        safe = re.sub(r"[^0-9A-Za-z._-]+", "_", desc).strip("_._") or "step"
        header = ("# %s\n# cwd=%s\n# cmd=%s\n# 退出码=%s\n# 原始输出 %d 字节\n"
                  % (desc, cwd, " ".join(str(c) for c in cmd), rc, len(raw))
                  ).encode("utf-8", "replace")
        (STEP_LOG_DIR / ("%02d-%s.log" % (idx, safe[:48]))).write_bytes(header + raw)
    except Exception:
        pass

    for line in text.splitlines():
        log("    | " + line)

    if rc != 0:
        raise StepFailure(desc, rc, text)
    log("<<< %s 完成（%.0fs，输出 %d 字节）" % (desc, time.time() - t0, len(raw)))


def child_env(extra=None) -> dict:
    """构造子进程环境：**强制 UTF-8 输出编码**。

    ⚠️ 本模块顶部对 sys.stdout 的 reconfigure **不会传给子进程**。而 GitHub Actions
       的 windows runner 控制台是 cp1252 —— 编不出 CJK 时 print 直接抛
       UnicodeEncodeError 打断构建。
       （2026-09-30 实测：Build Windows #3/#4 都是 PyInstaller 执行 .spec 时倒在
         spec 里那句中文 print 上；macOS 是 UTF-8、本机是中文 Windows 所以都没事。）
       PYTHONIOENCODING 是唯一能跨进程生效的开关。
    """
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    if extra:
        env.update(extra)
    return env


def download(url: str, dest: Path, min_bytes: int = 1) -> None:
    """带重试的下载。已存在且体积达标则跳过（本地重复跑时不重复拉 24MB）。"""
    if dest.exists() and dest.stat().st_size >= min_bytes:
        log("  已存在，跳过：%s（%d bytes）" % (dest.name, dest.stat().st_size))
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    last = None
    for attempt in range(1, 4):
        try:
            log("  下载（第 %d 次）：%s" % (attempt, url))
            tmp = dest.with_suffix(dest.suffix + ".part")
            # 关掉代理：runner 上若有 HTTP(S)_PROXY 指向内网，直连 HF 会被绕进代理
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            t0 = time.time()
            with opener.open(url, timeout=120) as resp, open(tmp, "wb") as fh:
                shutil.copyfileobj(resp, fh, 1024 * 1024)
            if tmp.stat().st_size < min_bytes:
                raise IOError("下载体积异常：%d bytes < %d" % (tmp.stat().st_size, min_bytes))
            tmp.replace(dest)
            log("  完成：%s（%d bytes，%.1fs）"
                % (dest.name, dest.stat().st_size, time.time() - t0))
            return
        except Exception as exc:  # noqa: BLE001 — 网络类异常全部重试
            last = exc
            log("  失败：%s: %s" % (type(exc).__name__, exc))
            time.sleep(3 * attempt)
    raise SystemExit("[ERROR] 下载失败（已重试 3 次）：%s\n        最后一个错误：%s" % (url, last))


def rmtree(path: Path) -> None:
    """安全删除目录。CI 上没有本机的 safe-delete 拦截，但 keep ignore_errors 以防半删状态。"""
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def http_health_ok(port: int, timeout: float = 3.0) -> bool:
    """探 /api/health。必须禁代理 —— 见 download() 里的同一条理由。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open("http://127.0.0.1:%d/api/health" % port, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


# =============================================================================
def main() -> int:
    version = read_version()
    zip_name = "AIStudyCompanion-v%s-win.zip" % version
    zip_path = DIST_DIR / zip_name

    log("Windows CI 打包开始")
    log("  repo       = %s" % REPO_ROOT)
    log("  python     = %s (%s)" % (sys.version.split()[0], sys.executable))
    log("  version    = %s" % version)
    log("  zip 目标   = %s" % zip_path)
    log("  node       = %s" % _try_ver(["node", "--version"]))
    log("  npm        = %s" % _try_ver(["npm", "--version"]))

    # ── [1/5] 模型 ────────────────────────────────────────────────────────
    step("[1/5] 下载 BGE 向量模型")
    hf = os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")
    base = "%s/Xenova/bge-small-zh-v1.5/resolve/main" % hf
    log("  下载源：%s" % hf)
    t_model = time.time()
    download("%s/onnx/model_quantized.onnx" % base, MODEL_DIR / "model.onnx", MODEL_MIN_BYTES)
    download("%s/tokenizer.json" % base, MODEL_DIR / "tokenizer.json", 1000)
    log("  模型就绪：%s（%.1f MB，本步累计 %.0fs）"
        % (MODEL_DIR, sum(p.stat().st_size for p in MODEL_DIR.glob("*")) / 1024 / 1024,
           time.time() - t_model))

    # ── [2/5] 前端 ────────────────────────────────────────────────────────
    step("[2/5] 前端构建（vite build → frontend/dist）")
    npm = which_npm()
    log("  使用 npm：%s" % npm)
    rmtree(FRONTEND_DIR / "dist")
    t_fe = time.time()
    run([npm, "ci"], FRONTEND_DIR, "npm ci")
    log("  npm ci 之后：node_modules/%s；vditor/dist 存在=%s"
        % (_count_items(FRONTEND_DIR / "node_modules"),
           (FRONTEND_DIR / "node_modules" / "vditor" / "dist").exists()))
    run([npm, "run", "build"], FRONTEND_DIR, "npm run build")
    fe_index = FRONTEND_DIR / "dist" / "index.html"
    if not fe_index.exists():
        raise SystemExit("[ERROR] 前端产物缺失：%s" % fe_index)
    fe_files = [p for p in (FRONTEND_DIR / "dist").rglob("*") if p.is_file()]
    log("  前端产物：%d 个文件，index.html %d bytes；本步累计 %.0fs"
        % (len(fe_files), fe_index.stat().st_size, time.time() - t_fe))

    # ── [3/5] PyInstaller ────────────────────────────────────────────────
    step("[3/5] PyInstaller 打包（onedir）")
    rmtree(APP_DIR)
    rmtree(BUILD_DIR / APP_NAME)
    t_pyi = time.time()
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
         "--distpath", str(DIST_DIR), "--workpath", str(BUILD_DIR),
         "AIStudyCompanion.spec"],
        BACKEND_DIR, "PyInstaller", env=child_env())

    exe = APP_DIR / (APP_NAME + ".exe")
    if not exe.exists():
        raise SystemExit("[ERROR] 未找到产物 exe：%s" % exe)
    app_bytes = sum(p.stat().st_size for p in APP_DIR.rglob("*") if p.is_file())
    log("  打包产物：%s（%.1f MB，本步 %.0fs）"
        % (APP_DIR, app_bytes / 1024 / 1024, time.time() - t_pyi))

    # ── [4/5] 冒烟 ───────────────────────────────────────────────────────
    step("[4/5] 冒烟测试（ASC_BROWSER=1 → 轮询 /api/health）")
    if os.environ.get("SKIP_SMOKE", "0") == "1":
        log("  [WARN] SKIP_SMOKE=1 —— 本次**没有**验证包能不能起来")
    else:
        smoke_port = int(os.environ.get("SMOKE_PORT", "8127"))
        smoke_out = DIST_DIR / "smoke_backend_stdout.txt"
        # 四个 ASC_* 一起隔离（照 backend/scripts/smoke_native_shell.py 的做法）：
        # 只设 ASC_DATA_DIR 的话，材料文件仍会落进 runner 的用户目录。
        data_dir = Path(tempfile.mkdtemp(prefix="asc_ci_smoke_"))
        # ⚠️ 必须走 child_env()：应用本身是中文的，在没有 PYTHONIOENCODING 的
        #    cp1252 环境下，它自己 print 中文就会崩在启动阶段（表现成「冒烟失败」），
        #    而且输出文件会被写成 cp1252 字节 —— 我们按 utf-8 读就成了乱码证据。
        env = child_env({
            "ASC_BROWSER": "1",
            "ASC_PORT": str(smoke_port),
            "ASC_DATA_DIR": str(data_dir),
            "ASC_FILES_DIR": str(data_dir / "files"),
            "ASC_DB_URL": "sqlite:///%s" % (data_dir / "app.db").as_posix(),
            "ASC_CHROMA_DIR": str(data_dir / "chroma"),
        })

        # ⚠️ 应用输出**不能**丢 /dev/null：这一步失败时它是唯一的原始线索。
        with open(smoke_out, "wb") as fh:
            proc = subprocess.Popen([str(exe)], cwd=str(APP_DIR), env=env,
                                    stdout=fh, stderr=subprocess.STDOUT)
        log("  已启动：pid=%d port=%d（输出 → %s）" % (proc.pid, smoke_port, smoke_out))

        ok = False
        deadline = time.time() + 90
        while time.time() < deadline:
            if http_health_ok(smoke_port):
                ok = True
                break
            if proc.poll() is not None:
                log("  [!!] 进程已退出（退出码 %s）" % proc.returncode)
                break
            time.sleep(1)

        # 只结束本次启动的进程树（/T 连带 uvicorn 子进程）；不碰 runner 上别的进程
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True)
        log("  冒烟用时与结果：%s" % ("通过" if ok else "失败"))

        if not ok:
            log("  --- 应用输出尾部（完整文件：%s）---" % smoke_out)
            try:
                tail = smoke_out.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]
                for line in tail:
                    log("  | " + line)
            except Exception as exc:  # noqa: BLE001
                log("  （读取输出失败：%s）" % exc)
            raise SystemExit("[ERROR] 冒烟失败：后端未在 90s 内响应 /api/health"
                             "（打包产物可能缺原生库）")
        log("  冒烟通过：/api/health 正常响应")

    # ── [5/5] zip + 守卫 ─────────────────────────────────────────────────
    step("[5/5] 打包 zip")
    zip_path.unlink(missing_ok=True)
    files = [p for p in APP_DIR.rglob("*") if p.is_file()]
    t0 = time.time()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in files:
            arc = str(Path(APP_NAME) / p.relative_to(APP_DIR)).replace("\\", "/")
            z.write(p, arc)
    zip_bytes = zip_path.stat().st_size
    log("  已写入 %d 个文件，用时 %.0fs" % (len(files), time.time() - t0))
    log("  发布包：%s（%.1f MB）" % (zip_path, zip_bytes / 1024 / 1024))

    assert_package(zip_path, len(fe_files), MODEL_DIR / "model.onnx")

    # 版本号→文件名 的传递：写文件让 workflow 读，避免 workflow 里重复硬编码版本号
    (DIST_DIR / "win_asset_name.txt").write_text(zip_name + "\n", encoding="ascii")
    # 体积证据（CI artifact 收走，用于跨版本对比）
    (DIST_DIR / "size_report_win.txt").write_text(
        "version=%s\nzip_bytes=%d\nzip_mb=%.1f\napp_mb=%.1f\nexe=%s\n"
        % (version, zip_bytes, zip_bytes / 1024 / 1024, app_bytes / 1024 / 1024, exe.name),
        encoding="ascii")

    step("打包完成")
    log("zip 路径：%s" % zip_path)
    return 0


def assert_package(zip_path: Path, fe_count: int, model_path: Path) -> None:
    """内容守卫：把「包看起来正常」变成会失败的断言。

    ⚠️ 只断言「zip 存在」是恒真的 —— 一个只装了 exe 的残包也会通过。
       这里逐项检查「少一个就一定是坏的」的结构性文件。
    """
    log("")
    log("  -- 内容守卫 --")
    problems = []
    with zipfile.ZipFile(zip_path) as z:
        infos = {i.filename: i for i in z.infolist()}
    names = list(infos)

    roots = {n.split("/")[0] for n in names}
    if roots != {APP_NAME}:
        problems.append("zip 内根目录不是唯一的 %s：%s" % (APP_NAME, sorted(roots)))

    # PyInstaller 6.x onedir：运行库在 _internal/，exe 在包根
    has_internal = any(n.startswith(APP_NAME + "/_internal/") for n in names)
    base = APP_NAME + "/_internal/" if has_internal else APP_NAME + "/"
    log("  内部根：%s（%d 个条目）" % (base, len(names)))

    def need(key: str, label: str) -> None:
        if key in infos:
            log("    [ok] %s" % label)
        else:
            problems.append("缺 %s（%s）" % (label, key))

    need(APP_NAME + "/" + APP_NAME + ".exe", "启动器 exe 在包根")
    need(base + "base_library.zip", "标准库归档 base_library.zip")
    need(base + "dist/index.html", "前端入口 dist/index.html")
    need(base + "extension/manifest.json", "浏览器扩展随包")

    model_key = base + "data/models/bge-small-zh-v1.5/model.onnx"
    if model_key in infos:
        got = infos[model_key].file_size
        want = model_path.stat().st_size
        if got == want:
            log("    [ok] BGE 模型（%d bytes）" % got)
        else:
            problems.append("BGE 模型体积不符：包内 %d vs 源 %d" % (got, want))
    else:
        problems.append("缺 BGE 模型（%s）" % model_key)

    pack_dist = [n for n in names if n.startswith(base + "dist/") and not n.endswith("/")]
    if len(pack_dist) == fe_count:
        log("    [ok] 包内 dist 条目数 == 源码 dist 文件数（%d）" % fe_count)
    else:
        problems.append("包内 dist 条目数 %d != 源码 %d" % (len(pack_dist), fe_count))

    bad = [n for n in names if re.search(r"(PySide6|shiboken6|qtpy)", n, re.I)]
    if bad:
        problems.append("包内混入 Qt 相关条目 %d 项：%s" % (len(bad), ", ".join(bad[:3])))
    else:
        log("    [ok] 包内无 Qt 相关条目")

    size = zip_path.stat().st_size
    if size >= ZIP_MIN_BYTES:
        log("    [ok] 整包体积 %.1f MB（>= %.0f MB）" % (size / 1024 / 1024, ZIP_MIN_BYTES / 1024 / 1024))
    else:
        problems.append("整包体积仅 %.1f MB，疑似漏收运行库" % (size / 1024 / 1024))

    # 分辨力自检：不存在的路径必须判为不存在，否则上面的 need() 全是恒真的
    ghost = base + "__no_such_file__.dll"
    if ghost in infos:
        problems.append("分辨力自检失败：不存在的路径被判为存在")

    if problems:
        raise SystemExit("[ERROR] 内容守卫失败：\n  - " + "\n  - ".join(problems))
    log("  内容守卫全部通过")


if __name__ == "__main__":
    _log_fh = None
    try:
        DIST_DIR.mkdir(parents=True, exist_ok=True)
        _log_fh = open(BUILD_LOG, "w", encoding="utf-8", errors="replace")
        sys.stdout = _Tee(sys.stdout, _log_fh)
        sys.stderr = _Tee(sys.stderr, _log_fh)
    except Exception:
        _log_fh = None

    _rc = 0
    try:
        _rc = main()
    except BaseException as _exc:  # noqa: BLE001 — SystemExit 也要留下证据
        try:
            report_failure(_exc)
        except BaseException:
            pass
        _rc = 1
    finally:
        try:
            if _log_fh is not None:
                _log_fh.flush()
        except Exception:
            pass
    sys.exit(_rc)
