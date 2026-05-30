"""Reference transcription — model catalog, on-demand download, transcribe (ASR).

The clone-time speech-to-text that fills `ref_text` (transcription.md). This
service owns everything that does NOT need the model loaded: the static catalog,
the torch-less status snapshot, and the Whisper single-file download. The download
choreography (SSE progress, 60 s cooldown, retry/backoff, per-download replay
reset) is the shared `DownloadOrchestrator`, the same one `setup_manager` uses;
only `_fetch_model` (stream + sha256-verify the `.pt`) is Whisper-specific. The
actual decode + inference is delegated to `asr_manager` (the ONE ASR engine
boundary), so this module — and the test suite — import no torch/whisper/av.

`_fetch_model` is indirected exactly like `setup_manager._run_snapshot`, so tests
exercise the status/cooldown/event logic without a real multi-GB network download.
"""

import logging
import os

# `threading` / `time` are imported so tests can patch threading.Thread / time.sleep
# on this module to reach the shared worker (the patch is module-global; see
# download_orchestrator). They are otherwise exercised through the orchestrator.
import threading  # noqa: F401  (patched by tests)
import time  # noqa: F401  (patched by tests)
from pathlib import Path

from ..core import device, paths
from ..core.logging import redact
from . import asr_manager
from .download_orchestrator import DownloadOrchestrator
from .errors import ServiceError

log = logging.getLogger(__name__)

# Curated, fidelity-first catalog (transcription.md §2). tiny/base omitted as too
# low-fidelity for a clone reference; `large-v3` is the default (max fidelity).
MODELS: list[dict] = [
    {"id": "small", "label": "Small", "size_mb": 470},
    {"id": "medium", "label": "Medium", "size_mb": 1500},
    {"id": "large-v3-turbo", "label": "Large v3 Turbo", "size_mb": 1600},
    {"id": "large-v3", "label": "Large v3 (max fidelity)", "size_mb": 3100},
]
DEFAULT_MODEL = "large-v3"

# Reference-audio containers accepted (matches the profile-create gate; EDGE-T9).
_SUPPORTED_EXTS = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".webm"}

# LanguageSelect's full English names → ISO codes Whisper expects. "Auto"/unknown
# → None (auto-detect). Kept here (not in asr_manager) so it's testable engine-free.
_LANG_CODES = {
    "english": "en", "spanish": "es", "portuguese": "pt", "french": "fr",
    "german": "de", "italian": "it", "dutch": "nl", "russian": "ru",
    "chinese": "zh", "japanese": "ja", "korean": "ko", "hindi": "hi", "arabic": "ar",
}


# ---------------------------------------------------------------------------
# Catalog / status
# ---------------------------------------------------------------------------
def _known_ids() -> set[str]:
    return {m["id"] for m in MODELS}


def _model_path(model_id: str) -> Path:
    return paths.whisper_models_dir() / f"{model_id}.pt"


def _is_present(model_id: str) -> bool:
    p = _model_path(model_id)
    return p.exists() and p.stat().st_size > 0


def status() -> dict:
    """Catalog + per-model presence + the resolved compute device. Torch-less:
    the device falls back to cpu when the engine isn't installed (CAT-2)."""
    dev = device.detect_device()
    out = {
        "models": [{**m, "downloaded": _is_present(m["id"])} for m in MODELS],
        "default_model": DEFAULT_MODEL,
        "device": dev,
        "gpu": dev == "cuda",
    }
    label = device.device_label()
    if label:
        out["device_label"] = label
    return out


def _lang_code(language: str | None) -> str | None:
    if not language:
        return None
    key = language.strip().lower()
    if key in ("", "auto"):
        return None
    return _LANG_CODES.get(key)  # unknown → None (auto-detect)


# ---------------------------------------------------------------------------
# Download (Whisper-specific fetch; choreography is the shared orchestrator)
# ---------------------------------------------------------------------------
def _fetch_model(model_id: str) -> None:
    """Stream the single-file `.pt` from openai-whisper's own (authoritative) URL,
    verify its embedded sha256, and atomically rename into place. Indirected for
    tests. Reads the URL from `whisper._MODELS` so it stays version-matched (CAT-3)."""
    import hashlib
    import urllib.request

    import whisper  # type: ignore  # only the URL map is needed here (engine extra)

    url = whisper._MODELS.get(model_id)
    if not url:
        raise RuntimeError(f"openai-whisper has no URL for model '{model_id}'.")
    expected_sha = url.split("/")[-2]  # whisper embeds the sha256 in the URL path
    dest = _model_path(model_id)
    tmp = dest.with_name(dest.name + ".part")

    sha = hashlib.sha256()
    downloaded = 0
    req = urllib.request.Request(url, headers={"User-Agent": "Parrot"})
    with urllib.request.urlopen(req) as resp:  # noqa: S310 - fixed https CDN URL
        total = int(resp.headers.get("Content-Length") or 0)
        with open(tmp, "wb") as f:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                sha.update(chunk)
                downloaded += len(chunk)
                pct = round(downloaded / total, 4) if total > 0 else 0.0
                _downloads.publish_progress(
                    model_id,
                    filename=f"{model_id}.pt",
                    downloaded=downloaded,
                    total=total,
                    pct=pct,
                )
    if expected_sha and sha.hexdigest() != expected_sha:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("Downloaded file failed its checksum — please retry.")
    tmp.replace(dest)


# The shared download state machine, wired to the Whisper checkpoint specifics. The
# fetch/known/present callables are late-bound thunks so tests that monkeypatch
# `_fetch_model` / `_is_present` still take effect inside the worker.
_downloads = DownloadOrchestrator(
    id_key="model",
    known_ids=lambda: _known_ids(),
    is_present=lambda model_id: _is_present(model_id),
    fetch=lambda model_id: _fetch_model(model_id),
    unknown_message=lambda model_id: f"Unknown transcription model: '{model_id}'.",
)

# Module-level aliases preserve the public surface (router) and the test surface
# (which patches `_bus.publish` / `_fetch_model` and reads `_active` / `_last_failure`).
_bus = _downloads._bus
_active = _downloads._active
_last_failure = _downloads._last_failure
_download_worker = _downloads.worker


def bind_loop(loop) -> None:
    """Called from the app lifespan so the download worker thread can publish SSE."""
    _downloads.bind_loop(loop)


def start_download(model_id: str) -> dict:
    return _downloads.start(model_id)


def download_stream():
    """Async SSE generator for the Whisper model download (terminal-closing)."""
    return _downloads.stream()


# ---------------------------------------------------------------------------
# Transcribe
# ---------------------------------------------------------------------------
def _check_ext(filename: str | None) -> None:
    ext = os.path.splitext(filename or "")[1].lower() or ".wav"
    if ext not in _SUPPORTED_EXTS:
        raise ServiceError(
            415,
            f"Unsupported audio format '{ext}'. Use one of: {', '.join(sorted(_SUPPORTED_EXTS))}.",
        )


def transcribe(audio_bytes: bytes, filename: str | None, model_id: str, language: str) -> dict:
    """Validate, then transcribe the clip into a `ref_text` candidate (BR-T1/T3).

    An empty transcript is a valid "no speech" result (EDGE-T1), returned as
    text:"" — it is the UI's job to nudge, not this layer's to 500.
    """
    model_id = (model_id or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    if model_id not in _known_ids():
        raise ServiceError(400, f"Unknown transcription model: '{model_id}'.")
    _check_ext(filename)
    if not _is_present(model_id):
        raise ServiceError(409, "Download a transcription model before transcribing.")
    if not audio_bytes:
        raise ServiceError(400, "No audio was provided to transcribe.")

    try:
        out = asr_manager.transcribe(
            audio_bytes,
            model_path=_model_path(model_id),
            device=device.detect_device(),
            language=_lang_code(language),
        )
    except ModuleNotFoundError as e:  # whisper/av absent → engine extra not installed
        raise ServiceError(
            500,
            "Voice engine is not installed. Reinstall Parrot or run "
            f"`uv sync --extra engine` in the sidecar. ({e})",
        )
    except ValueError as e:  # undecodable bytes (EDGE-T7)
        raise ServiceError(500, f"Couldn't read that audio file. ({redact(str(e))})")

    return {"text": out["text"], "language": out["language"], "model": model_id}


def _reset_for_tests() -> None:
    _downloads.reset_for_tests()
