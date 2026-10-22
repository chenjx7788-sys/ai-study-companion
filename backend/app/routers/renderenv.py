"""渲染组件（Chromium）路由：状态查询 / 按需下载 / 下载进度（AI 视频出片用）。

与 asr.py / zipvoice.py 同构 —— 前端复用同一套「下载按钮 + 轮询」交互。
"""
from fastapi import APIRouter, HTTPException

from ..services import renderenv as renderenv_svc

router = APIRouter(prefix="/renderenv", tags=["renderenv"])


@router.get("/status")
def status():
    return renderenv_svc.get_status()


@router.post("/download")
def start_download():
    r = renderenv_svc.start_download()
    if r == "busy":
        raise HTTPException(409, "渲染组件正在下载中，请稍候")
    return {"ok": True, "already": r == "installed"}


@router.get("/download/status")
def download_status():
    return renderenv_svc.download_status()
