"""自定义背景图素材服务（解压型皮肤 radio / night / ambient 共用）。

【为什么是「全局库」而不是「作品级单张」】
`bgm.py` 已经趟过这条路：用户会反复用同一张图（自己的书桌、喜欢的风景），
只给作品级单张的话，第二个作品要再传一次。所以这里与背景音乐同构 ——
**一份库 + 作品只存 id**，删作品不删素材（库与作品目录同级）。

【与形象上传（`videos/_avatars/`）的三处关键区别】
1. **必须有缩略图**。视觉方案面板一次要列出用户全部背景图，原图动辄 3–8MB，
   直接给原图会把面板拖成 MB 级流量。列表统一给缩略图。
2. **同一张图重复上传要幂等**。形象按时间戳生成 id，重复传两张一样的会堆成两条；
   背景按内容 md5 命中已有记录（与 `bgm.add_user_track` 同一策略）。
3. **尺寸下限放宽但不取消**。背景是 `background-size: cover` 铺满 1920×1080，
   短边不足会被放大到糊；但竖构图（手机照片）是合理用法，所以只卡短边、不卡长宽比。

⚠️⚠️ 缩略图是**派生资源**：判定同步一律用**指纹**（源文件 size + mtime_ns），
   不用「最后修改时间」；一致性手段是**重建**，不是删除（本项目强制纪律）。
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import time
from datetime import datetime
from pathlib import Path

from ..core.config import settings

# 上传上限。手机直出照片常见 3–8MB，10MB 留足余量。
BG_UPLOAD_MAX_MB = 10
# 短边下限：cover 铺满 1920×1080 时，短边低于 720 会被明显放大（1080/720 = 1.5×）。
BG_MIN_SHORT_SIDE = 720
# 缩略图宽度（列表里按卡片宽度显示，2 倍图足够）
THUMB_W = 480
THUMB_QUALITY = 82
# 一次最多列出多少张（只影响 UI 列表长度，不删文件）
MAX_BACKGROUNDS = 60

# 文件后缀统一 `.img`（与形象一致：落盘存原始字节，MIME 靠魔数嗅探）
_EXT = ".img"

# 列表取图入口。前端 `<img src>` 是**直连**（不走 axios 的 baseURL），
# 所以这里必须带全 `/api` 前缀。口径只有这一处。
BG_URL_PREFIX = "/api/videos/background"

# 上传背景 id 的形态：`u-<yyyyMMddHHMMSS>-<内容 md5 前 4 位>`。
# ⚠️ 与形象 id 的差别：末段允许十六进制（内容 md5），不是纯数字 —— 因为它是
#    **内容派生**的（同一张图重复上传幂等），而形象的末段只是时间戳的补充。
_BG_ID_RE = re.compile(r"^u-[0-9]{8,20}-[0-9a-f]{1,8}$")


def valid_id(bg_id: str) -> bool:
    """id 形态是否合法（**不查文件是否存在**）。

    调用方（路由）用它挡路径穿越：`../`、`:`（NTFS 的 ADS 分隔符）都不在字符集内。
    """
    return bool(_BG_ID_RE.match((bg_id or "").strip()))


# 内置背景：解压型皮肤各有一套**程序化/资产化**的默认画面（radio 无线稿底图、
# night 用 assets/night/、ambient 纯光晕），所以这里**故意为空**——
# 用户上传的背景是「叠加在默认画面之上」的替代项，不是并列的一档。
# 留成常量是为了让 `/options` 的 `builtin` 数组有稳定形状（前端不必判 None）。
BUILTIN_BACKGROUNDS: list[dict] = []


# ---------- 目录 ----------

def bg_root() -> Path:
    """背景库根目录。**与作品目录同级**（`videos/_backgrounds`），删作品不删素材。"""
    p = Path(settings.data_dir) / "videos" / "_backgrounds"
    p.mkdir(parents=True, exist_ok=True)
    return p


def user_dir() -> Path:
    p = bg_root() / "user"
    p.mkdir(parents=True, exist_ok=True)
    return p


def thumbs_dir() -> Path:
    p = bg_root() / "thumbs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _index_path() -> Path:
    return bg_root() / "index.json"


def bg_abspath(bg_id: str) -> Path | None:
    """id → 原图路径。非 `u-` 前缀一律 None（内置项没有文件）。"""
    if not bg_id or not bg_id.startswith("u-"):
        return None
    return user_dir() / f"{bg_id}{_EXT}"


def bg_thumb_abspath(bg_id: str) -> Path | None:
    if not bg_id or not bg_id.startswith("u-"):
        return None
    return thumbs_dir() / f"{bg_id}.jpg"


# ---------- index.json（带 mtime+size 指纹的读缓存） ----------

# 列表接口每条记录都要查 label / 缩略图 → 一次列表就是 N 次读盘。
# 键取 (mtime, size)：文件被外部工具就地改写时也能靠 size 变化感知。
_index_cache: tuple[tuple[float, int], list[dict]] | None = None


def _load_index() -> list[dict]:
    global _index_cache
    p = _index_path()
    if not p.exists():
        return []
    try:
        st = p.stat()
        stamp = (st.st_mtime, st.st_size)
        if _index_cache is not None and _index_cache[0] == stamp:
            return [dict(r) for r in _index_cache[1]]   # 给副本，调用方改动不污染缓存
        data = json.loads(p.read_text(encoding="utf-8"))
        rows = [d for d in data if isinstance(d, dict) and d.get("id")]
    except Exception:
        return []
    _index_cache = (stamp, rows)
    return [dict(r) for r in rows]


def _save_index(rows: list[dict]) -> None:
    global _index_cache
    try:
        _index_path().write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        _index_cache = None     # 写盘后缓存作废（mtime 粒度可能不足一秒）
    except Exception:
        pass


# ---------- 图片校验与派生 ----------

def probe_image(data: bytes) -> tuple:
    """返回 (w, h)；不是可解码的图片就抛 ValueError（带可读原因）。"""
    if not data:
        raise ValueError("文件内容为空")
    try:
        from PIL import Image
    except BaseException:
        raise ValueError("本机缺少图片处理组件，暂时无法校验这张图")
    try:
        with Image.open(io.BytesIO(data)) as im:
            w, h = im.size
            im.verify()          # 截断/损坏的文件在这里暴露（只 open 不 verify 会漏）
    except BaseException:
        raise ValueError("无法识别这张图片，请换一张试试（支持 JPG / PNG / WebP）")
    if not w or not h or w <= 0 or h <= 0:
        raise ValueError("图片尺寸异常")
    return int(w), int(h)


def _write_thumb(bg_id: str, raw: bytes) -> bool:
    """生成并落盘缩略图。失败**不抛错**：没有缩略图只是列表退化用原图，不是错误。"""
    out = bg_thumb_abspath(bg_id)
    if out is None:
        return False
    try:
        from PIL import Image
        with Image.open(io.BytesIO(raw)) as im:
            im = im.convert("RGB")
            w, h = im.size
            if w > THUMB_W:
                im = im.resize((THUMB_W, max(1, round(h * THUMB_W / w))), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=THUMB_QUALITY, optimize=True)
        out.write_bytes(buf.getvalue())
        return True
    except BaseException:
        try:
            if out.exists():
                out.unlink()
        except BaseException:
            pass
        return False


def _thumb_stamp(bg_id: str) -> str:
    """缩略图指纹：源文件 size + mtime_ns。

    ⚠️ 判定「缩略图是否还对应这张原图」必须用**指纹**，不能用「谁比谁新」——
       原图被外部工具就地改写（同名、内容变了）时 mtime 可能不变或倒退。
    ⚠️ URL 带上它做版本号：换图后浏览器不会拿旧缓存（与形象缩略图同一手法）。
    """
    p = bg_abspath(bg_id)
    if p is None:
        return "0"
    try:
        st = p.stat()
        return hashlib.md5(f"{st.st_size}:{st.st_mtime_ns}".encode()).hexdigest()[:10]
    except OSError:
        return "0"


def url_of(bg_id: str, *, full: bool = False) -> str:
    """取图地址；缩略图缺失时**回落原图**（不让列表出现破图框）。"""
    if not bg_id:
        return ""
    if not full and (bg_thumb_abspath(bg_id) or Path()).exists():
        return f"{BG_URL_PREFIX}/{bg_id}/thumb?v={_thumb_stamp(bg_id)}"
    return f"{BG_URL_PREFIX}/{bg_id}/file?v={_thumb_stamp(bg_id)}"


# ---------- 上传 / 删除 ----------

def bg_label(bg_id: str) -> str:
    """展示名：从 id 里解出可读时间。

    ⚠️ 不用 id 本身（`u-20261012134501-ab12`）当展示名，也不统一叫「已上传背景」——
       多张图会变成一串同名项，用户根本分不清哪个是哪个（与形象 label 同一理由）。
    """
    m = re.match(r"^u-(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})", bg_id or "")
    if m:
        return f"已上传 · {m.group(2)}-{m.group(3)} {m.group(4)}:{m.group(5)}"
    return "已上传背景"


def add_user_background(filename: str, data: bytes) -> dict:
    """上传背景图：校验 → 落盘 → 生成缩略图 → 写索引。

    ⚠️ **幂等**：同一张图（内容 md5 命中已有记录且文件仍在）直接返回原记录，
       不重复落盘 —— 否则用户「换一张试试」试到同一张时会在列表里堆出多条同名项。
    """
    size_mb = len(data) / 1024 / 1024
    if size_mb > BG_UPLOAD_MAX_MB:
        raise ValueError(f"图片过大（{size_mb:.1f}MB），请控制在 {BG_UPLOAD_MAX_MB}MB 以内")
    w, h = probe_image(data)
    if min(w, h) < BG_MIN_SHORT_SIDE:
        raise ValueError(
            f"图片太小（{w}×{h}），短边至少 {BG_MIN_SHORT_SIDE}px —— "
            "背景会被铺满整屏，太小的图会被放大到糊")

    digest = hashlib.md5(data).hexdigest()
    rows = _load_index()
    for r in rows:
        if r.get("hash") == digest:
            p = bg_abspath(r["id"])
            if p is not None and p.exists():
                return dict(r)      # 同一张图，直接复用

    bid = f"u-{datetime.now().strftime('%Y%m%d%H%M%S')}-{digest[:4]}"
    target = user_dir() / f"{bid}{_EXT}"
    target.write_bytes(data)
    # 缩略图失败**不当作上传失败**（`url_of` 会自动回落原图），但记录里如实标明，
    # 免得以后有人以为「这里一定有缩略图」。
    _write_thumb(bid, data)
    display = Path(filename or "未命名").stem.strip() or "未命名"
    entry = {"id": bid, "name": display, "hash": digest, "w": w, "h": h,
             "bytes": len(data), "thumb": bool((bg_thumb_abspath(bid) or Path()).exists()),
             "added_at": datetime.now().isoformat(timespec="seconds")}
    rows = [r for r in rows if r.get("id") != bid]
    rows.insert(0, entry)
    _save_index(rows)
    return entry


def delete_user_background(bg_id: str) -> str:
    """删除一个用户上传的背景，返回四态字符串（绝不抛错）。

      "deleted"  文件确实已不存在（本次删掉，或索引里有记录但文件早被手工删了 —— 幂等）
      "invalid"  id 不是上传背景，**或**库与磁盘上都找不到它（真的不存在 → 调用方 404）
      "failed"   文件此刻仍然存在 → 没删掉（被占用 / 护栏拦截 / 权限）

    ⚠️ 判据是「文件是否真的不存在」，**不是「有没有抛异常」**：
       本机删除走 safe-delete 护栏（单 turn 累计删除数超阈值会抛 SystemExit），
       `except Exception` 接不住它；一旦吞掉它又按「没抛异常」判成功，
       就会得到「接口报成功、文件还在」的假删除。
    ⚠️ 返回值以**索引记录 / 文件事实**为准（与 `bgm.delete_user_track` 同口径）：
       文件可能早已被用户手工删掉，此时仍应清掉索引记录并返回成功。
    """
    b = (bg_id or "").strip()
    if not _BG_ID_RE.match(b):
        return "invalid"
    root = user_dir()
    try:
        p = (root / f"{b}{_EXT}").resolve()
        if p.parent != root.resolve():
            return "invalid"
    except BaseException:
        return "invalid"

    rows = _load_index()
    existed = any(r.get("id") == b for r in rows)
    try:
        on_disk = p.exists()
    except BaseException:
        on_disk = False
    # 索引里没有、磁盘上也没有 → 从来没存在过（不是「删除成功」）
    if not existed and not on_disk:
        return "invalid"

    # ⚠️ Windows 现实：文件正被读着时 unlink 会失败 —— 页面刚请求过这张背景的缩略图
    #    （uvicorn 的 FileResponse 刚打开过它），此时点删除会抛 PermissionError。
    #    与 delete_avatar 同一套**有界短重试**（最多 6 次、累计约 2.25s）。
    targets = [p, thumbs_dir() / f"{b}.jpg"]
    for _i in range(6):
        for t in targets:
            try:
                if t.is_file():
                    t.unlink()
            except BaseException:
                pass                  # ← 护栏 SystemExit / 文件占用都在这里，不外抛
        try:
            if not p.exists():
                break
        except BaseException:
            break
        if _i < 5:
            time.sleep(0.15 * (_i + 1))
    try:
        if p.exists():
            return "failed"
    except BaseException:
        return "failed"

    if existed:
        _save_index([r for r in rows if r.get("id") != b])
    return "deleted"


# ---------- 对外接口 ----------

def _sweep_orphan_thumbs() -> None:
    """清掉「源图已不在」的孤儿缩略图（派生资源不该留在盘上）。

    ⚠️ 为什么必须有这一步：缩略图是**派生缓存**，而删除原图时它可能正被读着
       （Windows 禁删已打开的文件，实测：同一个删除请求隔 2 秒重发才成功）——
       那一次删不掉，就永久留在盘上了。`list_backgrounds` 是天然的收口点
       （它本来就要遍历用户目录，多一次 thumbs/ 的 glob 可以忽略）。
    ⚠️ 只动 `thumbs/` 下且**无同名源图**的文件；源图仍在的一律不碰。
    ⚠️ 全程 `except BaseException`：safe-delete 护栏抛的是 SystemExit，
       `except Exception` 接不住，一旦外抛会波及 uvicorn 请求线程。
    """
    try:
        for f in thumbs_dir().glob("*.jpg"):
            src = user_dir() / f"{f.stem}{_EXT}"
            try:
                if not src.exists() and f.is_file():
                    f.unlink()
            except BaseException:
                pass
    except BaseException:
        pass


def list_backgrounds() -> dict:
    """可选背景 = 内置（当前为空）+ 用户已上传的。

    ⚠️ 上传过的必须能被枚举出来：否则「上传」退化成一次性动作 ——
       传完刷新页面就再也选不到（作品还引用着，界面里却找不到），
       用户只会以为上传没生效，然后反复重传（与 `list_avatars` 同一教训）。
    """
    _sweep_orphan_thumbs()
    builtin = [{**it, "kind": "builtin", "builtin": True, "url": it.get("url") or ""}
               for it in BUILTIN_BACKGROUNDS]
    mine: list[dict] = []
    try:
        files = [f for f in user_dir().iterdir()
                 if f.is_file() and f.suffix.lower() == _EXT]
        files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        known = {r["id"]: r for r in _load_index() if r.get("id")}
        for f in files[:MAX_BACKGROUNDS]:
            bid = f.stem
            r = known.get(bid) or {}
            mine.append({"id": bid, "kind": "user", "builtin": False,
                         "label": bg_label(bid),
                         "name": r.get("name") or "",
                         "w": int(r.get("w") or 0), "h": int(r.get("h") or 0),
                         "bytes": int(r.get("bytes") or 0),
                         "added_at": r.get("added_at") or "",
                         "exists": True, "url": url_of(bid)})
    except BaseException:
        pass
    return {"builtin": builtin, "mine": mine}


def exists(bg_id: str) -> bool:
    if not bg_id:
        return False
    p = bg_abspath(bg_id)
    return bool(p and p.exists())


def resolve(bg_id: str) -> Path | None:
    """id → 原图路径；**不可用时返回 None**（渲染侧据此回落皮肤自带画面）。

    ⚠️ 这里**绝不抛错**：背景只是一个可选修饰，图丢了不该让整条成片渲染不出来。
       与 `bgm.resolve`（音轨必须存在，缺失就报错）的容忍度不同 ——
       没有背景音乐的播客是残次品，没有自定义背景的画面对用户毫无损失。
    """
    p = bg_abspath(bg_id)
    if p is None:
        return None
    try:
        return p if p.is_file() else None
    except BaseException:
        return None


def bg_info(bg_id: str) -> dict:
    """背景信息（前端回填 + 「已被删除」标注用）。**绝不静默改作品。**

    ⚠️⚠️ 查不到时要返回 `exists=False` 的**占位**，而不是 None / 空串：
       界面必须能区分「没选背景」与「选的背景已被删掉」。后者若不显示出来，
       用户会以为这个作品从来没选过背景，随手选一张新图就**悄悄改了作品的画面**，
       而他对这条作品并没有做任何操作（本项目明令禁止的静默改作品）。
    """
    b = (bg_id or "").strip()
    if not valid_id(b):
        return {"id": b, "label": b or "未选择", "exists": False, "url": ""}
    ok = exists(b)
    return {"id": b, "label": bg_label(b), "exists": ok, "url": url_of(b) if ok else ""}
