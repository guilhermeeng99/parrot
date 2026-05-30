"""`/transcribe/*` — reference ASR: model catalog/status, download, transcribe.

Thin router over `services/transcribe.py` (transcription.md §4). Mirrors the
setup gate's shape — a JSON status, a JSON-body download trigger, an SSE progress
stream — plus the multipart transcribe call that fills `ref_text` for cloning.
"""

import anyio
from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..services import transcribe as svc
from .deps import require_loopback

router = APIRouter(prefix="/transcribe")


class DownloadRequest(BaseModel):
    model: str


@router.get("/status")
def transcribe_status() -> dict:
    return svc.status()


@router.post("/download")
def start_download(body: DownloadRequest) -> dict:
    return svc.start_download(body.model)


@router.get("/download-stream")
async def download_stream(_: None = Depends(require_loopback)):
    return StreamingResponse(
        svc.download_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@router.post("")
async def transcribe_audio(
    ref_audio: UploadFile = File(...),
    model: str = Form(...),
    language: str = Form("Auto"),
) -> dict:
    # Offload the BLOCKING transcription (model load + Whisper inference, seconds
    # on CPU) to a worker thread so it never stalls the event loop / `/healthz`.
    audio_bytes = await ref_audio.read()
    return await anyio.to_thread.run_sync(
        svc.transcribe, audio_bytes, ref_audio.filename, model, language
    )
