"""库内内容发现接口（2026-10-26）。

问答里的资源卡片走 `services.discover` 直调；这个独立接口的价值是：
① 单独验证检索质量（不用每次跑一遍 LLM）；
② 将来「找内容」页/搜索框可直接复用。

不写索引、不动 kb 契约 —— 纯只读。
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Material
from ..services import brief as brief_svc
from ..services import discover as discover_svc
from .materials import DocumentCreateReq, create_document, material_to_dict

router = APIRouter(prefix="/resources", tags=["resources"])


@router.get("/search")
def search_resources(
    q: str = Query(..., min_length=1, description="自然语言问法，如「我想读三国类图书」"),
    limit: int = 6,
    kinds: str | None = None,
    db: Session = Depends(get_db),
):
    """跨「材料 / 笔记 / AI 视频 / AI 讲书」找内容，返回可点击的资源卡片数据。"""
    ks = [k.strip() for k in kinds.split(",") if k.strip()] if kinds else None
    items = discover_svc.search(db, q, limit=max(1, min(int(limit), 20)), kinds=ks)
    return {
        "query": q,
        "subject": discover_svc.extract_subject(q),
        "items": items,
    }


class BriefReq(BaseModel):
    query: str = Field(..., min_length=2, description="用户原话，如「我想读三国类图书」")


@router.post("/brief")
def create_topic_brief(req: BriefReq, db: Session = Depends(get_db)):
    """库外推荐 → 一份可学的入门导读（2026-10-26）。

    补的是「推荐说完就结束」：库里没有相关内容时 AI 只能给书名（还得标注「建议核实」），
    用户手上没书这条路就断了。现在可以把主题直接变成材料库里的导读，
    随后能读、能出讲义、能出题 —— 推荐因此有了下一步。

    幂等键 `brief:<主题>`：同一主题重复点只返回已有那份，**不重复调模型**（省钱也省等）。
    """
    q = (req.query or "").strip()
    subject = discover_svc.extract_subject(q)
    key = (subject or q).strip()[:60]
    ref = "brief:" + key
    exist = db.query(Material).filter(Material.origin_ref == ref).first()
    if exist is not None:
        d = material_to_dict(exist, db)
        d["duplicated"] = True
        return d
    try:
        md = brief_svc.generate(subject, q)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, "生成导读失败：%s" % str(e)[:200])
    return create_document(DocumentCreateReq(
        title=brief_svc.subject_title(subject, q),
        content=md,
        origin="brief",
        origin_ref=ref,
        origin_meta={"topic": subject or q, "query": q, "generated": True},
    ), db)
