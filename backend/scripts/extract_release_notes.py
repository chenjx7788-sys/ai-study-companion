#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""从 CHANGELOG.md 抽出「当前版本」那一节，生成 GitHub Release 正文。

背景（2026-09-30）：v0.1.5 的 Release 正文是 **12 遍重复的同一行 Full Changelog 链接**，
一个字说明都没有。原因是 build-macos.yml 用了 `generate_release_notes: true`，而它是
**两个 matrix job**（arm64 / x86_64），每次运行各追加一次；本轮为了让 Windows 构建跑通
移动了 4 次 tag → 攒成 12 行。仓库里 `CHANGELOG.md` 其实写得很完整，只是没人把它接上去。

本脚本把「CHANGELOG → Release 正文」变成确定性的一步：

  · **只有一个写入者**（build-macos.yml 的 release-notes job）→ 结构上不可能重复
  · 版本号只有一个来源：`--version` / `GITHUB_REF_NAME` / CHANGELOG 最新一节
  · 下载表由版本号拼出（与打包脚本的命名规则一致），不手写、不复制粘贴
  · 找不到该版本条目时**不静默**：正文里明确写出来并附 compare 链接，**退出码仍为 0**
    —— 这一步跑在产物上传之后，为一条文案把流水线判红得不偿失

用法：
  python backend/scripts/extract_release_notes.py [--version vX.Y.Z] [--out PATH]
  python backend/scripts/extract_release_notes.py --print      # 只打到 stdout，不写文件
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent
CHANGELOG = REPO_ROOT / "CHANGELOG.md"
DEFAULT_OUT = BACKEND_DIR / "dist" / "release_body.md"

REPO_SLUG = "chenjx7788-sys/ai-study-companion"
PRODUCT_NAME = "知萤"
PRODUCT_TAGLINE = "本地私有的 AI 学习知识中枢"

# ⚠️ 与 build_windows_ci.py 的 zip_name / build_macos.sh 的产物名**必须一致**。
#    改了命名这里不跟着改 → Release 说明里的文件名点开是 404。
WIN_ZIP = "AIStudyCompanion-v%s-win.zip"
MAC_ARM64_ZIP = "ai-study-companion-macos-arm64.zip"
MAC_X86_64_ZIP = "ai-study-companion-macos-x86_64.zip"

# `## [v0.1.5] - 2026-09-28` / `## [0.1.5]` / `## v0.1.5` 都认
SECTION_RE = re.compile(r"^##\s*\[?v?(\d+\.\d+\.\d+)\]?\s*(?:[-—·]\s*(\d{4}-\d{2}-\d{2}))?\s*$",
                        re.M)


def parse_sections(text: str) -> list:
    """把 CHANGELOG 切成 [{version, date, body}]，顺序＝文件里的顺序（最新在前）。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    hits = list(SECTION_RE.finditer(text))
    out = []
    for i, m in enumerate(hits):
        end = hits[i + 1].start() if i + 1 < len(hits) else len(text)
        body = text[m.end():end].strip("\n")
        # ⚠️ CHANGELOG 用 `---` 分节，切出来会带着尾部那条水平线 → 正文里会多一条
        #    孤零零的 `---`（实测）。这里剔掉尾部所有水平线与空行。
        body = re.sub(r"(?:\s*\n)?\s*-{3,}\s*$", "", body).strip("\n")
        out.append({"version": m.group(1), "date": m.group(2) or "", "body": body})
    return out


def norm_version(v: str) -> str:
    return (v or "").strip().lstrip("vV")


def build_body(version: str, section: dict | None) -> str:
    ver = norm_version(version)
    head = "**v%s** · %s · %s" % (ver, PRODUCT_NAME, PRODUCT_TAGLINE)
    if section and section.get("date"):
        head = "**v%s（%s）** · %s · %s" % (ver, section["date"], PRODUCT_NAME, PRODUCT_TAGLINE)

    parts = [head, ""]
    if section and section.get("body"):
        parts += [section["body"], ""]
    else:
        # 不静默：把「为什么这里是空的」写清楚，下一个人一眼能查到
        parts += [
            "> ⚠️ 本次未在仓库 `CHANGELOG.md` 里找到 `v%s` 的条目，因此这里没有逐条说明。" % ver,
            "> 要补齐：在 `CHANGELOG.md` 顶部新增 `## [v%s] - YYYY-MM-DD` 一节，再重新生成本页。" % ver,
            "",
        ]

    parts += [
        "### 下载",
        "",
        "| 平台 | 文件 |",
        "|---|---|",
        "| Windows 10 / 11（64 位） | `%s` |" % (WIN_ZIP % ver),
        "| macOS · Apple Silicon | `%s` |" % MAC_ARM64_ZIP,
        "| macOS · Intel | `%s` |" % MAC_X86_64_ZIP,
        "",
        "免安装绿色版：Windows 解压后双击 `AIStudyCompanion.exe`；macOS 解压后打开 `.app`"
        "（首次需右键 → 打开）。本地向量模型已随包内置（可切换在线 API），"
        "语音模型首次使用时按需下载。",
        "",
        "完整记录见仓库 [`CHANGELOG.md`](https://github.com/%s/blob/main/CHANGELOG.md)。"
        % REPO_SLUG,
    ]
    return "\n".join(parts).rstrip("\n") + "\n"


def pick_version(sections: list, explicit: str) -> tuple:
    """返回 (version, section 或 None, 来源说明)。"""
    if explicit:
        v = norm_version(explicit)
        for s in sections:
            if s["version"] == v:
                return v, s, "--version/环境变量"
        return v, None, "--version/环境变量（CHANGELOG 里没有这一节）"
    if sections:
        return sections[0]["version"], sections[0], "CHANGELOG 最新一节"
    return "", None, "CHANGELOG 里一节都没解析到"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default=os.environ.get("GITHUB_REF_NAME", ""),
                    help="如 v0.1.5；默认取 GITHUB_REF_NAME")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--print", dest="to_stdout", action="store_true")
    args = ap.parse_args()

    if not CHANGELOG.exists():
        print("[ERROR] 找不到 %s" % CHANGELOG)
        return 1
    sections = parse_sections(CHANGELOG.read_text(encoding="utf-8"))
    if not sections:
        print("[ERROR] %s 里没解析到任何 `## [vX.Y.Z]` 小节" % CHANGELOG.name)
        return 1

    version, section, src = pick_version(sections, args.version)
    body = build_body(version, section)

    if section:
        print("[OK] 版本 %s（来自 %s）→ 正文 %d 字符 / %d 行"
              % (version, src, len(body), len(body.splitlines())))
    else:
        # 不判红：这一步在产物上传之后跑，为一条文案把流水线判红得不偿失
        print("[WARN] CHANGELOG 里没有 v%s 的条目（%s）—— 正文已写明这一点" % (version, src))

    if args.to_stdout:
        sys.stdout.write(body)
        return 0

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(body.encode("utf-8"))     # ⚠️ write_bytes，绝不 write_text（会翻 CRLF）
    print("[OK] 已写出 %s（%d 字节）" % (out, out.stat().st_size))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
