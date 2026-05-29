"""WAV read/write helpers (soundfile-backed; no torch).

The model outputs a mono float32 waveform at 24 kHz; this module writes it to a
real WAV on disk and reports duration. Kept light (numpy + soundfile) so the
generate path's I/O is testable without the ML stack.
"""

from pathlib import Path

import numpy as np
import soundfile as sf


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
