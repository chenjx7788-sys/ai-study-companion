"""炼丹炉应用服务（2026-10-27）：序列化 / 校验 / 人格注入 / AI 草稿与优化 / 试聊装配。

定位：应用 = 给别人用的产品（人格 + 知识边界 + 独立会话）；技能 = 给自己用的能力，双概念分立。
硬规则（违反即静默出错）：
- R1 检索必须锁 where={"material_id": {"$in": ids}}，严禁 where=None（静默检索全库）
- R3 应用至少绑 1 份资料（创建 400、解绑到 0 拒存）
- R7 AI 只产草稿与优化建议，最终值必须经人保存（本模块所有 assist/preview 均不入库）
"""
import re
import secrets

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..core.config import settings
from ..models import App, AppMaterial, AppVersion, Material, MaterialChunk, AIAsset, ChatSession, ChatMessage
from . import llm
from . import retrieval_agent as retrieval_agent_svc
from . import search as search_svc
from . import settings_store

# ---- 丹灵形象（2026-10-27 P1：程序化独版形象，零外部依赖）----
# 存储形态：spirit 列存紧凑令牌 "g:<deco>:<palette>"（如 g:flame:2）。
# 兼容老值 book/spark/calm（P0 三内置变体）→ 渲染端按映射解释。
# ⚠️ 后端无文生图通道（llm.py 是纯 chat 客户端），独版形象走 artworks.py 同款程序化路线；
#    调色板只有 8 组且前后端各持一份，禁增删（移位会让存量令牌变色），只可微调色值。
DECOS = ("book", "flame", "halo", "sprout", "star", "horn")   # 头顶装饰（人格气质）
PALETTE_COUNT = 8                                              # 与前端 spirit.js PALETTES 等长
SPIRITS = ("book", "spark", "calm")                            # 老值（仅兼容读，不再下发）
_LEGACY_TO_DECO = {"book": "book", "spark": "flame", "calm": "halo"}
_SPIRIT_RE = re.compile(r"^g:(book|flame|halo|sprout|star|horn):([0-7])$")
_LIMITS = {"name": 64, "persona": 4000, "boundaries": 2000, "miss_text": 500,
           "welcome": 500, "questions": 3, "question_len": 80}


def _spirit_hash(s: str) -> int:
    """前后端共用的极简哈希（JS 可逐字镜像）：sum((i+1)*ord(c))。独版形象无需加密强度。"""
    return sum((i + 1) * ord(c) for i, c in enumerate(s))


def roll_spirit(name: str, seed: int | str = 0, deco: str | None = None) -> str:
    """按名字确定性生成独版形象令牌：调色板随 (name, seed) 哈希，装饰可指定或同哈希定。
    ⚠️ 与前端 spirit.js::rollSpiritPreview 同算法——出炉汇总页的预览要和入库真身一致。"""
    h = _spirit_hash(f"{name}::{seed}")
    d = deco if deco in DECOS else DECOS[h % len(DECOS)]
    return f"g:{d}:{h % PALETTE_COUNT}"


def normalize_spirit(value: str | None, name: str = "") -> str:
    """入库前归一化：g: 令牌原样保留；deco/老值 → 按名字哈希成令牌；空 → 默认 book。"""
    v = (value or "").strip()
    if _SPIRIT_RE.match(v):
        return v
    deco = v if v in DECOS else _LEGACY_TO_DECO.get(v)
    return roll_spirit(name or "app", deco=deco)


def reroll_spirit(app: App) -> str:
    """换一身：保留装饰、随机换配色（避免与当前撞色）。"""
    cur = app.spirit or ""
    m = _SPIRIT_RE.match(cur)
    deco = m.group(1) if m else _LEGACY_TO_DECO.get(cur, "book")
    cur_p = int(m.group(2)) if m else -1
    p = secrets.randbelow(PALETTE_COUNT - 1)
    if p >= cur_p:
        p += 1                       # 跳过当前色，保证必有变化
    return f"g:{deco}:{p}"


def get_app_or_404(db: Session, app_id: int) -> App:
    app = db.get(App, app_id)
    if not app:
        raise HTTPException(404, "应用不存在")
    return app


def material_ids(db: Session, app_id: int) -> list[int]:
    return [r.material_id for r in
            db.query(AppMaterial).filter(AppMaterial.app_id == app_id).all()]


def app_to_dict(app: App, db: Session) -> dict:
    mids = material_ids(db, app.id)
    mats = db.query(Material).filter(Material.id.in_(mids)).all() if mids else []
    # 默契度口径：已聊 N 场（会话数直接统计，不建表）
    session_count = db.query(ChatSession).filter(ChatSession.app_id == app.id).count()
    return {
        "id": app.id, "name": app.name, "spirit": app.spirit or "book",
        "persona": app.persona or "", "boundaries": app.boundaries or "",
        "miss_text": app.miss_text or "", "welcome": app.welcome or "",
        "questions": app.questions or [], "model_id": app.model_id or "",
        "suggest_on": bool(app.suggest_on), "status": app.status or "active",
        "material_ids": mids,
        "materials": [{"id": m.id, "title": m.title, "format": m.format} for m in mats],
        "material_count": len(mids), "session_count": session_count,
        "created_at": app.created_at.isoformat() if app.created_at else None,
        "updated_at": app.updated_at.isoformat() if app.updated_at else None,
    }


def list_apps_slim(db: Session) -> list[dict]:
    """列表卡片专用投影（2026-10-30，O1）。

    原来 GET /apps 对每个应用调 app_to_dict：绑定的 material_ids、材料详情、会话 count 各一次，
    N 个应用 3N 次查询，且把整段人格（上限 4000 字）下发 —— 卡片实际只显示 46 字。
    现在绑定数/会话数各一次 group by 算完，人格只回前 300 字（字段名改为 persona_head，
    避免「同名不同义」）；需要全文请走 GET /apps/{id}。
    """
    from sqlalchemy import func
    bind = dict(db.query(AppMaterial.app_id, func.count())
                .group_by(AppMaterial.app_id).all())
    sess = dict(db.query(ChatSession.app_id, func.count())
                .filter(ChatSession.app_id.isnot(None))
                .group_by(ChatSession.app_id).all())
    rows = db.query(App).order_by(App.updated_at.desc(), App.id.desc()).all()
    return [{
        "id": a.id, "name": a.name, "spirit": a.spirit or "book",
        "persona_head": (a.persona or "")[:300],
        "status": a.status or "active",
        "material_count": int(bind.get(a.id, 0)),
        "session_count": int(sess.get(a.id, 0)),
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "updated_at": a.updated_at.isoformat() if a.updated_at else None,
    } for a in rows]


def check_materials_ready(db: Session, ids: list[int]) -> list[Material]:
    """绑定前置校验：存在 + 解析完成（parsing 期间 AI 接口一律 400）"""
    mats = []
    for mid in ids:
        m = db.get(Material, mid)
        if not m:
            raise HTTPException(400, f"资料不存在：#{mid}")
        if m.parsed_status != "success":
            raise HTTPException(400, f"「{m.title}」解析未完成，暂不能炼入")
        mats.append(m)
    return mats


def validate_fields(name, persona, spirit, questions, *,
                    boundaries: str | None = None, miss_text: str | None = None,
                    welcome: str | None = None) -> None:
    """字段校验。boundaries/miss_text/welcome 传 None = 「本次不改」，不校验。
    ⚠️ 2026-10-29 补：这三段上限此前只写在 _LIMITS 里、没人执行 —— 直接打接口可写任意长，
       而它们会被 build_injection 原样拼进 system prompt（挤压真正的知识上下文）。
       前端 maxlength 不是防线，接口才是。"""
    if not (name or "").strip():
        raise HTTPException(400, "应用名称不能为空")
    if not (persona or "").strip():
        raise HTTPException(400, "人格设定不能为空")
    if spirit and not (_SPIRIT_RE.match(spirit) or spirit in SPIRITS or spirit in DECOS):
        raise HTTPException(400, "形象只支持内置变体或 g: 形象令牌")
    if len(name.strip()) > _LIMITS["name"]:
        raise HTTPException(400, "应用名称过长")
    if len(persona) > _LIMITS["persona"]:
        raise HTTPException(400, "人格设定过长")
    for _key, _val, _label in (("boundaries", boundaries, "回答边界"),
                               ("miss_text", miss_text, "未命中口径"),
                               ("welcome", welcome, "开场白")):
        if _val is not None and len(_val) > _LIMITS[_key]:
            raise HTTPException(400, f"{_label}过长（上限 {_LIMITS[_key]} 字）")
    if len(questions) > _LIMITS["questions"]:
        raise HTTPException(400, "建议问题最多 3 条")
    for q in questions:
        if len(str(q)) > _LIMITS["question_len"]:
            raise HTTPException(400, "建议问题单条过长")


def build_injection(app: App) -> str:
    """人格与边界注入段（拼在 system 尾部）。
    ⚠️ 纯字符串拼接（照抄技能 build_injection 模式），禁用 str.format —— persona 里的花括号会炸。
    空段不留空标题。"""
    parts = [f"【你是「{app.name}」】"]
    if (app.persona or "").strip():
        parts.append(app.persona.strip())
    if (app.boundaries or "").strip():
        parts.append("【回答边界】\n" + app.boundaries.strip())
    if (app.miss_text or "").strip():
        parts.append("【未命中口径】资料里找不到时，严格按此口径如实拒答，不得硬答、不得动用通用知识：\n"
                     + app.miss_text.strip())
    return "\n\n".join(parts)


def search_locked(query: str, db: Session, ids: list[int], top_k: int | None = None) -> list[dict]:
    """R1：应用检索唯一入口。where 永远带 material_id $in，本函数不接受空 ids。"""
    if not ids:
        raise HTTPException(400, "应用的资料已被删除或尚未绑定，请先在配置页重新绑定资料")
    hits = search_svc.search(query, db, kinds=("chunk", "note"),
                             where={"material_id": {"$in": ids}}, top_k=top_k)
    allow = set(ids)
    return [h for h in hits if h.get("material_id") in allow]


def filter_hits(hits: list[dict]) -> list[dict]:
    """命中过滤（与主问答同口径）：from_fts/from_entity/from_title 免阈值，其余 vec_score 达阈值。
    ⚠️⚠️ 应用语义里「绑定资料」是**语料宇宙**、不是单问强意图（不同于主问答的指定条目必引用）
    —— 不做阈值过滤，向量召回永远能捞出点什么 → kb_miss 永不发生、边界形同虚设
    （探针 V4/V5 实测抓红：绑 A 问 B 的内容也 kb_hit=True）。"""
    try:
        th = float(settings_store.load().get("kb_hit_threshold", settings.kb_hit_threshold))
    except Exception:
        th = settings.kb_hit_threshold
    return [h for h in hits
            if h.get("from_fts") or h.get("from_entity") or h.get("from_title")
            or (h.get("vec_score") or 0.0) >= th]


# ---------- AI 辅助（草稿 / 优化）：只产出建议，绝不入库 ----------

def _chapter_map(db: Session, material_id: int) -> dict[int, AIAsset]:
    """分章讲义（知识地图）：{章号: 最新版}。读法与 simple_learn._chapter_notes 同口径。"""
    rows = (db.query(AIAsset)
            .filter(AIAsset.material_id == material_id, AIAsset.type == "learn_note_ch")
            .order_by(AIAsset.version.desc(), AIAsset.id.desc()).all())
    out: dict[int, AIAsset] = {}
    for a in rows:
        ch = (a.anchor or {}).get("ch")
        if isinstance(ch, int) and ch not in out:
            out[ch] = a
    return out


def _latest_asset(db: Session, material_id: int, asset_type: str) -> AIAsset | None:
    return (db.query(AIAsset)
            .filter(AIAsset.material_id == material_id, AIAsset.type == asset_type)
            .order_by(AIAsset.version.desc(), AIAsset.id.desc()).first())


def _material_excerpt(db: Session, m: Material, limit: int = 900) -> str:
    """草稿素材（2026-10-27 P1）：分章讲义（知识地图）→ 整篇讲义 → 最新摘要 → 前 2 正文块。
    分章讲义是材料的结构化解经（章名+要点），比随机 chunk 采样更能撑起人格草稿。"""
    ch_notes = _chapter_map(db, m.id)
    if ch_notes:
        per = max(110, limit // min(len(ch_notes), 8))
        parts = []
        for ch in sorted(ch_notes)[:8]:
            n = ch_notes[ch]
            name = (n.anchor or {}).get("name") or f"第{ch}章"
            parts.append(f"◆ {name}\n{(n.content or '').strip()[:per]}")
        return "\n".join(parts)[:limit]
    a = _latest_asset(db, m.id, "learn_note")
    if a and (a.content or "").strip():
        return a.content.strip()[:limit]
    a = _latest_asset(db, m.id, "summary")
    if a and (a.content or "").strip():
        return a.content.strip()[:limit]
    chunks = (db.query(MaterialChunk)
              .filter(MaterialChunk.material_id == m.id)
              .order_by(MaterialChunk.id).limit(2).all())
    return "\n".join((c.content or "")[:450] for c in chunks)[:limit]


_DRAFT_PROMPT = """你在为「炼丹炉」设计一个 AI 应用的配置草稿。用户把下列资料炼成一个有专属人格与知识边界的应用助手。

资料：
{blocks}

只输出一个 JSON 对象（不要任何其他内容、不要代码块包裹），字段：
- "name": 应用名（≤20字，体现资料，如「《XXX》领读人」）
- "spirit": 丹灵头顶装饰，按人格气质只能从 "book"（书卷气）/ "flame"（元气火苗）/ "halo"（沉稳光环）/ "sprout"（成长嫩芽）/ "star"（启明星）/ "horn"（小犄角）中选一个
- "persona": 人格设定（≤300字），用【身份与视角】【语言风格】【回答策略】【禁用表达】四段结构
- "boundaries": 回答边界（≤150字，只答什么 / 拒答什么）
- "miss_text": 未命中口径（≤80字，超出资料范围时的拒答话术，要有人格感）
- "welcome": 开场白（≤60字，第一人称，不说「作为 AI」）
- "questions": 3 个建议问题的数组（各 ≤20字，必须是资料内容能回答的）"""


def assist_draft(db: Session, ids: list[int]) -> dict:
    if not ids:
        raise HTTPException(400, "请至少选择 1 份资料再生成草稿")
    mats = check_materials_ready(db, ids)
    blocks = []
    for m in mats[:5]:                      # 草稿素材上限 5 份，防 prompt 膨胀
        blocks.append(f"《{m.title}》\n{_material_excerpt(db, m)}")
    prompt = _DRAFT_PROMPT.replace("{blocks}", "\n\n".join(blocks))[:6000]
    raw = llm.chat([{"role": "user", "content": prompt}], kind="app_draft")
    obj = llm._parse_drill_json_object(
        raw, keys=("name", "persona", "boundaries", "miss_text", "welcome", "questions"),
        err_msg="草稿生成失败，请重试")
    if obj.get("spirit") not in DECOS:
        obj["spirit"] = _LEGACY_TO_DECO.get(obj.get("spirit"), "book")   # 老提示词残留值也接住
    obj["questions"] = [str(q)[:80] for q in (obj.get("questions") or [])][:3] or []
    for k in ("name", "persona", "boundaries", "miss_text", "welcome"):
        obj[k] = str(obj.get(k) or "").strip()
    return obj


_POLISH_STRUCT = {
    "persona": "【身份与视角】【语言风格】【回答策略】【禁用表达】",
    "bounds": "【只答】【拒答】【拒答方式】【冲突时】",
}


def assist_polish(name: str, field: str, text: str) -> dict:
    """字段级结构化优化（借鉴竞品编排栏）。field ∈ persona/bounds。"""
    if field not in _POLISH_STRUCT:
        raise HTTPException(400, "field 只支持 persona / bounds")
    if not (text or "").strip():
        raise HTTPException(400, "先写点内容，或用 AI 草稿")
    prompt = (
        f"把以下 AI 应用「{name or '未命名应用'}」的{'人格设定' if field == 'persona' else '回答边界'}"
        f"优化为结构化描述。\n\n原文：\n{text.strip()[:2000]}\n\n"
        f"要求：用 {_POLISH_STRUCT[field]} 几段组织；总长度 ≤300 字；保留原意、不虚构资料内容；"
        "只输出一个 JSON 对象 {\"text\": \"...\"}，不要其他内容。"
    )
    raw = llm.chat([{"role": "user", "content": prompt}], kind="app_polish")
    obj = llm._parse_drill_json_object(raw, keys=("text",), err_msg="优化失败，请重试")
    return {"text": str(obj["text"]).strip()[:2000]}


# ---------- 闲置主动搭话（P1）：一句人格化搭话 + 建议问题；不落库 ----------

_NUDGE_PROMPT = """{persona_block}

用户在对话页停留了一会儿没有说话。请主动开口，只输出一个 JSON 对象：
- "text": 一句自然的搭话（≤40字，第一人称，有人格感，不说「作为 AI」，可引导回资料内容）
- "questions": 至多 2 个用户此刻可能想问的问题（各 ≤20字，必须是资料能回答的；没有合适就给空数组）"""


def gen_nudge(db: Session, app: App, session_id: int | None = None) -> dict:
    """搭话生成。轻量单次调用；路由侧吞异常（前端静默不显示，打扰度第一）。"""
    mats = db.query(Material).filter(Material.id.in_(material_ids(db, app.id))).all()
    titles = "、".join(f"《{m.title}》" for m in mats) or "（未绑定资料）"
    history = ""
    if session_id:
        rows = (db.query(ChatMessage)
                .filter(ChatMessage.session_id == session_id)
                .order_by(ChatMessage.id.desc()).limit(6).all())
        rows.reverse()
        history = "\n".join(f"{'用户' if r.role == 'user' else app.name}：{(r.content or '')[:120]}"
                            for r in rows)
    prompt = (_NUDGE_PROMPT.replace("{persona_block}", build_injection(app))
              + "\n\n资料范围：" + titles
              + "\n最近对话：\n" + (history or "（还没聊过）"))
    raw = llm.chat([{"role": "user", "content": prompt[:4000]}], kind="app_nudge")
    obj = llm._parse_drill_json_object(raw, keys=("text",), err_msg="搭话生成失败")
    text = str(obj.get("text") or "").strip()[:60]
    qs = [str(q).strip()[:40] for q in (obj.get("questions") or []) if str(q).strip()][:2]
    if not text:
        raise HTTPException(502, "搭话内容为空")
    return {"text": text, "questions": qs}


# ---------- 试聊装配（preview）：吃表单草稿值，不落库、不建会话 ----------

PREVIEW_HISTORY_TURNS = 3        # 最多带最近 3 轮（即 6 条消息）
PREVIEW_HISTORY_CHAR_CAP = 4000  # 历史总字符上限，超出从最旧端丢
PREVIEW_MSG_CHAR_CAP = 1200      # 单条消息上限


def normalize_preview_history(history) -> list[dict]:
    """把前端传来的 history 收敛成可安全拼进 messages 的片段（2026-10-29）。

    ⚠️ 三件必须挡住的事，缺一件都是静默故障：
      ① role 只认 user/assistant —— 放行 system 等于让试聊页改写人格注入（越狱）；
      ② content 必须是 str —— dict/None 进 messages 会让上游直接 400，
         而前端看到的是「生成失败」，完全对不上症状；
      ③ 首条必须是 user —— 部分上游要求 user/assistant 交替，以 assistant 开头会报错。
    脏数据一律丢弃而非抛错：试聊是调试器，不该因为一条坏记录整块不可用。
    """
    if not isinstance(history, list):
        return []
    kept: list[dict] = []
    for m in history:
        if not isinstance(m, dict):
            continue
        role, content = m.get("role"), m.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str):
            continue
        content = content.strip()
        if not content:
            continue
        kept.append({"role": role, "content": content[:PREVIEW_MSG_CHAR_CAP]})
    kept = kept[-PREVIEW_HISTORY_TURNS * 2:]
    # 总量从最旧端截：保留最近的部分（越近越相关）
    total, out = 0, []
    for m in reversed(kept):
        if total + len(m["content"]) > PREVIEW_HISTORY_CHAR_CAP:
            break
        total += len(m["content"])
        out.append(m)
    out.reverse()
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


def _preview_retrieve(db: Session, ids: list[int], query: str) -> list[dict]:
    """试聊检索（2026-10-30，O7）：复用正式对话的 retrieve() 编排，只把语料宇宙锁到绑定资料。

    为什么必须复用：预览原来直接 search_locked + filter_hits —— 没有改写重检、没有碎片过滤、
    没有计划模式的注入上限，于是同一个问题会「试聊拒答、正式对话答得出」（也可能反过来），
    用户会误判成「人格/边界配错了」。现在两侧同一套判据，差异只剩「锁范围」这一项业务语义。

    口径说明：
    - 范围锁仍是双保险（retrieve 的 lock 闭包）：实体图谱召回路不吃 where。
    - 开关口径沿用正式侧「仅显式 False 才关」（conf 缺省是 ""，按 falsy 判会把默认当成关）。
    - 预览不做 specified 强意图的恒等过滤：应用没有「指定单份必引用」语义。
    """
    conf = settings_store.load()
    try:
        th = float(conf.get("kb_hit_threshold", settings.kb_hit_threshold))
    except Exception:
        th = settings.kb_hit_threshold
    allow = set(ids)

    def _do_search(q: str) -> list[dict]:
        return search_svc.search(q, db, kinds=("chunk", "note"),
                                 where={"material_id": {"$in": ids}}, top_k=5)

    def _lock(hs: list[dict]) -> list[dict]:
        return [h for h in hs if h.get("material_id") in allow]

    def _quality(hs: list[dict]) -> list[dict]:
        f = [h for h in hs
             if h.get("from_fts") or h.get("from_entity") or h.get("from_title")
             or (h.get("vec_score") or 0.0) >= th]
        solid = [h for h in f if not search_svc.is_fragment(h.get("text") or "")]
        return solid if solid else f

    res = retrieval_agent_svc.retrieve(
        query, query,
        do_search=_do_search, lock=_lock, quality_filter=_quality, augment=None,
        can_plan=conf.get("retrieval_plan_enabled") is not False,
        can_rewrite=conf.get("query_rewrite_enabled") is not False,
        top_k=5)
    return res["filtered"][:res["inject_limit"]]


def build_preview_messages(db: Session, cfg: dict, question: str,
                           history=None) -> tuple[list[dict], bool]:
    """返回 (messages, kb_hit)。与正式对话同语义：检索锁 + 人格注入 + 未命中口径。

    history：试聊面板的多轮上下文（只影响「模型看到的对话」，不参与检索）。
    ⚠️ 检索**仍然只按当前问题**做：历史里的旧问题不重复检索，
       否则每轮都把历史检索片段重塞一遍，token 随轮数线性膨胀。
    """
    ids = cfg.get("material_ids") or []
    if not ids:
        raise HTTPException(400, "试聊至少绑定 1 份资料")
    check_materials_ready(db, ids)
    hist = normalize_preview_history(history)
    hits = _preview_retrieve(db, ids, question)
    # 追问兜底（2026-10-29）：多轮里「它多长」「那第二条呢」这类依赖上文的问法，
    # 按当前问题单独检索必然 miss，紧接着会被判成 kb_miss → 按未命中口径拒答，
    # 多轮等于没用（还更糟：用户以为能追问，实际被判「不在资料范围」）。
    # 故仅在首次未命中时，用「上一轮用户问题 + 当前问题」再检索一次。
    # ⚠️ 只救 miss、只重试 1 次：命中路径零变化，避免旧话题把新问题拽回去。
    if not hits and hist:
        last_user = next((m["content"] for m in reversed(hist) if m["role"] == "user"), "")
        if last_user:
            merged = f"{last_user} {question}"[:300]
            hits = _preview_retrieve(db, ids, merged)
    pseudo = App(name=cfg.get("name") or "未命名应用", persona=cfg.get("persona") or "",
                 boundaries=cfg.get("boundaries") or "", miss_text=cfg.get("miss_text") or "")
    system = build_injection(pseudo)
    if hits:
        # ⚠️⚠️ 正文在 hit["text"]（见 services/vector.py 与 llm.build_kb_qa_prompt 的同口径用法）。
        # 2026-10-29 实测抓到：原代码取 `snippet` / `content`，两个字段检索结果里都不存在
        # → ctx 恒为空 → 模型看到「知识库相关内容」是空的 → **永远按未命中口径拒答**。
        # 症状极具误导性：kb_hit=True（检索确实命中）但答案是 miss_text 原文，
        # 用户会以为「人格/边界配错了」。保留 snippet/content 兜底是给非检索来源的 hit 用。
        ctx = "\n\n".join((h.get("text") or h.get("snippet") or h.get("content") or "")
                          for h in hits[:5])[:2400]
        system += "\n\n【要求】只依据「知识库相关内容」回答；回答带有人格。"
        user = f"【知识库相关内容】\n{ctx}\n\n【问题】\n{question}"
    else:
        # 未命中 = kb_miss 语义预览：严格按定制口径拒答，不兜底
        system += "\n\n【当前状况】资料中未命中相关内容。请严格按未命中口径回应，不得硬答、不得动用通用知识。"
        user = question
    msgs: list[dict] = [{"role": "system", "content": system}]
    msgs.extend(hist)
    msgs.append({"role": "user", "content": user})
    return msgs, bool(hits)


# ---------- 人格版本化（P2）：PATCH 致实际变化时自动快照；回滚先快照当前 ----------

TRACKED_FIELDS = ("name", "spirit", "persona", "boundaries", "miss_text",
                  "welcome", "questions", "model_id", "suggest_on")


def _snapshot_of(app: App) -> dict:
    return {k: getattr(app, k) for k in TRACKED_FIELDS}


# 版本留档上限（O6）：最近 N 条 + 最早的 1 条
VERSION_KEEP = 30


def maybe_snapshot(db: Session, app: App, incoming: dict, note: str = "") -> int:
    """incoming = 即将应用的新值（仅含本次要改的追踪字段）。
    与现值逐一比对，有实际差异才把**旧值**整体存为一个版本；返回新版本号或 0（未快照）。
    ⚠️ 在 PATCH 应用新值之前调用；比对规范化（strip/同型）防止「同义改写」产生垃圾版本。"""
    def norm(v):
        if isinstance(v, str):
            return v.strip()
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        return v
    changed = any(k in incoming and norm(incoming[k]) != norm(getattr(app, k)) for k in TRACKED_FIELDS)
    if not changed:
        return 0
    ver = (db.query(AppVersion).filter(AppVersion.app_id == app.id)
           .order_by(AppVersion.version.desc()).first())
    nxt = (ver.version if ver else 0) + 1
    db.add(AppVersion(app_id=app.id, version=nxt, snapshot=_snapshot_of(app), note=note))
    # O6（2026-10-30）：长跑应用每次实质修改留一条，会堆到几百条且面板不分页
    # → 只留最近 30 条 + 最早的 1 条（最早那条通常是初始配置，回滚锚点）。
    db.flush()
    olds = (db.query(AppVersion).filter(AppVersion.app_id == app.id)
            .order_by(AppVersion.version.desc()).all())
    if len(olds) > VERSION_KEEP + 1:
        keep = {v.id for v in olds[:VERSION_KEEP]} | {olds[-1].id}
        for v in olds:
            if v.id not in keep:
                db.delete(v)
    return nxt


def list_versions(db: Session, app_id: int) -> list[dict]:
    rows = (db.query(AppVersion).filter(AppVersion.app_id == app_id)
            .order_by(AppVersion.version.desc()).all())
    # O2（2026-10-30）：不再下发全量 snapshot —— 每条含整段人格（上限 4000 字），
    # 前端只用 persona_head / note / version / created_at，回滚按 id 走，正文根本不需要。
    return [{"id": r.id, "version": r.version, "note": r.note or "",
             "persona_head": str((r.snapshot or {}).get("persona") or "")[:40],
             "created_at": r.created_at.isoformat() if r.created_at else None}
            for r in rows]


def restore_version(db: Session, app: App, version_id: int) -> App:
    """回滚到指定版本：先快照当前态（可撤销），再整体套用快照值。"""
    v = db.get(AppVersion, version_id)
    if not v or v.app_id != app.id:
        raise HTTPException(404, "版本不存在")
    snap = v.snapshot or {}
    if not snap.get("persona"):
        raise HTTPException(400, "该版本快照数据异常（无人格）")
    maybe_snapshot(db, app, snap, note=f"回滚到 v{v.version} 前自动快照")
    for k in TRACKED_FIELDS:
        if k in snap:
            setattr(app, k, snap[k])
    db.commit()
    db.refresh(app)
    return app


# ---------- 应用空间·用量（P2）：全部现算，不建冗余表 ----------

def app_stats(db: Session, app_id: int) -> dict:
    from datetime import date, datetime, time, timedelta
    from sqlalchemy import func
    sess_count = db.query(ChatSession).filter(ChatSession.app_id == app_id).count()
    q_count = (db.query(ChatMessage)
               .filter(ChatMessage.app_id == app_id, ChatMessage.role == "user").count())
    # O3（2026-10-30）：原来把该应用所有助手消息 .all() 读进内存再 Python 计数，
    # 消息一多就是纯浪费；改成 SQL 聚合。
    # 口径（看板命中率，2026-10-30）：分母只算「真的给出了回答」的助手消息 ——
    # 报错/中断（error 非空）与空内容不是「资料没命中」，计进分母会把命中率系统性拉低，
    # 且用户看到的数字无法解释。被排除的条数单独回 failed_count，面板可解释。
    from sqlalchemy import and_, or_
    answered = and_(
        ChatMessage.role == "assistant",
        ChatMessage.content.isnot(None),
        ChatMessage.content != "",
        or_(ChatMessage.error.is_(None), ChatMessage.error == ""),
    )
    hits = int((db.query(func.count(ChatMessage.id))
                .filter(ChatMessage.app_id == app_id, answered,
                        ChatMessage.kb_hit.is_(True)).scalar()) or 0)
    valid = int((db.query(func.count(ChatMessage.id))
                 .filter(ChatMessage.app_id == app_id, answered).scalar()) or 0)
    miss = valid - hits
    all_ans = int((db.query(func.count(ChatMessage.id))
                   .filter(ChatMessage.app_id == app_id,
                           ChatMessage.role == "assistant").scalar()) or 0)
    failed = all_ans - valid
    last = (db.query(ChatSession).filter(ChatSession.app_id == app_id)
            .order_by(ChatSession.updated_at.desc()).first())
    # 近 7 天提问趋势（按自然日；O3：加时间过滤，别把全表都 group 一遍）
    since = datetime.combine(date.today() - timedelta(days=6), time.min)
    rows = (db.query(func.date(ChatMessage.created_at), func.count())
            .filter(ChatMessage.app_id == app_id, ChatMessage.role == "user",
                    ChatMessage.created_at >= since)
            .group_by(func.date(ChatMessage.created_at)).all())
    by_day = {str(d): c for d, c in rows}
    daily = []
    today = date.today()
    for i in range(6, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        daily.append({"date": d[5:], "count": int(by_day.get(d, 0))})
    return {"session_count": sess_count, "question_count": q_count,
            "hit_count": hits, "miss_count": miss,
            "answered_count": valid, "failed_count": failed,
            "hit_rate": round(hits / valid, 2) if valid else None,
            "last_active": last.updated_at.isoformat() if last and last.updated_at else None,
            "daily": daily}


# ---------- 删除级联 ----------

def delete_app_cascade(db: Session, app: App) -> dict:
    """级联：消息 → 会话 → 版本 → 绑定 → 应用。
    ⚠️ bulk delete 不过 ORM 级联（SQLite 默认不强制外键），顺序手动保证；资料本体不动。"""
    # ⚠️⚠️ 消息按 **session_id** 删，不只按 app_id（2026-10-29 修）：只认 app_id 会漏掉
    #    会话里 app_id 为 NULL 的历史消息（归属曾被错误改写的存量数据）→ 孤儿消息
    #    → SQLite 复用被删会话 id 后，孤儿消息挂到新会话上（实测串台）。
    _sess_ids = [r[0] for r in db.query(ChatSession.id).filter(ChatSession.app_id == app.id).all()]
    _cond = ChatMessage.app_id == app.id
    if _sess_ids:
        _cond = or_(_cond, ChatMessage.session_id.in_(_sess_ids))
    n_msg = db.query(ChatMessage).filter(_cond).delete(synchronize_session=False)
    n_sess = db.query(ChatSession).filter(ChatSession.app_id == app.id).delete(synchronize_session=False)
    db.query(AppVersion).filter(AppVersion.app_id == app.id).delete(synchronize_session=False)
    db.query(AppMaterial).filter(AppMaterial.app_id == app.id).delete(synchronize_session=False)
    db.delete(app)
    db.commit()
    return {"ok": True, "deleted_sessions": n_sess, "deleted_messages": n_msg}
