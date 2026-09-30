"""浏览器扩展专用路由（MVP · 2026-09-23）

定位：知萤主应用的「遥控器」通道 —— Chrome/Edge 扩展把网页上选中的
内容发过来，主应用调 AI 处理后返回；扩展本身不含 AI 能力。

设计约束（见《AI伴学助手_浏览器扩展技术方案-20260923.md》）：
- **HTTP + SSE，不上 WebSocket**：一问一答场景用不上长连接，且 HTTPS 页面
  直连 ws://127.0.0.1 会被混合内容策略拦截（扩展从 background SW 发 fetch）。
- **独立 /api/ext 前缀 + X-BrainMate-Token**：现有 /api/* 一律不动
  （项目约定「新增能力优先加接口、不改接口」，主应用前端零影响）。
- AI 口径与「仅本次阅读」（routers/ephemeral.py）**完全一致**：正文从请求体取，
  不落库、不写向量；摘要的 SSE 事件结构也照搬，扩展侧解析逻辑与主应用前端同构。
- 保存走 `/materials/clip/save` **同一个函数**（幂等查重 / safe_stem / 异步索引），
  不新造入库路径。

⚠️ 与 ephemeral.py 的代码重叠（摘要生成器）是**刻意**的：那边契约是「主应用内
临时阅读」，这边是「外部调用方」，各自演进时互不拖累。
"""
import json
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app_version import __version__ as APP_VERSION
from ..database import get_db
from ..services import ext_token
from ..services import llm
from ..services import parser as parser_svc

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}

# 与 ephemeral.py 同口径的长度上限（避免「同一句话在扩展和主应用里解释质量不同」）
EXPLAIN_SELECT_MAX = 2000
EXPLAIN_CONTEXT_MAX = 4000


# ---------- 鉴权 ----------

def require_token(x_brainmate_token: str | None = Header(default=None)):
    """FastAPI 依赖：校验 X-BrainMate-Token，不符一律 401（不区分缺失/错误，不泄露信息）。"""
    if not ext_token.verify(x_brainmate_token):
        raise HTTPException(401, "invalid token")


# 受保护的扩展端点（全部要 token）
router = APIRouter(prefix="/ext", tags=["ext"], dependencies=[Depends(require_token)])
# 免 token 的配对信息端点（仅主应用设置页使用，见 /token 的防护说明）
pairing_router = APIRouter(prefix="/ext", tags=["ext-pairing"])


# ---------- 请求模型 ----------

class ExtExplainReq(BaseModel):
    selected_text: str                # 用户选中的文字（必填）
    context_text: str | None = None   # 选区前后段落（可选，提升解释质量）
    page_title: str | None = None
    page_url: str | None = None       # 仅随响应回显，便于「保存结果」时溯源


class ExtSummaryReq(BaseModel):
    text: str                         # 选中段落或整页正文（必填）
    title: str | None = None
    instruction: str | None = None


class ExtSaveReq(BaseModel):
    url: str                          # 页面 URL（幂等键）
    text: str | None = None           # 正文（扩展端 Readability 抽取后直传，跳过抓取）
    title: str | None = None


# ---------- 工具 ----------

def _sse_event(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _err_text(e: Exception) -> str:
    detail = getattr(e, "detail", None)
    return str(detail) if detail else str(e)


# ---------- ① 探测（兼验 token） ----------

@router.get("/ping")
def ping():
    """扩展唤起功能前的第一步：主应用活着吗 + token 配对了吗（401 即未配对）。"""
    return {"ok": True, "version": APP_VERSION, "name": "知萤"}


# ---------- ② 解释（同步，不落库） ----------

@router.post("/explain")
def explain(req: ExtExplainReq):
    """划词通俗解释。口径 = ephemeral.explain 的无追问分支（llm.explain 同一调用）。"""
    selected = (req.selected_text or "").strip()
    if not selected:
        raise HTTPException(400, "请先选中要解释的文字")
    if len(selected) > EXPLAIN_SELECT_MAX:
        selected = selected[:EXPLAIN_SELECT_MAX]
    context = (req.context_text or "")[:EXPLAIN_CONTEXT_MAX]

    content = llm.explain(selected, context, [], None)
    return {"content": content, "ephemeral": True,
            "page_url": req.page_url or "", "page_title": req.page_title or ""}


# ---------- ③ 总结（SSE 流式，不落库） ----------

@router.post("/summary/stream")
def summary_stream(req: ExtSummaryReq):
    """与 /ai/ephemeral/summary/stream 结构一致：chunks 从请求体来，不落库。"""
    body = (req.text or "").strip()
    if not body:
        raise HTTPException(400, "正文为空，无法生成总结")
    chunks = parser_svc.split_markdown(body)
    if not chunks:
        raise HTTPException(400, "正文为空，无法生成总结")

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
            yield _sse_event("done", {"ephemeral": True})

        return StreamingResponse(gen_long(), media_type="text/event-stream", headers=SSE_HEADERS)

    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": f"以下是文档全文（含页码标记）：\n\n{llm.format_chunks(chunks)}{extra}"},
    ]

    def gen():
        try:
            for text in llm.summary_chat_stream(messages, kind="summary"):
                yield _sse_event("token", {"t": text})
        except Exception as e:
            yield _sse_event("error", {"message": _err_text(e)[:200]})
            return
        yield _sse_event("done", {"ephemeral": True})

    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


# ---------- ④ 保存到知识库（复用 clip/save 同一函数） ----------

@router.post("/save")
def save(req: ExtSaveReq, db: Session = Depends(get_db)):
    """薄包装 /materials/clip/save：幂等查重、safe_stem 落盘、异步解析建索引全部继承。

    duplicated=true → 扩展提示「该页面已在知识库中」，不重复入库。
    正文过短/过长的 400 文案与主应用剪藏完全一致，扩展直接展示 detail 即可。
    """
    from .materials import ClipSaveReq, clip_save
    return clip_save(ClipSaveReq(url=req.url, text=req.text, title=req.title), db)


# ---------- ⑤ 配对信息（免 token，但有来源防护） ----------

@pairing_router.get("/token")
def reveal_token(request: Request):
    """返回配对 token，仅供主应用设置页展示（用户复制到扩展设置里完成配对）。

    ⚠️ 为什么不能完全敞开：当前 CORS 是 `*`，若不加任何防护，任意网页都能
    fetch 走 token，配对机制形同虚设。浏览器跨站 fetch 必带
    `Sec-Fetch-Site: cross-site`，据此拒绝；主应用前端（同源/同站）与
    pywebview（常无此头）不受影响。根治仍待 CORS/Origin 加固（方案 §8）。
    """
    sfs = (request.headers.get("sec-fetch-site") or "").strip().lower()
    if sfs == "cross-site":
        raise HTTPException(403, "forbidden")
    return {"token": ext_token.get_or_create()}


@pairing_router.post("/token/rotate")
def rotate_token(request: Request):
    """重新生成配对 token（旧 token 立即失效，已配对的扩展须重贴新值）。

    免 token、同样拒跨站网页（与 /token 同一防护）——否则任意站点都能把用户的
    扩展踢下线（DoS）。仅主应用设置页的「重新生成」按钮调用，由用户主动轮换凭证。
    """
    sfs = (request.headers.get("sec-fetch-site") or "").strip().lower()
    if sfs == "cross-site":
        raise HTTPException(403, "forbidden")
    return {"token": ext_token.rotate()}


# ---------- ⑥ 扩展下载（免 token，同源防护同上） ----------

# 扩展源码定位：打包版在 _MEIPASS，源码态在仓库根。
# ⚠️ 本文件比 main.py 深一层（routers/ 里）→ 是**四个** parent：routers→app→backend→仓库根。
if getattr(sys, "frozen", False):
    EXT_DIR = Path(sys._MEIPASS) / "extension"
else:
    EXT_DIR = Path(__file__).resolve().parent.parent.parent.parent / "extension"

# 打包 zip 时跳过的项（开发残留/系统垃圾）
_EXT_SKIP = {"__pycache__", ".DS_Store", "Thumbs.db"}


def _guard_same_site(request: Request):
    """与 /token 同一防护：跨站网页 fetch 必带 Sec-Fetch-Site: cross-site → 拒。"""
    sfs = (request.headers.get("sec-fetch-site") or "").strip().lower()
    if sfs == "cross-site":
        raise HTTPException(403, "forbidden")


@pairing_router.get("/download")
def download_extension(request: Request):
    """现场把 extension/ 打成 zip 下载（不维护预构建产物 → 永远和源码同步）。

    免 token：下载的只是扩展源码本身（仓库里公开的东西），不涉及任何用户数据；
    但同样拒跨站网页（与 /token 一致，防恶意站点给用户塞来路不明的「同名扩展」）。
    """
    _guard_same_site(request)
    if not EXT_DIR.is_dir():
        raise HTTPException(404, "扩展源码目录不存在（打包不完整）")

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(EXT_DIR.rglob("*")):
            if not p.is_file() or any(part in _EXT_SKIP for part in p.parts):
                continue
            # zip 内包一层 ZhiYing-Extension/ 目录：用户解压后即得可直接加载的文件夹
            zf.write(p, "ZhiYing-Extension/" + p.relative_to(EXT_DIR).as_posix())
    buf.seek(0)
    return StreamingResponse(
        buf, media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="ZhiYing-Extension.zip"'},
    )
