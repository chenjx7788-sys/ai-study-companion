"""浏览器扩展配对 token 的生成与校验（MVP · 2026-09-23）

为什么**不进 settings_store.FIELDS**（`data/llm_settings.json`）：
- FIELDS 里的键会随 `GET/PUT /api/settings` 整体读出/写回；
- 当前 CORS 是 `*`（main.py），任意网页都能调这个接口 ——
  token 进了 FIELDS 就等于「任意网页可读、可改」，配对机制形同虚设。
- 独立文件 `data/ext_token.txt` 不经过任何设置读写链路，唯一的出口是
  `GET /api/ext/token`（带 Sec-Fetch-Site 防护，见 routers/ext.py）。

⚠️ 这是 MVP 级别的纵深：真正的根治是 CORS/Origin 加固（方案文档 §8 已立项）。
"""
import hmac
import secrets
from pathlib import Path

from ..core.config import settings

TOKEN_PATH = settings.data_dir / "ext_token.txt"


def get_or_create() -> str:
    """读现有 token；不存在则生成 64 位 hex 并落盘（幂等）。"""
    try:
        t = TOKEN_PATH.read_text(encoding="utf-8").strip()
        if t:
            return t
    except OSError:
        pass
    t = secrets.token_hex(32)
    try:
        TOKEN_PATH.write_text(t, encoding="utf-8")
    except OSError:
        pass   # 写不进去就只用内存值（下次启动重新生成），不阻塞服务
    return t


def verify(candidate: str | None) -> bool:
    """常量时间比对；空值一律拒绝。"""
    if not candidate:
        return False
    return hmac.compare_digest(candidate.strip(), get_or_create())


def rotate() -> str:
    """无条件生成新 token 并落盘（旧 token 立即失效，已配对的扩展须重贴新值）。

    与 get_or_create 的区别：后者幂等（已有就返回旧的），本函数每次调用都换新。
    仅由主应用设置页的「重新生成」按钮触发（见 routers/ext.py 的 /token/rotate）。
    """
    t = secrets.token_hex(32)
    try:
        TOKEN_PATH.write_text(t, encoding="utf-8")
    except OSError:
        pass   # 写不进去时 get_or_create 仍会读到旧值 → 新 token 无效，但至少不崩
    return t
