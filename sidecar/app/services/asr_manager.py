"""The single entry point for the Whisper ASR model — reference transcription only.

This is the ASR analogue of `model_manager`/`engine.omnivoice_backend`: the ONE
place `whisper`, `torch`, and `av` are imported, so the light app and the test
suite never touch them (tests monkeypatch `transcribe`, so the heavy imports
below never run without the `engine` extra + a downloaded model). It owns:

  - decoding arbitrary container audio (webm/m4a/mp3/ogg/flac/wav) to a 16 kHz
    mono float32 array via PyAV — whose wheel bundles the ffmpeg libraries, so NO
    system ffmpeg binary is required (transcription.md §3);
  - loading the user-chosen Whisper checkpoint DIRECTLY from its cached `.pt`
    path (skips openai-whisper's full-file sha re-hash on every load), running
    inference with the fixed anti-hallucination config, then FREEING the model so
    it never stays co-resident with the OmniVoice TTS model (transcription.md ENG-2);
  - a CUDA-OOM → CPU fallback so a small GPU degrades to slow rather than failing
    the clone (ENG-1).

Calls are serialized by a process-wide lock: Parrot is single-user, and the model
is loaded/freed per call, so two concurrent transcriptions must not race the GPU.
"""

import gc
import logging
import threading
from pathlib import Path

log = logging.getLogger(__name__)

# Whisper's fixed input rate. The model front-end is trained on 16 kHz mono.
SAMPLE_RATE = 16000

# Serialize transcription: the model is loaded + freed per call (ENG-2), so
# overlapping calls would double-load the GPU. Single-user app → a lock is enough.
_lock = threading.Lock()


def transcribe(audio_bytes: bytes, *, model_path: Path, device: str, language: str | None) -> dict:
    """Decode `audio_bytes` → run Whisper at `model_path` → free the model.

    `language` is an already-resolved ISO code (e.g. "pt") or None for auto-detect
    — the name→code mapping lives in the `transcribe` service (testable without the
    engine). Returns ``{"text": str, "language": str}``; an empty/whitespace result
    is a valid "no speech" outcome (EDGE-T1), not an error. Raises on a decode
    failure or a missing engine, which the service maps to a 4xx/5xx.
    """
    samples = _decode_16k_mono(audio_bytes)
    with _lock:
        try:
            return _run(samples, model_path, device, language)
        except Exception as e:  # any CUDA-path failure (OOM / arch / driver) → CPU
            if device == "cuda" and _is_cuda_failure(e):
                log.warning("Whisper CUDA path failed; falling back to CPU for this call: %s", e)
                _free_cuda()
                return _run(samples, model_path, "cpu", language)
            raise


def _run(samples, model_path: Path, device: str, language: str | None) -> dict:
    """Load the checkpoint, transcribe, and release it (so VRAM is freed)."""
    import whisper  # type: ignore  # heavy (pulls torch); engine extra only

    # Passing the PATH (not a model id) loads the checkpoint directly — no network
    # and no sha256 re-hash of the multi-GB file that load-by-id would do (ENG-4).
    model = whisper.load_model(str(model_path), device=device)
    try:
        result = model.transcribe(
            samples,
            task="transcribe",
            language=language,  # None → auto-detect
            # Anti-hallucination, fixed (ENG-3): greedy with no temperature-fallback
            # ladder, and no previous-text conditioning (kills repetition drift).
            temperature=0.0,
            condition_on_previous_text=False,
            fp16=(device == "cuda"),
        )
    finally:
        del model
        _free(device)
    text = (result.get("text") or "").strip()
    detected = result.get("language") or (language or "")
    return {"text": text, "language": detected}


def _decode_16k_mono(audio_bytes: bytes):
    """Decode container bytes → 16 kHz mono float32 [-1, 1] via PyAV (no ffmpeg bin).

    Handles webm (the in-app recorder's format), m4a, mp3, ogg, flac, wav. Raises a
    clear error when the bytes carry no decodable audio stream."""
    import io

    import av  # type: ignore  # wheel bundles ffmpeg libs; engine extra only
    import numpy as np

    try:
        with av.open(io.BytesIO(audio_bytes)) as container:
            if not container.streams.audio:
                raise ValueError("no audio stream in the file")
            resampler = av.AudioResampler(format="flt", layout="mono", rate=SAMPLE_RATE)
            chunks: list = []
            for frame in container.decode(audio=0):
                for resampled in _as_list(resampler.resample(frame)):
                    chunks.append(resampled.to_ndarray().reshape(-1))
            for resampled in _as_list(resampler.resample(None)):  # flush the resampler
                chunks.append(resampled.to_ndarray().reshape(-1))
    except ValueError:
        raise
    except Exception as e:  # av raises a grab-bag of errors on corrupt input
        raise ValueError(f"couldn't decode the audio: {e}") from e

    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks).astype(np.float32)


def _as_list(resampled):
    """PyAV ≥9 returns a list from resample(); older returns a frame-or-None."""
    if resampled is None:
        return []
    return resampled if isinstance(resampled, list) else [resampled]


def _is_cuda_failure(exc: Exception) -> bool:
    """A CUDA load/run failure that should degrade to CPU rather than 500 the clone.

    Covers OOM *and* the realistic Windows case: device.detect_device() can hand us
    'cuda' for a GPU whose compute capability is outside the shipped torch wheel's
    arch list (device.py only WARNS, never blocks), so the model load fails with a
    non-OOM RuntimeError ('no kernel image is available', 'CUDA driver version is
    insufficient', cuBLAS/cuDNN init errors). Per the north star, none of these may
    become a first-run wall — they all retry on CPU (transcription.md ENG-1)."""
    name = type(exc).__name__
    blob = f"{name} {exc}".lower()
    return any(
        token in blob
        for token in (
            "outofmemory", "out of memory", "cuda", "cublas", "cudnn",
            "kernel image", "no kernel", "device-side", "driver version",
        )
    )


def _free(device: str) -> None:
    gc.collect()
    if device == "cuda":
        _free_cuda()


def _free_cuda() -> None:
    try:
        import torch  # type: ignore

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass
