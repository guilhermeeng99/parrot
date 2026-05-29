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
from . import generation_progress, model_manager
from .errors import ServiceError

log = logging.getLogger(__name__)

_gpu_pool: ThreadPoolExecutor | None = None

# Serializes a whole begin()→infer→finish() section so only ONE generation drives
# the progress bus / registers the per-call forward hook at a time. The progress
# bus and the shared singleton model's forward-hook are single-generation BY
# DESIGN, yet the WS conversational loop and up-to-4 GPU workers can otherwise
# overlap calls on the one bus + one model — interleaving begin/report/finish and
# double-registering the hook. NOT performance-critical: Parrot is single-user
# clone-and-speak, so back-to-back generations queuing here is the intended
# behavior, not a bottleneck. Lazily created (like model_manager._get_lock) so it
# binds to the running loop, not import time.
_run_lock: asyncio.Lock | None = None


def _get_run_lock() -> asyncio.Lock:
    global _run_lock
    if _run_lock is None:
        _run_lock = asyncio.Lock()
    return _run_lock


_OOM_MESSAGE = (
    "TTS engine stopped mid-generation. This usually means it ran out of memory. "
    "Try the Flush button to reload the model, then regenerate. Underlying error: {e}"
)
_GENERIC_MESSAGE = (
    "Couldn't synthesize audio. See Settings → Engine → View backend log for the "
    "full trace. Underlying error: {e}"
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
    num_step = int(params.get("num_step", 16) or 16)

    # Real %-complete progress: the engine calls `progress_cb` once per diffusion
    # step (omnivoice has no native hook — tts counts the model's forward passes),
    # and generation_progress fans that out over SSE for the Speak UI's bar.
    def _on_step(done: int, total: int) -> None:
        generation_progress.report(done, total)  # publishes thread-safely

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
            num_step=num_step,
            guidance_scale=params.get("guidance_scale", 2.0),
            denoise=params.get("denoise", True),
            postprocess_output=params.get("postprocess_output", True),
            t_shift=params.get("t_shift"),
            layer_penalty_factor=params.get("layer_penalty_factor"),
            position_temperature=params.get("position_temperature"),
            class_temperature=params.get("class_temperature"),
            progress_cb=_on_step,
        )
        return samples, time.perf_counter() - started

    # Serialize the whole progress-bus + forward-hook section: begin() before the
    # executor so a subscriber that connects first sees the start phase, and only
    # one generation may own the single bus/hook at a time (see _run_lock).
    async with _get_run_lock():
        generation_progress.begin(num_step)
        try:
            samples, gen_time = await loop.run_in_executor(gpu_pool(), _infer)
        except ServiceError:
            generation_progress.fail()
            raise
        except Exception as e:
            generation_progress.fail()
            if _looks_like_oom(e):
                log.warning("Synthesis OOM; flushing model: %s", e)
                model_manager.flush()
                raise ServiceError(500, _OOM_MESSAGE.format(e=e))
            log.exception("Synthesis failed")
            raise ServiceError(500, _GENERIC_MESSAGE.format(e=e))
        generation_progress.finish()

    return {
        "samples": samples,
        "sample_rate": model_manager.sample_rate(),
        "seed": seed,
        "generation_time": round(gen_time, 2),
    }
