"""内置插画库 + 确定性选择（解压视觉方案 V2.1 §5）。

浅色电台 `radio` 用 **SVG 线条插画**（程序化、零资产、风格统一）；
深色电台 `night` 用 **AI 预生成彩色插画**（内置 PNG 资产，运行时本地读，**不依赖云**）。

⚠️⚠️ 插画选择必须**确定性**（`hash(材料键 + skin)`）—— 随机选择会让同一配置每次渲染
   产物不同 → 指纹（script_signature）失效、「危险参数变更后一键重建」不可复现（§5.3）。
   所以这里不用 random，用 md5 种子排序，同一材料同一皮肤永远得到同一组插画。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

# 浅色插画的统一线稿风格（线条色用 currentColor：颜色由模板的 .artwork 按变体上色，
# 插画本体与配色解耦 —— 2026-10-12 预设变体机制）
_LINE = ('stroke="currentColor" stroke-width="4.5" fill="none" '
         'stroke-linecap="round" stroke-linejoin="round"')


def _svg(inner: str) -> str:
    return f'<svg viewBox="0 0 320 220" aria-hidden="true"><g {_LINE}>{inner}</g></svg>'


# ---- 浅色电台 radio 的中央线条插画（viewBox 统一 0 0 320 220）----
RADIO_ARTWORKS = [
    {"id": "tea", "brand": "且 听", "svg": _svg(
        '<ellipse cx="150" cy="64" rx="64" ry="15"/>'
        '<path d="M86 66 C88 120 96 150 108 158 Q150 178 192 158 C204 150 212 120 214 66"/>'
        '<path d="M214 84 C252 88 256 122 240 140 C230 152 214 150 208 140"/>'
        '<path d="M58 194 Q150 214 242 194"/>'
        '<path d="M120 44 C114 33 122 25 118 14"/>'
        '<path d="M150 47 C144 34 152 25 148 12"/>'
        '<path d="M180 44 C174 33 182 25 178 14"/>')},
    {"id": "lamp", "brand": "守 光", "svg": _svg(
        '<ellipse cx="160" cy="188" rx="56" ry="9"/>'
        '<path d="M104 194 Q160 210 216 194"/>'
        '<path d="M160 188 L160 98"/>'
        '<path d="M126 98 L138 42 L182 42 L194 98 Z"/>'
        '<path d="M126 98 Q160 109 194 98"/>'
        '<path d="M142 118 L134 140"/>'
        '<path d="M160 120 L160 146"/>'
        '<path d="M178 118 L186 140"/>')},
    {"id": "plant", "brand": "见 绿", "svg": _svg(
        '<path d="M116 118 L128 194 L192 194 L204 118 Z"/>'
        '<path d="M108 118 L212 118"/>'
        '<path d="M160 118 L160 74"/>'
        '<path d="M160 100 C128 96 114 72 120 52 C144 58 158 76 160 98 Z"/>'
        '<path d="M160 84 C192 80 206 56 200 36 C176 42 162 60 160 82 Z"/>'
        '<path d="M160 70 C148 54 150 36 160 26 C170 36 172 54 160 70 Z"/>')},
]

# ---- 深色电台 night 的背景插画（PNG 文件名，位于 assets/night/）----
NIGHT_BACKGROUNDS = [
    {"id": "night_dusk", "brand": "夜 曲", "file": "night_dusk.png"},
    {"id": "night_violet", "brand": "星 澜", "file": "night_violet.png"},
    {"id": "night_teal", "brand": "月 潮", "file": "night_teal.png"},
]

# 预设变体 → 夜景插画（2026-10-12：night 的 3 套皮肤各自不同插画）。
# ⚠️⚠️ 默认套（空变体）**固定 night_dusk**：这是全部存量 night 作品的既有画面，
#    若改成「从三张里哈希挑一张」，老作品重渲染会**静默换图**而指纹不变（visual_sig
#    不感知插画）—— 这种不一致比报错危险得多。
NIGHT_VARIANT_BG = {"": "night_dusk", "violet": "night_violet", "teal": "night_teal"}


def night_assets_dir() -> Path:
    """深色插画资产目录（`services/assets/night/`）。"""
    return Path(__file__).resolve().parent / "assets" / "night"


def night_background_path(item: dict) -> Path:
    return night_assets_dir() / str(item.get("file") or "")


def _seed(*parts) -> int:
    h = hashlib.md5("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return int(h[:8], 16)


def pick_radio_artworks(material_key, count: int | None = None) -> list:
    """确定性选出插画**轮换序列**（同一材料键永远得到同一顺序）。

    用种子排序而非 random.shuffle —— random 即使播种也依赖实现，md5 排序跨进程稳定。
    """
    pool = sorted(RADIO_ARTWORKS,
                  key=lambda a: _seed(material_key, "radio", a["id"]))
    if count is None or count >= len(pool):
        return pool
    return pool[:max(1, int(count))]


def pick_night_background(material_key, variant: str = "") -> dict:
    """按**变体**选夜景背景：空变体固定 night_dusk（存量零回归，见 NIGHT_VARIANT_BG 注释）。

    `material_key` 保留在签名里仅为兼容既有调用 —— 变体机制后选择不再哈希
    （哈希挑图会让同一配置在不同时期渲出不同画面，指纹管不住它）。
    """
    want = NIGHT_VARIANT_BG.get(str(variant or "").strip().lower(), "night_dusk")
    for b in NIGHT_BACKGROUNDS:
        if b["id"] == want:
            return b
    return NIGHT_BACKGROUNDS[0]


def night_backgrounds_available() -> list:
    """返回**实际存在于磁盘**的夜景资产（闸门「插画资产可用」判据用）。"""
    return [b for b in NIGHT_BACKGROUNDS if night_background_path(b).exists()]
