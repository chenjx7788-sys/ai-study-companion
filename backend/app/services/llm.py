"""LLM 服务：OpenAI 兼容协议，总结模型 / 问答模型分开配置

prompt 原则：
- 摘要：层级大纲 + 每条要点标注页码；长文档两段式（先分块再汇总）
- 知识点：概念 + 一句话解释 + 出处页码，严格 JSON 输出
- 解读/追问：注入所选文本 + 所在章节上下文 + 追问历史
"""
import json
import re
from fastapi import HTTPException
from openai import OpenAI
from . import settings_store

MAX_SINGLE_CHARS = 24000   # 超过则两段式摘要
GROUP_CHARS = 12000        # 两段式每组大小


def get_client(base_url: str | None = None, api_key: str | None = None) -> OpenAI:
    conf = settings_store.load()
    base_url = base_url or conf["llm_base_url"]
    api_key = api_key or conf["llm_api_key"]
    if not api_key:
        raise HTTPException(400, "请先在「管理中心」配置 LLM 模型的 API Key")
    return OpenAI(base_url=base_url, api_key=api_key)


def _llm_error(e: Exception) -> HTTPException:
    """把 OpenAI SDK/网络异常转成可读错误（否则前端只能看到 Internal Server Error 或对象）"""
    msg = str(e) or e.__class__.__name__
    if "api_key" in msg.lower() or "auth" in msg.lower():
        msg = "API Key 无效或已过期，请到「设置」页检查"
    elif "rate" in msg.lower() or "429" in msg:
        msg = "模型服务限流，请稍候重试"
    elif "timeout" in msg.lower() or "connect" in msg.lower():
        msg = "模型服务连接超时，请检查网络后重试"
    return HTTPException(502, f"LLM 调用失败：{msg[:200]}")


def _chat_create(client, model, messages, temperature=0.3, stream=False, reasoning_effort=None, thinking=None):
    """发起 chat.completions 调用，带参数降级兜底（循环逐级降级）：
    - temperature 不支持（如 kimi-for-coding 仅允许 1）→ 改 1 重试；
    - reasoning_effort / thinking 不支持（部分服务商/中转）→ 去掉重试。
    thinking ∈ {None 不干预, 'enabled', 'disabled'}，经 extra_body 传给 DeepSeek 等支持方。"""
    def call(temp, effort, think):
        kwargs = dict(model=model, messages=messages, temperature=temp, stream=stream)
        if effort:
            kwargs["reasoning_effort"] = effort
        if think:
            kwargs["extra_body"] = {"thinking": {"type": think}}
        return client.chat.completions.create(**kwargs)

    temp, effort, think = temperature, reasoning_effort, thinking
    while True:
        try:
            return call(temp, effort, think)
        except Exception as e:
            s = str(e).lower()
            # temperature 限制：从错误信息解析允许值（如「only 0.6/1 is allowed」）
            m = re.search(r"only\s+([\d.]+)\s+is allowed", s)
            if "temperature" in s and m and temp != float(m.group(1)):
                temp = float(m.group(1))
                continue
            if (effort or think) and ("reasoning" in s or "effort" in s or "thinking" in s):
                effort, think = None, None
                continue
            raise


def log_usage(kind: str, model: str, prompt_tokens: int, completion_tokens: int, estimated: bool = False):
    """LLM 调用记账（失败静默，不影响主流程）"""
    try:
        from ..database import SessionLocal
        from ..models import LLMUsage
        db = SessionLocal()
        db.add(LLMUsage(kind=kind, model=model, prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens, estimated=estimated))
        db.commit()
        db.close()
    except Exception:
        pass


def chat(messages: list[dict], model: str | None = None, kind: str = "chat") -> str:
    """非问答、非总结场景（划线解读/追问、相关问题推荐）用问答模型：
    与 summary_chat 一致，按 chat_model_id 解析多模型配置，否则旧单套配置下会拿到空 key 而报 400。"""
    conf = settings_store.load()
    base_url, api_key, resolved = settings_store.resolve_model(conf.get("chat_model_id"))
    use_model = model or resolved or conf["chat_model"]
    try:
        resp = _chat_create(get_client(base_url, api_key), use_model, messages)
    except HTTPException:
        raise
    except Exception as e:
        raise _llm_error(e)
    if resp.usage:
        log_usage(kind, use_model, resp.usage.prompt_tokens, resp.usage.completion_tokens)
    return resp.choices[0].message.content


def chat_stream(messages: list[dict], model_id: str | None = None, reasoning_effort: str | None = None,
                kind: str = "chat"):
    """流式输出：逐 token yield (kind, text)，kind ∈ {'reasoning', 'content'}。
    reasoning_effort：None=关闭推理（默认，thinking=disabled），low/high/max=开启推理。
    kind：记账业务场景（全局问答默认 chat；解读/提问/文本加工等流式入口需显式传入对应 kind）"""
    conf = settings_store.load()
    # 未显式指定模型时回退到「问答模型」chat_model_id —— 不能直接传空给 resolve_model，
    # 否则会回退到全局 llm_base_url/llm_api_key（多模型模式下为空）而报 400。
    base_url, api_key, model = settings_store.resolve_model(model_id or conf.get("chat_model_id"))
    use_model = model or conf["chat_model"]
    thinking = "enabled" if reasoning_effort else "disabled"
    try:
        resp = _chat_create(get_client(base_url, api_key), use_model, messages,
                            stream=True, reasoning_effort=reasoning_effort, thinking=thinking)
    except HTTPException:
        raise
    except Exception as e:
        raise _llm_error(e)
    total_in = sum(len(m["content"]) for m in messages)
    total_out = 0
    for chunk in resp:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        rc = getattr(delta, "reasoning_content", None)
        c = getattr(delta, "content", None)
        if rc:
            total_out += len(rc)
            yield "reasoning", rc
        if c:
            total_out += len(c)
            yield "content", c
    # 中英混合按 ~1.6 字符/token 粗估
    log_usage(kind, use_model, int(total_in / 1.6), int(total_out / 1.6), estimated=True)


def summary_chat(messages: list[dict], kind: str = "summary") -> str:
    """总结/解读/出题等非问答场景：用配置的总结模型（summary_model_id）"""
    conf = settings_store.load()
    base_url, api_key, model = settings_store.resolve_model(conf.get("summary_model_id"))
    use_model = model or conf["summary_model"]
    try:
        # thinking=disabled：总结/出题类任务无需推理，关闭思考直接输出（推理模型默认开启会显著变慢）
        resp = _chat_create(get_client(base_url, api_key), use_model, messages, thinking="disabled")
    except HTTPException:
        raise
    except Exception as e:
        raise _llm_error(e)
    if resp.usage:
        log_usage(kind, use_model, resp.usage.prompt_tokens, resp.usage.completion_tokens)
    return resp.choices[0].message.content


def summary_chat_stream(messages: list[dict], kind: str = "summary"):
    """总结类（摘要等）流式输出：用总结模型逐 token yield text"""
    conf = settings_store.load()
    base_url, api_key, model = settings_store.resolve_model(conf.get("summary_model_id"))
    use_model = model or conf["summary_model"]
    try:
        # thinking=disabled：摘要流式同样关闭推理，直接输出
        resp = _chat_create(get_client(base_url, api_key), use_model, messages, stream=True, thinking="disabled")
    except HTTPException:
        raise
    except Exception as e:
        raise _llm_error(e)
    total_in = sum(len(m["content"]) for m in messages)
    total_out = 0
    for chunk in resp:
        if not chunk.choices:
            continue
        c = getattr(chunk.choices[0].delta, "content", None)
        if c:
            total_out += len(c)
            yield c
    log_usage(kind, use_model, int(total_in / 1.6), int(total_out / 1.6), estimated=True)


# ---------- 文本准备 ----------

def format_chunks(chunks: list[dict]) -> str:
    """给原文块加上页码标记，供模型引用"""
    return "\n\n".join(f"[P{c['page_no']}] {c['content']}" for c in chunks)


def split_groups(chunks: list[dict]) -> list[list[dict]]:
    groups, cur, size = [], [], 0
    for c in chunks:
        size += len(c["content"])
        cur.append(c)
        if size >= GROUP_CHARS:
            groups.append(cur)
            cur, size = [], 0
    if cur:
        groups.append(cur)
    return groups


# ---------- 提示词（默认模板，可在设置页覆盖；settings 里留空即用默认） ----------

DEFAULT_PROMPTS = {
    "prompt_summary": """你是一名学习助手，擅长把长文档读薄。请为用户生成「脉络梳理摘要」，要求：
1. 按文档原有章节结构组织，输出层级大纲（Markdown：## 章节，- 要点，子要点缩进两个空格）；
2. 每条要点末尾用 (P页码) 标注出处，页码取自原文中的 [P数字] 标记；
3. 要点是对内容的提炼压缩，不是照抄原文；
4. 总长度控制在原文的 10% 以内。""",
    "prompt_keywords": """你是一名学习助手。请从文档中提炼核心知识点，严格输出 JSON 数组，不要输出其他任何内容：
[{"concept": "概念名", "explanation": "一句话解释（≤60字）", "page_no": 页码数字}]
要求：
1. 挑选文档中最重要、最值得记忆的概念，5-20 个；
2. 页码取自原文 [P数字] 标记；
3. explanation 用自己的话解释，不照抄。""",
    "prompt_explain": """你是一名伴学助手。用户在阅读学习材料时选中了一段文字，请你：
1. 用通俗易懂的方式解释这段文字的含义；
2. 必要时结合它所在的上下文补充背景；
3. 如果涉及专业概念，给出例子帮助理解；
4. 回答简洁聚焦，不要偏离所选内容。追问时保持同一主题。""",
    "prompt_kb_qa": "你是伴学助手。优先基于给定的知识库内容回答。引用知识库内容时，在相关句末用 [1]、[2] 等编号标注来源（编号对应【知识库相关内容】中的 [1][2] 标记）；知识库未覆盖的部分可补充通用知识并注明，不要编造不存在的编号。",
    "prompt_general": "你是一名学习助手。用户的个人知识库中没有找到与问题相关的内容，请基于通用知识回答，保持简洁准确。",
    "prompt_kb_overview": """你是一名学习助手。用户问的是「知识库整体有什么内容」这类概览问题。下面给你的是该知识库的完整目录（资料清单 + 笔记清单）。请基于目录如实、清晰地回答用户，列出知识库包含的资料和笔记，可按主题适当归类；目录里没有的内容不要编造。""",
    "prompt_review": """你是一名记忆教练。根据用户的学习笔记生成一道自测选择题，严格输出 JSON（不要输出其他内容）：
{"question": "题干（≤50字，考察理解）", "options": ["选项A", "选项B", "选项C", "选项D"], "correct_index": 0, "explanation": "解析（≤80字，说明正确项为何对）"}
要求：4 个选项中只有 1 个正确，正确项位置随机；3 个干扰项要似是而非（常见误区/易混淆概念），不能是明显错误。""",
    "prompt_suggest": """你是一名学习引导助手。根据用户的问题和 AI 的回答，生成 3 个用户可能想继续追问的相关问题。
要求：1. 每个问题 ≤25 字，口语化、具体；2. 由浅入深或换角度（应用/对比/边界）；3. 严格输出 JSON 数组，不要输出其他内容：
["问题1", "问题2", "问题3"]""",
    "prompt_quiz": """你是一名测评出题助手。根据资料的核心内容生成 {count} 道单选题，严格输出 JSON 数组（不要输出其他内容）：
[{"question": "题干（≤50字，考察理解）", "options": ["选项A", "选项B", "选项C", "选项D"], "correct_index": 0, "explanation": "解析（≤80字）"}]
要求：覆盖资料最重要的知识点，难度递进；每个选项似是而非（常见误区/易混淆概念），正确项位置随机。""",
    "prompt_recall": """你是一名学习教练，擅长用费曼学习法帮人巩固知识。根据用户的学习笔记生成一道「复述题」，严格输出 JSON（不要输出其他内容）：
{"question": "复述指令（如「请用自己的话解释：XXX」，考察能否讲清核心概念）", "answer": "参考答案要点（≤120字，列出应涵盖的关键点，供用户对照）"}
要求：聚焦一个核心概念，引导用户主动组织语言复述，而非简单回忆定义。""",
    "prompt_material_ask": """你是一名伴学助手。请基于给定的材料内容回答用户的问题。要求：1. 回答严格依据材料，不编造材料中没有的信息；2. 可适当总结、解释，帮助用户理解；3. 回答简洁聚焦，直接回应问题。""",
    "prompt_note_rewrite": """你是一名文字加工助手。请把下面这段文字改写得更通顺、更专业、更简洁，保持原意、事实和关键数字不变，不增删信息。直接输出改写后的文字，不要加任何前后缀或解释。""",
    "prompt_note_expand": """你是一名文字加工助手。请在保持原意和事实不变的前提下，对下面这段文字进行扩写：补充必要的背景、细节、例子或解释，让内容更充实、更容易理解；不得编造原文中没有的事实。直接输出扩写后的文字，不要加任何前后缀。""",
    "prompt_note_summarize": """你是一名文字加工助手。请把下面这段文字压缩成更精炼的要点（可用 Markdown 列表结构），去掉冗余表述，突出核心结论与关键信息，保持事实不变。直接输出总结后的文字，不要加任何前后缀。""",
    "prompt_note_continue": """你是一名文字加工助手。请接着用户提供的文字往下续写，保持原有的语气、风格和逻辑连贯，自然地补充后续内容。不要重复已有内容，直接输出续写内容，不要加任何前后缀或解释。""",
    "prompt_stats_report": """你是一名学习教练。请根据用户本周的学习数据与规则引擎诊断出的问题，生成一份「学习分析报告」，严格按以下四段输出 Markdown：
## 一、数据摘要
客观罗列本周学习情况（学习天数、时长、笔记、复习、问答命中率），只整理不发挥、不编造。
## 二、问题诊断
针对下面给出的每个问题，解释「为什么这是个问题、背后可能的原因」。
## 三、行动建议
每个问题对应一条具体、可执行的建议（带动作，而非空泛口号）。
## 四、进阶方法
结合用户的学习主题，推荐一条进阶学习路径。
要求：语气温和鼓励、建议具体可执行、不编造数据、每段标题用 ## 开头。""",
}


def get_prompt(key: str) -> str:
    """设置页有自定义覆盖则用覆盖，否则用默认模板"""
    conf = settings_store.load()
    custom = (conf.get(key) or "").strip()
    return custom or DEFAULT_PROMPTS[key]


# ---------- 摘要（B2） ----------


def generate_summary(chunks: list[dict], instruction: str | None = None, kind: str = "summary") -> str:
    total = sum(len(c["content"]) for c in chunks)
    extra = f"\n补充要求：{instruction}" if instruction else ""
    sys_prompt = get_prompt("prompt_summary")

    if total <= MAX_SINGLE_CHARS:
        return summary_chat([
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": f"以下是文档全文（含页码标记）：\n\n{format_chunks(chunks)}{extra}"},
        ], kind=kind)

    # 两段式：分组摘要 → 汇总
    partials = []
    for i, group in enumerate(split_groups(chunks)):
        part = summary_chat([
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": f"以下是长文档的第 {i+1} 部分（含页码标记），请只梳理本部分：\n\n{format_chunks(group)}"},
        ], kind=kind)
        partials.append(part)
    combined = "\n\n".join(f"=== 第{i+1}部分摘要 ===\n{p}" for i, p in enumerate(partials))
    return summary_chat([
        {"role": "system", "content": sys_prompt + "\n5. 现在给你的是各部分的分段摘要，请合并为一份完整、不重复的全文脉络大纲。"},
        {"role": "user", "content": combined + extra},
    ], kind=kind)


# ---------- 知识点（B3） ----------

def generate_keywords(chunks: list[dict], instruction: str | None = None) -> list[dict]:
    text = format_chunks(chunks)
    if len(text) > MAX_SINGLE_CHARS:
        text = text[:MAX_SINGLE_CHARS]
    extra = f"\n补充要求：{instruction}" if instruction else ""
    raw = summary_chat([
        {"role": "system", "content": get_prompt("prompt_keywords")},
        {"role": "user", "content": f"文档内容：\n\n{text}{extra}"},
    ], kind="keywords")
    # 容错：截取 JSON 数组部分
    start, end = raw.find("["), raw.rfind("]")
    try:
        items = json.loads(raw[start:end + 1])
        return [i for i in items if isinstance(i, dict) and "concept" in i]
    except Exception:
        raise HTTPException(500, "知识点解析失败，请重试")


# ---------- 划线解读 / 追问（B4/B5） ----------

def explain_messages(selected_text: str, context: str, history: list[dict], question: str | None = None) -> list[dict]:
    messages = [{"role": "system", "content": get_prompt("prompt_explain")}]
    messages.append({
        "role": "user",
        "content": f"【选中的文字】\n{selected_text}\n\n【所在位置上下文】\n{context}",
    })
    if question is None:
        messages.append({"role": "user", "content": "请解读这段文字。"})
    else:
        # history: [{role: user/assistant, content}]
        messages.extend(history)
        messages.append({"role": "user", "content": question})
    return messages


def explain(selected_text: str, context: str, history: list[dict], question: str | None = None) -> str:
    return chat(explain_messages(selected_text, context, history, question), kind="explain")


# ---------- 文本加工（改写/扩写/续写/总结；笔记与材料文本共用） ----------

NOTE_TRANSFORM_PROMPTS = {
    "rewrite": "prompt_note_rewrite",
    "expand": "prompt_note_expand",
    "summarize": "prompt_note_summarize",
    "continue": "prompt_note_continue",
}


def note_transform_messages(mode: str, content: str, title: str | None = None) -> list[dict]:
    """文本加工（改写/扩写/续写/总结）：返回 messages。title 仅作上下文，不要求模型复述标题。"""
    head = f"（标题「{title}」仅作理解主题用，不要重复输出）\n" if title else ""
    if mode == "continue":
        return [
            {"role": "system", "content": get_prompt(NOTE_TRANSFORM_PROMPTS[mode])},
            {"role": "user", "content": f"{head}已有内容：\n{content}\n\n请接着往下续写。"},
        ]
    return [
        {"role": "system", "content": get_prompt(NOTE_TRANSFORM_PROMPTS[mode])},
        {"role": "user", "content": f"{head}待加工内容：\n{content}"},
    ]


def ask_messages(question: str, context: str, material_title: str) -> list[dict]:
    return [
        {"role": "system", "content": get_prompt("prompt_material_ask")},
        {"role": "user", "content": f"材料标题：{material_title}\n\n材料内容：\n{context}\n\n问题：{question}"},
    ]


def ask_material(question: str, context: str, material_title: str) -> str:
    """基于单个材料全文回答用户提问（不依赖划线，直接对材料提问）"""
    return chat(ask_messages(question, context, material_title), kind="ask")


# ---------- 全局问答 prompt（M4） ----------

def build_kb_qa_prompt(question: str, hits: list[dict]) -> list[dict]:
    ctx = "\n\n".join(f"[{i}] {h['title']} P{h['page_no']}\n{h['text']}" for i, h in enumerate(hits, 1))
    return [
        {"role": "system", "content": get_prompt("prompt_kb_qa")},
        {"role": "user", "content": f"【知识库相关内容】\n{ctx}\n\n【问题】{question}"},
    ]


# ---------- 复习卡片出题（V2 记忆曲线） ----------

def _parse_review_json(raw: str, keys: tuple) -> dict:
    start, end = raw.find("{"), raw.rfind("}")
    try:
        card = json.loads(raw[start:end + 1])
        assert all(k in card for k in keys)
        return card
    except Exception:
        raise HTTPException(500, "出题失败，请重试")


def generate_review_question(note_title: str, note_content: str) -> dict:
    """从笔记生成自测选择题：{question, options, correct_index, explanation}"""
    raw = summary_chat([
        {"role": "system", "content": get_prompt("prompt_review")},
        {"role": "user", "content": f"笔记标题：{note_title}\n\n笔记内容：\n{note_content[:3000]}"},
    ], kind="review")
    return _parse_review_json(raw, ("question", "options", "correct_index", "explanation"))


def generate_recall_question(note_title: str, note_content: str) -> dict:
    """从笔记生成费曼复述卡：{question, answer}（引导用户主动复述而非再认）"""
    raw = summary_chat([
        {"role": "system", "content": get_prompt("prompt_recall")},
        {"role": "user", "content": f"笔记标题：{note_title}\n\n笔记内容：\n{note_content[:3000]}"},
    ], kind="review")
    return _parse_review_json(raw, ("question", "answer"))


def convert_to_choice(question: str, answer: str) -> dict:
    """把已有简答卡转成选择题：{question, options, correct_index, explanation}"""
    raw = summary_chat([
        {"role": "system", "content": get_prompt("prompt_review")},
        {"role": "user", "content": f"已有的自测卡片：\n题干：{question}\n答案：{answer}\n\n请把它改造成一道选择题。"},
    ], kind="review")
    return _parse_review_json(raw, ("question", "options", "correct_index", "explanation"))


# ---------- 相关问题推荐（问答后追问引导） ----------

def generate_suggestions(question: str, answer: str) -> list[str]:
    """基于问答内容生成 3 个相关问题；失败静默返回空（不阻塞主流程）"""
    try:
        raw = chat([
            {"role": "system", "content": get_prompt("prompt_suggest")},
            {"role": "user", "content": f"【用户问题】{question}\n\n【AI 回答】\n{answer[:1500]}"},
        ], kind="suggest")
        start, end = raw.find("["), raw.rfind("]")
        items = json.loads(raw[start:end + 1])
        return [str(q) for q in items][:3]
    except Exception:
        return []


def generate_review_questions(note_title: str, note_content: str, count: int = 3) -> list[dict]:
    """把一条长笔记拆成 count 道互不重复的选择题（拆卡用）"""
    raw = summary_chat([
        {"role": "system", "content": get_prompt("prompt_review")
            + f"\n请围绕这条笔记生成 {count} 道互不重复的选择题，覆盖不同知识点，严格输出 JSON 数组，不要输出其他内容：[{{...}}, {{...}}]"},
        {"role": "user", "content": f"笔记标题：{note_title}\n\n笔记内容：\n{note_content[:3000]}"},
    ], kind="review")
    start, end = raw.find("["), raw.rfind("]")
    try:
        items = json.loads(raw[start:end + 1])
        keys = ("question", "options", "correct_index", "explanation")
        return [i for i in items if all(k in i for k in keys)][:count]
    except Exception:
        raise HTTPException(500, "拆卡失败，请重试")


def generate_quiz(material_title: str, core_text: str, count: int = 8) -> list[dict]:
    """基于资料核心内容生成 count 道单选题（测一测）"""
    raw = summary_chat([
        {"role": "system", "content": get_prompt("prompt_quiz").replace("{count}", str(count))},
        {"role": "user", "content": "资料标题：" + material_title + "\n\n核心内容：\n" + core_text[:6000]},
    ], kind="quiz")
    start, end = raw.find("["), raw.rfind("]")
    try:
        items = json.loads(raw[start:end + 1])
        keys = ("question", "options", "correct_index", "explanation")
        return [i for i in items if all(k in i for k in keys)][:count]
    except Exception:
        raise HTTPException(500, "出题失败，请重试")


# ---------- 数据统计面板 · AI 分析报告（模块 H） ----------

def stats_report_messages(summary_text: str, issues_text: str, topics_text: str) -> list[dict]:
    """学习分析报告的 messages（供一次性 / 流式两种生成方式复用）"""
    return [
        {"role": "system", "content": get_prompt("prompt_stats_report")},
        {"role": "user", "content":
            f"【本周数据摘要】\n{summary_text}\n\n"
            f"【规则引擎诊断出的问题】\n{issues_text or '（无）'}\n\n"
            f"【用户学习主题（材料标签）】\n{topics_text or '（暂无标签）'}"},
    ]


def generate_stats_report(summary_text: str, issues_text: str, topics_text: str) -> str:
    """生成四段式学习分析报告（数据摘要 / 问题诊断 / 行动建议 / 进阶方法）"""
    return summary_chat(stats_report_messages(summary_text, issues_text, topics_text), kind="stats_report")
