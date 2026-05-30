"""WAV read/write helpers (soundfile-backed; no torch).

The model outputs a mono float32 waveform at 24 kHz; this module writes it to a
real WAV on disk and reports duration. Kept light (numpy + soundfile) so the
generate path's I/O is testable without the ML stack.
"""

import io
from pathlib import Path

import numpy as np
import soundfile as sf


def wav_file_to_mp3_bytes(path: Path) -> bytes:
    """Re-encode a WAV file on disk to MP3 bytes (History export-as-mp3 path).

    Parrot's pipeline is WAV end-to-end (24 kHz model output), but a user exporting
    a clip wants a small, shareable file. soundfile's bundled libsndfile (>= 1.1)
    encodes MP3 directly, so no extra encoder dependency (LAME/ffmpeg) is needed —
    this stays in the light `soundfile` dep, off the heavy engine extra."""
    data, sr = sf.read(str(path), dtype="float32", always_2d=False)
    return _encode_mp3(data, sr)


def wav_bytes_to_mp3_bytes(data: bytes) -> bytes:
    """Re-encode in-memory WAV bytes to MP3 bytes (stateless export of a FRESH
    result, which lives only in memory and may have no history row — e.g. after the
    user cleared History). Raises ValueError on undecodable input."""
    try:
        samples, sr = sf.read(io.BytesIO(data), dtype="float32", always_2d=False)
    except Exception as e:
        raise ValueError(f"not decodable WAV audio: {e}") from e
    return _encode_mp3(samples, sr)


def _encode_mp3(samples, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, samples, sample_rate, format="MP3")
    return buf.getvalue()


def save_wav(samples: np.ndarray, sample_rate: int, path: Path) -> None:
    """Write a mono float32 waveform to `path` as a 16-bit PCM WAV."""
    data = np.asarray(samples, dtype=np.float32).reshape(-1)
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), data, sample_rate, subtype="PCM_16", format="WAV")


def duration_seconds(samples: np.ndarray, sample_rate: int) -> float:
    n = int(np.asarray(samples).reshape(-1).shape[0])
    if sample_rate <= 0:
        return 0.0
    return round(n / sample_rate, 2)


def to_pcm16_bytes(samples: np.ndarray) -> bytes:
    """Convert a float32 [-1,1] waveform to little-endian PCM16 bytes (WS stream)."""
    clipped = np.clip(np.asarray(samples, dtype=np.float32).reshape(-1), -1.0, 1.0)
    return (clipped * 32767.0).astype("<i2").tobytes()
