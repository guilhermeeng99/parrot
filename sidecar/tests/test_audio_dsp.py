"""Post-inference DSP: peak-normalize math, `raw` short-circuit, graceful degrade.

The load-bearing contract (synthesis.md): DSP is best-effort. A non-`raw` preset
masters + chains + normalizes to -2 dBFS, but if pedalboard is missing OR the
chain raises, `process()` must still return audio (just normalized) — never an
error. `raw` returns the model output untouched.
"""

import numpy as np

from app.services import audio_dsp

_SR = 24000


def _dbfs(samples: np.ndarray) -> float:
    peak = float(np.max(np.abs(samples)))
    return 20.0 * np.log10(peak)


def test_peak_normalize_hits_target_dbfs():
    quiet = (np.sin(np.linspace(0, 2 * np.pi, 4800)) * 0.01).astype(np.float32)
    out = audio_dsp.peak_normalize(quiet)
    assert abs(_dbfs(out) - audio_dsp.TARGET_PEAK_DBFS) < 0.05  # ~-2 dBFS within rounding


def test_peak_normalize_leaves_silence_unchanged():
    silence = np.zeros(4800, dtype=np.float32)
    out = audio_dsp.peak_normalize(silence)
    assert np.array_equal(out, silence)  # no divide-by-zero, returned as-is


def test_process_raw_returns_input_untouched():
    samples = (np.sin(np.linspace(0, 2 * np.pi, 4800)) * 0.3).astype(np.float32)
    out = audio_dsp.process(samples, _SR, "raw")
    assert np.array_equal(out, samples)  # raw short-circuits before any DSP/normalize


def test_process_without_pedalboard_does_not_raise(monkeypatch):
    """pedalboard unavailable → every effect degrades to a no-op; audio still ships
    (peak-normalized), never an error (synthesis.md edge: 'pedalboard missing')."""
    monkeypatch.setattr(audio_dsp, "_pedalboard", lambda: None)
    samples = (np.sin(np.linspace(0, 2 * np.pi, 4800)) * 0.3).astype(np.float32)
    out = audio_dsp.process(samples, _SR, "broadcast")
    assert out.dtype == np.float32
    assert abs(_dbfs(out) - audio_dsp.TARGET_PEAK_DBFS) < 0.05  # normalized, not chained


def test_process_degrades_when_chain_raises(monkeypatch):
    """If the effect chain itself blows up, ship the unprocessed (but normalized)
    audio rather than failing the whole synthesis."""
    monkeypatch.setattr(audio_dsp, "_pedalboard", lambda: object())  # truthy "module"

    def boom(pb, preset):
        raise RuntimeError("pedalboard chain exploded")

    monkeypatch.setattr(audio_dsp, "_board_for", boom)
    samples = (np.sin(np.linspace(0, 2 * np.pi, 4800)) * 0.3).astype(np.float32)
    out = audio_dsp.process(samples, _SR, "cinematic")  # must not raise
    assert out.dtype == np.float32
    assert abs(_dbfs(out) - audio_dsp.TARGET_PEAK_DBFS) < 0.05
