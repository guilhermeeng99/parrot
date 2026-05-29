"""`/setup/*` — first-run model gate + download (first-run-setup.md)."""

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..services import setup_manager as svc
from .deps import require_loopback

router = APIRouter(prefix="/setup")


class DownloadRequest(BaseModel):
    repo_id: str


@router.get("/status")
def setup_status() -> dict:
    return svc.setup_status()


@router.post("/download")
def start_download(body: DownloadRequest) -> dict:
    return svc.start_download(body.repo_id)


@router.get("/download-stream")
async def download_stream(_: None = Depends(require_loopback)):
    return StreamingResponse(
        svc.download_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )
