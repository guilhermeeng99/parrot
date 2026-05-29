"""`WS /ws/tts` — optional chunked-PCM streaming synthesis (synthesis.md §WS).

Synthesis channel only (not an event bus). The socket stays open for successive
requests (conversational mode). Each request: `start` JSON → N binary PCM16 mono
frames → `done` JSON. A request missing `text` gets an inline `error` frame and
the socket stays open. WS does NOT write a history row and applies only broadcast
mastering + -2 dBFS normalization (no effect presets).
"""

import os

import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, ValidationError

from ..core.logging import redact
from ..services import audio_io
from ..services import generate as generate_service
from ..services.errors import ServiceError

router = APIRouter()

# Samples per binary frame (~200 ms @ 24 kHz). Overridable for tuning.
_CHUNK_SAMPLES = int(os.environ.get("PARROT_STREAM_CHUNK", "4800"))


class WsTtsRequest(BaseModel):
    """The per-message synthesis request (synthesis.md §WS). Validated before we
    touch the model so a malformed frame is a recoverable inline `error`, never a
    socket-closing exception. Extra keys (e.g. profile_id, ref_text) pass through
    to generate_pcm, which already knows how to read them."""

    model_config = ConfigDict(extra="allow")

    text: str
    speed: float = 1.0
    num_step: int = 16
    guidance_scale: float = 2.0
    language: str | None = None
    instruct: str | None = None
    voice: str | None = None
    seed: int | None = None


@router.websocket("/ws/tts")
async def ws_tts(ws: WebSocket) -> None:
    await ws.accept()
    while True:
        try:
            req = await ws.receive_json()
            await _handle_request(ws, req)
        except WebSocketDisconnect:
            return  # client gone mid-stream — benign; only a disconnect breaks the loop.
        except ServiceError as e:
            await ws.send_json({"type": "error", "detail": e.detail})
        except Exception as e:
            # Any other per-request failure (validation, model, encode) is inline +
            # REDACTED so a secret never lands in an error frame, and the socket
            # stays open for the next request (conversational mode).
            await ws.send_json({"type": "error", "detail": redact(str(e))})


async def _handle_request(ws: WebSocket, req: dict) -> None:
    try:
        parsed = WsTtsRequest.model_validate(req)
    except ValidationError as e:
        first = e.errors()[0] if e.errors() else {}
        loc = ".".join(str(p) for p in first.get("loc", []))
        msg = first.get("msg", "Invalid request.")
        await ws.send_json({"type": "error", "detail": redact(f"{loc}: {msg}" if loc else msg)})
        return

    if not parsed.text.strip():
        await ws.send_json({"type": "error", "detail": "Missing 'text' field in request"})
        return

    result = await generate_service.generate_pcm(parsed.model_dump())

    sr = result["sample_rate"]
    samples = np.asarray(result["samples"], dtype=np.float32).reshape(-1)
    await ws.send_json(
        {"type": "start", "sample_rate": sr, "channels": 1, "format": "pcm16", "engine": "omnivoice"}
    )
    for start in range(0, samples.shape[0], _CHUNK_SAMPLES):
        frame = samples[start : start + _CHUNK_SAMPLES]
        await ws.send_bytes(audio_io.to_pcm16_bytes(frame))
    await ws.send_json(
        {
            "type": "done",
            "duration_s": result["duration_seconds"],
            "gen_time_s": result["generation_time"],
            "samples": int(samples.shape[0]),
            "sample_rate": sr,
            "engine": "omnivoice",
        }
    )
