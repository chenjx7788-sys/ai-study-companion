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
#
#   阶段 1 新增（WP8 / WP8b）：
#     SIGN_IDENTITY  代码签名身份。默认 '-'（ad-hoc）。正式分发传
#                    "Developer ID Application: <名字> (<TEAMID>)"。
#                    ⚠️ ad-hoc **不能**免掉「右键 → 打开」，只解决内部一致性
#                    （嵌套 QtWebEngineProcess.app 签名完整，避免 killed: 9）。
#     SKIP_SIGN      设 1 则跳过签名步骤（仅本地调试用；CI 不要设）
#     SKIP_NATIVE_SMOKE 设 1 则跳过原生外壳冒烟（无 GUI 的 CI runner 才需要）
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

echo "=== [5/7] 后端健康冒烟（ASC_BROWSER=1 强制浏览器模式） ==="
# ⚠️ 这一步只证明「打包后原生库能 import、后端能起来」。
#    它**结构上测不到 Qt 外壳** —— ASC_BROWSER=1 会让 launcher.py 整段跳过 pywebview
#    原生窗口（launcher.py:230 的 if 分支），一个字节都没碰 Qt 路径。
#    真正的外壳判据是下一步 [5b]，见 WP8b。
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
  echo "[ERROR] 后端健康冒烟失败：后端未在 60s 内响应 /api/health（打包产物可能缺原生库）" >&2
  exit 1
fi
echo "后端健康冒烟通过：/api/health 正常响应"

echo ""
echo "=== [5b/7] 原生外壳冒烟：断言实际后端确为 Qt（WP8b） ==="
# 不设 ASC_BROWSER → 起真实原生窗口 → 读 data_dir/launcher_backend.log 里的 backend=。
# 这条判据针对 R4 的失败形态：spec 漏收 PySide6/QtWebEngine 时，pywebview 会**静默回落**
# 到 cocoa（Darwin 候选列表是 [qt, cocoa]）—— 不报错、不警告，上一步照样全绿。
if [ "${SKIP_NATIVE_SMOKE:-0}" = "1" ]; then
  echo "[WARN] SKIP_NATIVE_SMOKE=1，跳过原生外壳冒烟（本次**没有**验证 Qt 外壳）"
else
  SMOKE_DATA_DIR="${SMOKE_DATA_DIR:-$(mktemp -d /tmp/asc_native_smoke.XXXXXX)}"
  if python3 "$BACKEND_DIR/scripts/smoke_native_shell.py" \
       --app-bin "$APP_BIN" \
       --expect-backend webview.platforms.qt \
       --port "${NATIVE_SMOKE_PORT:-8125}" \
       --timeout "${NATIVE_SMOKE_TIMEOUT:-90}" \
       --report "$BACKEND_DIR/dist/smoke_native_shell.json"; then
    echo "原生外壳冒烟通过：backend=webview.platforms.qt"
  else
    echo "[ERROR] 原生外壳冒烟失败 —— 打出的包实际没用 Qt 外壳。" >&2
    echo "        典型原因：spec 漏收 PySide6/QtWebEngine（R4）。" >&2
    echo "        证据：$BACKEND_DIR/dist/smoke_native_shell.json" >&2
    exit 1
  fi
fi

echo ""
echo "=== [5c/7] 代码签名（WP8 · R5） ==="
# 为什么单独一步、而不是靠 PyInstaller：
#   PyInstaller 6.x 的 BUNDLE 阶段其实会调 codesign，但失败时**只打一条 warning**
#   （osx.py：PYINSTALLER_STRICT_BUNDLE_CODESIGN_ERROR 默认 0）→
#   「签名失败但构建成功」的包会照常进 zip 发出去。本步骤把它变成会失败、有证据。
# 顺序由 macos_sign.py 自己保证：Mach-O → 嵌套 .app/.framework（由深到浅）→ 外层 bundle。
# 嵌套的 QtWebEngineProcess.app 必须被单独签 —— 它没签好，用户那边是 killed: 9。
if [ "${SKIP_SIGN:-0}" = "1" ]; then
  echo "[WARN] SKIP_SIGN=1，跳过签名（本次产物**未签名**，不可分发）"
else
  SIGN_IDENTITY="${SIGN_IDENTITY:--}"
  SIGN_ARGS=()
  if [ "$SIGN_IDENTITY" != "-" ]; then
    # 真身份才带 entitlements：那些是 hardened runtime 的豁免项，
    # 没有 hardened runtime 时不起作用（详见 macos_entitlements.plist 里的说明）。
    SIGN_ARGS+=(--entitlements "$BACKEND_DIR/scripts/macos_entitlements.plist")
  fi
  python3 "$BACKEND_DIR/scripts/macos_sign.py" \
    --app "$APP_BUNDLE" \
    --identity "$SIGN_IDENTITY" \
    "${SIGN_ARGS[@]+"${SIGN_ARGS[@]}"}" \
    --report "$BACKEND_DIR/dist/sign_report.json"
  echo "签名通过（identity=$SIGN_IDENTITY）；证据：$BACKEND_DIR/dist/sign_report.json"
fi

echo ""
echo "=== [6/7] 打包 zip 产物 ==="
# ⚠️ 顺序：签名**必须**在 zip 之前。zip 之后签没用 —— 用户解压出来是新的文件树。
cd "$BACKEND_DIR/dist"
ZIP_NAME="ai-study-companion-macos-${ARCH}.zip"
rm -f "$ZIP_NAME"
# 保留符号链接与权限（zip 默认会展开 symlink，这里用 -y 存符号链接）
zip -qry "$ZIP_NAME" "AIStudyCompanion.app"
ZIP_PATH="$BACKEND_DIR/dist/$ZIP_NAME"
echo "产物：${ZIP_PATH}（$(du -sh "$ZIP_PATH" | cut -f1)）"

echo ""
echo "=== 打包完成 ==="
if [ "${SIGN_IDENTITY:--}" = "-" ]; then
  echo "⚠️ 本次为 ad-hoc 签名：用户首次打开仍需「右键 → 打开」（Gatekeeper 不放行）。"
  echo "   要免掉这一步，需设 SIGN_IDENTITY 为 Developer ID 证书 + 后续公证（notarization）。"
fi
echo "$ZIP_PATH"
