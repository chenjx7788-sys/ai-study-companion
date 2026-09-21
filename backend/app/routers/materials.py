"""材料管理路由（PRD 模块 A）：上传 / 列表 / 详情 / 删除 / 重试解析

解析状态机：parsing → success / failed / scanned（无文本层的扫描件）
"""
import posixpath
import re
import shutil
import threading
from pathlib import Path
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.filename import safe_stem
from ..database import get_db, SessionLocal
from ..models import Material, MaterialChunk, AIAsset, Note, Highlight, Folder
from ..services import parser as parser_svc
from ..services import external as external_svc

router = APIRouter(prefix="/materials", tags=["materials"])

ALLOWED_FORMATS = {"pdf", "ppt", "pptx", "doc", "docx", "md", "markdown", "epub",
                   "mp3", "wav", "m4a", "mp4",
                   "jpg", "jpeg", "png", "webp", "bmp"}

# 支持列表文案：多处提示共用，避免改格式时漏改某一处
FORMAT_HINT = "PDF/PPT/Word/Markdown/EPUB/图片/音视频"


# ---------- 命名与路径（单一来源） ----------
# 标题有三个来源：网页 <title>（剪藏）/ 用户输入（新建、重命名）/ 上传文件名。
# 每处各写一遍清洗逻辑必然漂移，而漏掉任一处就在 Windows 上以
# `OSError [Errno 22] Invalid argument` 收场（实测：标题含英文双引号即 500）。
# 因此「标题去重 + 文件名安全化 + 文件名去重」统一收在这里。


def _title_taken(db: Session, title: str, exclude_id: int | None = None) -> bool:
    """DB 里是否已有同名材料（`exclude_id` 供「改标题」排除自身）"""
    q = db.query(Material).filter(Material.title == title)
    if exclude_id is not None:
        q = q.filter(Material.id != exclude_id)
    return q.first() is not None


def _unique_title(db: Session, title: str, exclude_id: int | None = None) -> str:
    """标题去重：撞车则按统一规则追加 `(n)`。

    用于**不落盘**的场景（引用模式导入）：只保证 DB 标题唯一，不碰文件名 ——
    也正因如此，它不该去看 files_dir（否则引用导入的标题会随磁盘状态漂移）。
    """
    base, n = title, 1
    while _title_taken(db, title, exclude_id):
        n += 1
        title = f"{base}({n})"
    return title


def _unique_title_and_path(db: Session, title: str, ext: str,
                           exclude_id: int | None = None) -> tuple[str, Path]:
    """**写文件的地方一律用这个**：标题 + 落盘路径同时去重后的 `(title, path)`。

    ⚠️ 两者必须一起判：`safe_stem` 会把 `a/b`、`a\b`、`a:b` 折叠成同一个 `a_b`，
    只比 title 就会让三个材料指向**同一个文件**、后者静默覆盖前者。
    """
    base, n = title, 1
    while True:
        stem = safe_stem(title)
        path = settings.files_dir / (f"{stem}.{ext}" if ext else stem)
        if not _title_taken(db, title, exclude_id) and not path.exists():
            return title, path
        n += 1
        title = f"{base}({n})"


# ---------- 序列化 ----------

def material_to_dict(m: Material, db: Session) -> dict:
    note_count = db.query(Note).filter(Note.material_id == m.id).count()
    has_ai = db.query(AIAsset).filter(AIAsset.material_id == m.id).first() is not None
    return {
        "id": m.id,
        "title": m.title,
        "format": m.format,
        "storage_mode": m.storage_mode or "copy",
        "folder_id": m.folder_id,
        # 来源渠道：列表/详情页据此显示徽章（本地上传/本地导入/网页剪藏/公众号/微信读书）。
        # 缺省回退 upload，保证老库（迁移前建的记录）也不会出现 None 触发前端报错。
        "origin": m.origin or "upload",
        "origin_ref": m.origin_ref or "",
        "origin_meta": m.origin_meta or {},
        "parsed_status": m.parsed_status,
        "parse_progress": m.parse_progress or 0,
        "parse_error": m.parse_error,
        "page_count": m.page_count,
        "tags": m.tags or [],
        "last_read_page": m.last_read_page or 0,
        "note_count": note_count,
        "has_ai": has_ai,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


# ---------- 解析任务（后台线程） ----------

def _parse_material(material_id: int):
    """后台解析：提取文本块 → 写入 MaterialChunk → 更新状态。

    - 解析出空文本：标记 scanned（扫描件，AI 功能不可用）
    - 旧版 .doc/.ppt 或加密/损坏文件：标记 failed 并记录原因
    """
    db = SessionLocal()
    try:
        m = db.get(Material, material_id)
        if not m:
            return
        last_pct = [0]
        def _progress(pct):
            if pct - last_pct[0] >= 5:
                last_pct[0] = pct
                m.parse_progress = pct
                db.commit()
        try:
            if m.format in parser_svc.MEDIA_FORMATS:
                m.parse_progress = 1
                db.commit()
                chunks = parser_svc.parse_media(m.file_path, on_progress=_progress)
            elif m.format in parser_svc.SLOW_FORMATS:   # PDF / 图片：OCR 慢，走进度
                m.parse_progress = 1
                db.commit()
                chunks = parser_svc.parse_file(m.file_path, m.format, on_progress=_progress)
            else:
                chunks = parser_svc.parse_file(m.file_path, m.format)
        except Exception as e:
            msg = str(e)
            if m.format in ("doc", "ppt"):
                msg = f"旧版 .{m.format} 格式暂无法直接解析，建议另存为 .{m.format}x 后重新上传"
            m.parsed_status = "failed"
            m.parse_error = msg[:250]
            db.commit()
            return

        # 去噪管道：页眉页脚/水印/乱码/重复（清洗失败不阻断解析，用原文兜底）
        raw_had_text = bool(chunks)
        try:
            from ..services import cleaner
            chunks, report = cleaner.clean_chunks(chunks, m.format)
            if any(report.values()):
                print(f"[cleaner] 材料 {m.id} 清洗: {report}")
        except Exception as e:
            print(f"[cleaner] 清洗异常，使用原文: {e}")

        # 清掉旧块（重试场景）
        db.query(MaterialChunk).filter(MaterialChunk.material_id == m.id).delete()

        if not chunks:
            m.parsed_status = "scanned"
            if m.format in parser_svc.MEDIA_FORMATS:
                m.parse_error = "未识别到语音内容（可能是纯音乐/静音文件）"
            elif raw_had_text:
                m.parse_error = "文本在去噪后为空（可能整篇均为页眉/水印类内容；可在设置页关闭去噪规则后点「重试解析」）"
            else:
                if m.format in parser_svc.IMAGE_FORMATS or m.format == "pdf":
                    m.parse_error = "OCR 未识别到文字（图片可能模糊/空白，或为手写内容）"
                elif m.format == "epub":
                    m.parse_error = ("未提取到文本：该 EPUB 正文可能是纯图片（漫画 / 影印版），"
                                     "可在「原文视图」翻看原书页面")
                else:
                    m.parse_error = "未提取到文本，可能是扫描件"
            db.commit()
            return

        for c in chunks:
            db.add(MaterialChunk(
                material_id=m.id,
                content=c["content"],
                page_no=c["page_no"],
                section_path=c.get("section_path", ""),
            ))
        m.page_count = max(c["page_no"] for c in chunks)
        db.commit()   # 先落 chunks，供索引查询

        # D1 自动归档：先建索引、再标记成功。
        # 若「成功」信号先于索引完成，用户可能在索引写入前删除材料，
        # 删除逻辑清理不到尚未入库的向量，随后索引才写入 → 形成孤儿向量。
        try:
            from ..services import kb_index
            if db.get(Material, m.id):   # 解析期间材料可能已被删除
                kb_index.index_material(db, m.id)
        except Exception:
            pass

        # 索引完成后才标记成功（重新取，避免索引内部 commit 后 m 失联）
        m = db.get(Material, m.id)
        if m is None:
            return   # 材料已在解析期间被删除，不再标记
        m.parsed_status = "success"
        m.parse_error = None
        m.parse_progress = 100
        db.commit()
    finally:
        db.close()


# ---------- 接口 ----------

@router.post("")
async def upload_material(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """A1 上传：格式/大小校验 → 落盘（同名自动加后缀）→ 建档 → 后台解析"""
    filename = file.filename or "未命名"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_FORMATS:
        raise HTTPException(400, f"暂支持 {FORMAT_HINT} 格式")

    content = await file.read()
    if len(content) > settings.max_file_mb * 1024 * 1024:
        raise HTTPException(400, f"文件超过 {settings.max_file_mb}MB，请压缩后上传")

    # 同名处理：标题与文件名都追加 (n)；文件名另需安全化（上传文件名也可能带非法字符）
    title, save_path = _unique_title_and_path(db, filename.rsplit(".", 1)[0], ext)
    save_path.write_bytes(content)

    m = Material(title=title, format=ext, file_path=str(save_path), parsed_status="parsing")
    db.add(m)
    db.commit()
    db.refresh(m)

    threading.Thread(target=_parse_material, args=(m.id,), daemon=True).start()
    return material_to_dict(m, db)


class DocumentCreateReq(BaseModel):
    title: str
    content: str


@router.post("/document")
def create_document(req: DocumentCreateReq, db: Session = Depends(get_db)):
    """新增文档：富文本内容存为 Markdown 材料，复用上传的解析/索引链路"""
    title = (req.title or "").strip()
    content = (req.content or "").strip()
    if not title:
        raise HTTPException(400, "请填写文档标题")
    if len(content) < 2:
        raise HTTPException(400, "正文内容过短（少于 2 字）")
    if len(content) > 200000:
        raise HTTPException(400, "正文过长，请拆分后保存")
    # 同名处理（与 upload_material 一致）+ 文件名安全化
    # 顺带修掉一个隐性上限：原实现完全没有长度约束，超长标题会撑爆文件名而 500
    title, save_path = _unique_title_and_path(db, title, "md")
    save_path.write_text(content, encoding="utf-8")

    m = Material(title=title, format="md", file_path=str(save_path), parsed_status="parsing")
    db.add(m)
    db.commit()
    db.refresh(m)
    threading.Thread(target=_parse_material, args=(m.id,), daemon=True).start()
    return material_to_dict(m, db)


class ImportLocalReq(BaseModel):
    paths: list[str]
    mode: str = "reference"   # reference 引用原文件 / copy 复制副本


def _import_single_file(db, p: Path, mode: str, folder_id: int | None):
    """导入单个文件，返回 (material_dict, None) 或 (None, fail_dict)"""
    ext = p.suffix.lower().lstrip(".")
    save_path = None
    try:
        if mode == "copy":
            if p.stat().st_size > settings.max_file_mb * 1024 * 1024:
                return None, {"path": str(p), "reason": f"超过 {settings.max_file_mb}MB"}
            # 标题去重 + 文件名安全化（本地文件名同样可能来自别的系统，带非法字符）
            title, save_path = _unique_title_and_path(db, p.stem, ext)
            shutil.copy2(p, save_path)
            storage, file_path = "copy", str(save_path)
        else:
            # 引用模式不落盘 → 只需标题去重（不占文件名，也不必看 files_dir 是否同名）
            title = _unique_title(db, p.stem)
            storage, file_path = "reference", str(p)   # 不复制，直接引用原路径

        m = Material(title=title, format=ext, file_path=file_path,
                     storage_mode=storage, folder_id=folder_id, parsed_status="parsing",
                     # 来源渠道与 storage_mode 对齐：引用原文件 = local（原文件在用户磁盘上），
                     # 复制副本 = upload（已进入本应用文件库）。这样「按来源筛选」时两种入口可分。
                     origin="local" if storage == "reference" else "upload",
                     origin_ref=str(p) if storage == "reference" else "")
        db.add(m)
        db.commit()
        db.refresh(m)
        threading.Thread(target=_parse_material, args=(m.id,), daemon=True).start()
        return material_to_dict(m, db), None
    except Exception as e:
        db.rollback()
        # copy 模式已复制副本但建档失败 → 清理孤儿副本，避免 files_dir 残留无记录文件
        if save_path is not None and Path(save_path).exists():
            # 同上：删不掉（含被 safe-delete 拦下的 SystemExit）也不能把原来的异常盖成 500
            _unlink_in_files_dir(save_path)
        return None, {"path": str(p), "reason": str(e)[:200]}


def _import_directory(db, dir_path: Path, parent_folder_id: int | None, mode: str, counter: list):
    """递归导入目录：建 Folder（保留层级）→ 导入文件 → 递归子目录，返回 (created, failed)"""
    folder = Folder(name=dir_path.name, parent_id=parent_folder_id)
    db.add(folder)
    db.commit()
    db.refresh(folder)

    created, failed = [], []
    try:
        children = sorted(dir_path.iterdir(), key=lambda c: c.name)
    except OSError:
        children = []
    for child in children:
        if counter[0] >= 500:   # 全局文件数量上限，防误扫整盘
            break
        if child.name.startswith("."):
            continue   # 跳过隐藏文件/隐藏目录
        if child.is_dir():
            c, f = _import_directory(db, child, folder.id, mode, counter)
            created += c
            failed += f
        elif child.is_file() and child.suffix.lower().lstrip(".") in ALLOWED_FORMATS:
            counter[0] += 1
            md, fail = _import_single_file(db, child, mode, folder.id)
            if md is not None:
                created.append(md)
            else:
                failed.append(fail)
    return created, failed


def _clean_path(s: str) -> str:
    """清理用户粘贴的路径：去空白 + 剥掉两端成对引号（Windows 资源管理器复制路径自带 ""）

    Windows 文件名不允许包含引号，剥两端成对引号安全无副作用。
    """
    t = (s or "").strip()
    while len(t) >= 2 and t[0] in "\"'" and t[-1] == t[0]:
        t = t[1:-1].strip()
    return t


@router.post("/import-local")
def import_local(req: ImportLocalReq, db: Session = Depends(get_db)):
    """导入本地文件/文件夹：目录保留层级（建 Folder 树），文件直接挂当前文件夹"""
    if req.mode not in ("reference", "copy"):
        raise HTTPException(400, "mode 仅支持 reference / copy")

    created, failed = [], []
    counter = [0]
    found = False
    for s in req.paths:
        p = Path(_clean_path(s))
        if not p.exists():
            continue
        if p.is_dir():
            found = True
            c, f = _import_directory(db, p, None, req.mode, counter)
            created += c
            failed += f
        elif p.is_file() and p.suffix.lower().lstrip(".") in ALLOWED_FORMATS:
            found = True
            counter[0] += 1
            md, fail = _import_single_file(db, p, req.mode, None)
            if md is not None:
                created.append(md)
            else:
                failed.append(fail)

    if not found:
        raise HTTPException(400, f"未找到支持格式的文件（{FORMAT_HINT}）")
    return {"created": created, "failed": failed}


@router.get("")
def list_materials(search: str | None = None, db: Session = Depends(get_db)):
    """A2 列表：默认按导入时间倒序，支持标题模糊搜索"""
    q = db.query(Material)
    if search:
        q = q.filter(Material.title.contains(search))
    materials = q.order_by(Material.created_at.desc()).all()
    return [material_to_dict(m, db) for m in materials]


@router.get("/search/fulltext")
def fulltext_search(q: str, folder_id: int | None = None, db: Session = Depends(get_db)):
    """全文搜索：在所有材料原文块中查找关键词。folder_id 指定时限定该文件夹（含子文件夹）。"""
    q = q.strip()
    if not q:
        return []
    query = db.query(MaterialChunk)
    if folder_id is not None:
        # 收集当前文件夹 + 全部子文件夹 id，再限定其下材料
        fids, queue = set(), [folder_id]
        while queue:
            cur = queue.pop(0)
            if cur in fids:
                continue
            fids.add(cur)
            for f in db.query(Folder).filter(Folder.parent_id == cur).all():
                queue.append(f.id)
        mids = [m.id for m in db.query(Material).filter(Material.folder_id.in_(fids)).all()]
        if not mids:
            return []
        query = query.filter(MaterialChunk.material_id.in_(mids))
    chunks = (query.filter(MaterialChunk.content.contains(q))
              .order_by(MaterialChunk.material_id).limit(60).all())
    results = []
    for c in chunks:
        m = db.get(Material, c.material_id)
        if not m:
            continue
        idx = c.content.find(q)
        start = max(0, idx - 30)
        snippet = ("…" if start > 0 else "") + c.content[start:idx + len(q) + 30] + "…"
        results.append({
            "material_id": m.id, "material_title": m.title,
            "page_no": c.page_no, "snippet": snippet,
        })
        if len(results) >= 20:
            break
    return results


# ---------- 外部网页剪藏（P0-4） ----------
#
# ⚠️ 这三个路由**必须定义在 `GET /{material_id}` 之前**（本文件下方）。
# 那条是 `material_id: int`，FastAPI 按声明顺序匹配 → 若在它之后，
# `/clip/preview` 会被当作 material_id 解析失败并返回 422。`import-local` 已踩过同一个坑。
#
# 合规边界（方案 §2.1 / §6.2）：网页与公众号属"需用户逐条确认"的来源
# （`external_svc.need_confirm`）。本组接口**只提供单篇预览与单篇落库**，
# 刻意不提供"批量直接入库"的入口 —— 批量场景走 `batch/preview` 出候选列表，
# 由前端逐条让用户点击 `save`。这不是交互偏好，是边界的实现方式。

# 单篇正文上限：与「新增文档」的 200000 字上限保持一致的量级。
# 网页正文正常在 5k~30k 字，超过 20 万字基本是异常页面（或抓错了整站索引）。
MAX_CLIP_CHARS = 200000

# WP15：浏览器视图取回的**页面原始源码**上限（字符）。比 MAX_CLIP_CHARS 大得多是
# 因为它是**源码**（含内联脚本），抽取之后才会缩到正文字数；这里只防「明显异常的大包」。
MAX_SOURCE_CHARS = 6_000_000


class ClipPreviewReq(BaseModel):
    url: str
    # ⚠️ 必须和 ClipSaveReq 一样支持 text —— SPA 降级通道是「粘贴正文 → 预览 → 入库」，
    # 少了这个字段，粘贴后预览会退化成真去抓那个抓不到的 URL，用户看到 blocked，
    # 以为粘贴没生效。实测踩过：preview(action=blocked) 而 save 却是好的。
    text: str | None = None
    title: str | None = None
    # WP15：与 ClipSaveReq 成对（浏览器取源路径）—— 预览与入库必须能走**同一份输入**，
    # 否则「预览说能存、入库报错」会再次出现（这正是 text 字段当年踩过的坑）。
    source: str | None = None


class ClipSaveReq(BaseModel):
    url: str
    # 允许前端直接带正文（SPA 降级时由用户粘贴正文）→ 不传 fetch，也不落"抓取失败"的锅
    text: str | None = None
    title: str | None = None
    # WP15：浏览器视图取回的**页面原始源码**。⚠️ 与 `text` **语义不同**（见 `_clip_fetch`）：
    # 源码必须**先抽取**，不能直通落库（否则存下去的是整篇 HTML）。
    source: str | None = None


class ClipBatchPreviewReq(BaseModel):
    urls: list[str]


def _clip_fetch(req_url: str, text: str | None, title: str | None,
                source: str | None = None) -> dict:
    """剪藏路径的正文来源：**页面源码（浏览器视图）> 前端正文（粘贴降级）> 抓取**。

    实现见 `external_svc.fetch_or_passthrough`（单一来源，临时阅读共用）。
    长度策略留在本层：剪藏语义是**入库**，超长一律拒绝 ——
    静默截断用户要保存的内容，比报错更糟。

    ⚠️⚠️ `source` 与 `text` **语义不同，绝不能混用**（WP15 新增）：
       `text`   = **已抽好的正文**（粘贴降级）→ `fetch_or_passthrough` 直通落库；
       `source` = **页面原始源码**（浏览器视图取回）→ 必须 `extract_from_source` **抽取**。
       把源码当 `text` 传 → 落库的是**整篇 HTML**（本项目最忌的「同一资源两个入口
       给出不同产物」）。所以这里按**先看 source** 的顺序分派，且源码路径**不**做
       超长拒绝（源码本来就大；抽完之后的正文长度由 `clip_save` 按原有口径判）。
    """
    body = (text or "").strip()
    if body and len(body) > MAX_CLIP_CHARS:
        raise HTTPException(400, f"正文过长（{len(body)} 字），请拆分后保存")
    src = (source or "").strip()
    if src:
        if len(src) > MAX_SOURCE_CHARS:
            raise HTTPException(400, f"页面源码过大（{len(src)} 字符），无法处理")
        return external_svc.extract_from_source(req_url, src)
    return external_svc.fetch_or_passthrough(req_url, text, title)


def _clip_to_markdown(title: str, url: str, meta: dict, text: str) -> str:
    """组装落库用的 Markdown：标题 + 来源信息 + 正文。

    ⚠️ 标题必须作为 `# ` 首行落进正文，否则 `split_markdown` 的 section_path 从第一段就是空，
    整篇没有章节名。来源信息放在正文**之前**的引用块里，便于溯源，也避免它被当成正文首段。
    """
    lines = [f"# {title}", ""]
    src_bits = []
    if meta.get("sitename"):
        src_bits.append(meta["sitename"])
    if meta.get("author"):
        src_bits.append(meta["author"])
    if meta.get("date"):
        src_bits.append(meta["date"])
    if src_bits:
        lines.append("> " + " · ".join(str(b) for b in src_bits))
        lines.append("")
    if url:
        lines.append(f"> 来源：{url}")
        lines.append("")
    lines.append(text.strip())
    lines.append("")
    return "\n".join(lines)


def _clip_existing(db: Session, norm_url: str) -> Material | None:
    """幂等查重：同一 URL 是否已入库（实现见 external_svc.find_saved_material）。

    保留本包装是为了 3 处调用点与既有验收脚本不动；口径只有 external.py 一份。
    """
    return external_svc.find_saved_material(db, norm_url)


@router.post("/clip/preview")
def clip_preview(req: ClipPreviewReq, db: Session = Depends(get_db)):
    """预览单个链接：抓取并抽取正文，**不落库、不建索引**。

    可选传 `text` 直接给正文（SPA 降级通道），此时跳过抓取。
    """
    url = (req.url or "").strip()
    if not url:
        raise HTTPException(400, "请输入网页链接")
    # 传了 source → 从源码抽取（浏览器视图路径）；传了 text → 粘贴降级；否则抓取。
    # 三条路共用 _clip_fetch 保证口径一致。
    r = _clip_fetch(url, req.text, req.title, req.source)
    payload = external_svc.preview_payload(r)
    # P2-4：预览阶段就把「超长」提示出来，而不是入库时才 400（否则用户白等一次抓取）。
    # 与 clip_save 共用 MAX_CLIP_CHARS 口径，两侧一致。
    if payload["chars"] > MAX_CLIP_CHARS:
        payload["action"] = "blocked"
        payload["reason"] = "TOO_LONG"
        payload["hint"] = (f"正文超长（{payload['chars']} 字），超过单篇保存上限。"
                          "请拆分后保存，或改为粘贴部分正文。")
    # 已入库状态：用归一化后的 URL 查库（重启后仍准确，不用内存标记）
    exist = _clip_existing(db, payload["url"])
    payload["saved"] = exist is not None
    payload["material_id"] = exist.id if exist else None
    return payload


@router.post("/clip/save")
def clip_save(req: ClipSaveReq, db: Session = Depends(get_db)):
    """把链接落库为材料：复用「新增文档」同一条解析/索引链路。

    - 正文优先取前端传的 `text`（粘贴降级），否则现场抓取
    - 幂等：同 URL 已存在 → 直接返回既有材料，`duplicated=True`，不重复入库
    - **不新建解析路径**：写 .md → Material(format="md") → `_parse_material` → cleaner/chunk/kb_index
    """
    url = (req.url or "").strip()
    if not url:
        raise HTTPException(400, "请输入网页链接")

    # ⚠️⚠️ 幂等查重**前移到抓取之前**（N3）。原先只有"抓取后的查重"，于是同一 URL 已入库时
    #    仍会先完整抓一遍（网络层最长 20s）才发现"已存在"—— 而用户最常见的路径
    #    「粘贴 → 预览 → 加入知识库」里，**预览已经抓过一次**，入库是第二次。
    #    ⚠️ 这里只用**本地可判定**的归一化身份（不展开短链、不发任何请求）：
    #       小红书短链场景会 miss → 落到下面那条"抓取后查重"兜底，**正确性不受影响**，
    #       只是没省下那一次抓取（为它专门展开短链也是一次网络往返，不值得）。
    exist_pre = _clip_existing(
        db, external_svc.normalize_url(external_svc.extract_first_url(url) or url))
    if exist_pre is not None:
        d = material_to_dict(exist_pre, db)
        d["duplicated"] = True
        return d

    r = _clip_fetch(url, req.text, req.title, req.source)
    norm = r.get("url") or ""
    if not r.get("ok"):
        # 抓取失败但没有正文可落库 → 400 + 可读原因（前端已有 hint 可展示）
        payload = external_svc.preview_payload(r)
        raise HTTPException(status_code=400, detail={
            "reason": payload["reason"], "hint": payload["hint"],
            "action": payload["action"],
        })

    # 幂等兜底：上面的前置查重只覆盖"本地可判定"的身份；短链这类**展开后才确定**的身份
    # 会走到这里（此时 `norm` 已是抓取路径归一化后的结果）。两处都调 `_clip_existing`，
    # 口径仍是同一处实现，不是两份逻辑。
    exist = _clip_existing(db, norm)
    if exist is not None:
        d = material_to_dict(exist, db)
        d["duplicated"] = True
        return d

    kind = r["kind"]
    if not external_svc.can_index(kind):
        # 兜底：能力表说不允许建索引的来源，一律拒绝落库（防止将来加渠道时漏挡）
        raise HTTPException(400, f"该来源（{external_svc.origin_label(kind)}）暂不支持加入知识库")

    text = (r.get("text") or "").strip()
    if len(text) < external_svc.MIN_CLIP_CHARS:
        raise HTTPException(400, "正文内容过短，无法保存")
    if len(text) > MAX_CLIP_CHARS:
        raise HTTPException(400, f"正文过长（{len(text)} 字），请拆分后保存")

    title = (req.title or "").strip() or (r.get("title") or "").strip() or "未命名网页"
    title = title[:180]          # 防超长标题撑爆 String(255)

    # 小红书图文：配图是**短时强签名直链**（实测改时间戳 / hash / 后缀任一处即 403），
    # 必须是**落库这一刻**下载到本地并把正文里的外链换成站内地址，否则用户过两天
    # 打开就是一片破图。取舍与实测见 `AI伴学助手_小红书图文抓取方案.md`。
    # ⚠️ **只在这里调**：「仅本次阅读」的契约是**不落盘**，那边直接用原始 URL 外链即可
    #    —— 当次阅读必然还在有效期内。见 `external.localize_images` 的说明。
    meta = dict(r.get("meta") or {})
    if kind == "xhs":
        # ⚠️⚠️ 图片 URL 的来源**不能只看 `meta.image_urls`**（P1-2）：走「仅本次阅读 →
        #    加入知识库」时是**粘贴降级**分支，那条路的 meta 只有 `{template, passthrough}`
        #    → 旧写法直接跳过本地化 → 落库正文留着**短时签名外链** → 过几天整篇破图；
        #    而同样一篇从候选列表直接点「加入知识库」（走抓取）图片却是好的
        #    —— **同一资源两个入口给出不同产物**，正是本项目反复出现的缺陷模式。
        #    改成「meta 有就用 meta，没有就从正文抽」：两条路径共用 `image_urls_in_markdown`。
        img_urls = list(meta.get("image_urls") or []) or external_svc.image_urls_in_markdown(text)
        # 子目录口径走单一函数（落库 / 删除共用）；note id 抽不到时用 URL 哈希兜底，
        # ⚠️ 不能退化成固定串（多篇笔记会同目录同名、互相覆盖）
        subdir = external_svc.assets_subdir_for(norm, meta)
        meta["xhs_asset_dir"] = subdir
        if img_urls:
            text, _img_stat = external_svc.localize_images(text, img_urls, subdir=subdir)
            # 签名 URL 会过期 → **不当稳定地址存**（留着只会让人以为还能用）
            meta.pop("image_urls", None)
            meta["image_paths"] = _img_stat.get("image_paths") or []
            meta["images_saved"] = int(_img_stat.get("images_saved") or 0)
            if _img_stat.get("images_failed"):
                # 逐张容错：失败张数如实记账，而不是静默变少（正文里那条外链保留原 URL）
                meta["images_failed"] = int(_img_stat["images_failed"])
            # 正文里的图就是这篇的图：粘贴降级路径没有 meta.images，补上（前端显示「N 张图」）
            if not meta.get("images"):
                meta["images"] = len(img_urls)

    markdown = _clip_to_markdown(title, norm, meta, text)

    # 同名处理：与 upload_material / create_document 一致（标题追加 (n)）
    # ⚠️ 这里只解决"标题撞车"，不解决"同 URL 重复" —— 后者由上面的 _clip_existing 负责。
    # ⚠️ 网页 <title> 是**远端可控文本**，必过 safe_stem：
    #    实测含英文双引号 → 500 [Errno 22]；含 `../` 更会把路径拼出 files_dir（路径穿越）。
    title, save_path = _unique_title_and_path(db, title, "md")
    try:
        save_path.write_text(markdown, encoding="utf-8")
    except OSError as e:
        raise HTTPException(500, f"写入文件失败：{str(e)[:120]}")

    # ⚠️ 用**本地化之后**的 meta：里面是 image_paths（站内地址），已不含会过期的签名 URL
    meta_snapshot = dict(meta)
    meta_snapshot["saved_from"] = "clip"
    m = Material(
        title=title, format="md", file_path=str(save_path), parsed_status="parsing",
        origin=kind, origin_ref=norm, origin_meta=meta_snapshot,
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    threading.Thread(target=_parse_material, args=(m.id,), daemon=True).start()
    d = material_to_dict(m, db)
    d["duplicated"] = False
    return d


@router.post("/clip/batch/preview")
def clip_batch_preview(req: ClipBatchPreviewReq, db: Session = Depends(get_db)):
    """批量链接：逐篇抓取，返回**候选列表**（含每篇状态与动作），**不入库**。

    ⚠️ 刻意只做"预览"。入库必须由前端逐条调用 `/clip/save`（逐篇确认）。
    本接口不返回任何"一键全部入库"的字段。
    """
    urls = [u.strip() for u in (req.urls or []) if (u or "").strip()]
    if not urls:
        raise HTTPException(400, "请粘贴至少一个链接")
    if len(urls) > 20:
        raise HTTPException(400, "一次最多处理 20 个链接，请分批粘贴")

    items = []
    for u in urls:
        try:
            # 批量场景一律走抓取（不接 text）—— 粘贴正文是单篇的降级动作，
            # 批量里"哪一段正文属于哪个 URL"无法可靠对应，硬做会张冠李戴。
            r = _clip_fetch(u, None, None)
        except HTTPException as e:
            items.append({
                "ok": False, "kind": external_svc.detect_kind(u) or "url",
                "kind_label": external_svc.origin_label(external_svc.detect_kind(u)),
                "need_confirm": True, "url": "", "input_url": u, "title": "",
                "chars": 0, "images": 0, "wx_image_post": False, "xhs_video_note": False,
                "meta": {}, "reason": "BAD_URL",
                "action": "blocked", "hint": str(e.detail), "wx_temp_link": False,
                "saved": False, "material_id": None,
            })
            continue
        p = external_svc.preview_payload(r)
        exist = _clip_existing(db, p["url"])
        p["saved"] = exist is not None
        p["material_id"] = exist.id if exist else None
        items.append(p)

    return {
        "items": items,
        "total": len(items),
        "ready": sum(1 for i in items if i["action"] == "ready"),
        "need_paste": sum(1 for i in items if i["action"] == "paste"),
        "blocked": sum(1 for i in items if i["action"] == "blocked"),
        # 明示：本接口只产出候选，入库需逐条确认（也是给前端的契约提示）
        "note": "逐条确认后才会入库；本接口不落库、不建索引。",
    }


@router.get("/{material_id}")
def get_material(material_id: int, db: Session = Depends(get_db)):
    m = db.get(Material, material_id)
    if not m:
        raise HTTPException(404, "材料不存在")
    data = material_to_dict(m, db)
    # 附带目录（章节去重，供学习页左栏导航）
    sections = (
        db.query(MaterialChunk.section_path)
        .filter(MaterialChunk.material_id == m.id, MaterialChunk.section_path != "")
        .distinct().all()
    )
    data["sections"] = [s[0] for s in sections]
    data["parse_progress"] = m.parse_progress or 0
    return data


@router.get("/{material_id}/chunks")
def get_chunks(material_id: int, db: Session = Depends(get_db)):
    """B1 阅读器文本视图数据：按页码/顺序返回解析块"""
    m = db.get(Material, material_id)
    if not m:
        raise HTTPException(404, "材料不存在")
    rows = (db.query(MaterialChunk)
            .filter(MaterialChunk.material_id == material_id)
            .order_by(MaterialChunk.page_no, MaterialChunk.id).all())
    return [{"id": r.id, "content": r.content, "page_no": r.page_no,
             "section_path": r.section_path} for r in rows]


class ChunkUpdateReq(BaseModel):
    content: str


@router.put("/{material_id}/chunks/{chunk_id}")
def update_chunk(material_id: int, chunk_id: int, req: ChunkUpdateReq, db: Session = Depends(get_db)):
    """转写校对：修正音视频转写文本（同音字等），并同步重建该块的向量索引"""
    c = db.get(MaterialChunk, chunk_id)
    if not c or c.material_id != material_id:
        raise HTTPException(404, "文本块不存在")
    content = (req.content or "").strip()
    if not content:
        raise HTTPException(400, "内容不能为空")
    c.content = content
    db.commit()
    reindex_warning = None
    try:
        from ..services import kb_index
        kb_index.reindex_chunk(db, c)
    except Exception as e:
        import logging
        reindex_warning = "已保存，但索引更新失败，后续问答可能检索不到（可到知识库重建索引）"
        logging.getLogger("uvicorn.error").warning(f"文本块 {chunk_id} 重索引失败: {e}")
    return {"id": c.id, "content": c.content, "page_no": c.page_no, "section_path": c.section_path,
            "reindex_warning": reindex_warning}


@router.delete("/{material_id}/chunks/{chunk_id}")
def delete_chunk(material_id: int, chunk_id: int, db: Session = Depends(get_db)):
    """编辑模式：删除单个文本块，同步清理向量索引"""
    c = db.get(MaterialChunk, chunk_id)
    if not c or c.material_id != material_id:
        raise HTTPException(404, "文本块不存在")
    try:
        from ..services import kb_index
        kb_index.deindex_chunk(db, c)
    except Exception:
        pass
    db.delete(c)
    db.commit()
    return {"ok": True}


@router.delete("/{material_id}/pages")
def delete_page(material_id: int, page_no: int, db: Session = Depends(get_db)):
    """编辑模式：删除整页（该页所有文本块），同步清理索引并更新页数"""
    m = db.get(Material, material_id)
    if not m:
        raise HTTPException(404, "材料不存在")
    rows = (db.query(MaterialChunk)
            .filter(MaterialChunk.material_id == material_id, MaterialChunk.page_no == page_no).all())
    if not rows:
        raise HTTPException(404, "该页无内容")
    try:
        from ..services import kb_index
        for c in rows:
            kb_index.deindex_chunk(db, c)
    except Exception:
        pass
    for c in rows:
        db.delete(c)
    db.commit()
    remaining = (db.query(MaterialChunk.page_no)
                 .filter(MaterialChunk.material_id == material_id).all())
    m.page_count = max((r[0] for r in remaining), default=0)
    db.commit()
    return {"ok": True, "page_count": m.page_count}


# ---------- 划线高亮（模块 B：主动划线标记） ----------

class HighlightCreateReq(BaseModel):
    chunk_id: int | None = None
    page_no: int = 0
    selected_text: str
    color: str = "yellow"
    start: int | None = None
    end: int | None = None


class HighlightUpdateReq(BaseModel):
    color: str = "yellow"


def _highlight_to_dict(h: Highlight) -> dict:
    return {"id": h.id, "material_id": h.material_id, "chunk_id": h.chunk_id,
            "page_no": h.page_no, "selected_text": h.selected_text,
            "color": h.color, "start": h.start, "end": h.end}


@router.get("/{material_id}/highlights")
def list_highlights(material_id: int, db: Session = Depends(get_db)):
    rows = (db.query(Highlight).filter(Highlight.material_id == material_id)
            .order_by(Highlight.page_no, Highlight.id).all())
    return [_highlight_to_dict(h) for h in rows]


@router.post("/{material_id}/highlights")
def create_highlight(material_id: int, req: HighlightCreateReq, db: Session = Depends(get_db)):
    m = db.get(Material, material_id)
    if not m:
        raise HTTPException(404, "材料不存在")
    text = (req.selected_text or "").strip()
    if not text:
        raise HTTPException(400, "高亮内容不能为空")
    color = req.color if req.color in ("yellow", "green", "blue") else "yellow"
    h = Highlight(material_id=material_id, chunk_id=req.chunk_id, page_no=req.page_no,
                  selected_text=text[:500], color=color, start=req.start, end=req.end)
    db.add(h)
    db.commit()
    db.refresh(h)
    return _highlight_to_dict(h)


@router.put("/{material_id}/highlights/{highlight_id}")
def update_highlight(material_id: int, highlight_id: int, req: HighlightUpdateReq, db: Session = Depends(get_db)):
    """切换划线颜色：更新颜色（替换而非叠加）"""
    h = db.get(Highlight, highlight_id)
    if not h or h.material_id != material_id:
        raise HTTPException(404, "高亮不存在")
    if req.color in ("yellow", "green", "blue"):
        h.color = req.color
    db.commit()
    return _highlight_to_dict(h)


@router.delete("/{material_id}/highlights/{highlight_id}")
def delete_highlight(material_id: int, highlight_id: int, db: Session = Depends(get_db)):
    h = db.get(Highlight, highlight_id)
    if not h or h.material_id != material_id:
        raise HTTPException(404, "高亮不存在")
    db.delete(h)
    db.commit()
    return {"ok": True}


@router.get("/{material_id}/file")
def get_file(material_id: int, download: bool = False, db: Session = Depends(get_db)):
    """B1 原文视图：inline 返回原始文件（PDF 由浏览器内联渲染，不触发下载）。
    download=1 时改为 attachment 触发下载。"""
    from fastapi.responses import FileResponse
    m = db.get(Material, material_id)
    if not m:
        raise HTTPException(404, "材料不存在")
    if not Path(m.file_path).exists():
        raise HTTPException(404, "源文件已丢失")
    media_map = {
            "pdf": "application/pdf", "mp3": "audio/mpeg", "wav": "audio/wav",
            "m4a": "audio/mp4", "mp4": "video/mp4",
            "epub": "application/epub+zip",
            "md": "text/markdown", "markdown": "text/markdown",
            "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
            "webp": "image/webp", "bmp": "image/bmp",
        }
    media = media_map.get(m.format, "application/octet-stream")
    # Cache-Control: no-cache —— 必须显式声明，否则浏览器/WebView 会启用「启发式缓存」
    # （缓存时长 ≈ 距今 × 10%）。源文件常是较早期下载的（Last-Modified 可能是一年前），
    # 启发式缓存可达数十天；而本接口 URL 只含 material_id，材料删除后 id 会被复用，
    # 于是换材料后仍渲染缓存里的旧文件（表现为「原文视图显示成别的材料」）。
    # no-cache 允许缓存但每次使用前必须向服务端校验，配合 ETag 未变时返回 304，兼顾正确与性能。
    return FileResponse(m.file_path, media_type=media,
                        filename=f"{m.title}.{m.format}",
                        headers={"Cache-Control": "no-cache"},
                        content_disposition_type="attachment" if download else "inline")


# ---------- EPUB 原文视图（章节清单 + zip 资源镜像） ----------

# 服务端剥脚本：iframe 已禁用脚本，这里再做一层兜底（防用户拿到 /epub-res 直链时执行）
_SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script\s*>", re.I | re.S)
_SCRIPT_SELF_RE = re.compile(r"<script\b[^>]*/\s*>", re.I)


def _strip_script(text: str) -> str:
    return _SCRIPT_SELF_RE.sub("", _SCRIPT_RE.sub("", text))


def _epub_material(db: Session, material_id: int) -> Material:
    """取 EPUB 材料并校验状态（章节清单与资源接口共用）"""
    m = db.get(Material, material_id)
    if not m:
        raise HTTPException(404, "材料不存在")
    if (m.format or "").lower() != "epub":
        raise HTTPException(400, "该材料不是 EPUB")
    if not Path(m.file_path).exists():
        raise HTTPException(404, "源文件已丢失")
    return m


@router.get("/{material_id}/epub")
def epub_chapters(material_id: int, db: Session = Depends(get_db)):
    """EPUB 章节清单：原文视图的分章导航

    index 与 MaterialChunk.page_no 一一对应，前端据此把「左栏目录」和 iframe 章节对齐。
    href 为 zip 内相对路径，前端拼上 /epub-res/ 前缀即得章节 URL。
    """
    from ..services import epub as epub_svc
    m = _epub_material(db, material_id)
    try:
        with epub_svc.EpubBook(m.file_path) as book:
            return {"chapters": book.chapters()}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/{material_id}/epub-res/{res_path:path}")
def epub_resource(material_id: int, res_path: str, db: Session = Depends(get_db)):
    """EPUB 原文视图资源镜像：按 zip 内原始路径回吐章节 XHTML / 图片 / CSS / 字体

    为什么镜像 zip 目录结构而不是「按章号取正文」：章节 XHTML 里的相对引用
    （../Images/a.png、../Styles/main.css，以及 CSS 内的 url()）会随路径自然解析到本接口，
    服务端无需重写任何链接，书内样式与插图得以原样生效。

    路径安全：normalpath 后必须留在 zip 内（拒绝 .. 越界）；zip 名集合本身也是白名单，
    未命中即 404，不会读出归档外的东西。
    """
    from fastapi.responses import Response
    from ..services import epub as epub_svc
    m = _epub_material(db, material_id)

    # uvicorn 已对 path 做过一次百分号解码，但前端可能又编码过一次 → 两种形态都试
    candidates: list[str] = []
    for raw in (res_path, unquote(res_path)):
        p = posixpath.normpath(raw).lstrip("/")
        if p and p != "." and not p.startswith("..") and p not in candidates:
            candidates.append(p)
    if not candidates:
        raise HTTPException(400, "非法资源路径")

    try:
        with epub_svc.EpubBook(m.file_path) as book:
            data = None
            for p in candidates:
                try:
                    data = book.read(p)
                    break
                except KeyError:
                    continue
    except ValueError as e:
        raise HTTPException(400, str(e))
    if data is None:
        raise HTTPException(404, "资源不存在")

    media = epub_svc.media_type(candidates[0])
    if epub_svc.is_html(candidates[0]):
        # 章节统一「自行解码 → 剥脚本 → 以 UTF-8 重编码」后回吐，并显式声明 charset：
        # 原样回吐字节时，若文件既无 XML 声明也无 <meta charset>，浏览器会按 windows-1252
        # 猜编码，中文整篇乱码（服务端已有可靠解码链，不必让浏览器再猜一次）。
        data = _strip_script(epub_svc.decode_text(data)).encode("utf-8")
        media = "text/html; charset=utf-8"
    return Response(content=data, media_type=media, headers={
        "Cache-Control": "no-cache",            # 与 get_file 同理：id 复用会串档，必须每次校验
        "X-Content-Type-Options": "nosniff",
    })


@router.patch("/{material_id}")
def update_material(material_id: int, payload: dict, db: Session = Depends(get_db)):
    """A6 标签更新 / B8 阅读进度保存（轻量合并接口）"""
    m = db.get(Material, material_id)
    if not m:
        raise HTTPException(404, "材料不存在")
    if "tags" in payload and isinstance(payload["tags"], list):
        m.tags = [str(t)[:20] for t in payload["tags"]][:10]
    if "last_read_page" in payload:
        page = int(payload["last_read_page"])
        if page >= 0:
            m.last_read_page = page
    if "folder_id" in payload:
        fid = payload["folder_id"]
        if fid is None:
            m.folder_id = None   # 移出文件夹（未分组）
        else:
            f = db.get(Folder, fid)
            if not f:
                raise HTTPException(400, "目标文件夹不存在")
            m.folder_id = fid
    db.commit()
    db.refresh(m)
    return material_to_dict(m, db)


@router.get("/{material_id}/notes/export")
def export_notes(material_id: int, db: Session = Depends(get_db)):
    """C6 导出该材料全部笔记为 Markdown 文件"""
    from fastapi.responses import Response
    m = db.get(Material, material_id)
    if not m:
        raise HTTPException(404, "材料不存在")
    notes = (db.query(Note).filter(Note.material_id == material_id)
             .order_by(Note.created_at).all())
    lines = [f"# {m.title} · 笔记导出\n"]
    for n in notes:
        anchor = n.anchor or {}
        src = []
        if anchor.get("page_no"):
            src.append(f"P{anchor['page_no']}")
        if n.source_type == "ai_asset":
            src.append("AI 生成")
        src_text = f"（{'，'.join(src)}）" if src else ""
        lines.append(f"\n## {n.title}{src_text}\n\n{n.content}\n")
    content = "".join(lines)
    return Response(
        content=content.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=utf-8''"
                 + __import__("urllib.parse", fromlist=["quote"]).quote(f"{m.title}-笔记.md")},
    )


@router.post("/{material_id}/reparse")
def reparse_material(material_id: int, db: Session = Depends(get_db)):
    """A4 解析失败重试"""
    m = db.get(Material, material_id)
    if not m:
        raise HTTPException(404, "材料不存在")
    if not Path(m.file_path).exists():
        if (m.storage_mode or "copy") == "reference":
            raise HTTPException(400, "源文件已丢失，请在详情页点击「重新定位」重新指向")
        raise HTTPException(400, "源文件已丢失，请重新上传")
    m.parsed_status = "parsing"
    m.parse_error = None
    db.commit()
    threading.Thread(target=_parse_material, args=(m.id,), daemon=True).start()
    return material_to_dict(m, db)


class DocumentUpdateReq(BaseModel):
    content: str
    title: str | None = None


@router.put("/{material_id}/document")
def update_document(material_id: int, req: DocumentUpdateReq, db: Session = Depends(get_db)):
    """编辑已有文档：更新内容（可选改标题），写回 .md 并重新解析"""
    m = db.get(Material, material_id)
    if not m:
        raise HTTPException(404, "文档不存在")
    if m.format not in ("md", "markdown"):
        raise HTTPException(400, "仅支持编辑 Markdown 文档")
    content = (req.content or "").strip()
    if len(content) < 2:
        raise HTTPException(400, "正文内容过短（少于 2 字）")

    # 标题变更（可选）：查重 + 重命名文件
    new_title = (req.title or "").strip() or m.title
    old_path = Path(m.file_path)
    is_ref = (m.storage_mode or "copy") == "reference"

    if new_title == m.title or is_ref:
        # ⚠️ 这两种情况都必须写回 **m.file_path 本身**，绝不能按标题重建路径：
        # ① 标题没变：重建出的 files_dir/<title>.md 与真实 file_path 可能不是同一个文件
        #    （引用模式导入的 .md 真实路径在用户磁盘上）→ 编辑写进没人看得见的孤儿文件，
        #    而 m.file_path 仍指向旧内容 = **编辑静默丢失**。
        # ② 引用模式：材料就是磁盘上那个文件 → 改标题只改 DB，
        #    **绝不移动/删除用户的文件**（见 _delete_material_impl 的安全红线）。
        new_path = old_path
    else:
        # 复制副本：跟随标题改名（沿用原行为），并过一遍安全化
        new_title, new_path = _unique_title_and_path(db, new_title, "md", exclude_id=m.id)

    new_path.write_text(content, encoding="utf-8")
    if new_title != m.title:
        if not is_ref and old_path.exists() and old_path != new_path:
            # 安全红线（与删除路径共用同一实现，不再各写一份）：
            # 只删 files_dir 内的副本，路径在目录外一律跳过。
            _unlink_in_files_dir(old_path)
        m.title = new_title
        m.file_path = str(new_path)
    m.parsed_status = "parsing"
    m.parse_error = None
    db.commit()
    threading.Thread(target=_parse_material, args=(m.id,), daemon=True).start()
    return material_to_dict(m, db)


def _unlink_in_files_dir(p) -> bool:
    """只在 `files_dir` **之内**物理删除一个文件；返回是否真的删了。

    安全红线（两道，缺一不可）：
      1. `relative_to(files_dir)` —— 不在目录内一律跳过。
         否则"改个标题""删个材料"就可能把用户的**原始文件**删掉（引用模式尤其危险）。
      2. `except BaseException` —— 环境注入的 safe-delete shim 在超配额时 `raise SystemExit`，
         而 **SystemExit 不是 Exception 的子类**，`except OSError` 接不住。
         实测：`DELETE /api/materials/30` 因此返回 **500，而 DB 记录其实已经删掉了** ——
         用户看到"删除失败"、材料却不见了，是最糟的一类不一致。
    ⚠️ 语义取舍：DB 是事实来源，**文件残留可接受**，但绝不能把异常抛给用户。
    """
    try:
        Path(p).resolve().relative_to(settings.files_dir.resolve())
    except BaseException:          # noqa: BLE001 不在 files_dir 内 → 跳过
        return False
    try:
        Path(p).unlink()
        return True
    except BaseException:          # noqa: BLE001 删不掉（含 SystemExit）→ 跳过，不报错
        return False


def _material_asset_dir(m: Material) -> Path | None:
    """材料所属的「本地化配图」目录（没有则 None）。

    目前只有小红书图文会落这个目录。一个 note id 只可能对应一个材料
    （`origin_ref` 归一化去重保证），所以目录归属唯一、可以整目录删。

    ⚠️ 优先读 `xhs_asset_dir`（落库时写下的**实际子目录名**），回退老记录的 `xhs_note_id`；
    后者在「note id 抽不到」时为空 → 目录清不掉、留下孤儿图片（P1-2 的连带修复）。
    """
    try:
        meta = m.origin_meta or {}
        sub = (str(meta.get("xhs_asset_dir") or "").strip()
               or str(meta.get("xhs_note_id") or "").strip())
        return external_svc.assets_dir(sub) if sub else None
    except BaseException:      # noqa: BLE001 取不到就不清，绝不阻断删除
        return None


def _cleanup_material_assets(asset_dir) -> None:
    """删除材料配图目录。**绝不向外抛**（清理失败不影响主删除流程）。

    两道保险：
      1. 只删 `data/assets/` 之内的目录（`relative_to` 校验，防路径穿越）
      2. `except BaseException` —— 见本模块顶部说明（safe-delete shim 会抛 SystemExit）
    """
    try:
        if not asset_dir or not asset_dir.exists():
            return
        asset_root = (Path(settings.data_dir) / "assets").resolve()
        asset_dir.resolve().relative_to(asset_root)   # 不在 assets 内 → ValueError
        shutil.rmtree(asset_dir, ignore_errors=True)
    except BaseException:      # noqa: BLE001 清理是尽力而为，绝不影响删除结果
        pass


def _delete_material_impl(db, m: Material):
    """删除单个材料：索引 + DB（级联笔记/块/AI产物）+ 物理文件。供路由与文件夹级联删除复用。

    安全红线：仅「复制副本」且文件在 files_dir 内才物理删除；引用模式只解绑、绝不碰原文件。
    """
    try:
        from ..services import kb_index
        kb_index.deindex_material(db, m.id)
    except BaseException:
        # ⚠️ 必须 BaseException：safe-delete shim 超配额时抛 SystemExit（不是 Exception 子类）。
        # 索引清理失败（含被拦）不阻断主删除流程。
        pass

    # 提前取快照：db.delete 后实例属性会 expire，读取可能触发重载报错
    file_path = Path(m.file_path)
    storage_mode = m.storage_mode or "copy"
    material_id = m.id
    # 配图目录同样要提前取（origin_meta 也会 expire）
    asset_dir = _material_asset_dir(m)
    db.delete(m)  # cascade 清理 chunks / notes；AIAsset 手动清
    db.query(AIAsset).filter(AIAsset.material_id == material_id).delete()
    db.commit()
    if storage_mode != "reference" and file_path.exists():
        _unlink_in_files_dir(file_path)
    # 本地化配图（P0-3h）：小红书的图在 data/assets/<note id>/，随材料一起清。
    # 顺序仍是「先删 DB、再清文件」—— 文件残留可接受，DB 残留不可接受。
    _cleanup_material_assets(asset_dir)


@router.delete("/{material_id}")
def delete_material(material_id: int, db: Session = Depends(get_db)):
    """A5 删除：文件 + DB（级联笔记/块/AI产物）+ 向量索引 联动清理"""
    m = db.get(Material, material_id)
    if not m:
        raise HTTPException(404, "材料不存在")
    _delete_material_impl(db, m)
    return {"ok": True}


class RelocateReq(BaseModel):
    new_path: str
    reparse: bool = False   # 重定位后是否重新解析（源文件内容可能已变化）


@router.post("/{material_id}/relocate")
def relocate_material(material_id: int, req: RelocateReq, db: Session = Depends(get_db)):
    """源文件被移动/改名后，重新指向新路径（失联兜底），可选触发重新解析"""
    m = db.get(Material, material_id)
    if not m:
        raise HTTPException(404, "材料不存在")
    p = Path(_clean_path(req.new_path))
    if not p.is_file():
        raise HTTPException(400, "文件不存在")
    ext = p.suffix.lower().lstrip(".")
    if ext != (m.format or "").lower():
        raise HTTPException(400, f"新文件格式（.{ext or '无'}）与原格式（.{m.format}）不一致，请选择同类型文件")
    m.file_path = str(p.resolve())
    if req.reparse:
        m.parsed_status = "parsing"
        m.parse_error = None
        db.commit()
        threading.Thread(target=_parse_material, args=(m.id,), daemon=True).start()
    else:
        db.commit()
    return material_to_dict(m, db)
