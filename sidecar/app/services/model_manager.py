"""The single entry point for model access (`get_model()`), per CLAUDE.md.

The model is loaded lazily on first use under an async lock (so concurrent
`/generate` requests don't double-load) and cached for the process lifetime. The
heavy import (`torch` + the vendored Apache-2.0 `omnivoice` model lib) happens
*here and nowhere else* — routers and other services only ever touch the model
through this module, and tests monkeypatch `get_model` to return a fake backend
so the engine suite needs no GPU.

A loaded backend MUST expose:
  - `sampling_rate: int`                      (24 kHz for OmniVoice)
  - `synthesize(text, *, ref_audio_path, ref_text, instruct, language, seed,
                speed, **params) -> np.ndarray`  (mono float32 in [-1, 1])
"""

import asyncio
import gc
import logging

from ..core import device

log = logging.getLogger(__name__)

# OmniVoice's native output rate; the fallback used before a backend is loaded.
DEFAULT_SAMPLE_RATE = 24000

_model = None
_lock: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    # Created lazily so it binds to the running loop, not import-time.
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


def _load_backend():
    """Construct the real OmniVoice backend on the detected device.

    Isolated so the heavy import is deferred and the failure (missing engine
    extra / model lib) is a single, clear message. Production installs the
    `engine` extra (`uv sync --extra engine`) and the vendored `omnivoice` lib;
    see docs/specs/packaging.md and docs/LICENSING.md.
    """
    dev = device.detect_device()
    log.info("Loading OmniVoice model on device=%s", dev)
    try:
        from ..engine.omnivoice_backend import OmniVoiceBackend
    except Exception as e:  # pragma: no cover - real-engine path, not in test venv
        raise RuntimeError(
            "Voice engine is not installed. Reinstall Parrot or run "
            "`uv sync --extra engine` in the sidecar. Underlying error: "
            f"{e}"
        ) from e
    return OmniVoiceBackend(device=dev)


async def get_model():
    """Return the loaded backend, loading it on first call under an async lock."""
    global _model
    if _model is not None:
        return _model
    async with _get_lock():
        if _model is None:  # re-check inside the lock
            loop = asyncio.get_running_loop()
            _model = await loop.run_in_executor(None, _load_backend)
    return _model


def sample_rate() -> int:
    """The model's output rate; the default before a backend has loaded."""
    if _model is not None and getattr(_model, "sampling_rate", None):
        return int(_model.sampling_rate)
    return DEFAULT_SAMPLE_RATE


def flush() -> None:
    """Unload the model and free GPU memory (the Flush/idle-unload path)."""
    global _model
    _model = None
    gc.collect()
    try:
        import torch  # type: ignore

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def _set_for_tests(fake) -> None:
    """Test-only: install a fake backend so get_model() returns it immediately."""
    global _model
    _model = fake
