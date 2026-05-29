"""Post-inference DSP effect presets (synthesis.md §DSP Effect Presets).

Pipeline for any non-`raw` preset: broadcast mastering → the named effect chain →
peak-normalize to -2.0 dBFS. `raw` short-circuits and returns the model output
untouched. DSP is best-effort: if `pedalboard` is unavailable, every effect
degrades to a no-op and the audio still ships (just peak-normalized) — never an
error (synthesis.md edge: "pedalboard missing").
"""

import logging

import numpy as np

log = logging.getLogger(__name__)

TARGET_PEAK_DBFS = -2.0

# id -> human label. The set is closed; an unknown id is a 400 upstream.
PRESETS: dict[str, str] = {
    "broadcast": "Broadcast",
    "cinematic": "Cinematic",
    "podcast": "Podcast",
    "warm": "Warm",
    "bright": "Bright",
    "raw": "Raw",
}


def validate_preset(preset: str) -> None:
    """Raise ValueError for an unknown preset (router maps it to HTTP 400)."""
    if preset not in PRESETS:
        valid = ", ".join(sorted(PRESETS))
        raise ValueError(f"Unknown effect preset: '{preset}'. Valid: [{valid}]")


def peak_normalize(samples: np.ndarray, dbfs: float = TARGET_PEAK_DBFS) -> np.ndarray:
    """Scale so the peak sits at `dbfs` dBFS. Silence is returned unchanged."""
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    if peak <= 0.0:
        return samples
    target = 10.0 ** (dbfs / 20.0)
    return (samples * (target / peak)).astype(np.float32, copy=False)


def _pedalboard():
    """Import pedalboard lazily; return the module or None (graceful degrade)."""
    try:
        import pedalboard  # type: ignore

        return pedalboard
    except Exception:
        return None


def _board_for(pb, preset: str):
    """Build the pedalboard effect chain for a preset (no master/normalize here)."""
    if preset == "cinematic":
        return pb.Pedalboard([pb.Reverb(room_size=0.5, wet_level=0.18), pb.Compressor(threshold_db=-18, ratio=2.0)])
    if preset == "podcast":
        return pb.Pedalboard([pb.Compressor(threshold_db=-22, ratio=4.0), pb.HighpassFilter(cutoff_frequency_hz=80)])
    if preset == "warm":
        return pb.Pedalboard([pb.LowShelfFilter(cutoff_frequency_hz=220, gain_db=3.0), pb.Compressor(threshold_db=-18, ratio=2.0)])
    if preset == "bright":
        return pb.Pedalboard([pb.HighShelfFilter(cutoff_frequency_hz=6000, gain_db=3.5), pb.Compressor(threshold_db=-18, ratio=2.0)])
    # broadcast (default): warm + compressed + clear
    return pb.Pedalboard(
        [pb.HighpassFilter(cutoff_frequency_hz=70), pb.Compressor(threshold_db=-18, ratio=3.0), pb.Gain(gain_db=1.0)]
    )


def process(samples: np.ndarray, sample_rate: int, preset: str) -> np.ndarray:
    """Apply the preset's DSP chain. `raw` returns the model output untouched;
    everything else masters, applies the chain (if pedalboard is present), then
    peak-normalizes to -2.0 dBFS."""
    validate_preset(preset)
    # Mono (samples,) contract, shared with audio_io.save_wav / to_pcm16_bytes:
    # the model emits a 1-D float32 waveform, and pedalboard's HighpassFilter etc.
    # silently mis-shape a 2-D array. Flatten defensively so a stray (n,1)/(1,n)
    # from the backend can't corrupt the chain.
    samples = np.asarray(samples, dtype=np.float32).reshape(-1)
    assert samples.ndim == 1
    if preset == "raw":
        return samples

    pb = _pedalboard()
    if pb is not None:
        try:
            board = _board_for(pb, preset)
            samples = board(samples, sample_rate).astype(np.float32, copy=False)
        except Exception as e:  # any DSP failure → ship the unprocessed audio
            log.warning("DSP chain '%s' failed; shipping unprocessed audio: %s", preset, e)
    return peak_normalize(samples)
