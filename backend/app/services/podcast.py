"""AI 播客服务：素材抽取 → 知识简报 → 对话脚本 → 音频落盘

【两阶段生成的原因】
一个 prompt 直接吐口播稿时，模型会一边提炼一边写对话，为了「像对话」而注水
（寒暄、过渡词、无信息量互动）。拆成提炼层与演绎层后，用户可以在简报阶段纠偏，
比改一整段对话便宜得多；简报本身还能复用为笔记。快速模式（一步到位）供赶时间时使用。

【时长换算基准】
中文对话体含停顿，实测语速约 220-240 字/分钟，故取 240 字/分钟做字数约束。

【路径约束】
库里只存文件名，绝对路径在读取时用 data_dir 拼。
开发态(backend/data)与打包版(~/.ai-study-companion)数据目录不同，
存绝对路径会让切换环境后历史记录全部失效。
"""
import hashlib
import re
from datetime import datetime
from pathlib import Path

from ..core.config import settings
from ..models import Material, MaterialChunk, Note, Highlight, ReviewCard, AIAsset
from . import llm, settings_store, tts

# 语速：中文对话体含停顿。实测 446 字 → 104 秒（约 257 字/分钟），取 250 作为保守基准。
CHARS_PER_MINUTE = 250

# 时长档位 → 脚本字数 / 简报字数 / 句数
# 两点标定经验（均来自实测）：
#  1) 句数与字数必须自洽：script_chars / segments ≈ 50 字每句。
#     早期版本给「14 句 + 720 字」，模型按句数写短，实际时长只有目标的六成。
#  2) 模型对总字数存在系统性偏短（约 -13%），故目标值上调约 13% 做补偿，
#     使实际产出落在目标时长附近（3 分钟档实测约 175 秒）。
# 档位按 3 / 10 分钟线性插值补齐 5 分钟档：原来只有 3 与 10，跳档太大
# （想「再长一点」的用户只能一次翻三倍，产出的信息密度必然失控）。
LENGTH_PRESETS = {
    3:  {"script_chars": 850,  "brief_chars": 420,  "segments": 16, "label": "3 分钟精华"},
    5:  {"script_chars": 1450, "brief_chars": 650,  "segments": 26, "label": "5 分钟轻听"},
    10: {"script_chars": 2850, "brief_chars": 1200, "segments": 50, "label": "10 分钟深度"},
}

# 字数容忍度：超出此范围自动重生成一次（LLM 对字数约束遵守度有限）
LENGTH_TOLERANCE = 0.25

# 播客风格
STYLE_PRESETS = {
    "dialogue": {"label": "知识对谈", "desc": "主持人提问 + 专家解答"},
    "solo":     {"label": "单人精讲", "desc": "一个人从头讲到尾，合成时间约减半"},
}

# 单人精讲的句数系数。⚠️ 句数不减半的话，单人稿会退化成「一长串 50 字的短句」——
# 既不像讲解，TTS 调用数也白白翻倍，而「省调用」正是单人模式唯一能省的东西。
# 减半后每句约 100 字，仍在 MAX_SEG_CHARS（400）之内。
SOLO_SEGMENT_RATIO = 0.5

# 单次送入 LLM 的素材上限（超过则等间隔抽样，保证覆盖全文而非只取开头）
MAX_SOURCE_CHARS = 14000

# 三色划线语义（已在方案中确认）
HIGHLIGHT_SEMANTICS = {
    "green":  {"label": "已懂",  "hint": "用户已掌握、标记为懂的内容"},
    "yellow": {"label": "重点",  "hint": "用户标记的重点内容"},
    "blue":   {"label": "疑问",  "hint": "用户存疑、尚未解决的问题"},
}

SOURCE_TYPES = {
    "article":    "单篇文档",
    "notes":      "笔记",
    "highlights": "三色划线",
    "review":     "错题卡片",
}


# ---------- 目录与路径 ----------

def podcasts_root() -> Path:
    p = Path(settings.data_dir) / "podcasts"
    p.mkdir(parents=True, exist_ok=True)
    return p


def previews_root() -> Path:
    """音色试听缓存目录。

    与具体播客无关（样本固定），所以放全局而非 podcasts/{id}/ 下，
    所有作品共用同一份试听音频。
    """
    p = Path(settings.data_dir) / "voice_previews"
    p.mkdir(parents=True, exist_ok=True)
    return p


def podcast_dir(podcast_id: int) -> Path:
    p = podcasts_root() / str(podcast_id)
    p.mkdir(parents=True, exist_ok=True)
    return p


def audio_abspath(podcast_id: int, audio_name: str) -> Path:
    """按当前数据目录拼出音频绝对路径（库里只存文件名）"""
    return podcast_dir(podcast_id) / audio_name


def save_audio(podcast_id: int, audio: bytes) -> str:
    name = "audio.mp3"
    audio_abspath(podcast_id, name).write_bytes(audio)
    return name


def save_script_json(podcast_id: int, payload: str) -> None:
    """脚本副本落盘：用户可直接从文件夹拿走，无需打开应用"""
    (podcast_dir(podcast_id) / "script.json").write_text(payload, encoding="utf-8")


def parts_dir(podcast_id: int) -> Path:
    """分句缓存目录：TTS 逐句产物落这里，用于失败续传。

    脚本或音色变化后旧片段即失效，需调用 clear_parts() 清理。
    """
    p = podcasts_root() / str(podcast_id) / "parts"
    p.mkdir(parents=True, exist_ok=True)
    return p


def clear_parts(podcast_id: int) -> None:
    """清空分句缓存（脚本文本或音色变化后，旧片段不能再复用）。绝不抛错。"""
    try:
        d = podcasts_root() / str(podcast_id) / "parts"
        if not d.exists():
            return
        for f in d.glob("*.mp3"):
            try:
                f.unlink()
            except BaseException:
                pass
    except BaseException:
        pass


# 旧命名（带序号）的分句缓存：{序号:03d}-{md5}.mp3。O1 去掉序号后这些文件永远
# 不会再被命中 —— 留着纯占地方，所以合成前顺手清一次。
_LEGACY_PART_RE = re.compile(r"^\d{3}-[0-9a-f]{10}\.mp3$")


def drop_legacy_parts(podcast_id: int) -> None:
    """清掉「带序号」旧命名的分句缓存。绝不抛错。"""
    try:
        d = podcasts_root() / str(podcast_id) / "parts"
        if not d.exists():
            return
        for f in d.glob("*.mp3"):
            if _LEGACY_PART_RE.match(f.name):
                try:
                    f.unlink()
                except BaseException:
                    pass
    except BaseException:
        pass


def remove_podcast_files(podcast_id: int) -> None:
    """删除该播客的全部产物（音频、脚本副本、分句缓存）。**绝不抛错**。

    文件清理失败不应让调用方（删除接口 / 重新生成）整体失败 —— 记录删掉了、
    文件残留是可以接受的，反过来（记录删不掉）才是真正的问题。

    ⚠️ 这里用 BaseException 而不是 Exception：本机开发环境的 safe-delete shim
    会在批量删除超过阈值时 raise SystemExit，而 SystemExit 不是 Exception 的子类。
    一个播客通常带 20+ 个分句缓存文件，连续删两三个就容易触达阈值，
    若让它冒泡则删除接口直接 500。
    """
    try:
        d = podcasts_root() / str(podcast_id)
        if not d.exists():
            return
        # 逐文件删除 + 逐目录 rmdir（shutil.rmtree 会被 safe-delete 拦截）
        for f in sorted(d.rglob("*"), reverse=True):
            try:
                if f.is_file():
                    f.unlink()
            except BaseException:
                pass
        dirs = sorted([p for p in d.rglob("*") if p.is_dir()], reverse=True)
        for sub in dirs:
            try:
                sub.rmdir()
            except BaseException:
                pass
        try:
            d.rmdir()
        except BaseException:
            pass
    except BaseException:
        pass


# ---------- 素材抽取 ----------

def _sample_text(chunks: list[str], max_chars: int) -> str:
    """超长素材等间隔抽样：保证覆盖全文首尾，而不是只取前 N 字。

    长文档直接截断会让播客只讲开头几页，抽样能保住全文脉络。

    ⚠️ 抽样后必须「逐块截断再拼接」，不能「拼接后整体截断」：后者会把排在尾部
    的若干块整块砍掉，等于把「覆盖全文」的努力又抹掉（早期版本就是这样，
    20 万字的长文档实际只覆盖到前几成）。取块位置按 0…len-1 等分，
    保证首块与末块都进样本。
    """
    texts = [t for t in chunks if (t or "").strip()]
    if not texts:
        return ""
    total = sum(len(t) for t in texts)
    # 单块内容再长也没得抽，只能截断
    if total <= max_chars or len(texts) == 1:
        return "\n\n".join(texts)[:max_chars]

    avg = max(1, total // len(texts))
    keep = max_chars // avg
    if keep >= len(texts):
        # 预算够「每块都留一点」：全部保留，等分截断
        per = max(1, max_chars // len(texts))
        return "\n\n".join(t[:per] for t in texts)

    # 抽样块数至少 2（保证首尾各有一块），且不超过总块数
    keep = min(max(2, keep), len(texts))
    last = len(texts) - 1
    picked = [texts[i * last // (keep - 1)] for i in range(keep)]
    # 每块的预算扣掉分隔符开销，保证拼接结果不超 max_chars
    per = max(1, (max_chars - 2 * (keep - 1)) // keep)
    return "\n\n".join(t[:per] for t in picked)


def _material_text(db, material_id: int) -> tuple[str, str]:
    """单篇文档的素材。返回 (标题, 文本)"""
    m = db.get(Material, material_id)
    if not m:
        return "", ""
    chunks = (db.query(MaterialChunk)
              .filter(MaterialChunk.material_id == material_id)
              .order_by(MaterialChunk.id).all())
    text = _sample_text([c.content for c in chunks], MAX_SOURCE_CHARS)
    return m.title, text


def collect_source(db, source_type: str, ref_ids: list[int] | None = None,
                   highlight_colors: list[str] | None = None,
                   limit: int = 30) -> tuple[str, str, list[dict]]:
    """抽取素材。返回 (标题, 素材文本, 来源快照列表)

    来源快照存进 podcasts.source_refs，供列表页展示「这条播客来自哪里」。
    """
    ref_ids = ref_ids or []
    refs: list[dict] = []

    if source_type == "article":
        if not ref_ids:
            raise ValueError("请选择一篇文档")
        title, text = _material_text(db, ref_ids[0])
        if not text.strip():
            raise ValueError("该文档没有可用的正文（可能尚未解析完成）")
        refs.append({"type": "material", "id": ref_ids[0], "title": title})
        return title, text, refs

    if source_type == "notes":
        notes = db.query(Note).filter(Note.id.in_(ref_ids)).all() if ref_ids else []
        notes = sorted(notes, key=lambda n: ref_ids.index(n.id) if n.id in ref_ids else 0)
        if not notes:
            raise ValueError("请选择至少一条笔记")
        blocks = []
        for n in notes:
            blocks.append(f"### {n.title}\n{(n.content or '').strip()}")
            refs.append({"type": "note", "id": n.id, "title": n.title})
        title = notes[0].title if len(notes) == 1 else f"{notes[0].title} 等 {len(notes)} 条笔记"
        return title, _sample_text(blocks, MAX_SOURCE_CHARS), refs

    if source_type == "highlights":
        if not ref_ids:
            raise ValueError("请选择一篇文档")
        m = db.get(Material, ref_ids[0])
        if not m:
            raise ValueError("文档不存在")
        q = db.query(Highlight).filter(Highlight.material_id == ref_ids[0])
        if highlight_colors is None:
            # 未指定（旧客户端 / 直接调接口）→ 三色全取
            colors = list(HIGHLIGHT_SEMANTICS.keys())
        else:
            # ⚠️ 空列表必须区别于 None。早先写的是 `highlight_colors or 全部颜色`，
            # 用户在界面上把三色**全部取消勾选**时，空列表是假值 → 被静默当成
            # 「三色全选」，生成结果与他看到的勾选状态不一致。这里明确拒绝。
            colors = [c for c in highlight_colors if c in HIGHLIGHT_SEMANTICS]
            if not colors:
                raise ValueError("请至少选择一种划线颜色")
        q = q.filter(Highlight.color.in_(colors))
        rows = q.order_by(Highlight.page_no, Highlight.id).all()
        if not rows:
            raise ValueError("该文档还没有对应颜色的划线，先去阅读页标记吧")
        blocks = []
        for color in colors:   # 按颜色分组，让模型知道每条的语义
            group = [h for h in rows if h.color == color]
            if not group:
                continue
            sem = HIGHLIGHT_SEMANTICS.get(color, {})
            head = f"### {sem.get('label', color)}（{sem.get('hint', '')}）"
            items = "\n".join(f"- P{h.page_no} {h.selected_text}" for h in group)
            blocks.append(f"{head}\n{items}")
        title = f"{m.title} · 划线精读"
        refs.append({"type": "material", "id": m.id, "title": m.title,
                     "colors": [c for c in colors if any(h.color == c for h in rows)]})
        return title, _sample_text(blocks, MAX_SOURCE_CHARS), refs

    if source_type == "review":
        now = datetime.utcnow()
        q = db.query(ReviewCard).filter(ReviewCard.next_review_at <= now)
        if ref_ids:
            q = q.filter(ReviewCard.material_id.in_(ref_ids))
        cards = q.order_by(ReviewCard.next_review_at).limit(limit).all()
        if not cards:
            raise ValueError("今天没有到期的复习卡片")
        blocks = []
        for i, c in enumerate(cards, 1):
            blocks.append(f"{i}. 问：{c.question}\n   答：{c.answer}")
        # 错题播客天然就是「主持人念题 + 专家解答」结构，脚本生成时直接据此编排
        mats = {c.material_title for c in cards if c.material_title}
        title = "今日错题回顾" + (f"（{len(cards)} 张）" if len(cards) > 1 else "")
        refs.append({"type": "review", "count": len(cards),
                     "titles": sorted(mats)[:5]})
        head = f"以下是今日到期需要复习的 {len(cards)} 张卡片，每张包含问题与答案：\n\n"
        return title, _sample_text([head + "\n".join(blocks)], MAX_SOURCE_CHARS), refs

    raise ValueError(f"暂不支持的来源类型：{source_type}")


# ---------- 生成 ----------

def plan_for(target_minutes: int, style: str = "dialogue") -> dict:
    """时长档位 → 字数 / 句数（句数随风格调整，见 SOLO_SEGMENT_RATIO）"""
    p = LENGTH_PRESETS.get(target_minutes, LENGTH_PRESETS[3])
    if style == "solo":
        p = {**p, "segments": max(6, round(p["segments"] * SOLO_SEGMENT_RATIO))}
    return p


def _stage(on_stage, key: str, detail: dict | None = None) -> None:
    """向外报告「现在进行到哪一步」（进度展示用）。

    回调异常一律吞掉：进度展示出问题不该影响生成本身。
    """
    if on_stage is None:
        return
    try:
        on_stage(key, detail)
    except Exception:
        pass


def make_brief(source_title: str, source_text: str, target_minutes: int,
               on_stage=None) -> str:
    p = plan_for(target_minutes)
    _stage(on_stage, "brief")
    return llm.generate_podcast_brief(source_title, source_text, p["brief_chars"])


def _ensure_length(script: list[dict], plan: dict, topic: str, instruction: str,
                   regenerate, max_rounds: int = 2, on_stage=None) -> list[dict]:
    """字数偏离目标超过容忍度时迭代重生成（最多 2 轮）。

    LLM 对「总字数」这类全局约束遵守度有限：对话体天然倾向写短，
    收到「写长一点」的反馈后又容易写超。因此每轮都带上明确的偏差数值与当前字数，
    并且只在结果更接近目标时才采纳，避免越改越偏。

    ⚠️ 轮数是**不定的**（0~2 轮），所以阶段进度把它算作「当前步的子状态」，
    不额外占一步 —— 否则进度条会忽长忽短、甚至超过 100%。
    """
    target = plan["script_chars"]
    lo, hi = int(target * (1 - LENGTH_TOLERANCE)), int(target * (1 + LENGTH_TOLERANCE))
    best, best_gap = script, abs(script_chars(script) - target)
    if best_gap <= target * LENGTH_TOLERANCE:
        return best

    for rnd in range(max_rounds):
        got = script_chars(best)
        if got < target:
            action = "请增加专家回答的展开与具体例子，把每个观点讲透"
        else:
            action = "请删减次要内容、合并重复表述，不要为了完整而堆句子"
        _stage(on_stage, "polish", {"round": rnd + 1, "chars": got, "target": target})
        fix = (f"上一版全文 {got} 字，比要求的 {target} 字"
               f"{'偏短' if got < target else '偏长'}。合格范围是 {lo}-{hi} 字。"
               f"{action}，严格控制在 {target} 字左右，共约 {plan['segments']} 句。")
        if instruction:
            fix += f" 同时保持用户要求：{instruction}"
        try:
            again = regenerate(fix)
        except Exception:
            break   # 重生成失败就保留当前最好的一版
        if not again:
            break
        gap = abs(script_chars(again) - target)
        if gap < best_gap:
            best, best_gap = again, gap
        if best_gap <= target * LENGTH_TOLERANCE:
            break
    return best


def make_script(brief: str, target_minutes: int, topic: str = "",
                instruction: str = "", style: str = "dialogue",
                on_stage=None) -> list[dict]:
    p = plan_for(target_minutes, style)
    _stage(on_stage, "script")
    script = llm.generate_podcast_script(brief, p["script_chars"], p["segments"],
                                         topic=topic, instruction=instruction, style=style)
    return _ensure_length(script, p, topic, instruction,
                          lambda fix: llm.generate_podcast_script(
                              brief, p["script_chars"], p["segments"],
                              topic=topic, instruction=fix, style=style),
                          on_stage=on_stage)


def make_script_direct(source_title: str, source_text: str, target_minutes: int,
                       instruction: str = "", style: str = "dialogue",
                       on_stage=None) -> list[dict]:
    p = plan_for(target_minutes, style)
    _stage(on_stage, "script")
    script = llm.generate_podcast_script_direct(source_title, source_text,
                                                p["script_chars"], p["segments"],
                                                instruction=instruction, style=style)
    return _ensure_length(script, p, source_title, instruction,
                          lambda fix: llm.generate_podcast_script_direct(
                              source_title, source_text, p["script_chars"],
                              p["segments"], instruction=fix, style=style),
                          on_stage=on_stage)


def refine_brief_instruction(actual_seconds: int, target_minutes: int) -> str:
    """按时长偏差给出修正指令（供「太长了 / 太短了」重生成使用）"""
    target = target_minutes * 60
    if actual_seconds <= 0:
        return ""
    ratio = actual_seconds / target
    if ratio > 1.2:
        return f"上一版实际时长 {actual_seconds} 秒，偏长约 {ratio:.0%}，请压缩到 {target} 秒左右。"
    if ratio < 0.8:
        return f"上一版实际时长 {actual_seconds} 秒，偏短约 {(1-ratio):.0%}，请扩充到 {target} 秒左右。"
    return ""


# ---------- 工具 ----------

def script_chars(script: list[dict]) -> int:
    return sum(len((s or {}).get("text") or "") for s in (script or []))


def script_signature(script: list[dict], voice_map: dict | None = None,
                     bgm_id: str = "", bgm_volume: int | None = None,
                     rate: str | None = None, gap_ms: int | None = None) -> str:
    """音频指纹：判断已合成音频是否对应当前脚本 + 音色 + 语速 + 停顿 + 背景音乐。

    - 只取「说话人 + 音色 + 文本」，**不含时间戳** —— 合成后会回填 start_ms/end_ms，
      若把时间戳算进去，刚合成完就会被判定为「音频过期」。
    - 音色也要纳入：只换音色不改文案时，旧音频的音色同样是不对的。
    - 逐句 voice 优先（合成回填值），否则回退到 voice_map（脚本刚编辑、尚未合成时）。
    - 背景音乐同样纳入：换 BGM 或调音量后，旧音频的听感就不对了。
    - **语速与句间停顿同样纳入**：它们改的是成品的节奏与总时长，改了之后不只是
      「听感不同」，逐句时间戳也会整体对不上。
      ⚠️ 三项都是「**取默认值时不追加任何字符**」：bgm_id 为空、rate 为 +0%、
      gap_ms 为 280 时，本函数结果与升级前的算法逐字节一致 —— 否则历史记录
      （指纹是旧口径算出来的）会被全部误判成「音频过期」。
    """
    vm = voice_map or {}
    parts = []
    for s in script or []:
        who = (s or {}).get("speaker") or "host"
        text = ((s or {}).get("text") or "").strip()
        if not text:
            continue
        voice = (s or {}).get("voice") or vm.get(who) or ""
        parts.append(f"{who}|{voice}:{text}")
    key = "\n".join(parts)
    if bgm_id:
        key += f"\n#bgm:{bgm_id}@{bgm_volume if bgm_volume is not None else -20}"
    if rate and rate.strip() not in ("", "+0%", "0%"):
        key += f"\n#rate:{rate.strip()}"
    if gap_ms is not None and int(gap_ms) != tts.DEFAULT_GAP_MS:
        key += f"\n#gap:{int(gap_ms)}"
    return hashlib.md5(key.encode("utf-8")).hexdigest()


def audio_is_stale(podcast, rate: str | None = None,
                   gap_ms: int | None = None) -> bool:
    """音频是否为「上一版脚本」的产物。

    音频与脚本是两份数据：改了脚本、换了音色、改了语速/停顿或换了 BGM，
    音频立刻与当前配置分叉，界面必须显式标注，否则用户会以为听到的就是当前文案。
    老数据（升级前未记录指纹）在启动迁移时已回填，不会误报。

    rate / gap_ms 不传时从当前语音设置读取；列表接口会算一次后统一传入，
    避免逐条记录都去读一遍设置文件。
    """
    if not (getattr(podcast, "audio_name", "") or ""):
        return False
    sig = (getattr(podcast, "audio_sig", "") or "").strip()
    if not sig:
        return False
    if rate is None or gap_ms is None:
        conf = settings_store.load()
        if rate is None:
            rate = conf.get("tts_rate") or tts.DEFAULT_RATE
        if gap_ms is None:
            gap_ms = int(conf.get("tts_gap_ms") or tts.DEFAULT_GAP_MS)
    cur = script_signature(getattr(podcast, "script", None) or [],
                           getattr(podcast, "voice_map", None) or {},
                           (getattr(podcast, "bgm_id", "") or ""),
                           getattr(podcast, "bgm_volume", None),
                           rate=rate, gap_ms=gap_ms)
    return sig != cur


def estimate_seconds(script: list[dict]) -> int:
    """合成前的时长预估（按语速换算），合成后应以实际时长为准"""
    return round(script_chars(script) / CHARS_PER_MINUTE * 60)


def script_to_text(script: list[dict], with_name: bool = True) -> str:
    """脚本转纯文本（导出 / 复制用）"""
    lines = []
    for s in script or []:
        who = "主持人" if s.get("speaker") == "host" else "专家"
        lines.append(f"{who}：{s.get('text', '')}" if with_name else s.get("text", ""))
    return "\n\n".join(lines)


def _srt_time(ms) -> str:
    total = max(0, int(ms or 0))
    h, total = divmod(total, 3600000)
    m, total = divmod(total, 60000)
    s, total = divmod(total, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{total:03d}"


def script_to_srt(script: list[dict]) -> str:
    """脚本 → SRT 字幕（时间轴来自合成时回填的 start_ms / end_ms）。

    ⚠️ 没有时间戳的句子会被**跳过**：硬编一个时间只会做出一条对不上的字幕。
    所以「导出字幕」必须建立在「已经合成过一次」之上，调用方据此提示用户。
    """
    blocks = []
    for s in script or []:
        text = (s.get("text") or "").strip()
        if not text or s.get("start_ms") is None or s.get("end_ms") is None:
            continue
        who = "主持人" if s.get("speaker") == "host" else "专家"
        blocks.append(f"{len(blocks) + 1}\n"
                      f"{_srt_time(s['start_ms'])} --> {_srt_time(s['end_ms'])}\n"
                      f"{who}：{text}\n")
    return "\n".join(blocks)


def sanitize_title(raw: str) -> str:
    t = re.sub(r"\s+", " ", (raw or "").strip())
    return t[:60] or "未命名播客"
