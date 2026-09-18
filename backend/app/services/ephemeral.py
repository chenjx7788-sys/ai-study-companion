"""「仅本次阅读」内存快照（P0-5）

设计要点（方案 §5.1 / §5.3）：

1. **一个字不落盘**：正文只存在进程内存，应用退出即消失。
   不写 `files/`、不写 `materials`/`material_chunks`、**不写 Chroma**
   （`vector.py` 会把明文原样存进 `documents` —— 向量化 = 落库）。
2. **LRU 上限 20 篇 / 单篇 200KB**（最坏约 4MB 内存）。
3. **切块与落库路径同源**：复用 `parser.split_markdown`，绝不另写一套。
   两条路径若各自切块，同一篇文章在「仅阅读」和「入库」下段数不同 → 用户当成 bug。
4. **绝不打日志正文**：正文进内存就意味着它可能进日志。
   本模块所有异常信息都只带"长度 / key"，不拼接正文（沿用 `llm_usage` 只记 token 不记内容的先例）。

⚠️ 语义诚实：这是**本次使用期间的快照，不是历史记录**。关闭应用即清空。
"""

import base64
import threading
import time
from collections import OrderedDict

from . import parser as parser_svc

# ---------- 容量口径 ----------

MAX_ITEMS = 20                      # LRU 上限：最多保留 20 篇
MAX_BYTES = 200 * 1024              # 单篇正文上限（UTF-8 字节，约 6 万汉字）


# ---------- 存储 ----------
#
# ⚠️ key 不用裸 URL：`GET /recent/{key}` 的路径参数**不能带斜杠**（URL 里全是斜杠）。
# 用 URL 的 base64url 编码做 key —— 字符集只有 A-Za-z0-9-_，可逆、稳定、可调试。
# 记录内部仍保留原始 `url`，前端展示与"已沉淀"判定都用它（稳定键）。

_recent: "OrderedDict[str, dict]" = OrderedDict()
_lock = threading.Lock()


def make_key(url: str) -> str:
    """原始 URL → 路由安全 key（base64url，去 padding）"""
    return base64.urlsafe_b64encode((url or "").encode("utf-8")).decode("ascii").rstrip("=")


def key_to_url(key: str) -> str:
    """key → 原始 URL（补齐 padding；解不出就返回空串，不抛）"""
    try:
        pad = "=" * (-len(key) % 4)
        return base64.urlsafe_b64decode((key + pad).encode("ascii")).decode("utf-8")
    except Exception:
        return ""


def _truncate_bytes(text: str, limit: int) -> tuple[str, bool]:
    """按 UTF-8 字节数截断，且不切断多字节字符（不产生乱码）。

    返回 (文本, 是否被截断)。字数不超限时原样返回 —— 对正常正文零行为变化。
    """
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text, False
    # 先按字节切，再把结尾不完整的多字节序列丢掉（errors="ignore" 正好做这件事）
    cut = raw[:limit].decode("utf-8", errors="ignore")
    return cut, True


# ---------- 写入 / 读取 ----------

def put(url: str, title: str, text: str, kind: str = "url",
        meta: dict | None = None, kind_label: str = "", input_url: str = "") -> dict:
    """存入/更新一篇临时阅读内容，返回记录（含 chunks）。

    - **同 URL 命中即更新**（不是新增一条）：重新抓取会拿到更新后的正文，
      若按新增处理，列表里会出现两条同标题、正文还不同 —— 用户会当成 bug。
    - 更新时保留原 `created_at`，只刷新 `updated_at`，列表顺序按"最近访问"走 LRU。
    - 超长正文按字节截断并标记 `truncated`（仍可阅读/可 AI，只是不完整）。
    - `input_url` 是**用户原始输入**（可能含 xsec_token 这类访问凭证），`url` 是归一化身份。
      小红书剥掉 token 就打不开 → 「最近阅读 → 加入知识库」必须靠它，故随快照一起存。
    """
    url = (url or "").strip()
    if not url:
        raise ValueError("url 为空")
    text = text or ""
    text, truncated = _truncate_bytes(text, MAX_BYTES)
    # 切块与落库路径共用同一函数（见模块 docstring 第 3 条）
    chunks = parser_svc.split_markdown(text)
    key = make_key(url)
    now = time.time()

    with _lock:
        prev = _recent.get(key)
        rec = {
            "key": key,
            "url": url,
            # 原始输入（含凭证）与归一化身份分开存：前者用于**再抓取**，后者用于**判重**
            "input_url": (input_url or url).strip(),
            "title": (title or "").strip() or "未命名网页",
            "kind": kind or "url",
            "kind_label": kind_label or "",
            "text": text,
            "chunks": chunks,
            "meta": dict(meta or {}),
            "chars": len(text),
            "bytes": len(text.encode("utf-8")),
            "chunk_count": len(chunks),
            "truncated": truncated,
            "created_at": (prev or {}).get("created_at") or now,
            "updated_at": now,
        }
        _recent[key] = rec
        _recent.move_to_end(key)        # 刚访问 → 最新
        _evict_locked()
        return rec


def get(key: str) -> dict | None:
    """取回单篇（含正文与 chunks），并把它移到"最新"（访问即刷新 LRU）。

    未命中返回 None —— 调用方转 404。**不抛异常**（前端可能是刷新后点了旧 key）。
    """
    with _lock:
        rec = _recent.get(key)
        if rec is None:
            return None
        _recent.move_to_end(key)
        return rec


def has(key: str) -> bool:
    with _lock:
        return key in _recent


def images_of(rec: dict) -> int:
    """一篇记录里的图片张数（`meta.images`，缺省 0）。

    ⚠️ **列表与单篇必须共用这一处口径**：两处各写一份表达式的话，将来改口径必然漏一处
    （本项目反复踩过「同一口径写两份」）。任何要展示"N 张图"的地方都调这里。
    """
    return int((rec.get("meta") or {}).get("images") or 0)


def wx_image_post_of(rec: dict) -> bool:
    """是否「图片消息」（公众号 `item_show_type=8`）。

    这类文章正文以图为主、文字天然很少（实测 8 张图 + 171 字说明），
    与 `images_of` 一样：**口径只在这里定义一处**，列表/单篇都调它。
    """
    return bool((rec.get("meta") or {}).get("wx_image_post"))


def xhs_video_note_of(rec: dict) -> bool:
    """是否「小红书视频笔记」（`meta.xhs_note_type == "video"`）。

    视频笔记抓到的是**配文 + 封面**：视频本体连 URL 都没取过（见 `external._xhs_extract`），
    所以「只有封面」是必然结果、不是抓漏 —— 但**必须标出来**，否则用户只能自己猜。
    ⚠️ 与 `wx_image_post_of` 同一条纪律：**口径只在这里定义一处**，
    列表（`list_recent`）与单篇（`routers.ephemeral.get_recent`）都调它。
    """
    return bool((rec.get("meta") or {}).get("xhs_video_note"))


def list_recent() -> list[dict]:
    """列表（**不含正文**）：只给标题 / 来源 / 时间 / 字数，供侧栏抽屉展示。

    ⚠️ 不下发正文是刻意的：列表接口若带上正文，一次请求会把 20 篇全文搬给前端，
    既浪费又让"仅本次阅读、用完即弃"的语义变得可疑。要正文请走 `get(key)`。
    """
    with _lock:
        items = list(_recent.values())
    out = []
    for r in reversed(items):           # 最新在前
        out.append({
            "key": r["key"],
            "url": r["url"],
            # 列表也要带原始输入：从「最近阅读」点进详情页后，「加入知识库」用的是它
            "input_url": r.get("input_url") or r["url"],
            "title": r["title"],
            "kind": r["kind"],
            "kind_label": r["kind_label"],
            "chars": r["chars"],
            "chunk_count": r["chunk_count"],
            "truncated": r["truncated"],
            # 图片张数：列表也按同一口径给出，避免"字数少"被误读成抓漏了
            "images": images_of(r),
            # ⚠️ 这两个标记以前**只有单篇路由发、列表不发** → 抽屉里看不到「为什么字这么少」。
            #    同一标记的列表 / 单条必须成对，口径都取上面两个 *_of() 函数。
            "wx_image_post": wx_image_post_of(r),
            "xhs_video_note": xhs_video_note_of(r),
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        })
    return out


def remove(key: str) -> bool:
    """单篇移除。返回是否真的删掉了。

    ⚠️ 只从内存字典弹出，不触发任何文件/DB/Chroma 操作 —— 但环境注入的
    safe-delete shim 仍可能拦到"删除"语义，因此这里不涉及文件系统，天然安全。
    """
    with _lock:
        return _recent.pop(key, None) is not None


def clear() -> int:
    """清空全部快照（供测试与"手动清空"用），返回清掉的条数。"""
    with _lock:
        n = len(_recent)
        _recent.clear()
        return n


def count() -> int:
    with _lock:
        return len(_recent)


def total_bytes() -> int:
    with _lock:
        return sum(r.get("bytes", 0) for r in _recent.values())


def _evict_locked() -> None:
    """LRU 淘汰：超出 20 篇就丢最旧的（调用方必须已持锁）"""
    while len(_recent) > MAX_ITEMS:
        _recent.popitem(last=False)


def stats() -> dict:
    """内部/测试用：当前条数、总字节、上限。不含任何正文。"""
    with _lock:
        return {
            "count": len(_recent),
            "total_bytes": sum(r.get("bytes", 0) for r in _recent.values()),
            "max_items": MAX_ITEMS,
            "max_bytes": MAX_BYTES,
        }
