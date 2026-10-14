"""语音合成模型路由：状态查询 / 手动下载 / 下载进度（本地 ZipVoice 合成模型）

与 routers/asr.py（Whisper 转写模型）同构 —— 前端复用同一套「下载卡片 + 轮询」交互。
"""
from fastapi import APIRouter, HTTPException

from ..services import zipvoice as zv_svc

router = APIRouter(prefix="/zipvoice", tags=["zipvoice"])


@router.get("/models")
def models_status():
    return zv_svc.get_models_status()


@router.post("/download")
def start_download():
    if not zv_svc.start_download():
        raise HTTPException(409, "模型正在下载中，请稍候")
    return {"ok": True}


@router.get("/download/status")
def download_status():
    return zv_svc.download_status()


@router.get("/voices")
def voices():
    """ZipVoice 可用音色（未下载模型时为空表，前端据此给出引导）。"""
    return {"installed": zv_svc.is_installed(), "voices": zv_svc.list_voices()}
