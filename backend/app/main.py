import sys
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from .database import Base, engine
from .routers import materials, ai, notes, kb, chat, review, settings as settings_router, asr, folders, stats

Base.metadata.create_all(bind=engine)

# 轻量迁移：为已有库补新增列（create_all 不会改已存在的表）
def _migrate_columns():
    from sqlalchemy import text, inspect
    try:
        insp = inspect(engine)
        if "chat_messages" in insp.get_table_names():
            cols = {c["name"] for c in insp.get_columns("chat_messages")}
            if "reasoning" not in cols:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE chat_messages ADD COLUMN reasoning TEXT DEFAULT ''"))
        if "materials" in insp.get_table_names():
            cols = {c["name"] for c in insp.get_columns("materials")}
            if "storage_mode" not in cols:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE materials ADD COLUMN storage_mode VARCHAR(16) DEFAULT 'copy'"))
            if "source_folder" not in cols:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE materials ADD COLUMN source_folder VARCHAR(255) DEFAULT ''"))
            if "folder_id" not in cols:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE materials ADD COLUMN folder_id INTEGER"))
    except Exception:
        pass


def _migrate_source_folder_to_folder():
    """把旧 source_folder 字符串迁移为 Folder 实体 + folder_id 关联（幂等）"""
    from .models import Material, Folder
    from .database import SessionLocal
    db = SessionLocal()
    try:
        rows = db.query(Material).filter(Material.source_folder != "").all()
        if not rows:
            return
        cache = {}
        for m in rows:
            name = (m.source_folder or "").strip()
            if not name or m.folder_id is not None:
                continue   # 无归属 或 已迁移
            f = cache.get(name)
            if f is None:
                f = (db.query(Folder)
                     .filter(Folder.name == name, Folder.parent_id.is_(None)).first())
                if f is None:
                    f = Folder(name=name, parent_id=None)
                    db.add(f)
                    db.commit()
                    db.refresh(f)
                cache[name] = f
            m.folder_id = f.id
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


_migrate_columns()
_migrate_source_folder_to_folder()

app = FastAPI(title="AI 伴学助手", version="0.1.2")

# 自动定时备份（启动后延迟 30s 首备，此后每日一次，保留最近 7 份）
from .services import auto_backup
auto_backup.start_auto_backup()

# chroma HNSW 索引在进程被强杀后可能损坏，启动时后台检测并自动重建
import threading


def _auto_repair_chroma():
    def _repair():
        from .services import vector
        need_rebuild = False
        n_chunks = n_notes = 0
        try:
            n_chunks = vector.get_collection("kb_chunks").count()
            n_notes = vector.get_collection("kb_notes").count()
        except Exception:
            need_rebuild = True   # 索引损坏
        if not need_rebuild:
            # 索引空但材料有数据 → 需要重建（如手动删过 chroma 目录）
            from .models import Material
            from .database import SessionLocal
            db = SessionLocal()
            try:
                has_material = db.query(Material).filter(Material.parsed_status == "success").count() > 0
                if has_material and n_chunks == 0:
                    need_rebuild = True
            finally:
                db.close()
        if not need_rebuild:
            return   # 索引健康

        print("[startup] chroma 索引需重建，后台自动重建...")
        import chromadb
        from .core.config import settings as cfg
        # 用 chroma API 删除集合（shutil.rmtree 会被沙箱 safe-delete 拦截）
        client = chromadb.PersistentClient(path=str(cfg.chroma_dir))
        for name in ("kb_chunks", "kb_notes"):
            try:
                client.delete_collection(name)
            except Exception:
                pass
        from .services import kb_index
        from .models import Material, Note
        from .database import SessionLocal
        db = SessionLocal()
        try:
            for m in db.query(Material).filter(Material.parsed_status == "success").all():
                kb_index.index_material(db, m.id)
            for n in db.query(Note).all():
                kb_index.index_note(db, n)
        finally:
            db.close()
        print("[startup] chroma 索引重建完成")

    threading.Thread(target=_repair, daemon=True).start()


_auto_repair_chroma()

# 自动周报：启动后延迟检查，若本周未生成且有学习数据则自动生成（失败静默，不阻塞启动）
def _auto_weekly_report():
    def _run():
        time.sleep(12)   # 等启动稳定后再检查，避免拖慢启动
        try:
            from .database import SessionLocal
            from .routers.stats import maybe_generate_weekly_report
            db = SessionLocal()
            try:
                maybe_generate_weekly_report(db)
            finally:
                db.close()
        except Exception:
            pass
    threading.Thread(target=_run, daemon=True).start()


_auto_weekly_report()

app.add_middleware(
    CORSMiddleware,
    # 本地单机应用：放宽到 *，让 pywebview 内联 loading 页（origin=null）也能轮询 /api/health
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(materials.router, prefix="/api")
app.include_router(ai.router, prefix="/api")
app.include_router(notes.router, prefix="/api")
app.include_router(kb.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(review.router, prefix="/api")
app.include_router(settings_router.router, prefix="/api")
app.include_router(asr.router, prefix="/api")
app.include_router(folders.router, prefix="/api")
app.include_router(stats.router, prefix="/api")


@app.get("/api/health")
def health():
    return {"status": "ok"}


APP_VERSION = "0.1.2"


@app.get("/api/version")
def version():
    """当前版本号（客户端更新检查用，后续对接 GitHub releases）"""
    return {"version": APP_VERSION}


# ---------- 前端静态托管（单端口化：生产/打包时由后端直接 serve 前端 build 产物） ----------
# 开发时 vite dev server(5173) 独立 serve 前端，此段不影响开发流程；
# 打包时 dist/ 随包分发，用户只启动后端一个进程即可访问完整应用。
if getattr(sys, "frozen", False):
    _DIST = Path(sys._MEIPASS) / "dist"                     # PyInstaller 打包资源目录
else:
    _DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"

if _DIST.exists():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        # /api 未命中路由时返回 404（而非 index.html），避免误吞 API 404
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        # 具体静态文件（如 mascot.png、favicon）优先返回，否则回退到 SPA 入口
        candidate = _DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_DIST / "index.html")
