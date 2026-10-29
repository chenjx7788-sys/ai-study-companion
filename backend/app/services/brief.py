"""主题导读生成（2026-10-26）：把「库外推荐」落成一份可学材料。

定位：问答里发现库里没有相关内容时，AI 只能给书名（还必须标注「建议核实」）——
用户手上没书，这条路就断了。这里把「就这个主题先给一份入门导读」做成一个真动作：
生成 → 存进材料库（可读 / 可出讲义 / 可出题）→ 闭环。

⚠️ 产品诚实性：正文首行固定挂一条「AI 生成、非原书内容」声明；
   提示词也禁止编造 ISBN / 版本 / 页数 / 章节，只能「常见读本包括…（请自行核实）」。
⚠️ 标题模板固定（`《主题》入门导读`）：它同时是材料库条目名与幂等键
   （`brief:<主题>`）的来源，同一主题重复点不会堆出「(2)(3)」。
"""
import re

from . import llm

# 固定声明：不依赖模型自觉（模型偶发会省掉）
DISCLAIMER = (
    "> 本导读由 AI 生成，用于判断「这个主题值不值得深入」，不是原书内容；"
    "书名、版本、页码请以原书为准。\n\n"
)

# 标题里主题最多留 24 字：材料标题会进文件名，过长会撑爆路径
MAX_TITLE_SUBJECT = 24

# 低于这个字数视为生成失败：半句话不值得占一个材料位
MIN_BODY_CHARS = 80


def subject_title(subject: str, query: str) -> str:
    """`《三国》入门导读` —— 主题优先，剥不出主题时退回原问法（截断）。"""
    s = re.sub(r"\s+", " ", (subject or "").strip() or (query or "").strip())
    s = s[:MAX_TITLE_SUBJECT].strip().strip("《》")
    return ("《%s》入门导读" % s) if s else "主题入门导读"


def generate(subject: str, query: str) -> str:
    """按主题生成导读 Markdown（**不含标题**）。LLM 失败抛 HTTPException（已转可读文案）。"""
    topic = (subject or "").strip() or (query or "").strip()
    user = "主题：%s\n用户原话：%s" % (topic, (query or "").strip())
    md = (llm.chat([
        {"role": "system", "content": llm.get_prompt("prompt_brief")},
        {"role": "user", "content": user},
    ], kind="brief") or "").strip()
    if len(md) < MIN_BODY_CHARS:
        raise ValueError("生成的导读内容过短（%d 字）" % len(md))
    return DISCLAIMER + md
