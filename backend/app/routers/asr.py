"""语音模型路由：状态查询 / 手动下载 / 下载进度（音视频转写 Whisper 模型）"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..services import asr as asr_svc

router = APIRouter(prefix="/asr", tags=["asr"])


class DownloadReq(BaseModel):
    size: str


@router.get("/models")
def models_status():
    return asr_svc.get_models_status()


@router.post("/download")
def start_download(req: DownloadReq):
    if req.size not in asr_svc.MODELS:
        raise HTTPException(400, "未知的模型版本")
    if not asr_svc.start_download(req.size):
        raise HTTPException(409, "已有模型在下载中，请稍候")
    return {"ok": True}


@router.get("/download/status")
def download_status():
    return asr_svc.download_status()
