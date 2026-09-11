"""LLM 配置存储：设置页保存到 data/llm_settings.json，优先于环境变量

多模型：llm_models 为模型列表，每个模型独立配置 base_url/api_key/model；
summary_model_id / chat_model_id 引用列表中的模型。旧单套配置自动迁移为默认模型。
"""
import json
from pathlib import Path
from ..core.config import settings

STORE_PATH = settings.data_dir / "llm_settings.json"

FIELDS = ["llm_base_url", "llm_api_key", "summary_model", "chat_model",
          "llm_models", "summary_model_id", "chat_model_id",
          "embedding_model", "embedding_base_url", "embedding_api_key",
          "asr_model_size",
          # 知识库切分与去噪（RAG P0 配置化）
          "chunk_size", "chunk_overlap",
          "clean_header_footer", "clean_watermark", "clean_garbled", "clean_dedup",
          # 笔记检索权重（默认 1.5，检索打分时生效，改动无需重建索引）
          "note_weight",
          "prompt_summary", "prompt_keywords", "prompt_explain", "prompt_kb_qa", "prompt_general", "prompt_review", "prompt_suggest", "prompt_quiz", "prompt_recall", "prompt_note_rewrite", "prompt_note_expand", "prompt_note_summarize", "prompt_note_continue", "prompt_stats_report", "kb_hit_threshold",
          "prompt_podcast_brief", "prompt_podcast_script", "prompt_podcast_script_direct",
          "prompt_podcast_script_solo", "prompt_podcast_script_solo_direct",
          # AI 播客 · 语音合成（edge-tts 免费音色，无需 Key）
          "tts_provider", "tts_voice_host", "tts_voice_expert", "tts_rate", "tts_gap_ms",
          # 预设标签库（材料库可先建标签再打给材料）
          "preset_tags"]


def _ensure_models(conf: dict) -> dict:
    """保证 conf 含 llm_models（兼容旧单套配置 → 迁移为模型列表，惰性幂等）

    注意：迁移出的模型**不自动引用**为总结/问答模型 —— 二者留空，由用户在设置页主动
    选择（前端对「未选择」态有明确引导）。避免用户「莫名被指定了某个模型」。
    """
    if conf.get("llm_models"):
        return conf

    models = []
    base = conf.get("llm_base_url") or ""
    key = conf.get("llm_api_key") or ""
    chat_model = conf.get("chat_model") or ""
    summary_model = conf.get("summary_model") or ""

    # 仅当配置过旧 key 或模型名时迁移；否则留空列表等用户新建
    if key or chat_model:
        models.append({"id": "legacy-chat", "name": "默认模型", "base_url": base,
                       "api_key": key, "model": chat_model})
        if summary_model and summary_model != chat_model:
            models.append({"id": "legacy-summary", "name": "总结模型", "base_url": base,
                           "api_key": key, "model": summary_model})

    conf["llm_models"] = models
    # 不自动引用：总结/问答模型留空，由用户主动选择
    conf["summary_model_id"] = conf.get("summary_model_id") or ""
    conf["chat_model_id"] = conf.get("chat_model_id") or ""
    return conf


def load() -> dict:
    """文件配置覆盖环境配置；自动补齐多模型结构"""
    conf = {f: getattr(settings, f, "") for f in FIELDS}
    if STORE_PATH.exists():
        try:
            saved = json.loads(STORE_PATH.read_text(encoding="utf-8"))
            conf.update({k: v for k, v in saved.items() if k in FIELDS and v is not None})
        except Exception:
            pass
    return _ensure_models(conf)


def save(payload: dict) -> dict:
    current = load()
    current.update({k: v for k, v in payload.items() if k in FIELDS})
    STORE_PATH.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    return current


def resolve_model(model_id: str | None):
    """按 model_id 找模型配置，返回 (base_url, api_key, model)；找不到回退全局默认"""
    conf = load()
    if model_id:
        for m in conf.get("llm_models") or []:
            if m.get("id") == model_id:
                return (m.get("base_url") or conf["llm_base_url"],
                        m.get("api_key") or conf["llm_api_key"],
                        m.get("model") or "")
    return (conf["llm_base_url"], conf["llm_api_key"], "")
