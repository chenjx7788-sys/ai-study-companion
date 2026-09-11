"""设置路由（PRD 模块 F1，从 M4 提前：AI 功能依赖 API Key 配置）"""
import time
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from ..database import get_db
from ..services import settings_store

router = APIRouter(prefix="/settings", tags=["settings"])

MASK = "******"


@router.get("")
def get_settings():
    """返回当前配置；Key 类字段脱敏。提示词字段返回自定义值（空=用默认），另附默认模板供前端展示"""
    from ..services import llm as llm_svc
    conf = settings_store.load()
    # 模型列表：api_key 脱敏
    models = [
        {**m, "api_key": MASK if m.get("api_key") else ""}
        for m in (conf.get("llm_models") or [])
    ]
    return {
        "llm_base_url": conf["llm_base_url"],
        "llm_api_key": MASK if conf["llm_api_key"] else "",
        "summary_model": conf["summary_model"],
        "chat_model": conf["chat_model"],
        "llm_models": models,
        "summary_model_id": conf.get("summary_model_id") or "",
        "chat_model_id": conf.get("chat_model_id") or "",
        # 向量模型：空 = 本地 BGE-small-zh；配置后走 OpenAI 兼容接口
        "embedding_model": conf["embedding_model"],
        "embedding_base_url": conf.get("embedding_base_url", ""),
        "embedding_api_key": MASK if conf.get("embedding_api_key") else "",
        # 语音模型默认转写版本：空 = 自动（small 优先）；small / base = 强制指定
        "asr_model_size": conf.get("asr_model_size") or "",
        # 知识库切分与去噪（修改后对「新解析/重建索引」的材料生效）
        "chunk_size": conf.get("chunk_size") or 700,
        "chunk_overlap": conf.get("chunk_overlap") if conf.get("chunk_overlap") is not None else 100,
        "clean_header_footer": bool(conf.get("clean_header_footer", True)),
        "clean_watermark": bool(conf.get("clean_watermark", True)),
        "clean_garbled": bool(conf.get("clean_garbled", True)),
        "clean_dedup": bool(conf.get("clean_dedup", True)),
        # 笔记检索权重（检索打分时读取，改动即时生效）
        "note_weight": float(conf.get("note_weight") or 1.5),
        # 提示词：自定义值（空字符串 = 使用默认模板）
        # 遍历 FIELDS 中所有 prompt_ 字段，确保新增提示词无需改此处
        **{k: conf.get(k, "") for k in settings_store.FIELDS if k.startswith("prompt_")},
        "kb_hit_threshold": conf.get("kb_hit_threshold", 0.15),
        # 预设标签库（材料库可先建标签，再打给材料）
        "preset_tags": conf.get("preset_tags") or [],
        "prompt_defaults": llm_svc.DEFAULT_PROMPTS,
        "configured": bool(conf["llm_api_key"]) or bool(conf.get("llm_models")),
    }


@router.put("")
def update_settings(payload: dict):
    for key_field in ("llm_api_key", "embedding_api_key"):
        if payload.get(key_field) == MASK:
            payload.pop(key_field)  # 掩码原样回传 = 未修改
    # 切分参数范围钳制：分块 200–2000 字，重叠 0 至分块的一半（防异常值打爆索引）
    try:
        if "chunk_size" in payload:
            payload["chunk_size"] = max(200, min(2000, int(payload["chunk_size"])))
        if "chunk_overlap" in payload:
            payload["chunk_overlap"] = max(0, int(payload["chunk_overlap"]))
            size = int(payload.get("chunk_size") or settings_store.load().get("chunk_size") or 700)
            payload["chunk_overlap"] = min(payload["chunk_overlap"], size // 2)
    except (TypeError, ValueError):
        raise HTTPException(400, "chunk_size / chunk_overlap 必须为数字")
    # 笔记权重钳制：0.5–3.0（<1 让笔记低于原文优先级；过高会挤占原文命中）
    try:
        if "note_weight" in payload:
            payload["note_weight"] = round(max(0.5, min(3.0, float(payload["note_weight"]))), 2)
    except (TypeError, ValueError):
        raise HTTPException(400, "note_weight 必须为数字")
    # 模型列表里每个模型的 api_key 掩码原样回传 = 保留原值
    if isinstance(payload.get("llm_models"), list):
        current = settings_store.load()
        cur_models = {m["id"]: m for m in (current.get("llm_models") or [])}
        for m in payload["llm_models"]:
            if m.get("api_key") == MASK and m.get("id") in cur_models:
                m["api_key"] = cur_models[m["id"]].get("api_key", "")
    settings_store.save(payload)
    return get_settings()


@router.post("/test-model")
def test_model(payload: dict):
    """单个模型连接测试：按传入的 base_url/api_key/model 直接发一次最小调用。
    api_key 传掩码或留空时按 id 取已保存值 —— 支持列表里未保存的新模型也能直接测。
    """
    from openai import OpenAI
    conf = settings_store.load()
    model_id = payload.get("id") or ""
    base_url = (payload.get("base_url") or "").strip()
    api_key = (payload.get("api_key") or "").strip()
    model = (payload.get("model") or "").strip()

    if model_id and (api_key == MASK or not api_key or not base_url or not model):
        stored = {m["id"]: m for m in (conf.get("llm_models") or [])}.get(model_id)
        if stored:
            if api_key == MASK or not api_key:
                api_key = stored.get("api_key", "")
            base_url = base_url or stored.get("base_url", "")
            model = model or stored.get("model", "")

    if not model:
        raise HTTPException(400, "该模型未填写模型名")
    if not api_key:
        raise HTTPException(400, "该模型未配置 API Key")

    client = OpenAI(base_url=base_url or conf["llm_base_url"] or None, api_key=api_key, timeout=15)
    t0 = time.time()
    try:
        r = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
        )
    except Exception as e:
        raise HTTPException(400, f"连接失败：{str(e)[:200]}")
    # 校验响应结构：部分站点（尤其 Base URL 漏填 /v1 时）会返回 HTML 首页且状态码仍是 200，
    # 仅判断「没抛异常」会把不可用的端点误报为「连接成功」，导致用户配好了却问不出内容。
    if not getattr(r, "choices", None):
        raise HTTPException(400, "连接失败：返回内容不是有效的 OpenAI 兼容响应"
                                 "（请检查 Base URL 是否需以 /v1 结尾）")
    return {"ok": True, "latency_ms": int((time.time() - t0) * 1000), "model": model}


@router.post("/list-models")
def list_models(payload: dict):
    """按 base_url + api_key 拉取服务商可用模型列表（OpenAI 兼容 /models）"""
    from openai import OpenAI
    conf = settings_store.load()
    model_id = payload.get("id") or ""
    base_url = (payload.get("base_url") or "").strip()
    api_key = (payload.get("api_key") or "").strip()

    if model_id and (api_key == MASK or not api_key):
        stored = {m["id"]: m for m in (conf.get("llm_models") or [])}.get(model_id)
        if stored:
            api_key = stored.get("api_key", "")
            base_url = base_url or stored.get("base_url", "")

    if not api_key:
        raise HTTPException(400, "请先填写 API Key")
    base_url = base_url or conf["llm_base_url"] or None

    client = OpenAI(base_url=base_url, api_key=api_key, timeout=15)
    try:
        resp = client.models.list()
        models = [m.id for m in resp.data if getattr(m, "id", None)]
    except Exception as e:
        raise HTTPException(400, f"获取模型列表失败：{str(e)[:200]}")
    if not models:
        raise HTTPException(400, "该服务商未返回任何模型")
    return {"ok": True, "models": models}


@router.post("/test-embedding")
def test_embedding():
    """向量模型测试：API 模式发起一次真实 embedding 调用并返回向量维度；本地模式返回本地模型信息"""
    conf = settings_store.load()
    if not conf.get("embedding_model"):
        return {"ok": True, "mode": "local", "model": "BGE-small-zh-v1.5（本地）", "dim": 512}
    from ..services import vector as vector_svc
    t0 = time.time()
    try:
        vec = vector_svc.embed_texts(["测试文本"])
    except Exception as e:
        raise HTTPException(400, f"Embedding 调用失败：{str(e)[:200]}")
    return {"ok": True, "mode": "api", "model": conf["embedding_model"],
            "dim": len(vec[0]), "latency_ms": int((time.time() - t0) * 1000)}


def _dir_size(path) -> int:
    from pathlib import Path
    p = Path(path)
    if not p.exists():
        return 0
    if p.is_file():
        return p.stat().st_size
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


@router.get("/storage")
def storage_stats():
    """F2 存储占用统计：原文 / 数据库 / 向量索引"""
    from ..core.config import settings as cfg
    return {
        "files_mb": round(_dir_size(cfg.files_dir) / 1024 / 1024, 1),
        "db_mb": round(_dir_size(cfg.data_dir / "app.db") / 1024 / 1024, 1),
        "chroma_mb": round(_dir_size(cfg.chroma_dir) / 1024 / 1024, 1),
        "data_dir": str(cfg.data_dir),
    }


@router.get("/backup")
def backup():
    """F2 一键备份：打包 data 目录（排除可重下的大模型）为 zip 下载"""
    import tempfile
    from pathlib import Path
    from fastapi.responses import FileResponse
    from ..services.auto_backup import make_backup_zip
    tmp = Path(tempfile.mkdtemp())
    zip_path = tmp / f"asc-backup-{time.strftime('%Y%m%d-%H%M')}.zip"
    make_backup_zip(zip_path)
    return FileResponse(str(zip_path), filename=zip_path.name)


@router.post("/restore")
async def restore(file: UploadFile = File(...)):
    """F2 备份恢复：上传备份 zip → 校验 → 备份当前数据 → 解压还原（需重启后端生效）

    安全策略：还原前先把当前 data 目录整体备份到 data_backup_<时间戳>，可随时回滚。
    """
    import shutil, zipfile, tempfile
    from pathlib import Path
    from ..core.config import settings as cfg

    if not file.filename.endswith(".zip"):
        raise HTTPException(400, "请上传备份的 zip 文件")

    tmp = Path(tempfile.mkdtemp())
    zip_path = tmp / "restore.zip"
    zip_path.write_bytes(await file.read())
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile:
        raise HTTPException(400, "文件损坏，不是有效的 zip")

    names = zf.namelist()
    if not any(n.rstrip("/") == "app.db" for n in names):
        raise HTTPException(400, "备份文件不完整：缺少 app.db（需为本系统「一键备份」生成的 zip）")

    # 备份当前数据目录（回滚保险）
    rollback = cfg.data_dir.parent / f"data_backup_{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copytree(cfg.data_dir, rollback)
    try:
        zf.extractall(cfg.data_dir)
    except Exception as e:
        shutil.rmtree(cfg.data_dir, ignore_errors=True)
        shutil.copytree(rollback, cfg.data_dir)
        raise HTTPException(500, f"还原失败，已自动回滚：{str(e)[:150]}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return {"ok": True, "rollback_dir": str(rollback),
            "message": "还原完成。请重启后端服务后刷新页面（向量索引连接需重建）"}


@router.get("/usage")
def usage_stats(db: Session = Depends(get_db)):
    """Token 消耗统计：今日 / 累计，按业务 kind 分组（流式为估算值）"""
    from datetime import datetime, timedelta
    from sqlalchemy import func
    from ..models import LLMUsage

    # "今日"按北京时间自然日切分（created_at 存 UTC，与数据统计面板口径一致，避免凌晨 0-8 点算到昨天）
    bj_today = datetime.utcnow() + timedelta(hours=8)
    today_start = datetime(bj_today.year, bj_today.month, bj_today.day) - timedelta(hours=8)

    def agg(since=None):
        q = db.query(
            LLMUsage.kind,
            func.count(LLMUsage.id),
            func.sum(LLMUsage.prompt_tokens),
            func.sum(LLMUsage.completion_tokens),
        )
        if since:
            q = q.filter(LLMUsage.created_at >= since)
        return {
            r[0]: {"calls": r[1], "prompt_tokens": r[2] or 0, "completion_tokens": r[3] or 0}
            for r in q.group_by(LLMUsage.kind).all()
        }

    def total(rows):
        return {
            "calls": sum(v["calls"] for v in rows.values()),
            "tokens": sum(v["prompt_tokens"] + v["completion_tokens"] for v in rows.values()),
        }

    today, all_time = agg(today_start), agg()
    return {"today": {**today, "_total": total(today)}, "all": {**all_time, "_total": total(all_time)}}
