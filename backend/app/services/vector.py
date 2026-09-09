"""向量库服务：Chroma 本地持久化 + 可插拔 embedding

embedding 策略（按优先级）：
1. 设置页配置了 embedding_model → 走 OpenAI 兼容 embeddings 接口
   （embedding_base_url / embedding_api_key 可单独配，默认复用 LLM 的）
2. 未配置 → Chroma 内置本地模型（onnxruntime，首次自动下载 ~80MB）

集合设计：
- kb_chunks：原文块（weight 1.0）
- kb_notes：笔记（weight 默认 1.5，可在知识库页修改，检索分数加权）
检索：两集合各取 top_k → 分数 = (1 - distance) × weight → 合并排序
"""
import hashlib
import re

from ..core.config import settings
from . import settings_store

CHUNK_COL = "kb_chunks"
NOTE_COL = "kb_notes"


# ---------- embedding ----------

def _embed_via_api(texts: list[str], conf: dict) -> list[list[float]]:
    from openai import OpenAI
    client = OpenAI(
        base_url=conf.get("embedding_base_url") or conf["llm_base_url"],
        api_key=conf.get("embedding_api_key") or conf["llm_api_key"],
        timeout=60,
    )
    resp = client.embeddings.create(model=conf["embedding_model"], input=texts)
    return [d.embedding for d in resp.data]


def embed_texts(texts: list[str], is_query: bool = False) -> list[list[float]]:
    conf = settings_store.load()
    if conf.get("embedding_model"):
        return _embed_via_api(texts, conf)
    # 本地兜底：BGE-small-zh 中文向量模型（ONNX 量化版，首次从国内镜像下载 ~25MB）
    return _bge_embed(texts, is_query)


# ---------- 本地 BGE-small-zh embedding ----------

_BGE_DIR = settings.data_dir / "models" / "bge-small-zh-v1.5"
_BGE_FILES = {
    "model.onnx": "https://hf-mirror.com/Xenova/bge-small-zh-v1.5/resolve/main/onnx/model_quantized.onnx",
    "tokenizer.json": "https://hf-mirror.com/Xenova/bge-small-zh-v1.5/resolve/main/tokenizer.json",
}
_BGE_QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："
_bge_session = None
_bge_tokenizer = None


def _ensure_bge():
    """下载模型文件并加载（幂等）"""
    global _bge_session, _bge_tokenizer
    if _bge_session is not None:
        return
    _BGE_DIR.mkdir(parents=True, exist_ok=True)
    for name, url in _BGE_FILES.items():
        target = _BGE_DIR / name
        if not target.exists():
            print(f"[bge] 下载 {name} ...")
            # hf-mirror 的 CDN 会拦 urllib 默认 UA，用 requests + 浏览器 UA
            import requests as _req
            r = _req.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=300)
            r.raise_for_status()
            target.write_bytes(r.content)
    import onnxruntime as ort
    from tokenizers import Tokenizer
    _bge_session = ort.InferenceSession(str(_BGE_DIR / "model.onnx"), providers=["CPUExecutionProvider"])
    _bge_tokenizer = Tokenizer.from_file(str(_BGE_DIR / "tokenizer.json"))
    _bge_tokenizer.enable_truncation(max_length=512)
    _bge_tokenizer.enable_padding()  # 批量推理必须等长


def _bge_embed(texts: list[str], is_query: bool) -> list[list[float]]:
    _ensure_bge()
    import numpy as np
    if is_query:
        texts = [_BGE_QUERY_PREFIX + t for t in texts]
    encodings = _bge_tokenizer.encode_batch(texts)
    input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
    attention = np.array([e.attention_mask for e in encodings], dtype=np.int64)
    token_type = np.array([e.type_ids for e in encodings], dtype=np.int64)
    outputs = _bge_session.run(None, {
        "input_ids": input_ids,
        "attention_mask": attention,
        "token_type_ids": token_type,
    })
    hidden = outputs[0]                                   # (B, L, 512)
    mask = attention[..., None]                           # (B, L, 1)
    pooled = (hidden * mask).sum(axis=1) / mask.sum(axis=1)  # mean pooling
    norms = np.linalg.norm(pooled, axis=1, keepdims=True)
    return (pooled / norms).tolist()


# ---------- Chroma 集合 ----------

def get_collection(name: str):
    import chromadb  # 懒加载：chromadb 导入约 2s，延迟到首次向量读写，加速冷启动
    client = chromadb.PersistentClient(path=str(settings.chroma_dir))
    return client.get_or_create_collection(name)


def _collection_for(kind: str) -> str:
    return NOTE_COL if kind == "note" else CHUNK_COL


def make_embedding_id(kind: str, ref_id: int, seq: int = 0) -> str:
    raw = f"{kind}:{ref_id}:{seq}"
    return hashlib.md5(raw.encode()).hexdigest()


# ---------- 写入 ----------

_SENT_SPLIT_RE = re.compile(r"(?<=[。！？；!?;])|\n")


def _sentences(text: str) -> list[str]:
    """按句边界（。！？； + 换行）切原子单元。列表/表格行以换行为界，天然保持整行不拆"""
    return [s for s in (p.strip() for p in _SENT_SPLIT_RE.split(text)) if s]


def _sliding(text: str, size: int, overlap: int) -> list[str]:
    """滑窗兜底：单句仍超长时的最终降级（保证任何输入不炸）"""
    parts, start = [], 0
    while start < len(text):
        parts.append(text[start:start + size])
        start += size - overlap
    return parts


def _chunking_params() -> tuple[int, int]:
    """切分参数：用户配置（设置页）优先，回落 config 默认值，并做范围钳制"""
    conf = settings_store.load()
    try:
        size = int(conf.get("chunk_size") or settings.chunk_size)
    except (TypeError, ValueError):
        size = settings.chunk_size
    try:
        overlap = int(conf.get("chunk_overlap") if conf.get("chunk_overlap") is not None
                      else settings.chunk_overlap)
    except (TypeError, ValueError):
        overlap = settings.chunk_overlap
    size = max(200, min(2000, size))
    overlap = max(0, min(overlap, size // 2))
    return size, overlap


def split_text(text: str, size: int | None = None, overlap: int | None = None) -> list[str]:
    """三级结构化切分，保持语义片段完整：

    L1 段落：按空行分段，贪婪打包至 size；
    L2 句切：单段超 size → 按句边界切分打包（PDF 页文本无空行，按行/句为单位）；
    L3 滑窗：单句仍超 size → 字数滑窗兜底。
    重叠为句级：新块开头携带前块末尾若干单元，总长 ≤ overlap。
    size/overlap 缺省时读设置页配置（chunk_size / chunk_overlap）。
    """
    if size is None or overlap is None:
        cfg_size, cfg_overlap = _chunking_params()
        size = cfg_size if size is None else size
        overlap = cfg_overlap if overlap is None else overlap
    # 防御性钳制：显式传参也可能 overlap >= size（_sliding 步长为 size-overlap，≤0 会死循环）
    size = max(1, size)
    overlap = max(0, min(overlap, size - 1))
    text = text.strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]

    # L1 → L2/L3：把全文展开为原子单元（段落 / 句 / 滑窗片）
    units: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if len(para) <= size:
            units.append(para)
        else:
            for sent in _sentences(para):
                if len(sent) <= size:
                    units.append(sent)
                else:
                    units.extend(_sliding(sent, size, overlap))

    # 贪婪打包 + 句级重叠
    blocks, buf, buf_units = [], "", []
    for u in units:
        cand = buf + "\n" + u if buf else u
        if len(cand) <= size:
            buf, buf_units = cand, buf_units + [u]
            continue
        blocks.append(buf)
        carry, total = [], 0
        if overlap > 0:
            for prev in reversed(buf_units):
                if total + len(prev) > overlap:
                    break
                carry.insert(0, prev)
                total += len(prev)
        # carry + u 超 size 时放弃重叠（best-effort，不复制整段）
        if carry and len("\n".join(carry + [u])) <= size:
            buf, buf_units = "\n".join(carry + [u]), carry + [u]
        else:
            buf, buf_units = u, [u]
    if buf and buf not in blocks[-1:]:
        blocks.append(buf)
    return blocks


def index_texts(items: list[dict], kind: str):
    """批量入库（幂等，先删后增）：items = [{embedding_id, text, metadata}]"""
    if not items:
        return
    col = get_collection(_collection_for(kind))
    col.delete(ids=[i["embedding_id"] for i in items])  # 不存在的 id 会被忽略
    batch = 4   # 小批量：降低 embedding 内存峰值（内存紧张环境下 16 会 MemoryError）
    for i in range(0, len(items), batch):
        group = items[i:i + batch]
        embeddings = embed_texts([g["text"] for g in group])
        col.add(
            ids=[g["embedding_id"] for g in group],
            documents=[g["text"] for g in group],
            embeddings=embeddings,
            metadatas=[_clean_metadata(g["metadata"]) for g in group],
        )


def _clean_metadata(meta: dict) -> dict:
    """清洗 metadata：chromadb 不接受 None，兜底为空串，避免脏数据（如孤儿笔记悬空外键）导致入库崩溃"""
    out = {}
    for k, v in (meta or {}).items():
        out[k] = "" if v is None else v
    return out


# ---------- 检索 ----------

def search(query: str, top_k: int | None = None, kinds: tuple = ("chunk", "note"),
           where: dict | None = None) -> list[dict]:
    """按集合检索 → 加权合并。
    kinds 限定类型范围：chunk(资料原文) / note(笔记)
    where 为 chroma metadata 过滤（如指定材料 {"material_id": {"$in": [1,2]}}）
    返回 [{ref_type, ref_id, material_id, text, metadata, score}]"""
    top_k = top_k or settings.kb_top_k
    q_emb = embed_texts([query], is_query=True)[0]
    hits = []
    # 笔记权重来自设置（可修改，默认 1.5）；每次检索现读，改动即时生效
    note_w = float(settings_store.load().get("note_weight") or 1.5)
    candidates = []
    if "chunk" in kinds:
        candidates.append((CHUNK_COL, 1.0))
    if "note" in kinds:
        candidates.append((NOTE_COL, note_w))
    for name, weight in candidates:
        col = get_collection(name)
        if col.count() == 0:
            continue
        n = min(top_k, col.count())
        if where:
            # where 过滤后的匹配数可能小于 n_results，先查匹配数避免 chroma 报错
            matched = col.get(where=where)
            if not matched or not matched.get("ids"):
                continue
            n = min(n, len(matched["ids"]))
        res = col.query(query_embeddings=[q_emb], n_results=n, **({"where": where} if where else {}))
        for i in range(len(res["ids"][0])):
            distance = res["distances"][0][i]
            meta = res["metadatas"][0][i] or {}
            hits.append({
                "ref_type": meta.get("ref_type", "chunk" if name == CHUNK_COL else "note"),
                "ref_id": meta.get("ref_id"),
                "material_id": meta.get("material_id"),
                "text": res["documents"][0][i],
                "metadata": meta,
                "score": round((1 - distance) * weight, 4),
            })
    hits.sort(key=lambda h: h["score"], reverse=True)
    return hits[:top_k]


# ---------- 删除 ----------

def delete_by_ref(ref_type: str, ref_id: int):
    col = get_collection(_collection_for(ref_type))
    results = col.get(where={"ref_id": ref_id})
    if results and results.get("ids"):
        col.delete(ids=results["ids"])


def delete_by_material(material_id: int):
    """删除材料时联动清理其全部索引（按 metadata.material_id 匹配）"""
    for name in (CHUNK_COL, NOTE_COL):
        col = get_collection(name)
        results = col.get(where={"material_id": material_id})
        if results and results.get("ids"):
            col.delete(ids=results["ids"])
