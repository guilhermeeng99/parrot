"""`POST /generate` — synthesize typed text and stream WAV back (synthesis.md).

Thin router: parse the multipart form, persist an inline reference clip to a temp
file (only when no `profile_id` — `profile_id` wins), delegate to the generate
service, then stream the encoded WAV in 16 KiB chunks with the `X-*` metadata
headers. The temp reference file is always cleaned up in `finally`.
"""

import asyncio
import os
import shutil
import tempfile
from typing import AsyncIterator

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import StreamingResponse

from ..services import generate as generate_service

router = APIRouter()

_CHUNK = 16384


def _spool_upload(src, dst_path: str) -> None:
    """Stream the upload's spooled file to `dst_path` (blocking; runs off-loop)."""
    src.seek(0)
    with open(dst_path, "wb") as out:
        shutil.copyfileobj(src, out, _CHUNK)


def _file_chunks(path) -> AsyncIterator[bytes]:
    async def gen():
        with open(path, "rb") as f:
            while True:
                data = f.read(_CHUNK)
                if not data:
                    break
                yield data

    return gen()


@router.post("/generate")
async def generate(
    text: str = Form(...),
    language: str | None = Form(None),
    ref_audio: UploadFile | None = File(None),
    ref_text: str | None = Form(None),
    instruct: str | None = Form(None),
    duration: float | None = Form(None),
    num_step: int = Form(16),
    guidance_scale: float = Form(2.0),
    speed: float = Form(1.0),
    denoise: bool = Form(True),
    postprocess_output: bool = Form(True),
    profile_id: str | None = Form(None),
    seed: int | None = Form(None),
    effect_preset: str = Form("broadcast"),
    t_shift: float | None = Form(None),
    layer_penalty_factor: float | None = Form(None),
    position_temperature: float | None = Form(None),
    class_temperature: float | None = Form(None),
):
    tmp_path: str | None = None
    # Inline reference is honored only when no profile is chosen (profile wins).
    if ref_audio is not None and not profile_id:
        suffix = os.path.splitext(ref_audio.filename or "")[1] or ".wav"
        fd, tmp_path = tempfile.mkstemp(suffix=suffix, prefix="parrot_ref_")
        os.close(fd)  # the executor reopens by path; avoid juggling the fd off-loop
        # The upload can be multi-MB — copy it to disk on a thread so the read+write
        # never blocks the event loop (UploadFile.file is the spooled temp file).
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _spool_upload, ref_audio.file, tmp_path)

    params = {
        "text": text,
        "language": language,
        "ref_audio_path": tmp_path,
        "ref_text": ref_text,
        "instruct": instruct,
        "duration": duration,
        "num_step": num_step,
        "guidance_scale": guidance_scale,
        "speed": speed,
        "denoise": denoise,
        "postprocess_output": postprocess_output,
        "profile_id": profile_id,
        "seed": seed,
        "effect_preset": effect_preset,
        "t_shift": t_shift,
        "layer_penalty_factor": layer_penalty_factor,
        "position_temperature": position_temperature,
        "class_temperature": class_temperature,
    }

    try:
        result = await generate_service.generate(params)
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass  # inline-ref temp cleanup is best-effort

    headers = {
        "X-Audio-Id": result["id"],
        "X-Gen-Time": str(result["generation_time"]),
        "X-Audio-Path": result["audio_path"],
        "X-Seed": "" if result["seed"] is None else str(result["seed"]),
        "X-Audio-Duration": str(result["duration_seconds"]),
        "Content-Length": str(os.path.getsize(result["path"])),
        # Let the WebView read the X-* headers off the streamed response.
        "Access-Control-Expose-Headers": (
            "X-Audio-Id, X-Gen-Time, X-Audio-Path, X-Seed, X-Audio-Duration, Content-Length"
        ),
    }
    return StreamingResponse(_file_chunks(result["path"]), media_type="audio/wav", headers=headers)
