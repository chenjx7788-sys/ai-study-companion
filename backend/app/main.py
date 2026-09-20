import sys
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from app_version import __version__ as APP_VERSION
from .core.config import settings
from .database import Base, engine
from .routers import materials, ai, notes, kb, chat, review, settings as settings_router, asr, folders, stats, podcasts, ephemeral, browser

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
            # 外部内容接入：来源渠道 / 溯源稳定键 / 元信息快照。
            # 存量记录全部视为本地上传（origin 默认 'upload'）→ 旧数据行为完全不变。
            if "origin" not in cols:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE materials ADD COLUMN origin VARCHAR(16) DEFAULT 'upload'"))
            if "origin_ref" not in cols:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE materials ADD COLUMN origin_ref TEXT DEFAULT ''"))
            if "origin_meta" not in cols:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE materials ADD COLUMN origin_meta TEXT DEFAULT ''"))
        if "podcasts" in insp.get_table_names():
            cols = {c["name"] for c in insp.get_columns("podcasts")}
            if "audio_sig" not in cols:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE podcasts ADD COLUMN audio_sig VARCHAR(32) DEFAULT ''"))
                # 回填：已有音频视为与其当前脚本一致（否则升级后所有旧播客都会被标成「音频过期」）
                _backfill_podcast_audio_sig()
            if "instruction" not in cols:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE podcasts ADD COLUMN instruction TEXT DEFAULT ''"))
            # 背景音乐（默认关闭）。旧记录 bgm_id 为空串 → 指纹不变，不会被误判过期。
            if "bgm_id" not in cols:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE podcasts ADD COLUMN bgm_id VARCHAR(64) DEFAULT ''"))
            if "bgm_volume" not in cols:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE podcasts ADD COLUMN bgm_volume INTEGER DEFAULT -20"))
            # 记住曲名：素材被删后 bgm_id 仍指向它（不静默改作品），但 track_name()
            # 会返回空 → 界面只能显示「已失效」。存下最后一次成功选择时的曲名。
            if "bgm_label" not in cols:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE podcasts ADD COLUMN bgm_label VARCHAR(64) DEFAULT ''"))
                _backfill_podcast_bgm_label()
    except Exception:
        pass


def _backfill_podcast_audio_sig():
    """为升级前已合成的播客回填 audio_sig（幂等）"""
    from .database import SessionLocal
    from .models import Podcast
    from .services.podcast import script_signature
    db = SessionLocal()
    try:
        rows = db.query(Podcast).filter(Podcast.audio_name != "").all()
        for p in rows:
            if not (p.audio_sig or "").strip():
                p.audio_sig = script_signature(p.script or [], p.voice_map or {})
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def _backfill_podcast_bgm_label():
    """为已有记录回填 BGM 曲名（幂等）。

    必须在**升级时**做：等曲目被删掉之后再想回填，名字就已经查不到了。
    """
    from .database import SessionLocal
    from .models import Podcast
    from .services import bgm
    db = SessionLocal()
    try:
        rows = db.query(Podcast).filter(Podcast.bgm_id != "").all()
        for p in rows:
            if not (p.bgm_label or "").strip():
                p.bgm_label = bgm.track_name(p.bgm_id or "")
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def _backfill_material_origin():
    """把存量材料按已知信息回填 origin（幂等）。

    升级前没有 origin 概念，全部按「本地上传」处理即可；这里只做一件额外的精确化：
    `storage_mode='reference'` 的记录本就来自「本地文件直引」入口 → 标为 local，
    便于后续按来源渠道筛选与统计时不把这两种入口混在一起。
    """
    from sqlalchemy import text
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "UPDATE materials SET origin = 'upload' "
                "WHERE origin IS NULL OR origin = '' OR origin NOT IN ('upload','local','url','wx','weread','feed')"
            ))
            conn.execute(text(
                "UPDATE materials SET origin = 'local' WHERE storage_mode = 'reference' AND origin = 'upload'"
            ))
            conn.execute(text("UPDATE materials SET origin_ref = '' WHERE origin_ref IS NULL"))
            conn.execute(text("UPDATE materials SET origin_meta = '{}' WHERE origin_meta IS NULL OR origin_meta = ''"))
    except Exception:
        pass   # 迁移失败不阻塞启动：缺列时下次启动会重试


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
_backfill_material_origin()
_migrate_source_folder_to_folder()

app = FastAPI(title="AI 伴学助手", version="0.1.3")

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
            # ⚠️ 判据不能只看「count == 0」：进程被强杀后 hnsw 索引可能**只落盘一部分**，
            #    此时 count() 返回一个**假的非零小值**（实测：sqlite 里 430 条，count() 只报 45）
            #    → 既不抛异常、也不为 0 → 自愈被**静默绕过**，向量检索长期失效而无人发现
            #    （用户侧表现为「问答只能命中字面词，意思相近的话答不出来」）。
            #    权威源是 DB 的 KbEntry 记账：索引数**少于**记账数即为残缺，一律重建。
            from .models import KbEntry, Material
            from .database import SessionLocal
            db = SessionLocal()
            try:
                has_material = db.query(Material).filter(Material.parsed_status == "success").count() > 0
                expect_chunks = db.query(KbEntry).filter(KbEntry.ref_type == "chunk").count()
                expect_notes = db.query(KbEntry).filter(KbEntry.ref_type == "note").count()
                if has_material and (n_chunks != expect_chunks or n_notes != expect_notes):
                    need_rebuild = True
                    # ⚠️「多出来」与「少了」都必须触发重建：
                    #    `count() > 记账` 是「有人绕过 deindex_material 删了材料」的可靠信号
                    #    （正常走 API 删除时 count() 会同步减少）；这类向量**从未被 Chroma 标记删除**，
                    #    会被检索召回已删除的内容 —— 实测验证脚本直连清场就留下过 13 个（430 vs 417）。
                    _why = ("残缺" if (n_chunks < expect_chunks or n_notes < expect_notes)
                            else "多于记账（疑似绕过 deindex 的残留向量）")
                    print(f"[startup] 检测到索引{_why}：chunk {n_chunks}/{expect_chunks}、"
                          f"note {n_notes}/{expect_notes} → 触发重建")
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

# CORS：本地单机应用，放宽到 * 让 pywebview 内联 loading 页（origin=null）也能轮询 /api/health
app.add_middleware(
    CORSMiddleware,
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
app.include_router(podcasts.router, prefix="/api")
# 「仅本次阅读」+「最近阅读」内存快照（P0-5）。prefix 自带 /ai/ephemeral，
# 与 ai.router（/ai）并列而不冲突（FastAPI 按更具体的路径优先匹配）。
app.include_router(ephemeral.router, prefix="/api")
# 应用内 AI 浏览器（阶段 2 · WP12）：宿主状态与调试接口。
# ⚠️ 必须与其它 router 一同注册在**下方 SPA catch-all 之前**：catch-all 对 `api/` 前缀
#    一律 404，晚注册会被吞掉 —— 症状是 404（极像「路径写错了」），见 routers/browser.py 头部。
app.include_router(browser.router, prefix="/api")


@app.get("/api/health")

def health():
    return {"status": "ok"}


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

# ---------- 材料配图（P0-3h：小红书图文等本地化后的图片） ----------
# ⚠️ 路径是 `/api/assets`，与前端产物的 `/assets` **刻意分开**：
#    前者是**用户数据**（data/assets，删应用数据即清），后者是**打包产物**（dist/assets）。
#    合并会让"清空用户数据"和"清理构建产物"变成同一件事。
# ⚠️ 必须注册在下面的 SPA catch-all **之前**：catch-all 对 `api/` 前缀一律 404，
#    注册晚了图片会被它吞掉（且症状是 404 而非路径错误，极难定位）。
# ⚠️ 不套用 _HashedAssets 的强缓存：文件名是 `img_01.jpg` 这种序号，
#    跨笔记重名而内容不同；URL 里已含笔记 id 子目录，走默认缓存即可。
_DATA_ASSETS = Path(settings.data_dir) / "assets"
_DATA_ASSETS.mkdir(parents=True, exist_ok=True)
app.mount("/api/assets", StaticFiles(directory=_DATA_ASSETS), name="material_assets")


if _DIST.exists():
    class _HashedAssets(StaticFiles):
        """Vite 产物文件名含内容哈希 → 可长期强缓存。

        若不显式声明 Cache-Control，浏览器/WebView 会按 RFC 7234 走「启发式缓存」
        （时长≈(now-Last-Modified)×10%）。升级后旧 index.html 仍可能被直接复用，
        从而去请求已不存在的旧分片（404）→ 路由懒加载静默失败 → 表现为「点击没反应」。
        """

        def file_response(self, *args, **kwargs):
            resp = super().file_response(*args, **kwargs)
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            return resp

    app.mount("/assets", _HashedAssets(directory=_DIST / "assets"), name="assets")

    # index.html 等非哈希文件：必须每次回源校验（配合 ETag 走 304，开销可忽略），
    # 否则升级后 WebView 会继续用旧入口，导致新旧分片错配。
    NO_CACHE = {"Cache-Control": "no-cache, must-revalidate"}

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        # /api 未命中路由时返回 404（而非 index.html），避免误吞 API 404
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        # 具体静态文件（如 mascot.png、favicon）优先返回，否则回退到 SPA 入口
        candidate = _DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate, headers=NO_CACHE)
        return FileResponse(_DIST / "index.html", headers=NO_CACHE)
