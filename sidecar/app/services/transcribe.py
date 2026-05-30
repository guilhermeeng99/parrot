"""Reference transcription — model catalog, on-demand download, transcribe (ASR).

The clone-time speech-to-text that fills `ref_text` (transcription.md). This
service owns everything that does NOT need the model loaded: the static catalog,
the torch-less status snapshot, and the download state machine (SSE progress,
60 s cooldown, retry/backoff, sha256 verify) — mirroring `setup_manager`. The
actual decode + inference is delegated to `asr_manager` (the ONE ASR engine
boundary), so this module — and the test suite — import no torch/whisper/av.

The download itself (`_fetch_model`) is indirected exactly like
`setup_manager._run_snapshot`, so tests exercise the status/cooldown/event logic
without a real multi-GB network download.
"""

import logging
import os
import threading
import time
from pathlib import Path

from ..core import device, paths
from ..core.logging import redact
from ..core.sse_broadcast import Broadcaster, keepalive_stream
from . import asr_manager
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

COOLDOWN_S = 60.0
_MAX_RETRIES = 3

_last_failure: dict[str, float] = {}  # model_id -> epoch seconds of last failure
_active: set[str] = set()
_active_lock = threading.Lock()

# Dedicated download bus (one per progress-broadcast surface, like setup_manager's).
_bus = Broadcaster(replay_maxlen=50)


def bind_loop(loop) -> None:
    """Called from the app lifespan so the download worker thread can publish SSE."""
    _bus.bind_loop(loop)


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
# Download (mirrors setup_manager: SSE progress, cooldown, retry/backoff)
# ---------------------------------------------------------------------------
def _event(model: str, phase: str, **extra) -> dict:
    base = {"model": model, "filename": "", "downloaded": 0, "total": 0, "pct": 0.0, "phase": phase}
    base.update(extra)
    return base


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
                _bus.publish(
                    _event(
                        model_id, "progress",
                        filename=f"{model_id}.pt",
                        downloaded=downloaded, total=total, pct=pct,
                    )
                )
    if expected_sha and sha.hexdigest() != expected_sha:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("Downloaded file failed its checksum — please retry.")
    tmp.replace(dest)


def _download_worker(model_id: str) -> None:
    _bus.publish(_event(model_id, "install_start"))
    _bus.publish(_event(model_id, "resolving"))
    try:
        last_error: Exception | None = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                _fetch_model(model_id)
                last_error = None
                break
            except Exception as e:  # transient network/OSError → backoff + retry
                last_error = e
                if attempt < _MAX_RETRIES:
                    _bus.publish(
                        _event(model_id, "install_retry", attempt=attempt, error=redact(str(e)))
                    )
                    time.sleep(min(2**attempt, 8))
        if last_error is not None:
            raise last_error
        _bus.publish(_event(model_id, "install_done", pct=1.0))
    except Exception as e:
        _last_failure[model_id] = time.time()
        _bus.publish(_event(model_id, "install_error", error=redact(str(e))))
        log.warning("Whisper model download failed for %s: %s", model_id, redact(str(e)))
    finally:
        with _active_lock:
            _active.discard(model_id)


def start_download(model_id: str) -> dict:
    if model_id not in _known_ids():
        raise ServiceError(400, f"Unknown transcription model: '{model_id}'.")

    last = _last_failure.get(model_id)
    if last is not None:
        remaining = COOLDOWN_S - (time.time() - last)
        if remaining > 0:
            raise ServiceError(429, f"That download just failed — retry in {int(remaining) + 1}s.")

    if _is_present(model_id):  # already downloaded → immediate done (no thread)
        _bus.publish(_event(model_id, "install_done", pct=1.0))
        return {"status": "download_started", "model": model_id}

    with _active_lock:
        if model_id not in _active:
            _active.add(model_id)
            threading.Thread(target=_download_worker, args=(model_id,), daemon=True).start()
    return {"status": "download_started", "model": model_id}


def _is_terminal(event: dict) -> bool:
    return event.get("phase") in ("install_done", "install_error")


def download_stream():
    """Async SSE generator: one `data:` line per event, keepalive on idle, STOP
    after a terminal install_done/install_error (shared fan-out helper)."""
    return keepalive_stream(_bus, is_terminal=_is_terminal)


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
    _last_failure.clear()
    with _active_lock:
        _active.clear()
    _bus.reset()
