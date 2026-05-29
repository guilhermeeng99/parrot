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

from ..services import audio_io
from ..services import generate as generate_service
from ..services.errors import ServiceError

router = APIRouter()

# Samples per binary frame (~200 ms @ 24 kHz). Overridable for tuning.
_CHUNK_SAMPLES = int(os.environ.get("PARROT_STREAM_CHUNK", "4800"))


@router.websocket("/ws/tts")
async def ws_tts(ws: WebSocket) -> None:
    await ws.accept()
    try:
        while True:
            req = await ws.receive_json()
            text = (req.get("text") or "").strip()
            if not text:
                await ws.send_json({"type": "error", "detail": "Missing 'text' field in request"})
                continue
            try:
                result = await generate_service.generate_pcm(req)
            except ServiceError as e:
                await ws.send_json({"type": "error", "detail": e.detail})
                continue

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
    except WebSocketDisconnect:
        return  # client gone mid-stream — benign; nothing to return.
