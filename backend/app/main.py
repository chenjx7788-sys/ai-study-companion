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
from .routers import materials, ai, notes, kb, chat, review, settings as settings_router, asr, folders, stats, podcasts, ephemeral, imgproxy, ext, simple_learn, drill, videos, zipvoice

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
            # 实体抽取内容指纹（重试解析时指纹未变则跳过 LLM 抽取，防重复消耗 token）
            if "extract_sig" not in cols:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE materials ADD COLUMN extract_sig VARCHAR(32) DEFAULT ''"))
            # 材料内容性质（P0 工艺分流）：常量默认值 —— 老行自动 ""，行为与改造前一致。
            if "doc_kind" not in cols:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE materials ADD COLUMN doc_kind VARCHAR(16) DEFAULT ''"))
        # 原文块性质（body / img_ocr）：老行全部视为 body → 存量行为完全不变。
        # img_ocr 由回填脚本按 saved_from=='clip' AND content LIKE '[图片]%' 单独打。
        if "material_chunks" in insp.get_table_names():
            cols = {c["name"] for c in insp.get_columns("material_chunks")}
            if "kind" not in cols:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE material_chunks ADD COLUMN kind VARCHAR(16) DEFAULT 'body'"))
            # 内嵌图 OCR 块的页内位置（PDF 图级定位）：老行空串 → 前端降级页级跳转
            if "img_rect" not in cols:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE material_chunks ADD COLUMN img_rect VARCHAR(64) DEFAULT ''"))
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
            # 生成方式（two_step / fast / verbatim）。旧记录空串 → 视为 AI 改写，无需回填。
            if "gen_mode" not in cols:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE podcasts ADD COLUMN gen_mode VARCHAR(16) DEFAULT ''"))
        if "videos" in insp.get_table_names():
            cols = {c["name"] for c in insp.get_columns("videos")}
            # 渲染耗时（阶段 2 补列）。旧的视频作品留 0 → 界面按「未记录」处理，
            # 不会凭空显示一个假的耗时。
            if "render_seconds" not in cols:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE videos ADD COLUMN render_seconds FLOAT DEFAULT 0"))
            # 跳过闸门（PRD 3.9.7）：连续失败计数 + 当前成片的跳过标注。
            # 旧行默认 0/False → 行为与改造前完全一致（不允许跳过、无标注）。
            if "gate_fail_count" not in cols:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE videos ADD COLUMN gate_fail_count INTEGER DEFAULT 0"))
            if "gate_skipped" not in cols:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE videos ADD COLUMN gate_skipped BOOLEAN DEFAULT 0"))
            # 本片音色（作品级，与 podcasts.voice_map 同构）。老行填 '{}' → 与改造前
            # 行为一致（跟随设置页的语音设置），不会凭空冒出一个音色。
            if "voice_map" not in cols:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE videos ADD COLUMN voice_map JSON DEFAULT '{}'"))
            # 自定义背景图（解压型皮肤）。老行默认 '' → 渲染走皮肤自带画面，
            # 与改造前逐帧一致（不会凭空出现一张背景）。
            if "bg_id" not in cols:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE videos ADD COLUMN bg_id VARCHAR(64) DEFAULT ''"))
            # 背景音乐（默认关闭，仅「本片单独生成」音轨生效）。旧记录 bgm_id 为空串 →
            # 指纹口径不变，不会被误判过期（与 podcasts 同款三列；视频无历史 BGM，无需回填 label）。
            if "bgm_id" not in cols:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE videos ADD COLUMN bgm_id VARCHAR(64) DEFAULT ''"))
            if "bgm_volume" not in cols:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE videos ADD COLUMN bgm_volume INTEGER DEFAULT -20"))
            if "bgm_label" not in cols:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE videos ADD COLUMN bgm_label VARCHAR(64) DEFAULT ''"))
            # 皮肤预设变体（电台浅色/深色各 3 套）。旧行默认 '' = 默认套 →
            # 渲染画面与升级前逐像素一致，不会凭空换配色。
            if "skin_variant" not in cols:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE videos ADD COLUMN skin_variant VARCHAR(16) DEFAULT ''"))
            # 画面署名（电台浅色/深色画面底部那行字）。旧行默认 '' = 回落作品标题。
            # ⚠️ 这条迁移会让**存量电台作品的画面真的变**（底部那行字从内置装饰字
            #    「且 听」/「夜 曲」变成作品标题），所以 visual_sig 会跟着变、界面
            #    如实报「视觉方案已更换」—— 那是**真实变化，不是误报**，提示重新生成
            #    正是我们要的（不删除旧成片，只标注 + 允许一键重建）。
            if "brand_text" not in cols:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE videos ADD COLUMN brand_text VARCHAR(32) DEFAULT ''"))
            # 存量**死状态**自愈：口播层选了、内容层却是空（或非法值）。
            # 旧口径的 `_norm_skin` 允许写入空串，而生成路径会直接拒绝 → 这份作品
            # 永远生成不出来（报「未知的视觉方案：talking_head」）。结构与渲染路径
            # 一直按 graph 兜底，这里把存量行对齐到同一口径。
            # ⚠️ 幂等：修完之后再无行命中，重启不会反复改写（也不动别的字段）。
            with engine.begin() as conn:
                fixed = conn.execute(text(
                    "UPDATE videos SET inner_skin = 'graph' "
                    "WHERE skin = 'talking_head' "
                    "AND (inner_skin IS NULL OR TRIM(inner_skin) = '' "
                    "     OR LOWER(TRIM(inner_skin)) NOT IN ('source', 'graph'))"
                )).rowcount
            if fixed:
                print(f"[migrate] 修复 {fixed} 部作品的空内容层（数字人口播 → 结构图）")
        if "activity_logs" in insp.get_table_names():
            cols = {c["name"] for c in insp.get_columns("activity_logs")}
            # 视频埋点明细（PRD 4.2）：stage / completed 等小对象 JSON。旧行默认 ''。
            if "detail" not in cols:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE activity_logs ADD COLUMN detail VARCHAR(255) DEFAULT ''"))
        if "drill_turns" in insp.get_table_names():
            cols = {c["name"] for c in insp.get_columns("drill_turns")}
            # 难度自适应（C 档）：记录本题**实际使用**的难度。
            # 旧数据留空 → 前端按「未记录」处理，不会凭空多出「已调整难度」标注。
            if "difficulty" not in cols:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE drill_turns ADD COLUMN difficulty VARCHAR(8)"))
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


def _backfill_chunk_kind():
    """给存量「网页剪藏材料里的图片 OCR 块」打 img_ocr 标记（幂等，每次启动都跑）。

    ⚠️ 判据是 `origin_meta.saved_from == 'clip'` 而**不是** `origin`：早期小红书笔记的
       origin 记的是 `'upload'`（实测 #40/#43/#45 都是小红书），按 origin 枚举判会漏掉
       最大的那几篇。`origin_meta` 在 SQLite 里是 TEXT，故按 dict / str 两种形态都认。
    ⚠️ 只改 `kind`、**绝不改 content** —— content 是 extract_sig 指纹的输入，
       动它会误触发重新抽实体、白烧 token；向量与全文检索读的都是 content，不受影响。
    """
    import json
    from sqlalchemy import or_
    from .database import SessionLocal
    from .models import Material, MaterialChunk
    db = SessionLocal()
    try:
        clip_ids = []
        for mid, raw_meta in db.query(Material.id, Material.origin_meta).all():
            meta = raw_meta
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = {}
            if isinstance(meta, dict) and meta.get("saved_from") == "clip":
                clip_ids.append(mid)
        if not clip_ids:
            return
        n = (db.query(MaterialChunk)
             .filter(MaterialChunk.material_id.in_(clip_ids),
                     or_(MaterialChunk.kind == "body", MaterialChunk.kind.is_(None)),
                     MaterialChunk.content.like("[图片]%"))
             .update({"kind": "img_ocr"}, synchronize_session=False))
        db.commit()
        if n:
            print(f"[migrate] 回填 {n} 个图片 OCR 块为 img_ocr（来自 {len(clip_ids)} 篇网页材料）")
    except Exception as e:
        db.rollback()
        print(f"[migrate] 回填 chunk kind 失败（不阻塞启动）: {e}")
    finally:
        db.close()


_migrate_columns()
_backfill_material_origin()
_migrate_source_folder_to_folder()
_backfill_chunk_kind()

app = FastAPI(title="知萤 - AI知识中枢", version="0.1.6")

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

# 陪练会话：启动时回收孤儿（PRD 2.2.6）。
# ⚠️ 会话生命周期的唯一权威是 DB：进程重启后内存任务清零，若不回收，
#    status ∈ {outlining,running} 的会话会永远停在「进行中」且点不开（僵尸卡片），
#    并永久占用并发槽位（updated_at 不再刷新 → 槽位只进不出）。
# ⚠️ 打印一律用 ASCII：CI 的 Windows runner 控制台是 cp1252，中文 print 会直接崩。
def _recover_drill_orphans():
    def _run():
        try:
            from .database import SessionLocal
            from .services import drill as drill_svc
            db = SessionLocal()
            try:
                n = drill_svc.recover_orphans(db)
                if n:
                    print(f"[startup] recovered {n} orphan drill session(s) -> failed")
            finally:
                db.close()
        except Exception:
            pass
    threading.Thread(target=_run, daemon=True).start()


_recover_drill_orphans()


# AI 视频：启动时回收孤儿作品（PRD 2.2.6）。
# ⚠️ 与陪练同理：进程重启后内存任务清零，若不回收，status ∈ {scripting,queued,rendering}
#    的作品会永远停在「渲染中」且点不开，并永久占住并发槽（updated_at 不再刷新）。
def _recover_video_orphans():
    def _run():
        try:
            from .database import SessionLocal
            from .services import video as video_svc
            db = SessionLocal()
            try:
                n = video_svc.recover_orphans(db)
                if n:
                    print(f"[startup] recovered {n} orphan video(s) -> failed")
                # ⚠️ 回收之后还要**把队列接回来**：内存里的渲染队列随进程消失，
                #    但 DB 里 status=queued 的作品还在。不重排的话它们没有执行者，
                #    而回收窗口（默认 30 分钟，必须 ≥ 单条渲染超时）又不会立刻清它 ——
                #    表现是「排队中」卡半小时，然后突然变「服务重启中断」。
                from .routers import videos as videos_router
                m = videos_router.resume_queued_renders()
                if m:
                    print(f"[startup] re-queued {m} video(s) for rendering")
            finally:
                db.close()
        except Exception:
            pass
    threading.Thread(target=_run, daemon=True).start()


_recover_video_orphans()

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
# 语音合成模型（routers/zipvoice.py）：/api/zipvoice/*（状态 / 按需下载 / 进度 / 音色）
# 与 asr（Whisper 转写模型）同构 —— 前端复用同一套「下载卡片 + 轮询」交互。
app.include_router(zipvoice.router, prefix="/api")
app.include_router(folders.router, prefix="/api")
app.include_router(stats.router, prefix="/api")
app.include_router(podcasts.router, prefix="/api")
app.include_router(simple_learn.router, prefix="/api")
# AI 陪练（模块 J）：/api/drill/*（会话 CRUD / 范围摘要 / start·answer 两条 SSE / finish）
# ⚠️ 必须与其它 router 一样注册在下方 SPA catch-all **之前**。
app.include_router(drill.router, prefix="/api")
# AI 视频（模块 K）：/api/videos/*（作品 CRUD / options / plan·render 两条 SSE / 产物）
# ⚠️ 必须注册在下方 SPA catch-all **之前**，否则接口会被它吞成 404。
app.include_router(videos.router, prefix="/api")
# 「仅本次阅读」+「最近阅读」内存快照（P0-5）。prefix 自带 /ai/ephemeral，
# 与 ai.router（/ai）并列而不冲突（FastAPI 按更具体的路径优先匹配）。
app.include_router(ephemeral.router, prefix="/api")
# 只读图片代理（routers/imgproxy.py）：外链图被防盗链拦掉时，由后端取回再转发。
# 服务于「不落盘」的临时阅读/剪藏预览，以及 R6 之前入库的历史材料（正文里仍是外链）。
# ⚠️ 必须与其它 router 一样注册在**下方 SPA catch-all 之前**。
app.include_router(imgproxy.router, prefix="/api")
# 浏览器扩展通道（routers/ext.py）：/api/ext/* 强制 X-BrainMate-Token；
# /token 是免 token 的配对信息端点（带 Sec-Fetch-Site 防护），与受保护端点分两个 router。
# ⚠️ 同样必须注册在下方 SPA catch-all 之前。
app.include_router(ext.router, prefix="/api")
app.include_router(ext.pairing_router, prefix="/api")



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
