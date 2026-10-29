"""库内内容发现（2026-10-26）：跨「材料 / 笔记 / AI 视频 / AI 讲书」的资源级检索。

为什么单独一个模块，而不是扩展 `kb_index`：
- `kb_index` 的契约是 chunk / note 两种 ref_type，视频与讲书没有 chunk 结构，
  硬塞进去会破坏 citations 与 stale 判据的语义；
- 「找内容」要的是**资源卡片**（形态 / 时长 / 入口），不是可引用的正文片段。

因此这里只读四张表做目录级匹配：不写索引、不改现有检索链路、不影响引用编号。

⚠️ 只匹配标题（+ 笔记正文、讲书简介），**不扫 material_chunks 正文**：
   3749 条块全量比对要几百毫秒且收益很低 —— 资源发现本来就该以标题为准，
   正文级检索仍由 /chat 原有的融合检索负责，两者职责不重叠。
"""
import re

from ..models import Material, Note, Podcast, Video

# 意图词 + 形态词：剥掉后剩下的才是主题。
# ⚠️⚠️ **长词必须排在短词前面**：`str.replace` 按顺序执行，若「帮我」先于「能帮我」，
#      「能帮我推荐一些吗」会变成「能 推荐一些吗」——「能」残留成噪声主题词。
# ⚠️ 单字一律不剥：「能」会吃掉「能力」、「下」会吃掉「上下文」、「讲」会吃掉「讲解」。
#    剥过头比剥不干净更糟（bigram 匹配本身对残留有容错）。
_STRIP_WORDS = (
    "可以帮我", "能帮我", "麻烦你", "帮我", "给我",
    "想了解", "想看看", "想读", "想学", "想看", "想听",
    "找一下", "找找", "搜一下", "推荐点", "推荐", "值得读", "入门书",
    "有没有", "有什么", "有哪些",
    "有声书", "讲书", "播客", "视频", "课程", "音频", "文章", "图书", "书籍",
    "书单", "清单",
    "内容", "资料", "材料", "笔记", "东西",
    "相关", "有关", "关于", "方面",
    "一些", "几本", "几门", "几个", "什么",
    "听听", "看看", "学学",
    "的", "了", "吗", "呢", "吧", "类", "点",
    "，", "、", "。", "？", "?", ",", "：", ":", " ",
)

# 形态词：用户点名某一类内容时（「有什么视频」「推荐点讲书」），
# 即使主题词为空或没命中，也该把这一类里最近的几条端出来。
_KIND_WORDS = {
    "video": ("视频", "课程", "录像"),
    "podcast": ("讲书", "播客", "音频", "有声书", "听书"),
    "article": ("文章", "帖子", "公众号", "剪藏"),
    "book": ("图书", "书籍", "书", "教材", "读物", "epub"),
    "note": ("笔记",),
}

_FORMAT_BADGE = {
    "pdf": "PDF", "docx": "Word", "md": "Markdown", "epub": "EPUB",
    "mp4": "视频课", "mp3": "音频课", "jpg": "图片", "png": "图片",
}

# 材料格式 → 内容形态（决定「我要视频」时谁该排在前面）
_FORMAT_FORM = {
    "mp4": "video", "mp3": "podcast",
    "pdf": "book", "epub": "book", "docx": "book",
    "md": "article", "jpg": "article", "png": "article",
}

_FORM_BOOST = 50     # 形态匹配加权：用户点名要视频，视频就该压过同分的文章


def extract_subject(q: str) -> str:
    """从「我想读三国类图书，能帮我推荐一些吗」里剥出主题词「三国」。

    ⚠️ 不能拿整句去匹配：口语里九成是意图词，整句比对必然 0 命中。
    """
    s = q or ""
    for w in _STRIP_WORDS:
        s = s.replace(w, " ")
    # 人称代词单独剥：不剥会残留「我三国」，bigram 虽能容错，但干净些更好
    s = re.sub(r"[我你他她它]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _prepare(subject: str) -> tuple[list[str], list[str]]:
    """把主题词拆成「英文整词 + 中文 bigram」。

    ⚠️ 中文不分词、用 bigram：剥壳后的残词（「我三国」）仍能命中「三国演义」。
    ⚠️ 英文必须走词边界整词：短词 AI 若也用 bigram，会命中 training 里的 "ai"。
    """
    low = (subject or "").lower()
    words = re.findall(r"[a-z0-9][a-z0-9\-_.]{1,}", low)
    cjk = re.sub(r"[a-z0-9\s\-_.]+", "|", low)
    grams: list[str] = []
    for seg in cjk.split("|"):
        seg = seg.strip()
        if len(seg) >= 2:
            grams += [seg[i:i + 2] for i in range(len(seg) - 1)]
    return words, grams


def _score(text: str, words: list[str], grams: list[str]) -> int:
    """命中强度 = 中文 bigram 命中数 + 英文整词命中数。0 = 不相关。"""
    if not text:
        return 0
    low = text.lower()
    n = sum(1 for g in grams if g in low)
    for w in words:
        if re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", low):
            n += 1
    return n


def _wanted_forms(q: str) -> set[str]:
    """问题里点名了哪几类内容（用于「只列视频」这种无主题词的诉求）。

    ⚠️ 必须「命中即消费」：「讲书」先把 podcast 标上并从串里抹掉，
       否则紧随其后的「书」会让 book 也命中 → 「推荐点讲书」连带列出全部 PDF。
    """
    s = q or ""
    hit: set[str] = set()
    for form, words in _KIND_WORDS.items():
        for w in sorted(words, key=len, reverse=True):
            if w in s:
                hit.add(form)
                s = s.replace(w, " ")
                break
    return hit


def _mmss(seconds) -> str:
    try:
        total = int(round(float(seconds or 0)))
    except (TypeError, ValueError):
        return ""
    return "%d:%02d" % (total // 60, total % 60) if total > 0 else ""


def _clip(text: str, n: int = 80) -> str:
    s = re.sub(r"\s+", " ", (text or "")).strip()
    return s[:n] + ("…" if len(s) > n else "")


def _pool(db, kinds_set: set[str]) -> list[dict]:
    """候选池：四类资源统一成同构 dict（不评分）。"""
    out: list[dict] = []
    if "material" in kinds_set:
        for m in db.query(Material).filter(Material.parsed_status == "success").all():
            fmt = (m.format or "").lower()
            out.append({
                "kind": "material", "id": m.id, "title": m.title or "",
                "badge": _FORMAT_BADGE.get(fmt, fmt.upper()),
                # mp4/mp3 的 page_count 存的是 ASR 分段数，标成「页」会误导
                "meta": ("%d 页" % m.page_count)
                        if (m.page_count and _FORMAT_FORM.get(fmt) in ("book", "article")) else "",
                "snippet": "", "route": "/study/%d" % m.id,
                "form": _FORMAT_FORM.get(fmt, "article"),
                "_body": "",
            })
    if "note" in kinds_set:
        for n in db.query(Note).all():
            out.append({
                "kind": "note", "id": n.id, "title": n.title or "",
                "badge": "笔记", "meta": "",
                "snippet": _clip(n.content), "route": "/knowledge",
                "form": "note", "_body": n.content or "",
            })
    if "video" in kinds_set:
        for v in db.query(Video).filter(Video.status == "finished").all():
            out.append({
                "kind": "video", "id": v.id, "title": v.title or "",
                "badge": "AI 视频", "meta": _mmss((v.duration_ms or 0) / 1000),
                "snippet": "", "route": "/video/%d" % v.id,
                "form": "video", "_body": "",
            })
    if "podcast" in kinds_set:
        for p in db.query(Podcast).filter(Podcast.status == "done").all():
            out.append({
                "kind": "podcast", "id": p.id, "title": p.title or "",
                "badge": "AI 讲书", "meta": _mmss(p.duration_sec),
                "snippet": _clip(p.brief), "route": "/podcast?p=%d" % p.id,
                "form": "podcast", "_body": p.brief or "",
            })
    return out


def search(db, query: str, limit: int = 6, kinds: list[str] | None = None) -> list[dict]:
    """跨四类内容做资源级检索，返回按相关度排序的卡片数据。

    kinds 可选值：material / note / video / podcast；None = 全部。
    """
    subject = extract_subject(query)
    words, grams = _prepare(subject)
    want = _wanted_forms(query)
    kinds_set = set(kinds) if kinds else {"material", "note", "video", "podcast"}

    scored: list[tuple[int, dict]] = []
    for it in _pool(db, kinds_set):
        sc = _score(it["title"], words, grams)
        if sc <= 0 and it["_body"]:            # 标题没中才看正文，正文权重降到 1/3
            sc = _score(it["_body"], words, grams) // 3
        if sc <= 0:
            continue
        if want and it["form"] in want:        # 用户点名形态 → 该形态优先
            sc += _FORM_BOOST
        scored.append((sc, it))

    # 补齐：**只在用户没给主题词时**（「推荐点讲书听听」）把该形态最近几条端出来。
    # ⚠️⚠️ 绝不能在有主题词却没命中时补齐：问「三国类图书」库里没有，
    #       补齐会返回《藏书馆分销方案》《政治学十五讲》—— 全是噪声，比空列表更糟。
    #       有主题但没命中 → 返回空，由上层走「库外推荐 + 标注未核实」。
    if want and not (words or grams) and not scored:
        for it in _pool(db, kinds_set):
            if it["form"] in want:
                scored.append((1, it))

    scored.sort(key=lambda x: (-x[0], -x[1]["id"]))
    out = []
    for sc, it in scored[:limit]:
        card = {k: v for k, v in it.items() if not k.startswith("_")}
        card["score"] = sc
        out.append(card)
    return out
