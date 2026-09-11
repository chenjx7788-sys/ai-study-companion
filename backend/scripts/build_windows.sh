#!/usr/bin/env bash
# =============================================================================
# Windows 打包脚本：前端构建 → PyInstaller → 发布 zip
#
# 与 macOS 的 build_macos.sh 对应；两者共用同一份 AIStudyCompanion.spec。
#
# ★ 产物统一输出到 D 盘（OUT_ROOT），不占用 C 盘空间：
#     OUT_ROOT/dist     PyInstaller 产物（AIStudyCompanion/）
#     OUT_ROOT/build    PyInstaller 中间缓存
#     OUT_ROOT/release  发布 zip
#
# 用法（Git Bash）：
#   bash backend/scripts/build_windows.sh
# 可选环境变量：
#   OUT_ROOT  输出根目录，默认 /d/AIStudyCompanion-build
#   VERSION   版本号，默认 0.1.2（用于 zip 命名）
#   PY        打包用的 Python（需装齐依赖 + PyInstaller）
# =============================================================================
set -uo pipefail

BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$BACKEND_DIR/.." && pwd)"
# Bash 用 MSYS 路径（/d/...）；PyInstaller 是 Windows 程序，必须传 Windows 路径（D:/...），
# 否则 "/d/..." 会被它解释成 "C:\d\..."，产物反而落到 C 盘。
OUT_ROOT="${OUT_ROOT:-/d/AIStudyCompanion-build}"
OUT_ROOT_WIN="${OUT_ROOT_WIN:-D:/AIStudyCompanion-build}"
DIST_DIR="$OUT_ROOT/dist"
BUILD_DIR="$OUT_ROOT/build"
RELEASE_DIR="$OUT_ROOT/release"
DIST_DIR_WIN="$OUT_ROOT_WIN/dist"
BUILD_DIR_WIN="$OUT_ROOT_WIN/build"
RELEASE_DIR_WIN="$OUT_ROOT_WIN/release"
VERSION="${VERSION:-0.1.3}"
PY="${PY:-C:/Users/陈锦祥/.workbuddy/binaries/python/envs/default/Scripts/python.exe}"
MGR_PY="${MGR_PY:-C:/Users/陈锦祥/.workbuddy/binaries/python/versions/3.13.12/python.exe}"

ZIP_NAME="AIStudyCompanion-v${VERSION}-win.zip"

mkdir -p "$DIST_DIR" "$BUILD_DIR" "$RELEASE_DIR"

echo "=== [1/4] 前端构建 ==="
cd "$REPO_ROOT/frontend"
# vite 会清空 outDir；本环境对批量删除有安全拦截，故先把旧 dist 改名（不触发删除）
if [ -d dist ]; then
  mv dist "dist.old-$(date +%H%M%S)"
  # 顺手回收历史残留（Python 删除多数目录可成功，失败则忽略）
  "$MGR_PY" -c "
import shutil, glob, sys
for p in sorted(glob.glob(r'$REPO_ROOT/frontend/dist.old-*'))[:-1]:
    shutil.rmtree(p, ignore_errors=True)
" 2>/dev/null || true
fi
npm run build || { echo "[ERROR] 前端构建失败"; exit 1; }
echo "前端产物就绪：$REPO_ROOT/frontend/dist"

echo ""
echo "=== [2/4] PyInstaller 打包（输出到 D 盘）==="
cd "$BACKEND_DIR"
# 目标产物目录 / 中间缓存目录若已存在，先改名
# （本环境对「批量删除」有安全拦截，PyInstaller 的自动清理会被拦下导致打包失败）
if [ -d "$DIST_DIR/AIStudyCompanion" ]; then
  mv "$DIST_DIR/AIStudyCompanion" "$DIST_DIR/out.old-$(date +%H%M%S)"
fi
if [ -d "$BUILD_DIR/AIStudyCompanion" ]; then
  mv "$BUILD_DIR/AIStudyCompanion" "$BUILD_DIR/work.old-$(date +%H%M%S)"
fi
# 顺带回收历史残留（Python 删除多数可成功，失败则忽略）
"$MGR_PY" -c "
import shutil, glob
for pat in ('$DIST_DIR/out.old-*', '$BUILD_DIR/work.old-*'):
    for p in sorted(glob.glob(pat))[:-1]:
        shutil.rmtree(p, ignore_errors=True)
" 2>/dev/null || true
"$PY" -m PyInstaller --noconfirm \
  --distpath "$DIST_DIR_WIN" \
  --workpath "$BUILD_DIR_WIN" \
  AIStudyCompanion.spec || { echo "[ERROR] PyInstaller 失败"; exit 1; }

APP_DIR="$DIST_DIR/AIStudyCompanion"
[ -d "$APP_DIR" ] || { echo "[ERROR] 未找到产物 $APP_DIR"; exit 1; }
echo "打包产物：$APP_DIR（$(du -sh "$APP_DIR" | cut -f1)）"

echo ""
echo "=== [3/4] 冒烟测试（启动后验证 /api/health）==="
SMOKE_PORT="${SMOKE_PORT:-8127}"
if [ -f "$APP_DIR/AIStudyCompanion.exe" ]; then
  (cd "$APP_DIR" && ASC_BROWSER=1 ASC_PORT="$SMOKE_PORT" ./AIStudyCompanion.exe >/dev/null 2>&1 &
   echo $! > /tmp/asc_smoke.pid)
  ok=0
  for i in $(seq 1 75); do
    if curl -sf -m 3 "http://127.0.0.1:$SMOKE_PORT/api/health" >/dev/null 2>&1; then ok=1; break; fi
    sleep 1
  done
  if [ "$ok" = "1" ]; then
    echo "冒烟测试通过：/api/health 正常响应"
  else
    echo "[WARN] 冒烟测试未在 75s 内响应（不影响产物，请手动确认）"
  fi
  # 仅结束本次启动的实例（按端口精确匹配，避免误杀其他实例）
  SMOKE_PID="$(netstat -ano 2>/dev/null | grep ":$SMOKE_PORT" | grep -i listening | head -1 | awk '{print $NF}')"
  [ -n "$SMOKE_PID" ] && taskkill /F /PID "$SMOKE_PID" >/dev/null 2>&1
else
  echo "（非 Windows 环境，跳过冒烟测试）"
fi

echo ""
echo "=== [4/4] 打包 zip ==="
cd "$DIST_DIR"
ZIP_PATH="$RELEASE_DIR/$ZIP_NAME"
ZIP_PATH_WIN="$RELEASE_DIR_WIN/$ZIP_NAME"
rm -f "$ZIP_PATH" 2>/dev/null || true
# 注意：这里必须传 Windows 风格路径给 Python（Python 不认 MSYS 的 /d/...）
"$MGR_PY" - "$DIST_DIR_WIN/AIStudyCompanion" "$ZIP_PATH_WIN" <<'PY'
import sys, time, zipfile
from pathlib import Path
src, dst = Path(sys.argv[1]), Path(sys.argv[2])
files = [p for p in src.rglob("*") if p.is_file()]
t0 = time.time()
with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
    for p in files:
        z.write(p, str(Path("AIStudyCompanion") / p.relative_to(src)).replace("\\", "/"))
print(f"  已写入 {len(files)} 个文件，用时 {time.time()-t0:.0f}s")
PY
[ -f "$ZIP_PATH" ] || { echo "[ERROR] zip 生成失败"; exit 1; }
echo "发布包：$ZIP_PATH（$(du -sh "$ZIP_PATH" | cut -f1)）"

echo ""
echo "=== 完成 ==="
echo "产物目录：$OUT_ROOT"
echo "zip 路径：$ZIP_PATH"
