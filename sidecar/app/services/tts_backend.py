"""Low-level synthesis runner — model off the event loop (synthesis.md BR-2).

`run()` resolves the model via `model_manager.get_model()` (loading lazily on
first use), runs inference on the GPU thread-pool executor so the FastAPI event
loop stays responsive during a long synthesis, then returns the raw mono float32
waveform plus timing/seed metadata. DSP, file write, and the history row are the
caller's job (`services/generate.py`).

OOM mid-generation is mapped to a recoverable, user-facing 500 and the model is
flushed so the next attempt reloads (synthesis.md Rule 10 / Edge Cases).
"""

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor

from ..core import device
from . import model_manager
from .errors import ServiceError

log = logging.getLogger(__name__)

_gpu_pool: ThreadPoolExecutor | None = None

_OOM_MESSAGE = (
    "TTS engine stopped mid-generation. This usually means it ran out of memory. "
    "Try the Flush button to reload the model, then regenerate. Underlying error: {e}"
)
_GENERIC_MESSAGE = (
    "Couldn't synthesize audio. See Settings → Logs → Backend for the full trace. "
    "Underlying error: {e}"
)


def gpu_pool() -> ThreadPoolExecutor:
    """The shared GPU executor, sized once from the detected device."""
    global _gpu_pool
    if _gpu_pool is None:
        _gpu_pool = ThreadPoolExecutor(
            max_workers=device.gpu_workers(), thread_name_prefix="parrot-gpu"
        )
    return _gpu_pool


# Genuine out-of-memory markers only. A bare "cuda error" (illegal access,
# device assert, driver mismatch, …) is NOT OOM and must route to the generic
# 500 — flushing + telling the user "out of memory" there is misleading.
_OOM_MARKERS = ("out of memory", "cuda_error_out_of_memory")


def _looks_like_oom(e: Exception) -> bool:
    try:
        import torch

        if isinstance(e, torch.cuda.OutOfMemoryError):  # type: ignore[attr-defined]
            return True
    except Exception:
        pass  # torch absent or no such attribute — fall back to message markers
    msg = str(e).lower()
    return any(marker in msg for marker in _OOM_MARKERS)


async def run(params: dict) -> dict:
    """Synthesize `params['text']` and return
    `{samples, sample_rate, seed, generation_time}`. Raises ServiceError on
    inference failure (OOM → recoverable message)."""
    model = await model_manager.get_model()
    loop = asyncio.get_running_loop()
    seed = params.get("seed")

    def _infer():
        started = time.perf_counter()
        samples = model.synthesize(
            params["text"],
            ref_audio_path=params.get("ref_audio_path"),
            ref_text=params.get("ref_text"),
            instruct=params.get("instruct"),
            language=params.get("language"),
            seed=seed,
            speed=params.get("speed", 1.0),
            duration=params.get("duration"),
            num_step=params.get("num_step", 16),
            guidance_scale=params.get("guidance_scale", 2.0),
            denoise=params.get("denoise", True),
            postprocess_output=params.get("postprocess_output", True),
            t_shift=params.get("t_shift"),
            layer_penalty_factor=params.get("layer_penalty_factor"),
            position_temperature=params.get("position_temperature"),
            class_temperature=params.get("class_temperature"),
        )
        return samples, time.perf_counter() - started

    try:
        samples, gen_time = await loop.run_in_executor(gpu_pool(), _infer)
    except ServiceError:
        raise
    except Exception as e:
        if _looks_like_oom(e):
            log.warning("Synthesis OOM; flushing model: %s", e)
            model_manager.flush()
            raise ServiceError(500, _OOM_MESSAGE.format(e=e))
        log.exception("Synthesis failed")
        raise ServiceError(500, _GENERIC_MESSAGE.format(e=e))

    return {
        "samples": samples,
        "sample_rate": model_manager.sample_rate(),
        "seed": seed,
        "generation_time": round(gen_time, 2),
    }
