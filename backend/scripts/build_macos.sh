#!/usr/bin/env bash
# =============================================================================
# macOS 打包脚本：把「后端 + 前端 dist + BGE 向量模型」打成免安装 .app（绿色版）
#
# 与 Windows 打包（backend/AIStudyCompanion.spec）共用同一份 spec，
# 差异只在：① 图标由 .ico 换成 .icns（脚本内自动生成）② 额外收集 pyobjc。
#
# 用法（在 macOS 上，或在 GitHub Actions macOS runner 上）：
#   ARCH=arm64 bash backend/scripts/build_macos.sh
#   可选环境变量：
#     ARCH        产物架构标签（arm64 / x86_64），仅用于 zip 命名，默认 arm64
#     HF_ENDPOINT BGE 模型下载源，默认 https://huggingface.co
#                 （国内本地打包可设 https://hf-mirror.com）
# =============================================================================
set -euo pipefail

# 定位 backend 目录（脚本所在目录的上一级）
BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$BACKEND_DIR/.." && pwd)"
ARCH="${ARCH:-arm64}"
HF_ENDPOINT="${HF_ENDPOINT:-https://huggingface.co}"

echo "=== [1/6] 下载 BGE 向量模型（data/ 被 .gitignore，仓库内无此模型） ==="
mkdir -p "$BACKEND_DIR/data/models/bge-small-zh-v1.5"
BGE_BASE="$HF_ENDPOINT/Xenova/bge-small-zh-v1.5/resolve/main"
if [ ! -s "$BACKEND_DIR/data/models/bge-small-zh-v1.5/model.onnx" ]; then
  curl -fL --retry 3 -o "$BACKEND_DIR/data/models/bge-small-zh-v1.5/model.onnx" \
    "$BGE_BASE/onnx/model_quantized.onnx"
fi
if [ ! -s "$BACKEND_DIR/data/models/bge-small-zh-v1.5/tokenizer.json" ]; then
  curl -fL --retry 3 -o "$BACKEND_DIR/data/models/bge-small-zh-v1.5/tokenizer.json" \
    "$BGE_BASE/tokenizer.json"
fi
echo "模型就绪：$(du -sh "$BACKEND_DIR/data/models/bge-small-zh-v1.5" | cut -f1)"

echo "=== [2/6] 生成 mascot.icns（从 mascot_b64 解码 128px PNG → iconset → icns） ==="
cd "$BACKEND_DIR"
python3 - <<'PY'
import base64
from pathlib import Path
from mascot_b64 import MASCOT_B64
Path("mascot.png").write_bytes(base64.b64decode(MASCOT_B64))
print("已解码 mascot.png (128px)")
PY

rm -rf mascot.iconset mascot.icns
mkdir -p mascot.iconset
# 128px 源图放大到 256/512 会略糊，但作为应用图标可接受
for s in 16 32 64 128 256 512; do
  sips -z "$s" "$s" mascot.png --out "mascot.iconset/icon_${s}x${s}.png" >/dev/null 2>&1 || true
  d=$((s * 2))
  if [ "$d" -le 512 ]; then
    sips -z "$d" "$d" mascot.png --out "mascot.iconset/icon_${s}x${s}@2x.png" >/dev/null 2>&1 || true
  fi
done
iconutil -c icns mascot.iconset -o mascot.icns
echo "已生成 mascot.icns"

echo "=== [3/6] 前端构建（vite build → frontend/dist） ==="
cd "$REPO_ROOT/frontend"
npm ci
npm run build
echo "前端产物就绪：$REPO_ROOT/frontend/dist"

echo "=== [4/6] PyInstaller 打包（onedir → dist/AIStudyCompanion.app） ==="
cd "$BACKEND_DIR"
python3 -m PyInstaller --noconfirm --clean AIStudyCompanion.spec
APP_BUNDLE="$BACKEND_DIR/dist/AIStudyCompanion.app"
if [ ! -d "$APP_BUNDLE" ]; then
  echo "[ERROR] 未找到打包产物 $APP_BUNDLE" >&2
  exit 1
fi
echo "打包产物：$APP_BUNDLE"

echo "=== [5/6] 冒烟测试（ASC_BROWSER=1 强制浏览器模式，验证打包后原生库可正常 import） ==="
SMOKE_PORT="${SMOKE_PORT:-8123}"
APP_BIN="$APP_BUNDLE/Contents/MacOS/AIStudyCompanion"
ASC_BROWSER=1 ASC_PORT="$SMOKE_PORT" "$APP_BIN" >/dev/null 2>&1 &
APP_PID=$!
ok=0
for i in $(seq 1 60); do
  if curl -sf "http://127.0.0.1:$SMOKE_PORT/api/health" >/dev/null 2>&1; then
    ok=1
    break
  fi
  sleep 1
done
kill "$APP_PID" 2>/dev/null || true
wait "$APP_PID" 2>/dev/null || true
if [ "$ok" != "1" ]; then
  echo "[ERROR] 冒烟测试失败：后端未在 60s 内响应 /api/health（打包产物可能缺原生库）" >&2
  exit 1
fi
echo "冒烟测试通过：/api/health 正常响应"

echo "=== [6/6] 打包 zip 产物 ==="
cd "$BACKEND_DIR/dist"
ZIP_NAME="ai-study-companion-macos-${ARCH}.zip"
rm -f "$ZIP_NAME"
# 保留符号链接与权限（zip 默认会展开 symlink，这里用 -y 存符号链接）
zip -qry "$ZIP_NAME" "AIStudyCompanion.app"
ZIP_PATH="$BACKEND_DIR/dist/$ZIP_NAME"
echo "产物：${ZIP_PATH}（$(du -sh "$ZIP_PATH" | cut -f1)）"

echo ""
echo "=== 打包完成 ==="
echo "$ZIP_PATH"
