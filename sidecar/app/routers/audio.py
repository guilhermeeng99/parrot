"""`/audio/*` — stateless audio utilities (no DB, no model).

Exists so the Speak screen can export a FRESH generated result as MP3 directly
from the WAV bytes it still holds in memory, without depending on a history row
(which the user may have already cleared). History-list exports use the by-id
`GET /history/{id}/audio.mp3` instead.
"""

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ..services import audio_io
from ..services.errors import ServiceError

router = APIRouter(prefix="/audio")


@router.post("/mp3")
async def transcode_to_mp3(request: Request):
    """Body: raw WAV bytes. Returns the same audio re-encoded as MP3."""
    data = await request.body()
    if not data:
        raise ServiceError(400, "No audio provided.")
    try:
        mp3 = audio_io.wav_bytes_to_mp3_bytes(data)
    except ValueError as e:
        raise ServiceError(400, f"Couldn't read that audio. ({e})")
    return Response(content=mp3, media_type="audio/mpeg")
