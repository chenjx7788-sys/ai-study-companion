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
from ..database import get_db, SessionLocal
from ..models import Material, MaterialChunk, AIAsset, Note, Highlight, Folder
from ..services import parser as parser_svc

router = APIRouter(prefix="/materials", tags=["materials"])

ALLOWED_FORMATS = {"pdf", "ppt", "pptx", "doc", "docx", "md", "markdown", "epub",
                   "mp3", "wav", "m4a", "mp4",
                   "jpg", "jpeg", "png", "webp", "bmp"}

# 支持列表文案：多处提示共用，避免改格式时漏改某一处
FORMAT_HINT = "PDF/PPT/Word/Markdown/EPUB/图片/音视频"


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

    stem = filename.rsplit(".", 1)[0]
    # 同名处理：标题与文件名都追加 (n)
    title, n = stem, 1
    while db.query(Material).filter(Material.title == title).first():
        n += 1
        title = f"{stem}({n})"
    save_name = f"{title}.{ext}"
    save_path = settings.files_dir / save_name
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
    # 同名处理（与 upload_material 一致）
    stem, n = title, 1
    while db.query(Material).filter(Material.title == title).first():
        n += 1
        title = f"{stem}({n})"
    save_path = settings.files_dir / f"{title}.md"
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
    # 同名处理：标题自动追加 (n)，与 upload_material 一致
    stem, n, title = p.stem, 1, p.stem
    while db.query(Material).filter(Material.title == title).first():
        n += 1
        title = f"{stem}({n})"
    save_path = None
    try:
        if mode == "copy":
            if p.stat().st_size > settings.max_file_mb * 1024 * 1024:
                return None, {"path": str(p), "reason": f"超过 {settings.max_file_mb}MB"}
            save_path = settings.files_dir / f"{title}.{ext}"
            shutil.copy2(p, save_path)
            storage, file_path = "copy", str(save_path)
        else:
            storage, file_path = "reference", str(p)   # 不复制，直接引用原路径

        m = Material(title=title, format=ext, file_path=file_path,
                     storage_mode=storage, folder_id=folder_id, parsed_status="parsing")
        db.add(m)
        db.commit()
        db.refresh(m)
        threading.Thread(target=_parse_material, args=(m.id,), daemon=True).start()
        return material_to_dict(m, db), None
    except Exception as e:
        db.rollback()
        # copy 模式已复制副本但建档失败 → 清理孤儿副本，避免 files_dir 残留无记录文件
        if save_path is not None and Path(save_path).exists():
            try:
                Path(save_path).unlink()
            except OSError:
                pass
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
    if new_title != m.title:
        stem, n = new_title, 1
        while db.query(Material).filter(Material.title == new_title, Material.id != m.id).first():
            n += 1
            new_title = f"{stem}({n})"

    old_path = Path(m.file_path)
    new_path = settings.files_dir / f"{new_title}.md"
    new_path.write_text(content, encoding="utf-8")
    if new_title != m.title:
        if old_path.exists() and old_path != new_path:
            old_path.unlink()
        m.title = new_title
        m.file_path = str(new_path)
    m.parsed_status = "parsing"
    m.parse_error = None
    db.commit()
    threading.Thread(target=_parse_material, args=(m.id,), daemon=True).start()
    return material_to_dict(m, db)


def _delete_material_impl(db, m: Material):
    """删除单个材料：索引 + DB（级联笔记/块/AI产物）+ 物理文件。供路由与文件夹级联删除复用。

    安全红线：仅「复制副本」且文件在 files_dir 内才物理删除；引用模式只解绑、绝不碰原文件。
    """
    try:
        from ..services import kb_index
        kb_index.deindex_material(db, m.id)
    except Exception:
        pass  # 索引清理失败不阻断主删除流程

    # 提前取快照：db.delete 后实例属性会 expire，读取可能触发重载报错
    file_path = Path(m.file_path)
    storage_mode = m.storage_mode or "copy"
    material_id = m.id
    db.delete(m)  # cascade 清理 chunks / notes；AIAsset 手动清
    db.query(AIAsset).filter(AIAsset.material_id == material_id).delete()
    db.commit()
    if storage_mode != "reference" and file_path.exists():
        try:
            file_path.resolve().relative_to(settings.files_dir.resolve())
            file_path.unlink()
        except (ValueError, OSError):
            pass   # 路径不在 files_dir 内，跳过删除（双保险）


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
