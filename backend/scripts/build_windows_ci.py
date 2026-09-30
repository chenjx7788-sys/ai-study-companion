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
import urllib.request
import zipfile
from pathlib import Path

# ── 控制台编码：GitHub Actions 的 windows runner 上 stdout 可能是 cp1252，
#    直接 print 中文会抛 UnicodeEncodeError 把整个构建打断（而且报错信息本身也是中文）。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent
FRONTEND_DIR = REPO_ROOT / "frontend"
DIST_DIR = BACKEND_DIR / "dist"
BUILD_DIR = BACKEND_DIR / "build"
MODEL_REL = Path("data") / "models" / "bge-small-zh-v1.5"
MODEL_DIR = BACKEND_DIR / MODEL_REL
APP_NAME = "AIStudyCompanion"
APP_DIR = DIST_DIR / APP_NAME

# 模型文件体积下限：真实值 24,010,842 bytes（quantized ONNX）。
# 只做「量级守卫」而非等值断言 —— 上游换量化档位时不该让 CI 红。
MODEL_MIN_BYTES = 20_000_000
# 整包体积下限：本地实测 257 MB。低于 100 MB 一定是漏收了运行库。
ZIP_MIN_BYTES = 100 * 1024 * 1024


def log(msg: str = "") -> None:
    print(msg, flush=True)


def step(title: str) -> None:
    log("")
    log("=" * 72)
    log(title)
    log("=" * 72)


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


def run(cmd, cwd: Path, desc: str, env=None) -> None:
    """执行一步，失败即中止。cmd 里的元素原样传给内核，不做 shell 解析。"""
    log(">>> %s" % desc)
    log("    cwd=%s" % cwd)
    log("    cmd=%s" % " ".join(str(c) for c in cmd))
    t0 = time.time()
    proc = subprocess.run(list(cmd), cwd=str(cwd), env=env)
    if proc.returncode != 0:
        raise SystemExit("[ERROR] %s 失败（退出码 %d）" % (desc, proc.returncode))
    log("<<< %s 完成（%.0fs）" % (desc, time.time() - t0))


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
            with opener.open(url, timeout=120) as resp, open(tmp, "wb") as fh:
                shutil.copyfileobj(resp, fh, 1024 * 1024)
            if tmp.stat().st_size < min_bytes:
                raise IOError("下载体积异常：%d bytes < %d" % (tmp.stat().st_size, min_bytes))
            tmp.replace(dest)
            log("  完成：%s（%d bytes）" % (dest.name, dest.stat().st_size))
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

    # ── [1/5] 模型 ────────────────────────────────────────────────────────
    step("[1/5] 下载 BGE 向量模型")
    hf = os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")
    base = "%s/Xenova/bge-small-zh-v1.5/resolve/main" % hf
    log("  下载源：%s" % hf)
    download("%s/onnx/model_quantized.onnx" % base, MODEL_DIR / "model.onnx", MODEL_MIN_BYTES)
    download("%s/tokenizer.json" % base, MODEL_DIR / "tokenizer.json", 1000)
    log("  模型就绪：%s（%.1f MB）"
        % (MODEL_DIR, sum(p.stat().st_size for p in MODEL_DIR.glob("*")) / 1024 / 1024))

    # ── [2/5] 前端 ────────────────────────────────────────────────────────
    step("[2/5] 前端构建（vite build → frontend/dist）")
    npm = which_npm()
    rmtree(FRONTEND_DIR / "dist")
    run([npm, "ci"], FRONTEND_DIR, "npm ci")
    run([npm, "run", "build"], FRONTEND_DIR, "npm run build")
    fe_index = FRONTEND_DIR / "dist" / "index.html"
    if not fe_index.exists():
        raise SystemExit("[ERROR] 前端产物缺失：%s" % fe_index)
    fe_files = [p for p in (FRONTEND_DIR / "dist").rglob("*") if p.is_file()]
    log("  前端产物：%d 个文件，index.html %d bytes"
        % (len(fe_files), fe_index.stat().st_size))

    # ── [3/5] PyInstaller ────────────────────────────────────────────────
    step("[3/5] PyInstaller 打包（onedir）")
    rmtree(APP_DIR)
    rmtree(BUILD_DIR / APP_NAME)
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
         "--distpath", str(DIST_DIR), "--workpath", str(BUILD_DIR),
         "AIStudyCompanion.spec"],
        BACKEND_DIR, "PyInstaller")

    exe = APP_DIR / (APP_NAME + ".exe")
    if not exe.exists():
        raise SystemExit("[ERROR] 未找到产物 exe：%s" % exe)
    app_bytes = sum(p.stat().st_size for p in APP_DIR.rglob("*") if p.is_file())
    log("  打包产物：%s（%.1f MB）" % (APP_DIR, app_bytes / 1024 / 1024))

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
        env = dict(os.environ)
        env["ASC_BROWSER"] = "1"
        env["ASC_PORT"] = str(smoke_port)
        env["ASC_DATA_DIR"] = str(data_dir)
        env["ASC_FILES_DIR"] = str(data_dir / "files")
        env["ASC_DB_URL"] = "sqlite:///%s" % (data_dir / "app.db").as_posix()
        env["ASC_CHROMA_DIR"] = str(data_dir / "chroma")

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
    sys.exit(main())
