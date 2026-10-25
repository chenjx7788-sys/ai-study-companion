"""技能原文抓取（P5：「发个 GitHub 地址装技能」的第一步）。

⚠️ 三条边界：
1. **主机白名单**（github / gitee / raw.githubusercontent）——这是给「装技能」用的抓取，
   不是通用网页抓取器；通用抓取走 external.py（剪藏链路），那里的判错/降级是为文章设计的，
   对「纯文本文件」会直接判 NOT_HTML，不适合读 SKILL.md。
2. **重定向后的最终主机也要在白名单里**（urllib 默认跟随 302，不查就是把白名单变成了摆设）。
3. **直连不走系统代理**（本机开发环境代理会把请求发去错误的地方；线上也不该经代理出内网）。

返回统一为 dict，绝不抛异常（调用方在 SSE 生成器里，异常会掐断整条流）。
"""
from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AIStudyCompanion/1.0"
MAX_BYTES = 512 * 1024          # 技能文件不该超过 512KB（正常 2–10KB）
ALLOWED_HOSTS = ("github.com", "raw.githubusercontent.com", "gitee.com")

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _candidates(url: str) -> list[str]:
    """把用户贴的 GitHub/Gitee 地址归一化成 raw 直链候选（按优先级排序）。

    支持：blob 文件页 / raw 直链 / tree 目录页（试目录下的 SKILL.md/skill.md）/
    仓库根（试 main、master 分支的 SKILL.md/skill.md）。
    """
    u = (url or "").strip().rstrip("/")
    if not u:
        return []

    m = re.match(r"https?://raw\.githubusercontent\.com/.+$", u)
    if m:
        return [u]

    m = re.match(r"https?://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.+)$", u)
    if m:
        o, r, b, p = m.groups()
        return [f"https://raw.githubusercontent.com/{o}/{r}/{b}/{p}"]

    m = re.match(r"https?://github\.com/([^/]+)/([^/]+)/tree/([^/]+)/(.+)$", u)
    if m:
        o, r, b, p = m.groups()
        base = f"https://raw.githubusercontent.com/{o}/{r}/{b}/{p}"
        return [base + "/SKILL.md", base + "/skill.md"]

    m = re.match(r"https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", u)
    if m:
        o, r = m.groups()
        return [f"https://raw.githubusercontent.com/{o}/{r}/{br}/{fn}"
                for br in ("main", "master") for fn in ("SKILL.md", "skill.md")]

    m = re.match(r"https?://gitee\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.+)$", u)
    if m:
        o, r, b, p = m.groups()
        return [f"https://gitee.com/{o}/{r}/raw/{b}/{p}"]

    m = re.match(r"https?://gitee\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", u)
    if m:
        o, r = m.groups()
        return [f"https://gitee.com/{o}/{r}/raw/{br}/SKILL.md" for br in ("main", "master")]

    return []


def _brief_of(url: str) -> str:
    """步骤行上显示的简短描述（主机 + 文件名），如 `github.com · SKILL.md`。"""
    p = urllib.parse.urlparse(url)
    name = p.path.rstrip("/").rsplit("/", 1)[-1] or p.netloc
    return f"{p.netloc} · {name}"


def fetch_skill_source(url: str, timeout: int = 20) -> dict:
    """抓取技能原文。返回 {"ok","text","brief","final_url","reason"}，永不抛异常。"""
    url = (url or "").strip()
    brief = _brief_of(url) if url else "技能地址"
    if not url:
        return {"ok": False, "text": "", "brief": brief, "final_url": "",
                "reason": "地址为空"}
    host = urllib.parse.urlparse(url).netloc
    if host not in ALLOWED_HOSTS:
        return {"ok": False, "text": "", "brief": brief, "final_url": "",
                "reason": f"只支持 GitHub / Gitee 的技能地址（当前是 {host or '无法解析的地址'}）"}

    cands = _candidates(url)
    if not cands:
        return {"ok": False, "text": "", "brief": brief, "final_url": "",
                "reason": "认不出这个地址指向哪份技能文件。支持：仓库里的文件页"
                          "（…/blob/分支/路径/SKILL.md）、raw 直链、仓库根目录"}

    last = ""
    for u in cands:
        try:
            req = urllib.request.Request(u, headers={"User-Agent": _UA})
            with _opener.open(req, timeout=timeout) as r:
                final_host = urllib.parse.urlparse(r.geturl()).netloc
                if final_host not in ALLOWED_HOSTS:
                    return {"ok": False, "text": "", "brief": brief, "final_url": u,
                            "reason": f"页面跳转到了白名单外的站点（{final_host}），已拒绝"}
                raw = r.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                return {"ok": False, "text": "", "brief": brief, "final_url": u,
                        "reason": f"文件超过 {MAX_BYTES // 1024}KB，不像一份技能文件"}
            text = raw.decode("utf-8", "replace").strip()
            if not text:
                last = "空文件"
                continue
            return {"ok": True, "text": text, "brief": brief, "final_url": u, "reason": ""}
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            continue                      # 404 时试下一个候选（分支/文件名猜测）
        except Exception as e:            # noqa: BLE001 —— 见模块 docstring：绝不外抛
            return {"ok": False, "text": "", "brief": brief, "final_url": u,
                    "reason": f"抓取失败：{type(e).__name__} {str(e)[:120]}"}
    return {"ok": False, "text": "", "brief": brief, "final_url": "",
            "reason": f"没找到技能文件（最后的尝试返回 {last or '失败'}）。"
                      f"如果技能不在仓库根目录，请发文件页的完整地址（含 /blob/）"}
