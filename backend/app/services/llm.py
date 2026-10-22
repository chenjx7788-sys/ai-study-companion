"""LLM 服务：OpenAI 兼容协议，总结模型 / 问答模型分开配置

prompt 原则：
- 摘要：层级大纲 + 每条要点标注页码；长文档两段式（先分块再汇总）
- 知识点：概念 + 一句话解释 + 出处页码，严格 JSON 输出
- 解读/追问：注入所选文本 + 所在章节上下文 + 追问历史
"""
import json
import re
import time
from fastapi import HTTPException
from openai import OpenAI
from . import settings_store

MAX_SINGLE_CHARS = 24000   # 超过则两段式摘要
# 两段式每组大小。
# ⚠️ 实测结论：不要靠放大分组来提速——摘要耗时由**输出 token 数**决定
# （实测约 144 tok/s：7k 输入/1.2k 输出的单次约 5s，28k 输入/8.2k 输出的单次约 57s），
# 分组越大，模型对每组要写的局部摘要越长（还会顶到服务商 max_tokens 上限被截断），
# 单次耗时涨的幅度远超组数下降的幅度。曾把此值调到 40000 验证：
# 24.7 万字的书组数 21 → 7，但单次 5s → 57s，总时长反而从 149s 涨到 ~400s，已回退。
# 真要提速应走「减少输出」或「分组并行」，而不是加大分组。
GROUP_CHARS = 12000

# 两段式「分组摘要」阶段的并发路数。
# 耗时由串行等待生成主导（见上），并发是唯一能成比例压缩总时长的杠杆：
# 21 组 × ~5s 串行 ≈ 149s，4 路并发 ≈ 6 轮 ≈ 30-40s。
# ⚠️ 若所用中转站并发限流（表现为 429「模型服务限流」），把这个值降到 2 或 1。
SUMMARY_CONCURRENCY = 4

# 摘要输出字数上限。为什么必须写死绝对字数：
# 1) 代码从不设置 max_tokens → 走服务商默认上限，实测输出**正好 8192 tokens**（≈1.2 万字）
#    即被截断，而提示词只写了「不超过原文 10%」这种比例约束——24.7 万字的书
#    期望 2.5 万字，必然超限，摘要尾部直接丢失；
# 2) 比例约束模型基本不遵守（实测每组 12000 字却输出 3300+ tokens ≈ 原文的 40%）；
# 3) 摘要耗时由输出 token 驱动，压输出即提速。
# ⚠️ 光写「字数上限」还不够：遇到**重复型/清单型**内容，模型会逐节罗列而不收敛
#    （实测 12000 字输入输出 5400 字，是上限的 5 倍）→ 必须同时写「按主题归纳，不要逐节罗列」，
#    实测同一输入 5400 字 → 82 字。两句缺一不可。
MAP_SUMMARY_CHARS = 1000     # 长文档分组时，每组的局部摘要上限
FINAL_SUMMARY_CHARS = 1500   # 合并后的全文摘要上限（单次调用路径同用）


def get_client(base_url: str | None = None, api_key: str | None = None) -> OpenAI:
    conf = settings_store.load()
    base_url = base_url or conf["llm_base_url"]
    api_key = api_key or conf["llm_api_key"]
    if not api_key:
        raise HTTPException(400, "[NEED_SETUP]请先在「管理中心」配置 LLM 模型的 API Key")
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
    与 summary_chat 一致，按 chat_model_id 解析多模型配置。
    ⚠️ 多模型模式下「问答模型」未选 = 未配置，直接报 NEED_SETUP（不回退旧字段）。"""
    conf = settings_store.load()
    if model:
        r = settings_store.resolve_model_strict(model)
        if r is None:
            raise HTTPException(400, "[NEED_SETUP]该模型未配置完整（缺少 API Key 或模型名），请到「管理中心」检查")
        base_url, api_key, resolved = r
    else:
        r = settings_store.resolve_usage("chat")
        if r is None:
            raise HTTPException(400, "[NEED_SETUP]请先在「管理中心」为问答模型指定用途")
        base_url, api_key, resolved = r
    use_model = model or resolved
    try:
        resp = _chat_create(get_client(base_url, api_key), use_model, messages)
    except HTTPException:
        raise
    except Exception as e:
        raise _llm_error(e)
    if resp.usage:
        log_usage(kind, use_model, resp.usage.prompt_tokens, resp.usage.completion_tokens)
    else:
        # 服务商未返回 usage → 按字符粗估（与流式口径一致），否则这次调用在统计里整个消失
        content = resp.choices[0].message.content or ""
        total_in = sum(len(m["content"]) for m in messages)
        log_usage(kind, use_model, int(total_in / 1.6), int(len(content) / 1.6), estimated=True)
    return resp.choices[0].message.content


def chat_stream(messages: list[dict], model_id: str | None = None, reasoning_effort: str | None = None,
                kind: str = "chat"):
    """流式输出：逐 token yield (kind, text)，kind ∈ {'reasoning', 'content'}。
    reasoning_effort：None=关闭推理（默认，thinking=disabled），low/high/max=开启推理。
    kind：记账业务场景（全局问答默认 chat；解读/提问/文本加工等流式入口需显式传入对应 kind）"""
    conf = settings_store.load()
    # 未显式指定模型时走到「问答用途」解析；多模型模式下用途未选 = 未配置（报 NEED_SETUP，不回退旧字段）
    if model_id:
        r = settings_store.resolve_model_strict(model_id)
        if r is None:
            raise HTTPException(400, "[NEED_SETUP]该模型未配置完整（缺少 API Key 或模型名），请到「管理中心」检查")
        base_url, api_key, model = r
    else:
        r = settings_store.resolve_usage("chat")
        if r is None:
            raise HTTPException(400, "[NEED_SETUP]请先在「管理中心」为问答模型指定用途")
        base_url, api_key, model = r
    use_model = model
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
    """总结/解读/出题等非问答场景：用配置的总结模型（summary_model_id）。
    ⚠️ 多模型模式下「总结模型」未选 = 未配置，直接报 NEED_SETUP（不回退旧字段）。"""
    conf = settings_store.load()
    r = settings_store.resolve_usage("summary")
    if r is None:
        raise HTTPException(400, "[NEED_SETUP]请先在「管理中心」为总结模型指定用途")
    base_url, api_key, use_model = r
    try:
        # thinking=disabled：总结/出题类任务无需推理，关闭思考直接输出（推理模型默认开启会显著变慢）
        resp = _chat_create(get_client(base_url, api_key), use_model, messages, thinking="disabled")
    except HTTPException:
        raise
    except Exception as e:
        raise _llm_error(e)
    if resp.usage:
        log_usage(kind, use_model, resp.usage.prompt_tokens, resp.usage.completion_tokens)
    else:
        # 服务商未返回 usage → 按字符粗估（与流式口径一致），否则这次调用在统计里整个消失
        content = resp.choices[0].message.content or ""
        total_in = sum(len(m["content"]) for m in messages)
        log_usage(kind, use_model, int(total_in / 1.6), int(len(content) / 1.6), estimated=True)
    return resp.choices[0].message.content


def summary_chat_stream(messages: list[dict], kind: str = "summary"):
    """总结类（摘要等）流式输出：用总结模型逐 token yield text。
    ⚠️ 多模型模式下「总结模型」未选 = 未配置，直接报 NEED_SETUP（不回退旧字段）。"""
    conf = settings_store.load()
    r = settings_store.resolve_usage("summary")
    if r is None:
        raise HTTPException(400, "[NEED_SETUP]请先在「管理中心」为总结模型指定用途")
    base_url, api_key, use_model = r
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
4. 精炼优先，不照抄原文；篇幅以用户消息中给出的字数上限为准（未给上限时不超过原文的 10%）。
5. 全文摘要末尾追加 `## 适用边界`（4 行：✅适用场景 / ❌不适用场景 / ⚠️前提条件 / 🔍已知局限），边界内容单独配额、不挤占正文篇幅；材料没有给出就写“材料未给出”，绝不编造。""",
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
    # ---- AI 陪练（模块 J）----
    "prompt_drill_examiner": """你是一名资深考官，围绕给定的学习素材出题。所有输出都必须是严格 JSON，不要输出任何其他内容（不要 Markdown 代码块）。

按用户消息指定的模式输出：

【模式一 · 出题纲】用户要求生成考点时，输出 JSON 数组：
[{"point": "考点描述（15-40字，指向素材里的具体知识点，不要泛泛而问）", "difficulty": "easy|medium|hard", "hint": "提问角度提示（可选，≤20字）", "source": "该考点来自哪份材料的标题（不确定则填空串）"}]
要求：1. 考点之间互不重复，合起来覆盖素材主要知识点；2. 优先考察「能不能讲出来」的理解与应用，不是死记硬背；3. 难度递进，先易后难；4. 只依据素材出题，绝不编造素材里没有的知识点。

【模式二 · 出题 + 期望要点】用户指定某个考点时，输出 JSON 对象：
{"question": "面向学习者的开放式问题（口语化、明确，不要「请论述」这类空话）", "expected_points": ["要点1", "要点2"], "source": "来源材料标题（不确定则空串）"}
要求：1. expected_points 列 2-5 条该题必须覆盖的关键点，供评分比对；2. 问题应能用一个自然段回答完，不需长篇大论；3. 仍严格依据素材。

【模式三 · 追问】用户给出原问题、期望要点与作答并明确要求追问时，输出 JSON 对象：
{"question": "针对作答中未讲透之处的一层深挖问题（一句话，不要重复原问题）", "expected_points": ["..."]}
要求：追问必须建立在素材事实上，不引入素材之外的新知识。""",
    "prompt_drill_judge": """你是一名严谨而友善的评分官。请根据「问题 + 期望要点 + 学习者作答」给出结构化评分，严格输出 JSON 对象，不要输出任何其他内容（不要 Markdown 代码块）：
{"score": 0-100 的整数, "verdict": "correct|partial|wrong", "dimensions": {"正确性": 0-100, "完整度": 0-100, "表达": 0-100}, "comment": "点评（≤120字，先肯定对的部分，再指出问题）", "missing_points": ["未覆盖的期望要点"], "followup": {"question": "需要追问时给出一个针对未讲透之处的追问；不需要则留空字符串", "expected_points": ["..."]}}

评分标准：
1. **正确性**：结论是否准确、有无事实性错误（最重要的一维）；
2. **完整度**：期望要点的覆盖比例；
3. **表达**：逻辑是否清晰、是否讲得让人听懂。
verdict 判定：score >= 80 且无关键错误 → correct；40-79 或漏掉主要要点 → partial；< 40 或方向性错误 → wrong。
要求：1. 严格对照期望要点，不要因措辞不同就判错（意思对即算覆盖）；2. missing_points 只列**期望要点中未被覆盖**的，不新增素材之外的要点；3. 不编造材料中没有的评价标准；4. followup.question 只在「作答明显没讲透、值得再挖一层」时给出，否则留空串。""",
    "prompt_learn_note": """你是一名顶尖学习教练。你的任务不是"总结"，而是把给定材料【转译】成一份结构化的学习笔记：让一个非本专业的初学者，不看原文也能在最短时间内抓住知识骨架、理解核心概念、并记住要点。

## 输出结构（必须严格照此，缺一不可）

1. 标题用 `# {材料名} · 学习笔记`

2. `## 必记要点 Top N`（3~5 条）
   —— 全篇最该记住的要点，编号列出，每条一句话点透。

3. `## 探索历程`
   —— 用一个统一的场景比喻，把全篇串成 3~5 幕（"第一幕/第二幕/..."），
      每幕点明对应哪个章节，让读者先建立整体心智模型。

4. 逐章展开（每章 B1/B2/... 结构完全一致）：
   - `## B{n} 标题`
     - `### 引入`：章前总览（本章核心问题，一句话点透）+ 逻辑链条（C1..Cn 清单）
     - `### C{n} 小节`：转译正文
     - `### 本节小结`：固定 ◆ 三条，每条一句话

5. 全篇末尾追加 `## 适用边界`（固定 4 行：✅适用场景 / ❌不适用场景 / ⚠️前提条件 / 🔍已知局限），材料没有明确给出就写“材料未给出”，绝不编造；逐章展开里不重复写边界。

## 转译规则（决定"看得懂"）

- 否定式纠偏：能破除刻板印象的，用「不是 X，而是 Y」句式。
- 表格降维：凡线性罗列的概念，能压成 Markdown 表格就压成表格。
- 章回式比喻：比喻要统一、克制，点到为止，不淹没干货。
- 每次只讲一次：一个概念首次出现给完整解释，后文一律用指代，严禁重复展开。
- 本节小结（◆三点）不得与正文重复措辞：小结是「提炼出的结论」，正文是「展开的讲解」，若正文本就只有一句判断，小结应换角度概括而非原句复述。

## 记忆钩子（决定"记得住"）

- 把抽象概念压成一句可复述的口诀（如"七宗罪=手术刀，马斯洛=地图"）。

## 硬约束（绝不违犯）

- 绝不编造原文没有的内容；不确定的信息宁可不写。
- 术语首次出现必须给一句通俗解释。
- 用页码标注出处（页码取自原文 [P数字] 标记），正文要点末尾标 (P页码)。
- 输出纯 Markdown，每章结构一致、可复现。""",
    "prompt_learn_note_conceptual": """你是一名顶尖学习教练。你的任务不是"总结"，而是把给定材料【转译】成一份结构化的学习笔记：让一个非本专业的初学者，不看原文也能在最短时间内抓住知识骨架、理解核心概念、并记住要点。

## 输出结构（必须严格照此）

1. 标题用 `# {材料名} · 学习笔记`

2. `## 必记要点 Top N`（3~5 条）
   —— 全篇最该记住的要点，编号列出，每条一句话点透。

3. `## 核心问题`
   —— 作者试图回答的根本问题是什么（1~2 句点透，不要展开）。

4. `## 底层假设`
   —— 作者默认成立的前提（2~4 条）。⚠️ 只写材料中**明确或可合理推知**的假设，推不出的宁可不写，不要替作者搭建他没说的前提。

5. 主体：**按材料结构自适应，二选一**（先判断材料有没有清晰章节）

   ⓐ **材料有清晰章节时** → 沿用章回体骨架：
      - `## B{n} 章节标题`
        - `### 引入`：本章核心问题（一句话）+ 逻辑链条（C1..Cn 清单）
        - `### C{n} 小节`：转译正文
        - `### 本节小结`：固定 ◆ 三条，每条一句话

   ⓑ **材料无明显章节时**（随笔式、主题式、条目式）→ 用框架结构：
      - `## F{n} 框架名`
        - 这一层在解决什么（一句话）
        - 推理链条：C1 → C2 → C3（用箭头串，写明因果）
        - 结论与主张

   ⚠️ 判断依据是**材料自身的行文结构**，不是你的偏好。**有章节就必须用 ⓐ**，不要为了省事退化成 ⓑ；反之**没有章节时绝不硬编章节**。

6. `## 适用边界`
   - ✅ 适合用来分析：…
   - ❌ 不能用来解释：…
   - ⚠️ 前提条件：…
   - 🔍 已知局限（含作者未提及的隐藏前提）：…

## 转译规则（决定"看得懂"）
- 否定式纠偏：能破除刻板印象的，用「不是 X，而是 Y」句式。
- 表格降维：凡线性罗列的概念，能压成 Markdown 表格就压成表格。
- 每次只讲一次：一个概念首次出现给完整解释，后文一律用指代，严禁重复展开。
- ⚠️ 不要用"统一的场景比喻串成 N 幕"来串讲（那是叙事类的做法）——认知类靠"问题 → 假设 → 推理链"建立心智模型，比喻点到为止即可。
- ⚠️ 走 ⓐ 时，每章的小结必须与正文换角度概括，不得原句复述。

## 记忆钩子（决定"记得住"）
- 把抽象概念压成一句可复述的口诀。

## 硬约束（绝不违犯）
- 绝不编造原文没有的内容；不确定的信息宁可不写。
- 术语首次出现必须给一句通俗解释。
- 用页码标注出处（页码取自原文 [P数字] 标记），正文要点末尾标 (P页码)。
- 输出纯 Markdown，结构一致、可复现。""",
    "prompt_learn_note_practical": """你是一名顶尖学习教练。你的任务不是"总结"，而是把给定材料【转译】成一份结构化的学习笔记：让一个非本专业的初学者，不看原文也能在最短时间内抓住知识骨架、理解核心概念、并记住要点。

## 输出结构（必须严格照此）

1. 标题用 `# {材料名} · 学习笔记`

2. `## 必记要点 Top N`（3~5 条）
   —— 全篇最该记住的要点，编号列出，每条一句话点透。

3. `## 它解决什么问题`
   —— 这份材料针对什么场景/痛点（1~2 句点透）。

4. `## 操作流程`（按材料实际步骤逐条展开）
   - `### S{n} 步骤名`
     - 怎么做：具体动作（能照做，不写"需要注意"这类空话）
     - 做完得到什么：这一步的产出/判定标志
     - 关键参数/取值（材料里有就给，没有就写"材料未提供"）

5. `## 判断标准`（材料没有则省略本节）
   —— 怎么判断做对了 / 什么情况下该走哪条分支。

6. `## 禁忌与坑点`（材料没有则省略本节）
   —— 材料明确提到的"不要做""常见错误"。材料没提到就不要编，宁缺勿滥。

7. `## 适用边界`
   - ✅ 适用场景：…
   - ❌ 不适用场景：…
   - ⚠️ 前提条件：…
   - 🔍 已知局限：…

## 转译规则（决定"看得懂"）
- 表格降维：参数/选项/对比，能压成 Markdown 表格就压成表格。
- 每次只讲一次：一个概念首次出现给完整解释，后文一律用指代，严禁重复展开。
- ⚠️ 不要用"场景比喻/第 N 幕"来串讲——工具类材料要的是准确，比喻会稀释信息。
- ⚠️ 材料里**没有**的步骤，绝不替它补充（这是工具书最容易出现的幻觉）。

## 记忆钩子（决定"记得住"）
- 把关键规则压成一句可复述的口诀。

## 硬约束（绝不违犯）
- 绝不编造原文没有的内容；不确定的信息宁可不写。
- 术语首次出现必须给一句通俗解释。
- 用页码标注出处（页码取自原文 [P数字] 标记），正文要点末尾标 (P页码)。
- 输出纯 Markdown，结构一致、可复现。""",
    "prompt_learn_note_fragment": """你是一名学习助手。下面的材料来自图片 OCR 或音视频转写，它**没有章节结构**，可能只是零散的笔记、板书、课件页或口语流水。请把它整理成一份**信息点清单**，让读者快速看清"这堆内容里到底讲了什么"。

## 输出结构（必须严格照此）

1. 标题用 `# {材料名} · 要点清单`

2. `## 内容概览`（1~2 句）
   —— 这份材料整体在讲什么（不要拔高、不要编造背景）。

3. `## 信息点`（按材料出现顺序，逐条列出）
   - 每条格式：`- **要点标题**：具体内容（标出处：图片→(P1)；音视频→(第N分钟)）`
   - 只整理材料里**真实出现**的信息，不做归类拔高。

4. `## 可深挖的线索`（可选，0~3 条）
   —— 材料里提到但**没展开**的概念/术语，列出以便用户另行查证。
   ⚠️ 只列"材料提到了什么"，**不要替它解释**。

## 铁律（这一套的重点）

- ⚠️ **禁止编造章节**：材料没有"第一章/第二章"，就不要写「第 N 章」。
- ⚠️ **禁止编造隐喻与"探索历程"**：碎片材料串不成故事，硬串就是编。
- ⚠️ **禁止补全**：OCR 可能识别出错字、转写可能有口误，看不懂的地方**如实标注「（此处文字不确定）」**，绝不猜测补全。
- ⚠️ **禁止拔高**：不要总结出材料里没有的"深层含义"。
- 材料过短（只有一两句）时，**如实给一两条信息点即可**，不要为凑结构而注水。

## 硬约束（绝不违犯）
- 用页码/时间标注出处（图片用 (P1)；音视频用 (第N分钟)）。
- 输出纯 Markdown，简洁为主。""",
    "prompt_learn_note_chapter": """你是一名顶尖学习教练。下面给你的是一部材料中的**其中一章**（不是全文）。请把这一章【转译】成一段结构化的学习笔记：让一个非本专业的初学者，不看原文也能抓住本章骨架、理解核心概念、并记住要点。

## 输出结构（必须严格照此）

1. 第一行输出 `## {章名}`（用用户消息里给出的章名，不要自己另起）。
2. `### 本章必记要点`（3-7 条，每条一句话、可直接背诵）。
3. `### 逐节转译`（按本章内部的小节/脉络推进，用大白话讲清"是什么、为什么、怎么用"；关键概念首次出现给一句人话解释；保留本章的例子与数据，但用自己的话重讲）。
4. `### 本章小结`（◆ 三句话收束本章：本章解决了什么问题 / 核心抓手 / 最容易记错的一点）。

## 硬约束（绝不违犯）

- 关键要点末尾用 (P页码) 标注出处，页码取自原文中的 [P数字] 标记。
- 只讲本章：不提及其他章节、不说"本书/全文"、不编造本章没有的内容。
- 是转译不是照抄，但不得丢失本章的关键信息量。
- 输出纯 Markdown。""",
    "prompt_doc_kind": """你是一名材料分类助手。请判断下面这段材料正文属于哪一类，只输出一个 JSON 对象，不要输出其他内容：
{"kind": "practical|conceptual|narrative", "reason": "一句话理由（≤30字）"}

判定标准：
- **practical**（工具实用类）：核心是"怎么做"——操作步骤、判断标准、参数、使用规范、禁忌坑点。典型：技术手册、API 文档、操作指南、制度流程、菜谱、教程。
- **conceptual**（认知思维类）：核心是"怎么想"——思考框架、底层假设、推理链条、判断维度。典型：心理学、商业分析、思维方法、理论教材、哲学社科。
- **narrative**（叙事类）：核心是"讲了什么故事/经历"——人物、事件、时间线、情节。典型：小说、传记、历史纪实、随笔散文。

注意：
1. 判断依据是**内容性质**，不是文件格式。
2. 介于两类之间时，选**主导**特征；实在无法判断，选 conceptual。
3. 只输出 JSON，不要解释、不要 Markdown 代码块。""",
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
    # ---- AI 播客 ----
    "prompt_podcast_brief": """你是一名知识提炼专家。请把下面的学习素材提炼成一份「知识简报」，供后续改写成播客对话脚本。

严格按以下结构输出 Markdown，不要输出任何额外说明：
## 核心主题
一句话说清这份素材在讲什么（≤40 字）
## 核心观点
3-5 条，每条为「一个明确判断句 + 简要论据」（每条 ≤60 字）
## 关键论据
2-4 条支撑上述观点的证据、数据或案例（每条 ≤60 字）
## 结论
这份素材最终要读者记住什么（1-2 条）
## 值得追问
2-3 个听众可能会追问的问题

要求：
1. 只提炼，不照抄原文；不按原文章节结构复述
2. 舍弃次要细节，保住信息量最大的判断
3. 不编造素材中没有的数据或事实
4. 全文总字数控制在 {brief_chars} 字以内（硬约束）""",
        "prompt_podcast_script": """你是一名播客编剧。请把「知识简报」改写成一段双人对话播客脚本。

两个角色：
- host（主持人）：代表听众提问，负责推进节奏、追问、适时小结
- expert（专家）：负责解答，讲清原理、举具体例子、给出结论
- 注意：「向听众提问」不是 host 的专属，host 和 expert 都可以直接问听众

严格输出 JSON 数组，不要输出其他任何内容（不要 Markdown 代码块）：
[{"speaker": "host", "text": "..."}, {"speaker": "expert", "text": "..."}]

要求：
1. 开场用具体问题或反常识切入，禁止「大家好，欢迎收听」这类套话
2. 主持人提问要像真人：短、具体、有追问感；禁止「能不能详细讲讲」这类空问题
3. 专家回答要口语化：多用短句、比喻和具体例子，避免书面语和超长并列句
4. 自然穿插举例、反问、阶段性小结（举例的具体要求见第 11 条）
5. 结尾不要「下期再见」式客套（结尾怎么写见第 14 条）
6. 每句 20-80 字，适合朗读；不要出现 Markdown 符号、括号注释、表情符号
7. 【最重要的约束】全文总字数必须控制在 {script_chars} 字左右，且不得超过 {max_chars} 字；
   共 {seg_count} 句，每句平均约 {avg_chars} 字。
   写超和写少都视为不合格 —— 落笔前先规划好每句承载多少信息，宁可少讲一个点，
   也不要为了显得完整而堆句子。
8. 只讲知识简报里的内容，不额外发挥

【教学质量要求 —— 每条都要能逐句核对，缺一项即视为不合格】
9. 【学习目标】开场必须明确告诉听众「看完能得到什么」，例如「看完你会拿到一张选择表」。
10. 【问听众】全文至少 3 次要「直接问听众」（句子里要出现「你」，host 和 expert 都可以问），
    而且要「分散在不同概念之间」，不要都堆在开场。
    其中至少 1 次要让听众先想一下再继续（例如「你手上有没有…？想三秒钟」）。
11. 【例子】每个核心概念都要配一个「具体例子」（用「比如…」引出）。例子必须来自素材；
    素材给了场景就具体化，没给就换素材里另一个真实场景，「绝不编造」。
    【篇幅分级】本次全文约 {script_chars} 字 —— 若这个篇幅装不下「每个概念都配例子」，
    至少保证 2 个核心概念有例子，不要为了凑数把句子写超。
12. 【代价】每个核心概念都要讲「它自己的代价」（成本 / 延迟 / 复杂度 / 门槛 / 风险）。
    如果素材里这个概念只写了优点，必须明说「这份材料只写了优点」，再从素材给出的
    结构或流程推出代价 —— 「绝不允许编造一个素材里没有的缺点」。
    篇幅不足时（同第 11 条的篇幅分级）至少覆盖 2 个核心概念的代价。
13. 【复现】核心概念名全文至少出现 2 次（首次讲解 + 后文复述），不要讲完就丢。
14. 【收尾动作】结尾必须给听众一个「可执行动作」（让他去做什么），不要用总结句收尾。
15. 【归属】讲某个概念的代价或例子时，「那句话里必须直接写出这个概念的名字」，
    不许用「它」「这个模式」之类指代 —— 否则听众对不上是在说哪个。
16. 【记忆锚点】中后段要给听众一句「能带走的说法」（口诀 / 一句话总结 /
    「三步」「两条」这类结构化表述），让他关掉之后还记得住。""",
        "prompt_podcast_script_direct": """你是一名播客编剧。请把下面的学习素材直接改写成一段双人对话播客脚本。

两个角色：
- host（主持人）：代表听众提问，负责推进节奏、追问、适时小结
- expert（专家）：负责解答，讲清原理、举具体例子、给出结论
- 注意：「向听众提问」不是 host 的专属，host 和 expert 都可以直接问听众

严格输出 JSON 数组，不要输出其他任何内容（不要 Markdown 代码块）：
[{"speaker": "host", "text": "..."}, {"speaker": "expert", "text": "..."}]

要求：
1. 先在心里提炼素材的核心观点与关键论据，再改写成对话；不要逐段复述原文
2. 开场用具体问题或反常识切入，禁止「大家好，欢迎收听」这类套话
3. 主持人提问要短、具体、有追问感；专家回答要口语化，多举例（具体要求见第 9 条）
4. 【最重要的约束】全文总字数必须控制在 {script_chars} 字左右，且不得超过 {max_chars} 字；
   共 {seg_count} 句，每句平均约 {avg_chars} 字。写超和写少都视为不合格。
5. 每句 20-80 字，适合朗读；不要出现 Markdown 符号、括号注释、表情符号
6. 不编造素材中没有的事实和数据

【教学质量要求 —— 每条都要能逐句核对，缺一项即视为不合格】
7. 【学习目标】开场必须明确告诉听众「看完能得到什么」，例如「看完你会拿到一张选择表」。
8. 【问听众】全文至少 3 次要「直接问听众」（句子里要出现「你」，host 和 expert 都可以问），
    而且要「分散在不同概念之间」，不要都堆在开场。
    其中至少 1 次要让听众先想一下再继续（例如「你手上有没有…？想三秒钟」）。
9. 【例子】每个核心概念都要配一个「具体例子」（用「比如…」引出）。例子必须来自素材；
    素材给了场景就具体化，没给就换素材里另一个真实场景，「绝不编造」。
    【篇幅分级】本次全文约 {script_chars} 字 —— 若这个篇幅装不下「每个概念都配例子」，
    至少保证 2 个核心概念有例子，不要为了凑数把句子写超。
10. 【代价】每个核心概念都要讲「它自己的代价」（成本 / 延迟 / 复杂度 / 门槛 / 风险）。
    如果素材里这个概念只写了优点，必须明说「这份材料只写了优点」，再从素材给出的
    结构或流程推出代价 —— 「绝不允许编造一个素材里没有的缺点」。
    篇幅不足时（同第 9 条的篇幅分级）至少覆盖 2 个核心概念的代价。
11. 【复现】核心概念名全文至少出现 2 次（首次讲解 + 后文复述），不要讲完就丢。
12. 【收尾动作】结尾必须给听众一个「可执行动作」（让他去做什么），不要用总结句收尾。
13. 【归属】讲某个概念的代价或例子时，「那句话里必须直接写出这个概念的名字」，
    不许用「它」「这个模式」之类指代 —— 否则听众对不上是在说哪个。
14. 【记忆锚点】中后段要给听众一句「能带走的说法」（口诀 / 一句话总结 /
    「三步」「两条」这类结构化表述），让他关掉之后还记得住。""",
        "prompt_podcast_script_solo": """你是一名播客主播。请把「知识简报」改写成一段**单人精讲**口播稿。

只有一个说话人：speaker 恒为 "host"，全文不要出现第二个人，也不要自问自答式的角色切换。

严格输出 JSON 数组，不要输出其他任何内容（不要 Markdown 代码块）：
[{"speaker": "host", "text": "..."}, {"speaker": "host", "text": "..."}]

要求：
1. 开场用具体问题或反常识切入，禁止「大家好，欢迎收听」这类套话
2. 全程第一人称讲解，像在给一个朋友把这件事讲透：先给结论，再讲为什么
3. 语言口语化：多用短句、比喻和具体例子，避免书面语和超长并列句
4. 每个要点讲完用一句话收束，再自然过渡到下一个要点；禁止小标题式罗列
5. 结尾不要「下期再见」式客套（结尾怎么写见第 14 条）
6. 每句 20-120 字，适合朗读；不要出现 Markdown 符号、括号注释、表情符号
7. 【最重要的约束】全文总字数必须控制在 {script_chars} 字左右，且不得超过 {max_chars} 字；
   共 {seg_count} 句，每句平均约 {avg_chars} 字。
   写超和写少都视为不合格 —— 落笔前先规划好每句承载多少信息，宁可少讲一个点，
   也不要为了显得完整而堆句子。
8. 只讲知识简报里的内容，不额外发挥

【教学质量要求 —— 每条都要能逐句核对，缺一项即视为不合格】
9. 【学习目标】开场必须明确告诉听众「看完能得到什么」，例如「看完你会拿到一张选择表」。
10. 【问听众】全文至少 3 次要「直接问听众」（句子里要出现「你」），
    而且要「分散在不同概念之间」，不要都堆在开场。
    其中至少 1 次要让听众先想一下再继续（例如「你手上有没有…？想三秒钟」）。
11. 【例子】每个核心概念都要配一个「具体例子」（用「比如…」引出）。例子必须来自素材；
    素材给了场景就具体化，没给就换素材里另一个真实场景，「绝不编造」。
    【篇幅分级】本次全文约 {script_chars} 字 —— 若这个篇幅装不下「每个概念都配例子」，
    至少保证 2 个核心概念有例子，不要为了凑数把句子写超。
12. 【代价】每个核心概念都要讲「它自己的代价」（成本 / 延迟 / 复杂度 / 门槛 / 风险）。
    如果素材里这个概念只写了优点，必须明说「这份材料只写了优点」，再从素材给出的
    结构或流程推出代价 —— 「绝不允许编造一个素材里没有的缺点」。
    篇幅不足时（同第 11 条的篇幅分级）至少覆盖 2 个核心概念的代价。
13. 【复现】核心概念名全文至少出现 2 次（首次讲解 + 后文复述），不要讲完就丢。
14. 【收尾动作】结尾必须给听众一个「可执行动作」（让他去做什么），不要用总结句收尾。
15. 【归属】讲某个概念的代价或例子时，「那句话里必须直接写出这个概念的名字」，
    不许用「它」「这个模式」之类指代 —— 否则听众对不上是在说哪个。
16. 【记忆锚点】中后段要给听众一句「能带走的说法」（口诀 / 一句话总结 /
    「三步」「两条」这类结构化表述），让他关掉之后还记得住。""",
        "prompt_podcast_script_solo_direct": """你是一名播客主播。请把下面的学习素材直接改写成一段**单人精讲**口播稿。

只有一个说话人：speaker 恒为 "host"，全文不要出现第二个人。

严格输出 JSON 数组，不要输出其他任何内容（不要 Markdown 代码块）：
[{"speaker": "host", "text": "..."}, {"speaker": "host", "text": "..."}]

要求：
1. 先在心里提炼素材的核心观点与关键论据，再改写成口播；不要逐段复述原文
2. 开场用具体问题或反常识切入，禁止「大家好，欢迎收听」这类套话
3. 全程第一人称讲解：先给结论，再讲为什么，多用短句、比喻和具体例子
4. 【最重要的约束】全文总字数必须控制在 {script_chars} 字左右，且不得超过 {max_chars} 字；
   共 {seg_count} 句，每句平均约 {avg_chars} 字。写超和写少都视为不合格。
5. 每句 20-120 字，适合朗读；不要出现 Markdown 符号、括号注释、表情符号
6. 不编造素材中没有的事实和数据

【教学质量要求 —— 每条都要能逐句核对，缺一项即视为不合格】
7. 【学习目标】开场必须明确告诉听众「看完能得到什么」，例如「看完你会拿到一张选择表」。
8. 【问听众】全文至少 3 次要「直接问听众」（句子里要出现「你」），
    而且要「分散在不同概念之间」，不要都堆在开场。
    其中至少 1 次要让听众先想一下再继续（例如「你手上有没有…？想三秒钟」）。
9. 【例子】每个核心概念都要配一个「具体例子」（用「比如…」引出）。例子必须来自素材；
    素材给了场景就具体化，没给就换素材里另一个真实场景，「绝不编造」。
    【篇幅分级】本次全文约 {script_chars} 字 —— 若这个篇幅装不下「每个概念都配例子」，
    至少保证 2 个核心概念有例子，不要为了凑数把句子写超。
10. 【代价】每个核心概念都要讲「它自己的代价」（成本 / 延迟 / 复杂度 / 门槛 / 风险）。
    如果素材里这个概念只写了优点，必须明说「这份材料只写了优点」，再从素材给出的
    结构或流程推出代价 —— 「绝不允许编造一个素材里没有的缺点」。
    篇幅不足时（同第 9 条的篇幅分级）至少覆盖 2 个核心概念的代价。
11. 【复现】核心概念名全文至少出现 2 次（首次讲解 + 后文复述），不要讲完就丢。
12. 【收尾动作】结尾必须给听众一个「可执行动作」（让他去做什么），不要用总结句收尾。
13. 【归属】讲某个概念的代价或例子时，「那句话里必须直接写出这个概念的名字」，
    不许用「它」「这个模式」之类指代 —— 否则听众对不上是在说哪个。
14. 【记忆锚点】中后段要给听众一句「能带走的说法」（口诀 / 一句话总结 /
    「三步」「两条」这类结构化表述），让他关掉之后还记得住。""",
    "prompt_video_script": """你是一名讲解视频的撰稿人。请把下面的学习素材改写成一份「要配画面讲解」的视频讲稿。

严格输出 JSON 数组，不要输出其他任何内容（不要 Markdown 代码块）：
[{"speaker": "host", "text": "..."}, {"speaker": "host", "text": "..."}]

要求：
1. 先在心里提炼核心观点与关键论据，再改写成口播；不要逐段复述原文
2. 开场用具体问题或反常识切入，禁止「大家好，欢迎收看」这类套话
3. 【最重要的约束】全文总字数必须控制在 {script_chars} 字左右，且不得超过 {max_chars} 字；
   共 {seg_count} 句，每句平均约 {avg_chars} 字。写超和写少都视为不合格。
4. 每句 20-120 字，适合朗读；不要出现 Markdown 符号、括号注释、表情符号
5. 【画面约束，视频特有，必须逐句满足】每句话里都要出现至少一个「在原文里原样存在的
   多字短语」（4-12 字，绝不能用单字）。系统会拿这个短语去原文里定位，
   把画面聚焦到讲到的那个位置。例如原文写的是「用户访谈」，
   讲稿里就必须出现「用户访谈」这四个字，不要改写成「和用户聊了聊」。
6. 不要写「如图所示」「请看这一页」这类依赖画面的指代 —— 画面由系统自动定位，
   你只要把内容讲清楚。
7. 不要用「它」「这个」「上面提到的」开头指代上一句的主语：每一句都要能独立听懂。
8. 不编造素材中没有的事实和数据
9. 【叙事结构，仅当素材是论文 / 技术研究类内容时】按讲解型论文视频的弧线组织：
   钩子（一个意外结论或挑衅性问题，先声夺人）→ 问题（这项工作之前缺什么、什么办法不好用）
   → 核心洞见（论文最妙的那一步，给足篇幅与类比）→ 机制（它是怎么做到的，简化讲）
   → 证据（最有说服力的前后对比 / 关键数字，只挑一两个）→ 意义（为什么重要、开启了什么）。
   相关工作一句话带过，实现细节与超参数不讲。素材不是论文类时不套用此条""",
    "prompt_video_points": """你是一名信息图编辑。请依据下面的视频讲稿与分镜表，为每个镜头设计「若干屏」结构图画面。

严格输出一个 JSON 对象，不要输出其他任何内容（不要 Markdown 代码块）：
{"title": "全片标题（不超过 14 字）",
 "scenes": [{"shot": 1, "title": "镜头标题（不超过 12 字）",
             "screens": [{"layout": "grid",
                          "elements": [{"t": "元素文本（不超过 16 字）", "k": "concept", "seg": 1,
                                        "tex": ["A_i =", "\\frac{r_i}{\\sum_j r_j}"]（可选，仅公式元素）}]}]}]}

字段说明：
- shot：镜头编号（必须使用分镜表里给定的编号，每个镜头恰好出现一次）
- title：这个镜头在讲什么，写成完整通顺的短语（会原样显示在画面顶部，
  禁止写半截词、禁止以「的/了/和」这类虚字结尾）
- screens：把这个镜头**按时间先后拆成若干屏**（像一叠幻灯片，每换一屏卡片整屏换掉）。
  一个 30-60 秒的镜头通常 2-5 屏；内容少时 1 屏也可以，但**每屏 3-6 个元素**。
- layout：**这一屏**的版式，只能取这五个值之一 ——
  grid（要点平铺，不确定时的默认）、flow（步骤/流程，元素按先后连成链）、
  hub（中心辐射：第 1 个元素是中心主题，其余是围绕它的分支）、
  compare（左右对比：前一半元素是一方，后一半是另一方）、
  pyramid（分层递进：元素自上而下逐层展开，越往下越具体）
  按**这一屏**的内容选最贴切的：讲流程用 flow、讲取舍对比用 compare、
  讲体系组成用 hub、讲层次递进用 pyramid，都不像就用 grid
- elements：这一屏里逐个点亮的元素；t 短句或短语，一个元素只讲一件事；
  k 只能取 concept（概念 / 机制）、good（良方 / 做法）、cost（代价 / 边界）
- tex（**可选**，只有讲稿里明确出现公式 / 数学关系的元素才给）：LaTeX 源码
  按**讲解顺序**拆成 2-6 项的数组（每项只含数学符号，不含中文）；
  此时 t 写这个公式的口头称呼（如「优势归一化」）。
  讲稿里没有的公式绝不编造；这一屏没有公式就**不要输出这个字段**
- chart（**可选**，只有讲稿里明确给出一组**数字的对比或趋势**才给）：
  {"type": "bar 或 line", "items": [{"label": "讲稿原词", "value": 数字}]}
  bar 用于 2-6 项对比（如几个方案的得分），line 用于 2-8 个点的趋势（如逐季度变化）；
  value **必须是讲稿里原样出现的数字**（不得换算单位、不得估算、不得编造）；
  label 用讲稿里的原词（不超过 8 字）；此时 t 写这组数据的口头称呼
  （如「三种方案得分对比」）。这一屏没有数据对比就**不要输出这个字段**
- sketch（**可选**，板书 / 动效图解两个皮肤共用：板书会把它画成一整块黑板上
  的简图、一笔一笔写出来；动效图解会把它变成**这一屏的结构图**，每个 label 逐个
  长出来、并按 type 摆成链 / 左右对照 / 层级 / 四宫格）。
  只有讲稿里真的存在**结构性关系**才给，type 只能取这四值之一：
  flow     = 步骤 / 因果链：labels 2-4 个，按先后顺序，每项是链上一步
  compare  = 两方取舍对比：labels **4 或 6 个（必须偶数）**，**前一半是左边一方、
             后一半是右边一方**；每边第一个是该方的标题，后面是该方的论据
  quadrant = 两维定位（如投入×回报）：labels **正好 4 个**，顺序固定为
             「左上 / 右上 / 右下 / 左下」；另给 x（横轴名）、y（纵轴名），各 ≤8 字
  pyramid  = 层级 / 递进：labels 3-4 个，**从顶层写到底层**（如 工具→方法→判断→视野）
  硬规则：
  ① 一个元素最多带一个 sketch，**不许与 tex / chart 同时给**；整条视频里带 sketch
     的元素不超过「总屏数 ÷ 3」—— 宁可少给，满屏都是图反而没人看
  ② labels 里每个词都必须**在本屏元素或讲稿里原样出现过**，不得自创、不得凑数；
     同一张图里不许出现两个相同的标签
  ③ 每个 label ≤ 8 个字（黑板上是大字，长了装不下也看不清）
  ④ 这一屏只是并列罗列、彼此没有步骤 / 对比 / 层次 / 两维关系 → **不要给**
  ⑤ 给了 sketch 时，t 写「这张图在讲什么」（4-18 字）：它会成为这一板的标题
- seg：该元素应当在第几句讲稿被点亮（必须在所属镜头的句号范围内）

要求：
1. 【一屏的容量】每屏 3-6 个元素，grid 最多 {max_group} 个 —— 这是**硬上限**：
   一屏塞进十几个元素会糊成一团、谁也看不清。内容多就**多拆几屏**，不要往一屏里堆。
2. 【全片总量】元素总数**不得少于 {point_count} 个**（这是**密度下限**，不是参考值）：
   画面上「长出来」的次数直接决定最长静止，数量不够会被质量闸门拦下、
   **整条片子不允许渲染**。请按讲稿顺序铺开，平均每句约 {per_seg} 个；
   长句（超过 30 字）给 2-4 个，短句至少 1 个。
   ⚠️ 总量靠「更多屏」达成，**不是**靠「每屏更多元素」。
3. 分布必须**铺满整条视频**，不许在开头一口气给完 ——
   视频最后 20% 的时间里必须仍有元素待点亮
4. 屏的时间顺序必须与讲稿一致：同一镜头里，后面那屏的 seg 只能更大，不许倒回去
5. 每个镜头都要有元素，不许留空镜头
6. 元素文本用讲稿里出现过的说法，不要自创新词
7. concept / good / cost 三类都要在全片出现；「代价 / 边界 / 风险」要单独成元素，不要混进良方
8. 不要编造讲稿里没有的内容
9. 公式元素（带 tex 的）**每屏最多 1 个**，且 k 取 concept；只有讲稿真的在推公式才给，
   一整条视频没有公式元素完全正常
10. 数据图元素（带 chart 的）**每屏最多 1 个**，且 k 取 concept；只有讲稿真的给了
    一组数字才给，一整条视频没有数据图元素完全正常；chart 与 tex 不要同时给一个元素
11. 草图元素（带 sketch 的）：**有结构关系就给**（动效图解靠它出图，只给文字卡
    就是一屏框框），上限仍是「总屏数 ÷ 3」。判断标准是讲稿里**真的存在**步骤链 /
    两方取舍 / 层级递进 / 两维定位之一；**不许为了凑数硬造关系** —— 图上画出一条
    讲稿里没有的因果，比不画这张图糟得多。labels 必须是本屏元素或讲稿里原样出现
    的词，这条会被程序校验，编造的整张图会被丢弃""",
    "prompt_video_anchor": """你是一名信息检索助手。下面是一份视频讲稿（每行一句，行首是序号）。
请为每一句挑出 1-4 个「定位关键词（anchor）」，用来在原始资料里找到这一句讲的是哪一处。

严格输出 JSON 数组，不要输出其他任何内容（不要 Markdown 代码块）：
[{"i": 1, "anchors": ["用户访谈", "访谈"]}]

硬规则（违反则定位必然失败）：
1. anchor 必须是**多字词**（4-12 字），绝不能用单字 ——
   单字（如「术」）会在同一页命中「技术 / 优术 / 术」多处，定位毫无意义。
2. anchor 必须是**原文里原样出现过的短语**，含标点要一致
   （原文用的是全角逗号「，」就不能写成半角逗号）。
   用户消息里给了「原文片段」时，**只允许从那一段里逐字原样摘取**：不得改写、不得拼接、
   不得摘半截词。整个片段里都找不到合适的短语 → 给空数组 []（下游会回退到句内切分，
   给一个错的锚点比给空数组更糟）。
3. 数量按句子长度给，但**下限是 2 个**（只要句中找得出两个互不重叠的多字词）：
   短句（不超过 30 字）给 2 个；中等（31-80 字）给 2-3 个；
   长句（超过 80 字）给 3-4 个，且按句子从前到后的顺序给出。
   ⚠️ 为什么下限是 2：下游是「逐个试定位、命中任何一个就算这句定位成功」，
   只给 1 个等于把整句的成败押在一个短语上 —— 它只要不在原文里，这句就直接算未定位。
   实测（内容同源的真实剪藏材料）：短句只给 1 个 anchor 时命中率卡在 89.5%，
   够不到 95% 的口径。两个 anchor 要落在句中**不同且不重叠**的位置；
   确实找不出两个就给 1 个，**不要为了凑数给一个不在原文里的词**。
4. 同一句的多个 anchor 要落在句子里**不同的位置**，不要给互为子串的词
   （不要同时给「用户访谈」和「访谈」这种会命中同一处的组合）。
5. 纯过渡句（如「我们先看第一部分」）给空数组 []。
6. 同一句内的 anchor 不得重复。
7. 拿到「原文片段」时，优先摘**片段中连续、完整的多字词**（如「需求管理」「用户访谈记录」），
   不要摘跨标点的拼接，也不要用片段里没出现过的同义词。

讲稿共 {seg_count} 句。只输出 JSON，不要输出其他任何内容。""",
}


def get_prompt(key: str) -> str:
    """设置页有自定义覆盖则用覆盖，否则用默认模板"""
    conf = settings_store.load()
    custom = (conf.get(key) or "").strip()
    return custom or DEFAULT_PROMPTS[key]


# ---------- 摘要（B2） ----------


def map_summary_group(group: list[dict], index: int, total: int, kind: str = "summary") -> str:
    """两段式的第一段：单个分组的局部摘要（index 从 0 起）

    单独暴露是为了让流式接口自己驱动循环、每完成一组回传一次进度——
    长文档要串行跑 N 组，全程无输出会让用户以为卡死。
    """
    return summary_chat([
        {"role": "system", "content": get_prompt("prompt_summary")},
        {"role": "user", "content": f"以下是长文档的第 {index + 1} 部分（含页码标记），请只梳理本部分，"
                                    f"严格控制在 {MAP_SUMMARY_CHARS} 字以内："
                                    f"按主题归纳，不要逐节/逐条罗列"
                                    f"（同一主题的多个小节合并为一条要点），不展开、不照抄：\n\n{format_chunks(group)}"},
    ], kind=kind)


def map_group_safe(group: list[dict], index: int, total: int, kind: str = "summary") -> str:
    """分组调用（失败重试一次）：并发场景下单组的偶发网络抖动/429
    不该让整篇摘要前功尽弃，重试一次后再失败才向上抛。"""
    last: Exception | None = None
    for attempt in range(2):
        try:
            return map_summary_group(group, index, total, kind)
        except Exception as e:   # HTTPException 也在此列（_llm_error 已转成可读文案）
            last = e
            if attempt == 0:
                time.sleep(1.5)
    raise last  # type: ignore[misc]


def reduce_summary(partials: list[str], instruction: str | None = None, kind: str = "summary") -> str:
    """两段式的第二段：把各分组摘要合并成一份完整、不重复的全文脉络大纲"""
    extra = f"\n补充要求：{instruction}" if instruction else ""
    combined = "\n\n".join(f"=== 第{i+1}部分摘要 ===\n{p}" for i, p in enumerate(partials))
    return summary_chat([
        {"role": "system", "content": get_prompt("prompt_summary")
         + "\n5. 现在给你的是各部分的分段摘要，请合并为一份完整、不重复的全文脉络大纲。"},
        {"role": "user", "content": combined
         + f"\n\n（合并后的全文摘要严格控制在 {FINAL_SUMMARY_CHARS} 字以内，按章节层级组织，不要逐条罗列）"
         + extra},
    ], kind=kind)


def generate_summary(chunks: list[dict], instruction: str | None = None, kind: str = "summary") -> str:
    """摘要（同步版）：短文档单次调用；长文档两段式（分组摘要 → 汇总）

    行为与拆分前一致。流式接口不走这里，而是自己驱动 map_summary_group /
    reduce_summary 的循环，以便逐组回传进度。
    """
    total = sum(len(c["content"]) for c in chunks)
    extra = f"\n补充要求：{instruction}" if instruction else ""
    sys_prompt = get_prompt("prompt_summary")

    if total <= MAX_SINGLE_CHARS:
        return summary_chat([
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": f"以下是文档全文（含页码标记）：\n\n{format_chunks(chunks)}\n\n"
                                        f"（全文摘要不超过原文的 10%，且最多 {FINAL_SUMMARY_CHARS} 字）{extra}"},
        ], kind=kind)

    # 两段式：分组摘要 → 汇总
    groups = split_groups(chunks)
    partials = [map_summary_group(g, i, len(groups), kind) for i, g in enumerate(groups)]
    return reduce_summary(partials, instruction, kind)


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


# ---------- 结构化学习笔记（简单学习） ----------

# 与摘要共用同一「输出 token 驱动耗时」判断：单次调用输出越长越慢。
# 一期不做两段式，超长材料截断到 MAX_SINGLE_CHARS（先跑通，P1 验收后再评估分组）。
LEARN_NOTE_MAX_CHARS = 24000

# 截断时写进讲义正文的固定标记（前端/转出的笔记据此识别「这份讲义没有覆盖全文」）
LEARN_TRUNC_MARK = "覆盖范围提示"


# 碎片类材料（图片 / 音视频）：解析产物**无章节结构**（图片=单块 page_no=1、
# 音视频=「第 N 分钟」），强制套章回体会让模型凭空编造章节 → 固定走 fragment 工艺，不调 LLM。
# ⚠️ 与 parser.py 的 IMAGE_FORMATS / MEDIA_FORMATS 保持一致（那边是解析口径，这里是工艺口径）。
FRAGMENT_FORMATS = {"jpg", "jpeg", "png", "webp", "bmp", "mp3", "wav", "m4a", "mp4"}

# doc_kind → 讲义提示词键。⚠️ narrative 与 auto 都映射到现有 prompt_learn_note ——
# 保证「判定失败 / 未判定 / 老数据」时行为与改造前**完全一致**（零回退风险）。
LEARN_NOTE_PROMPT_BY_KIND = {
    "narrative": "prompt_learn_note",
    "auto": "prompt_learn_note",
    "conceptual": "prompt_learn_note_conceptual",
    "practical": "prompt_learn_note_practical",
    "fragment": "prompt_learn_note_fragment",
}


def learn_note_prompt_key(doc_kind: str | None) -> str:
    """按 doc_kind 选讲义提示词键（未知值回落现有工艺）。"""
    return LEARN_NOTE_PROMPT_BY_KIND.get((doc_kind or "auto").strip().lower(), "prompt_learn_note")


DOC_KIND_VALUES = ("narrative", "conceptual", "practical", "fragment", "auto")
# 供 LLM 判定的三类（fragment 是「形态」决定、不由 LLM 判；auto 是失败兜底）
DOC_KIND_LLM_VALUES = ("practical", "conceptual", "narrative")
DOC_KIND_SAMPLE_CHARS = 4000   # 分类只需看开头一段，不必全文（省 token）


def classify_doc_kind(chunks: list[dict], fmt: str | None = None) -> str:
    """判定材料的内容性质，返回 narrative / conceptual / practical / fragment / auto。

    两级判定（先看形态、再看性质）：
    1. **确定性**：fmt ∈ FRAGMENT_FORMATS（图片 / 音视频）→ 直接 fragment，**不调 LLM**
       （这类材料解析出来就没有章节，不存在"认知类图片"的可能）。
    2. **LLM 判定**：其余文本类材料取正文开头一段调一次 summary_chat，判 practical/conceptual/narrative。
       ⚠️ 未配 Key / 调用失败 / 返回非法值 → 一律回落 "auto"（走现有通用工艺，**不阻塞、不报错**）。

    kind 归口为 "learn_note"：判定是讲义链路的内部环节，不单列统计口径
    （`prompt_doc_kind` 亦不暴露到设置页 —— 见 P0 送审稿决议 #4）。
    """
    if (fmt or "").strip().lower() in FRAGMENT_FORMATS:
        return "fragment"
    text = format_chunks(chunks)
    if not text.strip():
        return "auto"
    try:
        raw = summary_chat([
            {"role": "system", "content": get_prompt("prompt_doc_kind")},
            {"role": "user", "content": text[:DOC_KIND_SAMPLE_CHARS]},
        ], kind="learn_note")
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            return "auto"
        data = json.loads(raw[start:end + 1])
        kind = str(data.get("kind") or "").strip().lower()
        return kind if kind in DOC_KIND_LLM_VALUES else "auto"
    except Exception:
        return "auto"   # 判定是增强项：失败绝不拖垮讲义生成


# ---------- 章节切分（按章截断 / 分章生成共用；纯本地计算，不调 LLM） ----------

def split_chapters(chunks: list[dict]) -> list[dict]:
    """把原文块按「顶级章节」归并成有序章列表。

    归并键 = section_path 的**第一段**（层级分隔符 `/`；标题自带的 `/` 在解析层
    已转成全角，故按 `/` 切安全）。连续同键的块归一章；section_path 为空的块
    归入「卷首内容」章。返回 [{"key","name","chunks","chars"}]。
    **所有块都没有章节结构时返回 []**（调用方退回整文处理，行为与章节化之前一致）。
    """
    if not any((c.get("section_path") or "").strip() for c in chunks):
        return []
    chapters: list[dict] = []
    for c in chunks:
        sp = (c.get("section_path") or "").strip()
        key = sp.split("/", 1)[0] if sp else ""
        if chapters and chapters[-1]["key"] == key:
            chapters[-1]["chunks"].append(c)
        else:
            chapters.append({"key": key, "name": key or "卷首内容", "chunks": [c]})
    for ch in chapters:
        ch["chars"] = len(format_chunks(ch["chunks"]))
    return chapters


def chapter_prefix_count(chapters: list[dict], limit: int) -> int:
    """返回「整章纳入也不超 limit」的前缀章数（0 = 第一章自身就超限）。"""
    total, n = 0, 0
    for ch in chapters:
        add = ch["chars"] + (2 if n else 0)     # 章与章之间是 "\n\n" 拼接
        if total + add > limit:
            break
        total += add
        n += 1
    return n


def learn_note_coverage(chunks: list[dict]) -> dict:
    """讲义覆盖度（纯字符串长度计算，不调 LLM）。

    材料超长时 generate_learn_note 会截断：有章节结构时**截在章节边界**
    （末尾章整章舍弃，不把某一章切在半截）；无结构时退回按 LEARN_NOTE_MAX_CHARS 硬切。
    covered/missing_chapters 供调用方提示「哪些章没覆盖」，无结构时为空。
    """
    total = len(format_chunks(chunks))
    cov = {"total_chars": total, "truncated": total > LEARN_NOTE_MAX_CHARS,
           "covered_chapters": [], "missing_chapters": []}
    if not cov["truncated"]:
        cov["used_chars"] = total
        cov["ratio"] = 1.0
        return cov
    chapters = split_chapters(chunks)
    if chapters:
        n = chapter_prefix_count(chapters, LEARN_NOTE_MAX_CHARS)
        kept = [c for ch in chapters[:n] for c in ch["chunks"]]
        cov["used_chars"] = len(format_chunks(kept)) if kept else LEARN_NOTE_MAX_CHARS
        cov["covered_chapters"] = [ch["name"] for ch in chapters[:n]]
        cov["missing_chapters"] = [ch["name"] for ch in chapters[n:]]
    else:
        cov["used_chars"] = LEARN_NOTE_MAX_CHARS
    cov["ratio"] = round(cov["used_chars"] / total, 4) if total else 1.0
    return cov


def _learn_note_inputs(chunks: list[dict], title: str | None = None,
                       doc_kind: str = "auto") -> tuple[list[dict], dict, str]:
    """组装讲义生成的 messages / 覆盖度 / 截断后的前置提示（供一次性与流式共用）。"""
    text = format_chunks(chunks)
    cov = learn_note_coverage(chunks)
    if cov["truncated"]:
        if cov["covered_chapters"]:
            # 按章截断：只取「整章纳入也不超限」的前缀章，不把某一章切在半截
            kept = split_chapters(chunks)[:len(cov["covered_chapters"])]
            text = format_chunks([c for ch in kept for c in ch["chunks"]])
        else:
            text = text[:LEARN_NOTE_MAX_CHARS]
    head = f"（材料标题「{title}」仅作理解主题用，笔记标题请用它）\n" if title else ""
    if cov["truncated"]:
        head += ("⚠️ 本次只提供了材料的前一部分（材料过长已被截断）："
                 "请在讲义第 1 行之前如实写出覆盖范围，不要声称已覆盖全文。\n")
    messages = [
        {"role": "system", "content": get_prompt(learn_note_prompt_key(doc_kind))},
        {"role": "user", "content": f"{head}以下是材料原文（含页码标记）：\n\n{text}"},
    ]
    warn = ""
    if cov["truncated"]:
        warn = (f"> ⚠️ **{LEARN_TRUNC_MARK}**：本材料共约 {cov['total_chars']:,} 字，"
                f"单次处理上限 {LEARN_NOTE_MAX_CHARS:,} 字，"
                f"本讲义仅覆盖前 {cov['used_chars']:,} 字（约 {cov['ratio'] * 100:.1f}%），"
                f"未覆盖的章节不会出现在本讲义中。\n")
        if cov["missing_chapters"]:
            names = cov["missing_chapters"]
            shown = "、".join(f"《{n}》" for n in names[:6])
            tail = f" 等 {len(names)} 章" if len(names) > 6 else ""
            warn += f"> 未覆盖章节：{shown}{tail}。\n"
        warn += "\n"
    return messages, cov, warn


def generate_learn_note(chunks: list[dict], title: str | None = None,
                        doc_kind: str = "auto") -> str:
    """把材料转译成结构化学习笔记（简单学习核心产物）。

    输入 chunks 与摘要/知识点一致（含 page_no）；返回 Markdown 结构化笔记。
    骨架写死在 prompt_learn_note（必记要点 / 探索历程 / 逐章转译 / ◆三点小结 / 重点加权 / 去重）。

    ⚠️ 材料超过 LEARN_NOTE_MAX_CHARS 时只取前一段（末尾章节会丢）。此时：
    ① prompt 里明确告知模型「只看了一部分」，避免它声称覆盖全文；
    ② 正文开头插入「覆盖范围提示」块——它随讲义一起落库、转笔记，任何入口都看得见。
    """
    messages, cov, warn = _learn_note_inputs(chunks, title, doc_kind)
    body_txt = summary_chat(messages, kind="learn_note")
    if not cov["truncated"]:
        return body_txt
    return warn + body_txt.lstrip()


def generate_learn_note_stream(chunks: list[dict], title: str | None = None,
                               doc_kind: str = "auto"):
    """流式版学习笔记：逐 token yield 字符串（供 SSE 逐字推给前端）。

    与 generate_learn_note 完全同构（同一 messages、同一覆盖度处理），
    只是把 summary_chat 换成 summary_chat_stream，并把截断提示块放在
    首个 token 之前 yield（保证「覆盖范围提示」出现在讲义最前头，任何入口都看得见）。
    """
    messages, cov, warn = _learn_note_inputs(chunks, title, doc_kind)
    if warn:
        yield warn
    first = True
    for piece in summary_chat_stream(messages, kind="learn_note"):
        # 正文首段 Lstrip：一次性路径里 warn 后跟 body_txt.lstrip()，
        # 流式无法预知首段，退化为「截断时把首个非空 token 的前导空白去掉」。
        if first and cov["truncated"] and piece:
            piece = piece.lstrip()
            first = False
        else:
            first = False
        yield piece


def generate_learn_note_chapter_stream(chapter: dict, title: str | None = None,
                                       doc_kind: str = "auto",
                                       chapter_no: int = 1, chapter_total: int = 1):
    """单章讲义（流式）：把一章转译成结构化笔记段，逐 token yield。

    与整篇 generate_learn_note_stream 同构：章内仍超 LEARN_NOTE_MAX_CHARS 时硬切，
    并在段首垫「覆盖范围提示」（此时缺的不是整章，而是**本章末尾**）。
    ⚠️ 分章一律走 prompt_learn_note_chapter（章级输出结构统一）；doc_kind 参数保留
       仅为调用签名一致，便于以后按内容性质分化章工艺。
    """
    text = format_chunks(chapter["chunks"])
    warn = ""
    if len(text) > LEARN_NOTE_MAX_CHARS:
        warn = (f"> ⚠️ **{LEARN_TRUNC_MARK}**：本章共约 {len(text):,} 字，"
                f"超出单次处理上限 {LEARN_NOTE_MAX_CHARS:,} 字，本章末尾内容未覆盖。\n\n")
        text = text[:LEARN_NOTE_MAX_CHARS]
    if title:
        head = (f"（材料标题「{title}」，本次只提供第 {chapter_no}/{chapter_total} 章"
                f"《{chapter['name']}》，请只为这一章写笔记）\n")
    else:
        head = (f"（本次只提供第 {chapter_no}/{chapter_total} 章《{chapter['name']}》，"
                f"请只为这一章写笔记）\n")
    if warn:
        head += "⚠️ 本章内容过长已被截断：请如实转译已提供部分，不要声称覆盖本章全部。\n"
    messages = [
        {"role": "system", "content": get_prompt("prompt_learn_note_chapter")},
        {"role": "user", "content": f"{head}以下是本章原文（含页码标记）：\n\n{text}"},
    ]
    if warn:
        yield warn
    for piece in summary_chat_stream(messages, kind="learn_note"):
        yield piece


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
    keys = ("question", "options", "correct_index", "explanation")
    try:
        items = json.loads(raw[start:end + 1])
        qas = [i for i in items
               if isinstance(i, dict) and all(k in i for k in keys)][:count]
    except Exception:
        raise HTTPException(500, "拆卡失败，请重试")
    if not qas:
        # ⚠️ 同 generate_quiz：绝不能返回空数组 —— 调用方 /review/cards/split 是
        #     「先删该笔记全部卡、再插新卡」，空数组会静默清空用户的卡与复习进度。
        raise HTTPException(500, "拆卡失败：模型未返回可用题目，请重试")
    return qas


def generate_quiz(material_title: str, core_text: str, count: int = 8, kind: str = "quiz") -> list[dict]:
    """基于资料核心内容生成 count 道单选题

    kind 决定 token 统计归口：默认 "quiz"（测一测）；「简单学」入口传 "learn_note"，
    让该入口的讲义与出题两段消耗都归到「简单学」名下（提示词模板仍是共用的 prompt_quiz）。
    """
    raw = summary_chat([
        {"role": "system", "content": get_prompt("prompt_quiz").replace("{count}", str(count))},
        {"role": "user", "content": "资料标题：" + material_title + "\n\n核心内容：\n" + core_text[:6000]},
    ], kind=kind)
    start, end = raw.find("["), raw.rfind("]")
    keys = ("question", "options", "correct_index", "explanation")
    try:
        items = json.loads(raw[start:end + 1])
        qas = [i for i in items
               if isinstance(i, dict) and all(k in i for k in keys)][:count]
    except Exception:
        raise HTTPException(500, "出题失败，请重试")
    if not qas:
        # ⚠️⚠️ 绝不能返回空数组：调用方是「先删旧卡再插新卡」，
        #     空数组会让它把用户已有的题（连同复习进度）全部清空，还报「生成成功」。
        #     模型回了合法 JSON 但字段名不合规时就会走到这里——长材料下并不罕见。
        raise HTTPException(500, "出题失败：模型未返回可用题目，请重试")
    return qas


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


# ---------- AI 播客（模块 I） ----------

SCRIPT_SPEAKERS = ("host", "expert")


def _strip_code_fence(raw: str) -> str:
    """剥离 LLM 可能输出的 ```json ... ``` 包裹"""
    txt = (raw or "").strip()
    if txt.startswith("```"):
        txt = re.sub(r"^```[a-zA-Z]*\s*", "", txt)
        txt = re.sub(r"\s*```$", "", txt)
    return txt.strip()


def _parse_podcast_script(raw: str) -> list[dict]:
    """解析对话脚本 JSON 数组；容错代码块包裹、speaker 别名、空文本条目"""
    txt = _strip_code_fence(raw)
    start, end = txt.find("["), txt.rfind("]")
    if start < 0 or end <= start:
        raise HTTPException(500, "脚本解析失败，请重试")
    try:
        items = json.loads(txt[start:end + 1])
    except Exception:
        raise HTTPException(500, "脚本解析失败，请重试")
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        sp = str(it.get("speaker") or "").strip().lower()
        if sp not in SCRIPT_SPEAKERS:
            # 模型偶尔用 guest / 嘉宾 / speaker2 等别名，统一归一到 expert
            sp = "expert" if sp in ("guest", "speaker2", "speaker_2", "b", "2", "专家", "嘉宾") else "host"
        text = str(it.get("text") or "").strip()
        if text:
            out.append({"speaker": sp, "text": text})
    if not out:
        raise HTTPException(500, "脚本解析失败，请重试")
    return out


def generate_podcast_brief(source_title: str, source_text: str, brief_chars: int = 360) -> str:
    """提炼层：素材 → 结构化知识简报（可单独复用为笔记）"""
    raw = summary_chat([
        {"role": "system", "content": get_prompt("prompt_podcast_brief").replace("{brief_chars}", str(brief_chars))},
        {"role": "user", "content": f"素材标题：{source_title}\n\n素材内容：\n{source_text}"},
    ], kind="podcast_brief")
    return _strip_code_fence(raw)


# 脚本提示词：风格 × 是否跳过简报，四选一（单人稿有独立模板 —— 双人模板会写进
# 「主持人提问 / 专家解答」的角色轮转，光靠一句附加要求改不掉）
_SCRIPT_PROMPT_KEYS = {
    ("dialogue", False): "prompt_podcast_script",
    ("solo", False): "prompt_podcast_script_solo",
    ("dialogue", True): "prompt_podcast_script_direct",
    ("solo", True): "prompt_podcast_script_solo_direct",
}


def _script_prompt_key(style: str, direct: bool) -> str:
    st = style if style in ("dialogue", "solo") else "dialogue"
    return _SCRIPT_PROMPT_KEYS[(st, bool(direct))]


def generate_podcast_script(brief: str, script_chars: int = 750, seg_count: int = 16,
                            topic: str = "", instruction: str = "",
                            style: str = "dialogue") -> list[dict]:
    """演绎层：知识简报 → 口播脚本（dialogue 双人 / solo 单人精讲）"""
    extra = f"\n\n本期主题：{topic}" if topic else ""
    if instruction:
        extra += f"\n\n用户额外要求：{instruction}"
    avg = max(20, round(script_chars / max(1, seg_count)))
    prompt = (get_prompt(_script_prompt_key(style, False))
              .replace("{script_chars}", str(script_chars))
              .replace("{max_chars}", str(round(script_chars * 1.1)))
              .replace("{seg_count}", str(seg_count))
              .replace("{avg_chars}", str(avg)))
    raw = summary_chat([
        {"role": "system", "content": prompt},
        {"role": "user", "content": f"知识简报：\n\n{brief}{extra}"},
    ], kind="podcast_script")
    return _parse_podcast_script(raw)


def generate_podcast_script_direct(source_title: str, source_text: str,
                                   script_chars: int = 750, seg_count: int = 16,
                                   instruction: str = "", style: str = "dialogue") -> list[dict]:
    """快速模式：跳过简报，素材 → 脚本一步到位（省一次 LLM 调用，信息密度略降）"""
    extra = f"\n\n用户额外要求：{instruction}" if instruction else ""
    avg = max(20, round(script_chars / max(1, seg_count)))
    prompt = (get_prompt(_script_prompt_key(style, True))
              .replace("{script_chars}", str(script_chars))
              .replace("{max_chars}", str(round(script_chars * 1.1)))
              .replace("{seg_count}", str(seg_count))
              .replace("{avg_chars}", str(avg)))
    raw = summary_chat([
        {"role": "system", "content": prompt},
        {"role": "user", "content": f"素材标题：{source_title}\n\n素材内容：\n{source_text}{extra}"},
    ], kind="podcast_script")
    return _parse_podcast_script(raw)


# ---------- AI 视频（模块 K · V1.0） ----------
# 视频不重造链路：讲稿与结构图内容都走 summary_chat（与播客 / 陪练同模型）。
# 记账 kind：video_script（讲稿 + 定位锚点）、video_points_*（结构图元素与要点，按皮肤拆开）。
# ⚠️ 埋点边界（PRD 4.2）：埋点不含讲稿正文与来源内容，只上报行为量与长度。

VIDEO_KINDS = ("concept", "good", "cost")

# 结构图记账 kind → 视觉方案（皮肤）映射。
# ⚠️ 为什么要拆：graph / graph_paper / graph_clean / ppt / board / motion 六个皮肤
#    共用同一条 points 生成分支（同一份数据，只换画法），过去都记成 video_points
#    → 「Token 消耗」页看不出是哪个类型在花钱。拆开后每类可单独统计。
# ⚠️ 不在此表里的 skin（空/未知）回落旧的 `video_points`，历史数据口径不变。
POINTS_SKIN_KINDS = {
    "graph": "video_points_graph",
    "graph_paper": "video_points_graph_paper",
    "graph_clean": "video_points_graph_clean",
    "ppt": "video_points_ppt",
    "board": "video_points_board",
    "motion": "video_points_motion",
}


def points_usage_kind(skin: str = "") -> str:
    """结构图调用的记账 kind：按视觉方案拆分（空/未知 → 旧的 video_points）。"""
    return POINTS_SKIN_KINDS.get((skin or "").strip(), "video_points")

# 单个镜头的元素上限（PRD 3.6.6 #4）：9 个元素叠在一起会糊。
VIDEO_MAX_GROUP = 8
# 结构图要点的**单批**预算。⚠️⚠️ 为什么必须分批：服务商默认 max_tokens = 8192，
#     实测 240 个元素的输出会在 char ~15963 处被**截断** → JSON 非法 → 解析失败
#     → 整条生成链路报「结构图内容生成失败」（2026-10-09 实测：33 分钟 / 104 句的
#     播客 100% 复现）。60 是实测安全上界（59 个元素一次成功、240 个必挂）。
POINT_CHUNK_EVENT_BUDGET = 60
# 单批句数预算：句很短时「60 个事件」也可能撑爆输出（每句都要至少 1 个元素）。
POINT_CHUNK_SEG_BUDGET = 30


def generate_video_script(source_title: str, source_text: str, script_chars: int = 850,
                          seg_count: int = 16, instruction: str = "",
                          style: str = "solo") -> list[dict]:
    """演绎层：素材 → 视频讲稿（逐句，含画面可定位的 anchor 短语）。

    ⚠️ 与播客脚本共用 JSON 形状与解析器（纯函数复用），但用**独立的提示词**：
    视频讲稿多一条硬约束 —— 每句必须含一个「原文里原样存在的多字短语」，
    否则视觉定位无锚可用（这是视频与播客在讲稿层唯一的实质差别）。
    """
    avg = max(20, round(script_chars / max(1, seg_count)))
    prompt = (get_prompt("prompt_video_script")
              .replace("{script_chars}", str(script_chars))
              .replace("{max_chars}", str(round(script_chars * 1.1)))
              .replace("{seg_count}", str(seg_count))
              .replace("{avg_chars}", str(avg)))
    extra = f"\n\n用户额外要求：{instruction}" if instruction else ""
    if style == "dialogue":
        extra += ("\n\n【本期为双人对谈】请用 host（主持人提问）与 expert（专家解答）交替；"
                  "同一角色不要连续超过 3 句。")
    raw = summary_chat([
        {"role": "system", "content": prompt},
        {"role": "user", "content": f"素材标题：{source_title}\n\n素材内容：\n{source_text}{extra}"},
    ], kind="video_script")
    return _parse_podcast_script(raw)


def generate_video_points(script_text: str, seg_count: int,
                          point_count: int | None = None,
                          shots: list | None = None,
                          skin: str = "") -> dict:
    """结构图内容：讲稿 → {title, elements:[{t,k,seg,group,sc}],
                       scenes:[{shot,title,screens:[{layout,s,e}]}]}。

    ⚠️ 只让模型给「第几句」（seg），**绝对毫秒由后端按该句的 start_ms 算**：
    让模型写毫秒是必错的（它不知道 TTS 后的真实时间轴）。
    ⚠️ `point_count` 必须由调用方按**时长**算好（`video.needed_events`）——
       这里的默认值只按句数兜底。实测证明「一句一个元素」在真实音轨上会让最长静止到
       **42 秒**（19 句 / 200 秒），闸门直接不放行。
    ⚠️ `shots` 是分镜边界 [{n, a, b}]（a/b 为 1 基句号，调用方按 build_shots +
       讲稿时间轴算出）。传入后提示词附带分镜表，模型为每个镜头出 title/layout；
       不传（或校验不过）则退回旧扁平格式（全部 grid 版式，镜头标题沿用原截取逻辑）。
    ⚠️ 模型**经常少给**（实测：要 19 个只给 9 个 = 47%）。事件数直接决定闸门能否放行，
       而用户能做的只有「再点一次生成」→ 数量严重不足时**内部重试一次**，
       并把「上一次差多少」明确告诉模型（PRD 3.6.8 的「重试 1 次」策略，
       从"生成失败"扩展到"数量不足"；这里只重试、**不编造**任何元素）。
    ⚠️ `skin`：实际视觉方案（graph/graph_paper/graph_clean/ppt/board/motion），
       **只用于记账 kind 的拆分**，不影响提示词与产出。
    """
    kind = points_usage_kind(skin)
    n = max(8, int(point_count or seg_count))
    segs = _split_point_lines(script_text, seg_count)
    shots = _valid_point_shots(shots, seg_count)

    if shots:
        # ---- 有分镜表：按镜头边界分批（绝不劈开镜头），模型按镜头出 title/layout ----
        batches = _point_batches(segs, shots)
        if len(batches) <= 1:
            return _gen_points_single(script_text, seg_count, n, shots_table=shots, kind=kind)
        merged: list = []
        scenes_all: list = []
        title = ""
        seg_cursor = 0                     # 本批第一句的**全局**序号（0 基）
        for b_shots in batches:
            take = sum(sh["b"] - sh["a"] + 1 for sh in b_shots)
            part = segs[seg_cursor:seg_cursor + take]
            text = "\n".join(f"{j}. {t}" for j, t in enumerate(part, 1))
            # 批内分镜表：镜头号与句号都按批内重编（模型只见批内编号，防串号）
            local_table = [{"n": i, "a": sh["a"] - seg_cursor, "b": sh["b"] - seg_cursor}
                           for i, sh in enumerate(b_shots, 1)]
            sub = _gen_points_single(text, take, max(8, -(-n * take // len(segs))),
                                     want_title=(not title), shots_table=local_table, kind=kind)
            title = title or str(sub.get("title") or "")
            for e in sub.get("elements") or []:
                try:
                    local = int(e.get("seg") or 1)
                except (TypeError, ValueError):
                    local = 1
                # 批内序号 → **全局句号**（后面按 seg 算绝对毫秒，错一位整段错位）
                e["seg"] = max(1, min(seg_cursor + local, seg_count))
                merged.append(e)
            for sc in sub.get("scenes") or []:
                try:
                    local_shot = int(sc.get("shot") or 0)
                except (TypeError, ValueError):
                    continue
                if not (1 <= local_shot <= len(b_shots)):
                    continue
                # ⚠️ 屏的 s/e 是**批内句号**，必须加 seg_cursor 换算成全局句号 ——
                #    与元素 seg 同一处纪律，错一位整段屏切换错位。
                screens = []
                for s2 in sc.get("screens") or []:
                    if not isinstance(s2, dict):
                        continue
                    try:
                        lo = int(s2.get("s") or 1) + seg_cursor
                        hi = int(s2.get("e") or 1) + seg_cursor
                    except (TypeError, ValueError):
                        continue
                    lay = str(s2.get("layout") or "grid").strip().lower()
                    screens.append({"layout": lay,
                                    "s": max(1, min(lo, seg_count)),
                                    "e": max(1, min(hi, seg_count))})
                scenes_all.append({"shot": b_shots[local_shot - 1]["n"],
                                   "title": str(sc.get("title") or ""),
                                   "screens": screens})
            seg_cursor += take
        if not merged:
            raise HTTPException(500, "结构图内容生成失败：模型未返回可用元素，请重试")
        if len(merged) < max(8, int(n * 0.6)):
            raise HTTPException(
                500, f"结构图内容生成失败：分批只得到 {len(merged)} 个元素（目标 {n}），请重试")
        # 元素 group 由后端按 seg 归属镜头** authoritative 重算**（不信模型的场景归属，
        # 模型只管内容；渲染层按时间归属，两者殊途同归到同一镜头）
        _assign_group_by_shots(merged, shots)
        # 屏归属同样由后端按 seg 权威重算（信模型编号会出现「后一屏的点先亮」）
        _assign_sc_by_screens(merged, scenes_all)
        return {"title": title[:20] or "内容结构", "elements": merged, "scenes": scenes_all}

    # ---- 无分镜表（旧路径）：纯句数/事件预算分批，扁平 elements，全 grid ----
    k = 1
    if segs:
        k = max(1,
                -(-n // POINT_CHUNK_EVENT_BUDGET),
                -(-len(segs) // POINT_CHUNK_SEG_BUDGET))
    if k <= 1:
        return _gen_points_single(script_text, seg_count, n, kind=kind)

    # ⚠️ 分句失败（格式不符）时 `segs` 为 None → k 保持 1 → 走上面的单批路径，
    #    绝不因为「分不开」把原本能生成的情况弄坏。
    base, rem = divmod(len(segs), k)
    merged = []
    title = ""
    cursor = 0                     # 本批第一句的**全局**序号（0 基）
    for b in range(k):
        take = base + (1 if b < rem else 0)
        if take <= 0:
            continue
        part = segs[cursor:cursor + take]
        cursor += take
        text = "\n".join(f"{j}. {t}" for j, t in enumerate(part, 1))
        sub = _gen_points_single(text, len(part), max(8, -(-n // k)),
                                 want_title=(not title), kind=kind)
        title = title or str(sub.get("title") or "")
        for e in sub.get("elements") or []:
            try:
                local = int(e.get("seg") or 1)
            except (TypeError, ValueError):
                local = 1
            # 批内序号 → **全局句号**（后面按 seg 算绝对毫秒，错一位整段错位）
            e["seg"] = max(1, min(cursor - take + local, max(1, seg_count)))
            merged.append(e)
    if not merged:
        raise HTTPException(500, "结构图内容生成失败：模型未返回可用元素，请重试")
    if len(merged) < max(8, int(n * 0.6)):
        raise HTTPException(
            500, f"结构图内容生成失败：分批只得到 {len(merged)} 个元素（目标 {n}），请重试")
    return {"title": title[:20] or "内容结构", "elements": _reassign_groups(merged),
            "scenes": []}


def _valid_point_shots(shots: list | None, seg_count: int) -> list | None:
    """分镜表校验：从 1 连续编号、不重叠、恰好覆盖 [1, seg_count]。任何一条不符返回 None。

    ⚠️ 宁可整表作废（退回无分镜旧路径），也不拿一张「缺句/重叠」的表去约束模型 ——
       表错了，每个镜头的 title/layout 都会贴错位置。
    """
    if not shots or seg_count < 1:
        return None
    out = []
    try:
        for i, sh in enumerate(shots, 1):
            out.append({"n": int(sh.get("n") or i),
                        "a": int(sh.get("a") or 0), "b": int(sh.get("b") or 0)})
    except (TypeError, ValueError, AttributeError):
        return None
    for i, sh in enumerate(out, 1):
        if sh["n"] != i or not (1 <= sh["a"] <= sh["b"] <= seg_count):
            return None
    if out[0]["a"] != 1 or out[-1]["b"] != seg_count:
        return None
    for i in range(1, len(out)):
        if out[i]["a"] != out[i - 1]["b"] + 1:
            return None
    return out


def _point_batches(segs: list, shots: list) -> list:
    """把分镜表按句数预算切成若干批（**绝不劈开镜头**）。返回 [[shot...], ...]。

    ⚠️ 分批的动机是输出 token 上限（见 POINT_CHUNK_EVENT_BUDGET 注释）：
       单批输出过长必被截断。按镜头切批保证模型的场景编号不需要跨批对齐。
    """
    batches, cur, cur_segs = [], [], 0
    for sh in shots:
        scount = sh["b"] - sh["a"] + 1
        if cur and cur_segs + scount > POINT_CHUNK_SEG_BUDGET:
            batches.append(cur)
            cur, cur_segs = [], 0
        cur.append(sh)
        cur_segs += scount
    if cur:
        batches.append(cur)
    # 批数还需受事件预算约束（要 200+ 元素时，单批 30 句也可能撑爆输出）
    need_k = max(1, -(-len(segs) // POINT_CHUNK_SEG_BUDGET))
    while len(batches) < need_k and len(batches) < len(shots):
        # 把最大的批从中间劈开（按镜头边界）
        bi = max(range(len(batches)),
                 key=lambda i: sum(s["b"] - s["a"] + 1 for s in batches[i]))
        big = batches[bi]
        if len(big) < 2:
            break
        half = len(big) // 2
        batches[bi:bi + 1] = [big[:half], big[half:]]
    return batches


def _assign_group_by_shots(elements: list, shots: list) -> None:
    """按 seg 把元素的 group 归到**覆盖它的镜头号**（后端权威口径，不信模型的场景归属）。"""
    for e in elements:
        seg = int(e.get("seg") or 1)
        g = shots[-1]["n"]
        for sh in shots:
            if sh["a"] <= seg <= sh["b"]:
                g = sh["n"]
                break
        e["group"] = g


def _split_point_lines(script_text: str, seg_count: int) -> list | None:
    """把 `1. xxx\n2. yyy` 切成**纯文本**分句；任何一行不符就返回 None（回退单批）。

    ⚠️ 严格按 `^<序号>[.、] ` 逐行匹配：宁可回退单批，也不要「切歪了」导致
       句号错位 —— seg 是后面算绝对毫秒的唯一依据，错一位整段画面错位。
    """
    lines = [l for l in (script_text or "").split("\n") if l.strip()]
    if not lines or len(lines) != max(1, int(seg_count or 0)):
        return None
    out = []
    for i, l in enumerate(lines, 1):
        m = re.match(r"^\s*%d[.、]\s*" % i, l)
        if not m:
            return None
        out.append(l[m.end():])
    return out


def _gen_points_single(script_text: str, seg_count: int, need: int,
                       want_title: bool = True, shots_table: list | None = None,
                       kind: str = "video_points") -> dict:
    """**单批**结构图要点：讲稿 → {title, elements, scenes}。

    `generate_video_points` 分批后每一批都走这里，接口与旧实现等价。
    ⚠️ `kind`：记账用的业务场景（由 `points_usage_kind(skin)` 算出，按皮肤拆分）。
    ⚠️ 只让模型给「第几句」（seg），**绝对毫秒由后端按该句的 start_ms 算**：
       让模型写毫秒是必错的（它不知道 TTS 之后的真实时间轴）。
    ⚠️ `shots_table` 是批内分镜表 [{n,a,b}]（句号也是批内编号）；给了它，
       模型才按镜头输出 title/screens（每个镜头若干屏、每屏一个版式），
       否则退回扁平 elements。
    ⚠️ 模型**经常少给**（实测：要 19 个只给 9 个 = 47%）。事件数直接决定闸门能否放行，
       而用户能做的只有「再点一次生成」→ 数量严重不足时**内部重试一次**，
       并把「上一次差多少」明确告诉模型（PRD 3.6.8 的「重试 1 次」策略，
       从"生成失败"扩展到"数量不足"；这里只重试、**不编造**任何元素）。
    """
    n = max(8, int(need or seg_count))
    per_seg = max(1, round(n / max(1, seg_count)))
    prompt = (get_prompt("prompt_video_points")
              .replace("{seg_count}", str(seg_count))
              .replace("{point_count}", str(n))
              .replace("{per_seg}", str(per_seg))
              .replace("{max_group}", str(VIDEO_MAX_GROUP)))
    table = ""
    if shots_table:
        rows = "\n".join(f"镜头 {sh['n']}（第 {sh['a']}-{sh['b']} 句）"
                         for sh in shots_table)
        table = ("\n\n分镜表（句号指上面讲稿的行号；每个镜头的 screens 与各元素的 seg "
                 "都必须落在该镜头的句号范围内）：\n" + rows)
    user = f"视频讲稿（共 {seg_count} 句）：\n{script_text}{table}"
    raw = summary_chat([
        {"role": "system", "content": prompt},
        {"role": "user", "content": user},
    ], kind=kind)
    obj = _parse_video_points(raw, seg_count, shots_table)
    got = len(obj.get("elements") or [])
    if got >= max(8, int(n * 0.6)):
        return obj
    # 数量严重不足（< 目标的 60%）→ 重试一次，把差距直说
    try:
        raw2 = summary_chat([
            {"role": "system", "content": prompt},
            {"role": "user", "content": (
                f"{user}\n\n"
                f"⚠️ 上一次只给出了 {got} 个元素，**数量不足**。"
                f"本次必须给出不少于 {n} 个元素（平均每句约 {per_seg} 个），"
                f"否则视频会出现长时间画面静止、无法通过质量闸门。")},
        ], kind=kind)
        obj2 = _parse_video_points(raw2, seg_count, shots_table)
        if len(obj2.get("elements") or []) > got:
            return obj2
    except BaseException:
        pass
    return obj


def generate_video_anchors(script_lines: list[str], source_text: str = "") -> list[dict]:
    """定位锚点：逐句产 1-4 个 anchor 短语（长句给更多，按句中先后给）。

    返回 [{"i": 1, "anchors": [...]}]。空 anchors 是**合法**结果（过渡句）——
    调用方对该句回退到「本句文本切分」的兜底定位，所以这里不抛错。

    `source_text`：**视觉来源材料的原文片段**（不给则退回「猜」，见下）。

    ⚠️ 不传原文是本链路曾经的**根本缺陷**：提示词第 2 条硬性要求
       「anchor 必须是原文里原样出现过的短语」，而模型手里只有讲稿 ——
       讲稿是 LLM 改写过的，措辞与原文不同，模型只能**猜**哪个短语在原文里存在。
       实测（`_fetch_probe/_diag_avideo_locator_20261008.py`）：讲稿与材料同源的剪藏材料上，
       讲稿 4-gram 在页面文本里的命中率只有 18.2% → 闸门判据 4（≥95%）永远过不去
       → 「材料原件（source）」皮肤等于不可用。
       原文片段由 `locate.source_excerpt()` 从材料页面抽取，经用户消息交给模型。
    """
    numbered = "\n".join(f"{i}. {t}" for i, t in enumerate(script_lines, 1))
    prompt = get_prompt("prompt_video_anchor").replace("{seg_count}", str(len(script_lines)))
    user = f"讲稿：\n{numbered}"
    src = (source_text or "").strip()
    if src:
        # ⚠️ 刻意挂在**用户消息**上，而不是提示词里的 `{source_excerpt}` 占位符：
        #    用户可能在设置页改过这段提示词，改过的版本不会有占位符 ——
        #    靠占位符注入会**静默失效**（模型又回到「猜」的状态，且毫无报错）。
        user += ("\n\n原文片段（锚点只能从下面这段原文里**逐字原样摘取**；"
                 "整段都摘不到就给 []）：\n" + src)
    raw = summary_chat([
        {"role": "system", "content": prompt},
        {"role": "user", "content": user},
    ], kind="video_script")
    return _parse_video_anchors(raw, len(script_lines))


def retry_video_call(fn, tries: int = 2):
    """视频链路的 LLM 重试（PRD 3.4.7：JSON 非法 → 自动重试 1 次）。

    复用与陪练同一套重试节奏（首败 sleep 1.5s 再试），保持全项目一致。
    """
    last: Exception | None = None
    for attempt in range(max(1, tries)):
        try:
            return fn()
        except Exception as e:      # HTTPException 也在此列（已是可读文案）
            last = e
            if attempt < tries - 1:
                time.sleep(1.5)
    raise last  # type: ignore[misc]


def _clamp_point(it, seg_count: int) -> dict | None:
    """单个元素的边界钳制（类别白名单 / seg 范围）；不合法返回 None。"""
    if not isinstance(it, dict):
        return None
    t = str(it.get("t") or "").strip()
    if not t:
        return None
    k = str(it.get("k") or "").strip().lower()
    if k not in VIDEO_KINDS:
        k = "concept"
    try:
        seg = int(it.get("seg") or 1)
    except (TypeError, ValueError):
        seg = 1
    seg = max(1, min(seg, max(1, seg_count)))
    out = {"t": t[:24], "k": k, "seg": seg, "group": 1}
    # tex（可选，2026-10-21 动效图解）：公式元素。数组 = 按讲解顺序逐项的 LaTeX；
    # 字符串视为单项。只透白名单外的**形状**（长度/项数），不校验 LaTeX 合法性 ——
    # 非法公式在 `video.tex_to_mathml` 转换时失败、退化为文本块，不会白屏。
    # ⚠️ 旧皮肤（graph/ppt/board 等）从不读这个字段，透传对它们零影响。
    raw_tex = it.get("tex")
    terms = None
    if isinstance(raw_tex, list):
        terms = [str(x).strip()[:120] for x in raw_tex if str(x or "").strip()][:8]
    elif isinstance(raw_tex, str) and raw_tex.strip():
        terms = [raw_tex.strip()[:240]]
    if terms:
        out["tex"] = terms
    # chart（可选，2026-10-21 动效图解 P1）：数据图元素。校验只认「形状合法」——
    # type 白名单、2-8 项、value 是非负有限数、label 截断；任一项不合法整体丢弃
    # （降级为普通文本块，不白屏）。⚠️ value 的**真实性**靠提示词约束
    # （必须是讲稿里出现的数字），这里只防模型把结构写坏。
    raw_ch = it.get("chart")
    if isinstance(raw_ch, dict):
        ctype = str(raw_ch.get("type") or "").strip().lower()
        items = raw_ch.get("items")
        if ctype in ("bar", "line") and isinstance(items, list):
            vals = []
            for x in items[:8]:
                if not isinstance(x, dict):
                    vals = None
                    break
                try:
                    fv = float(x.get("value"))
                except (TypeError, ValueError):
                    vals = None
                    break
                if fv != fv or fv < 0 or fv in (float("inf"), float("-inf")):
                    vals = None
                    break
                vals.append({"label": str(x.get("label") or "")[:10], "value": fv})
            if vals and len(vals) >= 2:
                out["chart"] = {"type": ctype, "items": vals}
    return out


def _split_screen_units(els: list, layout: str, limits: dict) -> list:
    """一屏的元素按版式上限切成若干「屏」；不足下限的块降级 grid。

    ⚠️⚠️ **超上限必须切开、不能降级** —— 这是这次改造的核心：
       闸门密度下限把每镜逼到 15-21 个元素（151s/4 镜需 63 个事件），
       若按「越界即降级 grid」处理，flow/hub/compare/pyramid 的上限（6-7）
       永远被突破 → 版式**永远退化成 grid**（实测作品 5：四镜全 grid，结构化等于没生效）。
       正确做法：保持版式、把这一屏切成多屏 —— 密度与结构才能同时成立。
    ⚠️ 尾块可能小于下限（如 7 个元素、flow 上限 6 → 切 6+1）：不足下限的块单独降级 grid，
       绝不留一块「孤零零 1 张卡的 flow」（元素太少撑不起流程，观感比 grid 更糟）。
    返回 [(元素块, 该块版式), ...]（顺序即时间顺序）。
    """
    lo, hi = limits.get(layout, (1, 10))
    if len(els) <= hi:
        return [(els, layout if (layout == "grid" or len(els) >= lo) else "grid")]
    n_parts = -(-len(els) // max(1, hi))
    base, rem = divmod(len(els), n_parts)
    out, cursor = [], 0
    for i in range(n_parts):
        take = base + (1 if i < rem else 0)
        chunk = els[cursor:cursor + take]
        cursor += take
        if not chunk:
            continue
        out.append((chunk, layout if (layout == "grid" or len(chunk) >= lo) else "grid"))
    return out


def _assign_sc_by_screens(elements: list, scenes: list) -> None:
    """按 seg 把元素的 `sc` 归到**覆盖它的那一屏**（后端权威口径，不信模型的屏归属）。

    ⚠️ 与 `_assign_group_by_shots` 同一纪律：模型只管内容，归属一律后端重算 ——
       否则「屏的时间顺序」会被模型的随手编号破坏，出现「后一屏的点先亮」。
    ⚠️ 屏的 s/e 必须是**全局句号**（分批路径在合并时已换算过）。
    """
    for e in elements:
        try:
            seg = int(e.get("seg") or 1)
            shot = int(e.get("group") or 1)
        except (TypeError, ValueError):
            e["sc"] = 0
            continue
        screens = []
        for s in scenes or []:
            try:
                if int(s.get("shot") or 0) == shot:
                    screens = s.get("screens") or []
                    break
            except (TypeError, ValueError):
                continue
        idx = 0
        for j, scr in enumerate(screens):
            try:
                if int(scr.get("e") or 0) >= seg:
                    idx = j
                    break
            except (TypeError, ValueError):
                continue
        else:
            idx = max(0, len(screens) - 1)
        e["sc"] = idx


def _parse_video_points(raw: str, seg_count: int,
                        shots_table: list | None = None) -> dict:
    """解析结构图 JSON 对象：**新格式（scenes → screens 两层）优先，旧格式逐级兜底**。

    ⚠️ 三种格式都必须能吃（用户可能在设置页改过提示词，只认新格式 = 让自定义用户必挂）：
       ① {title, scenes:[{shot,title,screens:[{layout,elements}]}]}   当前格式
       ② {title, scenes:[{shot,title,layout,elements}]}              上一版（视为单屏）
       ③ {title, elements:[{t,k,seg,group}]}                         最初扁平格式
    ⚠️ 钳制必须在这里做完：模型给出的 seg 越界、k 非法、版式乱写都是常态，
       放任下去会在渲染期变成「元素永远不亮」或「一屏 15 个元素」这类静默故障。
    ⚠️ 每屏元素数**超上限时切开、低于下限才降级**（见 `_split_screen_units`）。
    返回的元素带 `sc`（屏序号、镜头内 0 基）；scenes 里每屏给 `s`/`e`（首末句号，全局）。
    """
    from .video import GRAPH_LAYOUTS, GRAPH_LAYOUT_LIMITS
    txt = _strip_code_fence(raw)
    start, end = txt.find("{"), txt.rfind("}")
    try:
        obj = json.loads(txt[start:end + 1])
        assert isinstance(obj, dict)
    except Exception:
        raise HTTPException(500, "结构图内容生成失败，请重试")
    title = str(obj.get("title") or "").strip()[:20] or "内容结构"

    scs = obj.get("scenes")
    if isinstance(scs, list) and scs:
        seg_range = {}
        if shots_table:
            for sh in shots_table:
                seg_range[int(sh["n"])] = (int(sh["a"]), int(sh["b"]))
        scenes_out, elements_out = [], []
        seen_shot = set()
        for sc in scs:
            if not isinstance(sc, dict):
                continue
            try:
                shot = int(sc.get("shot") or 0)
            except (TypeError, ValueError):
                continue
            if shot in seen_shot:
                continue
            if seg_range and shot not in seg_range:
                continue
            seen_shot.add(shot)
            stitle = str(sc.get("title") or "").strip()[:16]
            raw_screens = sc.get("screens")
            if not (isinstance(raw_screens, list) and raw_screens):
                # ② 上一版：镜头级单个 layout + elements → 视为「一镜一屏」
                raw_screens = [{"layout": sc.get("layout"),
                                "elements": sc.get("elements")}]
            screens_out = []
            for raw_scr in raw_screens:
                if not isinstance(raw_scr, dict):
                    continue
                layout = str(raw_scr.get("layout") or "").strip().lower()
                if layout not in GRAPH_LAYOUTS:
                    layout = "grid"
                els = []
                for it in raw_scr.get("elements") or []:
                    e = _clamp_point(it, seg_count)
                    if e is None:
                        continue
                    if seg_range:
                        lo_s, hi_s = seg_range[shot]
                        e["seg"] = max(lo_s, min(e["seg"], hi_s))
                    e["group"] = shot
                    els.append(e)
                if not els:
                    continue
                for chunk, lay in _split_screen_units(els, layout, GRAPH_LAYOUT_LIMITS):
                    sc_idx = len(screens_out)
                    for e in chunk:
                        e["sc"] = sc_idx
                    elements_out.extend(chunk)
                    screens_out.append({"layout": lay,
                                        "s": min(e["seg"] for e in chunk),
                                        "e": max(e["seg"] for e in chunk)})
            if screens_out:
                scenes_out.append({"shot": shot, "title": stitle,
                                   "screens": screens_out})
        if elements_out:
            elements_out.sort(key=lambda e: (e["seg"], e["group"], e["sc"]))
            return {"title": title, "elements": elements_out, "scenes": scenes_out}
        # scenes 结构在、但里面一个可用元素都没有 → 不静默，落去旧格式再试一次
    els = []
    for it in obj.get("elements") or []:
        e = _clamp_point(it, seg_count)
        if e is None:
            continue
        try:
            grp = int(it.get("group") or 1)
        except (TypeError, ValueError):
            grp = 1
        e["group"] = max(1, grp)
        e["sc"] = 0
        els.append(e)
    if not els:
        raise HTTPException(500, "结构图内容生成失败：模型未返回可用元素，请重试")
    return {"title": title, "elements": _reassign_groups(els), "scenes": []}



def _reassign_groups(els: list) -> list:
    """把 group 重排为连续序号，并强制「每镜头 ≤ VIDEO_MAX_GROUP」。

    ⚠️ 抽成独立函数是因为**分批生成**也要用它：分批各自重排会让镜头号重复/跳号
       （第 1 批与第 2 批都从 group=1 开始），渲染时会出现「同一屏塞进 15 个元素」。
       ⚠️ 绝不复制一份另写 —— 两处漂移 = 分批与非分批的镜头切分不一致。
    ⚠️ 模型经常把 15 个元素塞进 group=1。按出现顺序切块即可，元素顺序不受影响
       （每个元素仍带有自己的 seg）。
    """
    ordered = sorted(els, key=lambda e: (e["group"], e["seg"]))
    fixed, gid, used = [], 1, 0
    for e in ordered:
        if used >= VIDEO_MAX_GROUP:
            gid += 1
            used = 0
        e["group"] = gid
        used += 1
        fixed.append(e)
    # 还原成「按 seg 排序」的元素顺序（渲染时按 seg 升序点亮），但 group 已归并
    fixed.sort(key=lambda e: (e["seg"], e["group"]))
    return fixed


def _parse_video_anchors(raw: str, seg_count: int) -> list[dict]:
    """解析 anchor JSON 数组；**允许空数组**（全为过渡句），但不允许整体解析失败。"""
    txt = _strip_code_fence(raw)
    start, end = txt.find("["), txt.rfind("]")
    if start < 0 or end <= start:
        raise HTTPException(500, "定位锚点生成失败，请重试")
    try:
        items = json.loads(txt[start:end + 1])
    except Exception:
        raise HTTPException(500, "定位锚点生成失败，请重试")
    out = []
    seen = set()
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            i = int(it.get("i") or 0)
        except (TypeError, ValueError):
            continue
        if not (1 <= i <= seg_count) or i in seen:
            continue
        seen.add(i)
        words = []
        for w in (it.get("anchors") or []):
            w = str(w or "").strip()
            # 单字 anchor 一律丢弃（PRD 3.5.6 硬规则 1）；同时丢弃句内重复
            if len(w) >= 2 and w not in words:
                words.append(w)
        out.append({"i": i, "anchors": words[:4]})
    return out


# ---------- AI 陪练（模块 J） ----------
# 题型：题纲 / 出题（含期望要点） / 评分，全部走 summary_chat（与「测一测」同模型）。
# 记账 kind：出题链路 drill / 评分链路 drill_judge（统计页据此分账）。

def _parse_drill_json_object(raw: str, keys: tuple,
                             err_msg: str = "陪练生成失败，请重试") -> dict:
    """解析 LLM 返回的 JSON 对象（容错代码块包裹 / 前后杂质）。

    err_msg 用于**按阶段分流文案**（PRD 3.4.7）：评分阶段失败必须说「评分失败」，
    不能笼统说「生成失败」——那会让用户误以为出题炸了、跑去重开一场。
    """
    txt = _strip_code_fence(raw)
    start, end = txt.find("{"), txt.rfind("}")
    try:
        obj = json.loads(txt[start:end + 1])
        assert isinstance(obj, dict) and all(k in obj for k in keys)
        return obj
    except Exception:
        raise HTTPException(500, err_msg)


def _parse_drill_json_list(raw: str, keys: tuple,
                           err_msg: str = "陪练生成失败，请重试") -> list[dict]:
    """解析 LLM 返回的 JSON 数组；**绝不返回空数组**（否则调用方会静默把题纲写空）。"""
    txt = _strip_code_fence(raw)
    start, end = txt.find("["), txt.rfind("]")
    try:
        items = json.loads(txt[start:end + 1])
    except Exception:
        raise HTTPException(500, err_msg)
    out = [i for i in items if isinstance(i, dict) and all(k in i for k in keys)]
    if not out:
        raise HTTPException(500, "陪练生成失败：模型未返回可用考点，请重试")
    return out


def _retry_drill_call(fn, tries: int = 2):
    """陪练链路的 LLM 调用重试（PRD 3.3.7 / 3.4.6 / 3.5：JSON 非法 → 重试 1 次）。

    ⚠️ 只对**异常**重试（JSON 解析失败 / 网络抖动 / 429），不对生成质量做判断。
    ⚠️ 全部失败后抛**最后一次**异常：调用方按阶段降级 ——
       出题失败 → 该考点跳过；评分失败 → 该题标未评分且不阻断后续（PRD 3.4.7）。
    与 map_group_safe 同构（首败 sleep 1.5s 再试），保持全项目一致的重试节奏。
    """
    last: Exception | None = None
    for attempt in range(max(1, tries)):
        try:
            return fn()
        except Exception as e:      # HTTPException 也在此列（已是可读文案）
            last = e
            if attempt < tries - 1:
                time.sleep(1.5)
    raise last  # type: ignore[misc]


def generate_drill_outline(context: str, count: int, scope_label: str = "") -> list[dict]:
    """生成考点题纲（模式一）。返回 [{point, difficulty, hint, source}]，最多 count 条。"""
    def _once() -> list[dict]:
        raw = summary_chat([
            {"role": "system", "content": get_prompt("prompt_drill_examiner").replace("{count}", str(count))},
            {"role": "user", "content": f"【陪练范围】{scope_label}\n\n【素材内容】\n{context}\n\n"
                                        f"请按「模式一 · 出题纲」输出，生成 {count} 个互不重复的考点。"},
        ], kind="drill")
        return _parse_drill_json_list(raw, ("point",))[:count]

    return _retry_drill_call(_once)


def generate_drill_question(context: str, point: str, hint: str = "",
                            difficulty: str = "medium") -> dict:
    """为单个考点生成「开放式问题 + 期望要点」（模式二）。

    ⚠️ 问题与期望要点**必须同批产出**（PRD 3.4.5）：否则评分阶段无对照标准。
    """
    extra = f"\n难度：{difficulty}"
    if hint:
        extra += f"\n提问角度提示：{hint}"

    def _once() -> dict:
        raw = summary_chat([
            {"role": "system", "content": get_prompt("prompt_drill_examiner")},
            {"role": "user", "content": f"【素材内容】\n{context}\n\n【本次要考察的考点】{point}{extra}\n\n"
                                        f"请按「模式二 · 出题 + 期望要点」输出。"},
        ], kind="drill")
        obj = _parse_drill_json_object(raw, ("question", "expected_points"))
        obj["expected_points"] = [str(p).strip() for p in (obj.get("expected_points") or []) if str(p).strip()]
        if not obj["expected_points"]:
            raise HTTPException(500, "陪练生成失败：未产出期望要点，请重试")
        return obj

    return _retry_drill_call(_once)


def generate_drill_followup(context: str, question: str, expected_points: list,
                            user_answer: str) -> dict | None:
    """为同一考点生成一层追问（模式三）。失败返回 None（调用方静默降级到下一考点）。"""
    def _once() -> dict | None:
        raw = summary_chat([
            {"role": "system", "content": get_prompt("prompt_drill_examiner")},
            {"role": "user", "content": f"【素材内容】\n{context}\n\n【原问题】{question}\n\n"
                                        f"【期望要点】\n" + "\n".join(f"- {p}" for p in expected_points)
                                        + f"\n\n【学习者作答】\n{user_answer}\n\n请按「模式三 · 追问」输出。"},
        ], kind="drill")
        obj = _parse_drill_json_object(raw, ("question",))
        q = str(obj.get("question") or "").strip()
        if not q or q == question:
            return None
        pts = [str(p).strip() for p in (obj.get("expected_points") or []) if str(p).strip()]
        return {"question": q, "expected_points": pts or list(expected_points)}

    try:
        return _retry_drill_call(_once)
    except Exception:
        return None


def judge_drill_answer(question: str, expected_points: list, user_answer: str) -> dict:
    """评分（结构化 JSON）。⚠️ 必须把**期望要点**一并喂入，否则模型只能凭感觉给分（PRD 3.5）。

    返回 {score, verdict, dimensions, comment, missing_points, followup}，已做边界钳制与兜底。
    ⚠️ 文案分流（A3）：评分阶段失败说「本题评分失败」，不复用题纲/出题阶段的「生成失败」。
    """
    pts = "\n".join(f"- {p}" for p in (expected_points or [])) or "（未提供）"

    def _once() -> dict:
        raw = summary_chat([
            {"role": "system", "content": get_prompt("prompt_drill_judge")},
            {"role": "user", "content": f"【问题】{question}\n\n【期望要点】\n{pts}\n\n"
                                        f"【学习者作答】\n{user_answer}"},
        ], kind="drill_judge")
        obj = _parse_drill_json_object(raw, ("score", "verdict", "comment"),
                                       err_msg="本题评分失败，可稍后重试")
        return _normalize_judgement(obj)

    return _retry_drill_call(_once)


def _normalize_judgement(obj: dict) -> dict:
    """评分结果规范化：分数钳到 0-100、verdict 兜底、维度缺失用总分回填（PRD 3.5.7）。"""
    try:
        score = int(round(float(obj.get("score", 0))))
    except Exception:
        score = 0
    score = max(0, min(100, score))
    verdict = str(obj.get("verdict") or "").strip().lower()
    if verdict not in ("correct", "partial", "wrong"):
        verdict = "correct" if score >= 80 else ("partial" if score >= 40 else "wrong")
    dims = obj.get("dimensions")
    if not isinstance(dims, dict) or not dims:
        dims = {"正确性": score, "完整度": score, "表达": score}
    else:
        clean = {}
        for k in ("正确性", "完整度", "表达"):
            try:
                clean[k] = max(0, min(100, int(round(float(dims.get(k, score))))))
            except Exception:
                clean[k] = score
        dims = clean
    missing = [str(x).strip() for x in (obj.get("missing_points") or []) if str(x).strip()]
    fu = obj.get("followup") if isinstance(obj.get("followup"), dict) else {}
    fu_q = str(fu.get("question") or "").strip()
    fu_pts = [str(x).strip() for x in (fu.get("expected_points") or []) if str(x).strip()]
    return {
        "score": score, "verdict": verdict, "dimensions": dims,
        "comment": str(obj.get("comment") or "").strip()[:500],
        "missing_points": missing,
        "followup": ({"question": fu_q, "expected_points": fu_pts} if fu_q else None),
    }
