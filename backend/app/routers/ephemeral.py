"""「仅本次阅读」+「最近阅读」路由（P0-5）

设计（方案 §5.2 / §5.3）：

- **无状态 AI 接口**：正文从**请求体**取（不再从 DB 读 chunk），
  因此不写 `materials` / `material_chunks` / `ai_assets`，
  也不碰 Chroma（`vector.py` 会把明文存进 `documents` —— 向量化 = 落库）。
- **最近阅读 = 后端进程内存 LRU**（`services/ephemeral.py`），关闭应用即清空。
- ✅ 遵守项目约定**「新增能力优先加接口、不改接口」**：现有 `/ai/*` 全部不动，
  老前端零影响。

⚠️ 与剪藏（`/materials/clip/*`）的分工：
    `/materials/clip/save` = **落库**（进知识库、建索引）
    `/ai/ephemeral/*`      = **只读**（一个字不落）
    「临时阅读 → 转笔记」由前端串起来：先 `clip/save` 拿 material_id，再走既有转笔记契约。

⚠️ 正文只存内存，**绝不写日志**：本文件所有异常只回 reason/长度，不拼正文。
"""
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..services import ephemeral as ephemeral_svc
from ..services import external as external_svc
from ..services import llm
from ..services import parser as parser_svc

router = APIRouter(prefix="/ai/ephemeral", tags=["ephemeral"])

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}

# 与材料页一致的上下文口径（避免"同一个划线段落在两处给出不同解读质量"）
EXPLAIN_SELECT_MAX = 2000
EXPLAIN_CONTEXT_MAX = 4000
ASK_CONTEXT_MAX = 8000
ASK_QUESTION_MAX = 1000


# ---------- 请求模型 ----------

class EphemeralOpenReq(BaseModel):
    url: str
    # SPA 降级：用户粘贴正文 → 跳过抓取（与剪藏共用 external.fetch_or_passthrough）
    text: str | None = None
    title: str | None = None


class EphemeralRef(BaseModel):
    """临时内容定位。

    ⚠️ `key` 优先、`text` 兜底，两条都留是**刻意**的：
    key 指向内存快照（能直接复用与落库同源的 chunks）；
    但快照会被 LRU 淘汰或在应用重启后清空 —— 此时若只认 key，
    用户读到一半点"解读"就会 404。阅读页本来就持有正文，顺带带回即可无缝续用。
    """
    key: str | None = None
    text: str | None = None
    title: str | None = None


class EphemeralSummaryReq(EphemeralRef):
    instruction: str | None = None


class EphemeralExplainReq(EphemeralRef):
    selected_text: str
    anchor: dict = {}                 # {page_no}（块序号，1 起）
    question: str | None = None       # None=首次解读；有值=追问
    history: list[dict] = []          # 追问链由前端带（无状态，不查库）


class EphemeralAskReq(EphemeralRef):
    question: str


# ---------- 工具 ----------

def _sse_event(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _err_text(e: Exception) -> str:
    detail = getattr(e, "detail", None)
    return str(detail) if detail else str(e)


def _resolve(ref: EphemeralRef) -> tuple[list[dict], str, str]:
    """定位临时内容 → (chunks, title, url)。

    key 命中：直接用快照里的 chunks（与落库路径共用 `split_markdown`，块完全一致）。
    只用 text：现场 `split_markdown` —— **同一个函数**，所以两条路的结果同构。
    两者都无：404（诚实报错，然后前端重新打开该网页）。
    """
    if ref.key:
        rec = ephemeral_svc.get(ref.key)
        if rec:
            return rec["chunks"], rec["title"], rec["url"]
    body = (ref.text or "").strip()
    if body:
        return (parser_svc.split_markdown(body),
                (ref.title or "").strip() or "未命名网页",
                "")
    raise HTTPException(404, "临时内容已失效（应用重启或超过 20 篇会自动清理），请重新打开该网页")


def _clean_history(raw) -> list[dict]:
    """追问历史来自前端 → 只接受 user/assistant 两种角色，防注入任意 role。"""
    out = []
    for m in (raw or []):
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = m.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            out.append({"role": role, "content": content})
    return out


# ---------- ① 打开（写入内存快照） ----------

@router.post("/open")
def open_ephemeral(req: EphemeralOpenReq, db: Session = Depends(get_db)):
    """打开一个链接进入「仅本次阅读」：抓取（或粘贴）→ 存入内存快照 → 返回正文供渲染。

    **不落库**：不写 files/、不写 materials/chunks、不写 Chroma。
    抓取失败时**不写入快照**，把 `action`/`hint` 交给前端走"粘贴正文"降级通道。
    """
    url = (req.url or "").strip()
    if not url:
        raise HTTPException(400, "请输入网页链接")

    r = external_svc.fetch_or_passthrough(url, req.text, req.title)
    payload = external_svc.preview_payload(r)

    exist = external_svc.find_saved_material(db, payload["url"])
    payload["saved"] = exist is not None
    payload["material_id"] = exist.id if exist else None

    if not r.get("ok"):
        # 无正文可读 → 不占 LRU 位；前端据 action 决定展示"粘贴正文"还是"重取链接"
        payload.update({"key": None, "text": "", "chunks": [],
                        "chunk_count": 0, "truncated": False})
        return payload

    rec = ephemeral_svc.put(
        payload["url"] or url,
        payload["title"] or r.get("title") or "未命名网页",
        r.get("text") or "",
        kind=payload["kind"],
        meta=r.get("meta") or {},
        kind_label=payload["kind_label"],
        # ⚠️ 原始输入（可能是「标题 + 短链 + 说明」整段分享文案，也可能是带 xsec_token
        #    的完整链接）必须随快照存下 —— 详情页的「加入知识库」只有归一化 URL 可用，
        #    那对小红书是打不开的。见 services/ephemeral.py put() 的说明。
        input_url=payload.get("input_url") or url,
    )
    payload.update({
        "key": rec["key"],
        "text": rec["text"],
        "chunks": rec["chunks"],
        "chunk_count": rec["chunk_count"],
        "truncated": rec["truncated"],
        # 快照口径给前端（"本次使用期间"语义，UI 要照实写）
        "ephemeral": True,
    })
    return payload


# ---------- ② 摘要（SSE 流式，不落库） ----------

@router.post("/summary/stream")
def summary_stream(req: EphemeralSummaryReq):
    """临时内容摘要：SSE 逐 token；长文档两段式（分组并发 + 进度）。

    与 `/ai/summary/stream` 结构一致，唯一区别是 **chunks 从请求体/快照来，且不落库**。
    """
    chunks, _title, _url = _resolve(req)
    if not chunks:
        raise HTTPException(400, "正文为空，无法生成摘要")

    total = sum(len(c["content"]) for c in chunks)
    extra = f"\n补充要求：{req.instruction}" if req.instruction else ""
    sys_prompt = llm.get_prompt("prompt_summary")

    if total > llm.MAX_SINGLE_CHARS:
        groups = llm.split_groups(chunks)

        def gen_long():
            partials: list = [None] * len(groups)
            pool = ThreadPoolExecutor(max_workers=llm.SUMMARY_CONCURRENCY)
            try:
                yield _sse_event("progress", {"done": 0, "total": len(groups)})
                futures = {pool.submit(llm.map_group_safe, g, i, len(groups)): i
                           for i, g in enumerate(groups)}
                done = 0
                for fut in as_completed(futures):
                    partials[futures[fut]] = fut.result()
                    done += 1
                    yield _sse_event("progress", {"done": done, "total": len(groups)})
                content = llm.reduce_summary([p for p in partials if p], req.instruction)
            except Exception as e:
                yield _sse_event("error", {"message": _err_text(e)[:200]})
                return
            finally:
                pool.shutdown(wait=False, cancel_futures=True)
            yield _sse_event("token", {"t": content})
            # 不落库 → done 里没有 asset
            yield _sse_event("done", {"ephemeral": True})

        return StreamingResponse(gen_long(), media_type="text/event-stream", headers=SSE_HEADERS)

    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": f"以下是文档全文（含页码标记）：\n\n{llm.format_chunks(chunks)}{extra}"},
    ]

    def gen():
        full = ""
        try:
            for text in llm.summary_chat_stream(messages, kind="summary"):
                full += text
                yield _sse_event("token", {"t": text})
        except Exception as e:
            yield _sse_event("error", {"message": _err_text(e)[:200]})
            return
        yield _sse_event("done", {"ephemeral": True})

    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


# ---------- ③ 划线解读 / 追问（同步，不落库） ----------

@router.post("/explain")
def explain(req: EphemeralExplainReq):
    """临时内容的划线解读/追问。

    上下文取所选块 ±1 块（与材料页 `page_no ±1` 同口径）；
    `history` 由前端携带 → 服务端无状态，不查库、不落库。
    """
    chunks, _title, _url = _resolve(req)
    if not chunks:
        raise HTTPException(400, "正文为空，无法解读")

    selected = (req.selected_text or "").strip()
    if not selected:
        raise HTTPException(400, "请先选中要解读的文字")
    if len(selected) > EXPLAIN_SELECT_MAX:
        selected = selected[:EXPLAIN_SELECT_MAX]

    page = (req.anchor or {}).get("page_no", 1)
    try:
        page = max(1, int(page))
    except (TypeError, ValueError):
        page = 1
    near = [c["content"] for c in chunks if abs(int(c.get("page_no") or 0) - page) <= 1]
    context = "\n".join(near)[:EXPLAIN_CONTEXT_MAX]

    history = _clean_history(req.history)
    # 首次解读时 history 应为空；追问时前端会把整条链带回来
    question = (req.question or "").strip() or None
    content = llm.explain(selected, context, history if question else [], question)
    return {"content": content, "ephemeral": True}


# ---------- ④ 对临时内容提问（SSE 流式，不落库） ----------

@router.post("/ask/stream")
def ask_stream(req: EphemeralAskReq):
    """对临时内容直接提问（不依赖划线）。结果**不落库** —— 想留就走转笔记/入库。"""
    chunks, title, _url = _resolve(req)
    if not chunks:
        raise HTTPException(400, "正文为空，无法回答")

    q = (req.question or "").strip()
    if not q:
        raise HTTPException(400, "问题为空")
    if len(q) > ASK_QUESTION_MAX:
        q = q[:ASK_QUESTION_MAX]

    context = "\n".join(c["content"] for c in chunks)[:ASK_CONTEXT_MAX]
    messages = llm.ask_messages(q, context, title)

    def gen():
        full = ""
        try:
            for kind, text in llm.chat_stream(messages, kind="ask"):
                if kind == "content":
                    full += text
                    yield _sse_event("token", {"t": text})
        except Exception as e:
            yield _sse_event("error", {"message": _err_text(e)[:200]})
            return
        yield _sse_event("done", {"ephemeral": True})

    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


# ---------- ⑤ 最近阅读（内存快照的读/删） ----------

@router.get("/recent")
def list_recent(db: Session = Depends(get_db)):
    """最近阅读列表：**不含正文**，只给标题/来源/时间/字数 + 是否已沉淀。

    ⚠️ 语义必须诚实：这是**本次使用期间的快照**，关闭应用即清空，不是历史记录。
    """
    items = ephemeral_svc.list_recent()
    saved = external_svc.find_saved_map(db, [i["url"] for i in items])
    for it in items:
        mid = saved.get(it["url"])
        it["saved"] = mid is not None
        it["material_id"] = mid
    return {
        "items": items,
        "total": len(items),
        "note": "本次使用期间打开的网页，关闭应用后清空（不写磁盘、不进知识库）。",
    }


@router.get("/recent/{key}")
def get_recent(key: str, db: Session = Depends(get_db)):
    """取回单篇（继续阅读）：含正文与 chunks；命中同时刷新其 LRU 位置。"""
    rec = ephemeral_svc.get(key)
    if rec is None:
        raise HTTPException(404, "该临时内容已不在本次会话中（已被清理或应用已重启）")
    exist = external_svc.find_saved_material(db, rec["url"])
    return {
        "key": rec["key"],
        "url": rec["url"],
        "input_url": rec.get("input_url") or rec["url"],
        "title": rec["title"],
        "kind": rec["kind"],
        "kind_label": rec["kind_label"],
        "text": rec["text"],
        "chunks": rec["chunks"],
        "chars": rec["chars"],
        "chunk_count": rec["chunk_count"],
        "truncated": rec["truncated"],
        # 图片张数（与 preview_payload 同口径；正文以图片为主时字数少是正常的）
        "images": ephemeral_svc.images_of(rec),
        # 图片消息（公众号 item_show_type=8）：标注出来，避免"字少"被当成抓漏
        "wx_image_post": ephemeral_svc.wx_image_post_of(rec),
        # 视频笔记（小红书）：正文只有配文 + 封面，视频本体没抓 —— 同样要标出来，别让用户以为抓漏
        "xhs_video_note": ephemeral_svc.xhs_video_note_of(rec),
        "meta": rec["meta"],
        "created_at": rec["created_at"],
        "updated_at": rec["updated_at"],
        "saved": exist is not None,
        "material_id": exist.id if exist else None,
    }


@router.delete("/recent/{key}")
def delete_recent(key: str):
    """单篇移除（只弹内存字典，不涉及文件/DB/Chroma）。"""
    if not ephemeral_svc.remove(key):
        raise HTTPException(404, "该临时内容已不在本次会话中")
    return {"ok": True, "removed": key}
