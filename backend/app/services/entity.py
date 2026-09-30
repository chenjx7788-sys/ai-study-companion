"""知识图谱实体抽取服务（轻量实体索引 · P1）

职责：
1. 从材料原文块中抽取「实体 + 关系」（复用 LLM 调用链，不做图数据库、不做多跳查询）
2. 实体归并去重（跨 chunk 同名实体合并）+ 噪声过滤（单字/停用词/超长名）
3. 落库到 entities / relations / entity_refs 三表

设计要点（来自《AI伴学助手_知识图谱实体抽取探测报告.md》实测结论）：
- 实体类型强约束为 5 类（person/product/concept/knowledge/method），开放式抽取会让图谱变蜘蛛网
- 必须两道后处理：①按 name+material 归并 ②过滤单字/修辞性并列词（如「术/道/势」）
- 抽取失败静默降级：图谱是「锦上添花」的检索增强，绝不影响材料解析/索引主流程

⚠️ 抽取是 LLM 调用，放在后台线程、每材料只跑一轮（复用材料全部块拼接后的抽样文本），
成本与「主题标注」同量级（每材料 N 次调用，N=抽样块数，可按材料规模裁剪）。
"""
import re
from sqlalchemy.orm import Session

# 5 类实体，给 LLM 的强约束（中文展示名 → 存储枚举）
ENTITY_TYPES = ("person", "product", "concept", "knowledge", "method")
TYPE_LABELS = {
    "person": "人物", "product": "产品", "concept": "概念",
    "knowledge": "知识点", "method": "方法",
}

# 抽取时每个 chunk 最多喂给 LLM 的字数（超长截断，避免单次调用过大）
_CHUNK_MAX_CHARS = 2000

# 抽样块数上限：材料块再多也只抽前 N 块（控制调用成本；长文档首部通常是核心概念区）
_MAX_SAMPLE_CHUNKS = 30

EXTRACT_SYSTEM = """你是一名知识抽取助手。请从给定的学习材料片段中抽取实体与关系，用于构建帮助用户「学习与吸收」的知识图谱。

实体类型严格限定为以下 5 类（不要引入其他类型，type 用英文枚举）：
- person（人物）：真实或虚拟的人物、作者、专家
- product（产品）：具体产品、工具、软件、平台、App 名称
- concept（概念）：有明确定义的专业概念、术语（如"费曼学习法""用户价值公式"）
- knowledge（知识点）：材料中要说明的一条可记忆的要点/结论
- method（方法）：方法论、流程、步骤、模型、框架（如"RICE 优先级排序"）

抽取要求：
1. 只抽取这段文字中明确出现的实体，不要凭空联想、不要编造片段没有的内容；
2. "用户""系统""我们""你"这类泛称、以及"的""了""是"等虚词绝不能作为实体；
3. 单个汉字（如"术""道""势"这类用典单字）不要作为实体，除非是约定俗成的完整术语；
4. 每个实体 name 给一个 ≤20 字的标准名称；
5. 实体类型优先把「可记忆的要点/结论」标为 knowledge（而非 concept）——
   例如「用户价值 = 新体验 - 旧体验 - 替换成本」是 knowledge，「用户价值」是 concept；
6. 关系抽取时，除层级关系（包含/属于）外，主动识别以下「学习价值高」的关系类型：
   - 因果：「A 导致 B」「A 是 B 的原因」「因为 A 所以 B」
   - 对比：「A 与 B 的区别」「A 优于 B」「A 相比 B 更…」
   - 应用：「A 用于 B」「A 可以解决 B」「学会 A 能做 B」
   - 前提：「A 是 B 的前提」「学 B 之前要先懂 A」「A 依赖于 B」
   关系名用一个简短动词短语（≤10 字），不要臆造关系；
7. 对于「孤立实体」（片段中出现但与其它实体无明显关系的），主动判断它是否与主干概念
   存在隐含关联（如「PC端」是「直播」的载体），若存在则建立关系；确实无关则不强连；
8. 同一片段内实体/关系重复出现只列一次。

严格输出 JSON（不要输出任何其他内容，不要 Markdown 代码块），格式：
{"entities": [{"name": "实体名", "type": "person/product/concept/knowledge/method"}],
 "relations": [{"src": "实体A名", "rel": "关系", "dst": "实体B名"}]}"""


# ---------- 归并与过滤 ----------

# 单字实体黑名单（实测「术/道/势」这类用典单字被抽成独立实体）
_SINGLE_CHAR_BLACKLIST = {"术", "道", "势", "法", "器", "象", "数", "理", "气", "性", "相", "体", "用"}

# 泛称/虚词（即使 LLM 偶发漏网也兜底过滤）
_STOPWORDS = {"用户", "系统", "我们", "你们", "他们", "这个", "那个", "内容", "信息",
              "数据", "问题", "情况", "方式", "方面", "部分", "过程", "结果", "公司", "产品"}

_RE_NORMALIZE = re.compile(r"[\s　]+")


def _normalize(name: str) -> str:
    """实体名归一化：去首尾空白 + 压缩内部空白。用于归并与去重。"""
    return _RE_NORMALIZE.sub(" ", (name or "").strip())


def _is_valid_entity(name: str) -> bool:
    """噪声过滤：空名 / 超长名 / 单字（黑名单或通用单字）/ 泛称停用词 都视为无效。"""
    n = _normalize(name)
    if not n or len(n) > 40:
        return False
    if len(n) <= 1:
        return False
    if n in _SINGLE_CHAR_BLACKLIST or n in _STOPWORDS:
        return False
    return True


def _norm_type(t: str) -> str:
    """类型归一：LLM 偶发中文类型 / 大小写差异 → 5 类枚举；无效类型归为 concept。"""
    t = (t or "").strip().lower()
    if t in ENTITY_TYPES:
        return t
    # 中文类型反向映射
    zh = {"人物": "person", "产品": "product", "概念": "concept",
          "知识点": "knowledge", "方法": "method"}
    if t in zh:
        return zh[t]
    return "concept"


def clean_extract(parsed: dict) -> tuple[list[dict], list[dict]]:
    """对 LLM 抽取结果做归并 + 过滤，返回 (entities, relations)。

    - entities: [{name, type}]，按 (name, type) 去重且过滤噪声
    - relations: [{src, rel, dst}]，两端实体都必须是合法实体才保留
    """
    ents_out: dict[tuple[str, str], dict] = {}
    for e in (parsed or {}).get("entities") or []:
        if not isinstance(e, dict):
            continue
        name = _normalize(e.get("name") or "")
        if not _is_valid_entity(name):
            continue
        t = _norm_type(e.get("type") or "concept")
        ents_out[(name, t)] = {"name": name, "type": t}

    valid_names = {k[0] for k in ents_out}
    rels_out: list[dict] = []
    seen_rel: set[tuple[str, str, str]] = set()
    for r in (parsed or {}).get("relations") or []:
        if not isinstance(r, dict):
            continue
        src = _normalize(r.get("src") or "")
        dst = _normalize(r.get("dst") or "")
        rel = _normalize(r.get("rel") or "")
        if not src or not dst or not rel or len(rel) > 20:
            continue
        # 关系两端必须是已收录的合法实体（否则是悬空关系）
        if src not in valid_names or dst not in valid_names or src == dst:
            continue
        key = (src, rel, dst)
        if key in seen_rel:
            continue
        seen_rel.add(key)
        rels_out.append({"src": src, "rel": rel, "dst": dst})

    return list(ents_out.values()), rels_out


# ---------- 抽取入口 ----------

def _sample_chunks(chunks: list) -> list:
    """按结构化顺序抽块：优先取每份材料靠前的块（首部通常是核心概念区），裁剪超长块。"""
    sampled = []
    for c in chunks:
        text = (c.content or "").strip()
        if not text:
            continue
        sampled.append(text[:_CHUNK_MAX_CHARS])
        if len(sampled) >= _MAX_SAMPLE_CHUNKS:
            break
    return sampled


def extract_texts(base_url: str, api_key: str, model: str, texts: list[str]) -> list[dict]:
    """对若干段文本逐段抽取，返回未归并的 parsed dict 列表（每段一个）。

    用原生 urllib 直调（不依赖 openai SDK——项目打包态 openai 已含，但探测脚本
    环境未必有；这里统一走 urllib，代价是一次手工拼请求）。
    """
    import json as _json
    import urllib.request
    import urllib.error

    out: list[dict] = []
    url = (base_url or "").rstrip("/") + "/chat/completions"
    for text in texts:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": EXTRACT_SYSTEM},
                {"role": "user", "content": f"学习材料片段：\n\n{text}"},
            ],
            "temperature": 0.2,
        }
        req = urllib.request.Request(
            url, data=_json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = _json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"实体抽取 HTTP {e.code}: {e.read().decode('utf-8', 'ignore')[:200]}")
        raw = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        # Token 记账（kind=knowledge_graph，纳入「Token 消耗」页）：
        # 本函数绕开 llm.py 走原生 urllib（打包兼容性），若不记账，图谱抽取的成本完全隐形。
        _log_usage(model, text, raw, data.get("usage"))
        out.append(_parse_json(raw))
    return out


def _log_usage(model: str, text: str, raw: str, usage: dict | None):
    """实体抽取的 LLM 调用记账（失败静默，不影响抽取主流程）"""
    try:
        from .llm import log_usage
        if usage and usage.get("prompt_tokens") is not None:
            log_usage("knowledge_graph", model,
                      int(usage.get("prompt_tokens") or 0),
                      int(usage.get("completion_tokens") or 0))
        else:
            # 服务商未返回 usage → 按字符粗估（中英混合 ~1.6 字符/token）
            log_usage("knowledge_graph", model,
                      int(len(EXTRACT_SYSTEM + text) / 1.6), int(len(raw) / 1.6),
                      estimated=True)
    except Exception:
        pass


def _parse_json(raw: str) -> dict:
    import json as _json
    txt = (raw or "").strip()
    if txt.startswith("```"):
        txt = txt.strip("`")
        if txt.lower().startswith("json"):
            txt = txt[4:]
    s, e = txt.find("{"), txt.rfind("}")
    if s < 0 or e <= s:
        return {}
    try:
        obj = _json.loads(txt[s:e + 1])
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _resolve_llm():
    """复用项目模型配置（summary_model_id 优先，回退 legacy-chat）。"""
    from . import settings_store
    conf = settings_store.load()
    bid = conf.get("summary_model_id") or conf.get("chat_model_id") or "legacy-chat"
    base_url, api_key, model = settings_store.resolve_model(bid)
    if not model:
        base_url, api_key, model = settings_store.resolve_model("legacy-chat")
    if not api_key or not model:
        return None, None, None
    return base_url, api_key, model


def persist_entities(db: Session, material_id: int, chunks: list) -> int:
    """提取并落库一份材料的全部实体/关系/引用。返回实体数（失败返回 0）。

    ⚠️ 纯后台任务：任何异常都静默返回 0，绝不外抛（图谱是检索增强，不能拖垮主流程）。
    ⚠️ 抽取文本仅用于实体抽取，不对应 chunk 逐行落引用——引用（entity_refs）按
    「实体名在哪些 chunk 原文中出现」做子串匹配回填，保证检索扩展能精确关联到块。
    ⚠️ 指纹去重（C 方案）：内容未变且已有实体 → 不调 LLM，只本地重建引用表
    （重试解析会换 chunk id，引用必须重挂，但这步零 token）。
    """
    from ..models import Entity, Material, Relation, EntityRef

    sig = content_signature(chunks)
    m = db.get(Material, material_id)
    existing = db.query(Entity).filter(Entity.material_id == material_id).count()
    if existing and m is not None and (m.extract_sig or "") == sig:
        # 内容未变：跳过 LLM 抽取，仅按新 chunk 重建引用（chunk id 在重试解析后已变）
        rebuild_refs(db, material_id, chunks)
        return existing

    base_url, api_key, model = _resolve_llm()
    if not api_key:
        return 0
    try:
        parsed_list = extract_texts(base_url, api_key, model, _sample_chunks(chunks))
    except Exception:
        return 0

    # 归并 + 过滤（跨 chunk 同名实体合并）
    merged: dict[tuple[str, str], dict] = {}
    rels: dict[tuple[str, str, str], dict] = {}
    for p in parsed_list:
        ents, rs = clean_extract(p)
        for e in ents:
            merged[(e["name"], e["type"])] = e
        for r in rs:
            rels[(r["src"], r["rel"], r["dst"])] = r

    if not merged:
        return 0

    # 写库：先清旧（重试解析会重建），再插入
    db.query(Entity).filter(Entity.material_id == material_id).delete()
    db.query(Relation).filter(Relation.material_id == material_id).delete()
    db.query(EntityRef).filter(EntityRef.material_id == material_id).delete()
    db.flush()

    ent_count = 0
    for (name, t), _ in merged.items():
        db.add(Entity(name=name, type=t, material_id=material_id))
        ent_count += 1
    for (src, rel, dst), _ in rels.items():
        db.add(Relation(material_id=material_id, src=src, rel=rel, dst=dst))

    # 引用回填：实体名在哪些 chunk 原文出现 → 建 entity_refs（精确子串匹配）
    _fill_refs(db, material_id, chunks, [n for (n, _t) in merged])
    # 抽取成功：记录内容指纹，下次重试解析若内容未变则免抽
    if m is not None:
        m.extract_sig = sig
    db.commit()
    return ent_count


def content_signature(chunks: list) -> str:
    """材料内容指纹：全部 chunk 内容（按顺序）拼接的 MD5。
    用于「重试解析内容未变 → 跳过实体抽取」的判定。"""
    import hashlib
    h = hashlib.md5()
    for c in chunks:
        h.update((c.content or "").encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def _fill_refs(db: Session, material_id: int, chunks: list, names: list[str]):
    """引用回填：实体名在哪些 chunk 原文出现 → 建 entity_refs（精确子串匹配）。"""
    from ..models import EntityRef
    for name in names:
        for c in chunks:
            if name in (c.content or ""):
                db.add(EntityRef(entity=name, ref_type="chunk", ref_id=c.id,
                                 material_id=material_id))


def rebuild_refs(db: Session, material_id: int, chunks: list):
    """内容未变时只重建引用表（不调 LLM）：实体/关系复用，引用按新 chunk 重挂。"""
    from ..models import Entity, EntityRef
    names = [r[0] for r in db.query(Entity.name).filter(Entity.material_id == material_id).all()]
    db.query(EntityRef).filter(EntityRef.material_id == material_id).delete()
    db.flush()
    _fill_refs(db, material_id, chunks, names)
    db.commit()
