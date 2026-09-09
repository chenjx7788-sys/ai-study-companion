"""文件夹路由（材料库扩展）：列表 / 创建 / 重命名 / 移动 / 级联删除"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Folder, Material
from .materials import _delete_material_impl

router = APIRouter(prefix="/folders", tags=["folders"])


def _folder_to_dict(f: Folder, db: Session) -> dict:
    file_count = db.query(Material).filter(Material.folder_id == f.id).count()
    child_count = db.query(Folder).filter(Folder.parent_id == f.id).count()
    return {"id": f.id, "name": f.name, "parent_id": f.parent_id,
            "file_count": file_count, "child_count": child_count,
            "created_at": f.created_at.isoformat() if f.created_at else None}


@router.get("")
def list_folders(db: Session = Depends(get_db)):
    """平铺列表（含 parent_id，前端组树）"""
    rows = db.query(Folder).order_by(Folder.name).all()
    return [_folder_to_dict(f, db) for f in rows]


class FolderCreateReq(BaseModel):
    name: str
    parent_id: int | None = None


@router.post("")
def create_folder(req: FolderCreateReq, db: Session = Depends(get_db)):
    name = (req.name or "").strip()
    if not name:
        raise HTTPException(400, "文件夹名称不能为空")
    if len(name) > 100:
        raise HTTPException(400, "文件夹名称过长")
    if req.parent_id is not None:
        if not db.get(Folder, req.parent_id):
            raise HTTPException(400, "上级文件夹不存在")
    f = Folder(name=name, parent_id=req.parent_id)
    db.add(f)
    db.commit()
    db.refresh(f)
    return _folder_to_dict(f, db)


class FolderPatchReq(BaseModel):
    name: str | None = None
    parent_id: int | None = None   # 移动；null=移到顶层


def _is_descendant(db, folder_id: int, target_id: int) -> bool:
    """target 是否为 folder 的子孙（沿 parent 链回溯，带防环保护）"""
    cur, seen = target_id, set()
    while cur is not None:
        if cur == folder_id:
            return True
        if cur in seen:
            break
        seen.add(cur)
        p = db.get(Folder, cur)
        cur = p.parent_id if p else None
    return False


@router.patch("/{folder_id}")
def patch_folder(folder_id: int, req: FolderPatchReq, db: Session = Depends(get_db)):
    """重命名（name）/ 移动（parent_id）"""
    f = db.get(Folder, folder_id)
    if not f:
        raise HTTPException(404, "文件夹不存在")
    if req.name is not None:
        name = req.name.strip()
        if not name:
            raise HTTPException(400, "文件夹名称不能为空")
        f.name = name[:100]
    # 用 model_fields_set 区分「未传 parent_id」与「parent_id=null（移到顶层）」
    if "parent_id" in req.model_fields_set:
        if req.parent_id is None:
            f.parent_id = None   # 移到顶层
        else:
            if req.parent_id == folder_id or _is_descendant(db, folder_id, req.parent_id):
                raise HTTPException(400, "不能移动到自身或其子文件夹内")
            if not db.get(Folder, req.parent_id):
                raise HTTPException(400, "目标文件夹不存在")
            f.parent_id = req.parent_id
    db.commit()
    return _folder_to_dict(f, db)


def _collect_folder_ids(db, folder_id: int) -> list[int]:
    """收集 folder 及其全部子孙文件夹 id（BFS）"""
    ids, queue = [folder_id], [folder_id]
    while queue:
        cur = queue.pop(0)
        children = [f.id for f in db.query(Folder).filter(Folder.parent_id == cur).all()]
        ids += children
        queue += children
    return ids


@router.delete("/{folder_id}")
def delete_folder(folder_id: int, db: Session = Depends(get_db)):
    """删除文件夹：连带删其下文件 + 递归删子文件夹。

    安全红线：引用模式（reference）源文件只解绑、绝不物理删除。
    """
    f = db.get(Folder, folder_id)
    if not f:
        raise HTTPException(404, "文件夹不存在")
    ids = _collect_folder_ids(db, folder_id)
    materials = db.query(Material).filter(Material.folder_id.in_(ids)).all()
    for m in materials:
        try:
            _delete_material_impl(db, m)
        except Exception:
            db.rollback()   # 单个材料删除失败不阻断整体（失败材料残留为孤儿，不致命）
    # SQLite 默认不强制外键，直接批量删所有相关文件夹记录即可
    db.query(Folder).filter(Folder.id.in_(ids)).delete(synchronize_session=False)
    db.commit()
    return {"ok": True, "deleted_files": len(materials), "deleted_folders": len(ids)}
