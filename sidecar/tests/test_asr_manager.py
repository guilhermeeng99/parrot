"""ASR engine boundary: the CUDA→CPU degrade logic (ENG-1 / EDGE-T6).

`_is_cuda_failure` and `transcribe()`'s fallback retry are pure, torch-free logic
(the model load/run is `_run`, which is monkeypatched here), so they are tested
without whisper/torch/av — yet they are the "never a first-run wall" guarantee, so
they must not regress silently.
"""

from pathlib import Path

import pytest

from app.services import asr_manager


@pytest.mark.parametrize(
    "exc,expected",
    [
        (RuntimeError("CUDA out of memory. Tried to allocate ..."), True),
        (RuntimeError("no kernel image is available for execution on the device"), True),
        (RuntimeError("CUDA driver version is insufficient for CUDA runtime version"), True),
        (RuntimeError("cuBLAS init failed"), True),
        (RuntimeError("cuDNN error: CUDNN_STATUS_NOT_INITIALIZED"), True),
        (ValueError("no audio stream in the file"), False),
        (RuntimeError("some totally unrelated failure"), False),
    ],
)
def test_is_cuda_failure_classifies(exc, expected):
    assert asr_manager._is_cuda_failure(exc) is expected


def test_transcribe_falls_back_to_cpu_on_cuda_failure(monkeypatch):
    """A CUDA-classified failure on device='cuda' retries the SAME call on CPU
    rather than 500-ing the clone."""
    calls: list[str] = []

    def fake_run(samples, model_path, device, language):
        calls.append(device)
        if device == "cuda":
            raise RuntimeError("CUDA out of memory")
        return {"text": "olá", "language": "pt"}

    monkeypatch.setattr(asr_manager, "_decode_16k_mono", lambda b: b"samples")
    monkeypatch.setattr(asr_manager, "_run", fake_run)
    monkeypatch.setattr(asr_manager, "_free_cuda", lambda: None)

    out = asr_manager.transcribe(b"x", model_path=Path("m.pt"), device="cuda", language=None)

    assert out == {"text": "olá", "language": "pt"}
    assert calls == ["cuda", "cpu"]  # tried GPU, degraded to CPU


def test_transcribe_cpu_failure_propagates(monkeypatch):
    """On device='cpu' there's nowhere to fall back to — the error propagates."""
    monkeypatch.setattr(asr_manager, "_decode_16k_mono", lambda b: b"s")

    def boom(*a, **k):
        raise ValueError("decode boom")

    monkeypatch.setattr(asr_manager, "_run", boom)
    with pytest.raises(ValueError, match="decode boom"):
        asr_manager.transcribe(b"x", model_path=Path("m.pt"), device="cpu", language=None)


def test_transcribe_non_cuda_error_on_gpu_propagates(monkeypatch):
    """A non-CUDA error on device='cuda' must NOT be silently retried on CPU —
    only genuine CUDA-class failures degrade (else a real bug would be masked)."""
    monkeypatch.setattr(asr_manager, "_decode_16k_mono", lambda b: b"s")

    def boom(*a, **k):
        raise RuntimeError("totally unrelated")

    monkeypatch.setattr(asr_manager, "_run", boom)
    with pytest.raises(RuntimeError, match="totally unrelated"):
        asr_manager.transcribe(b"x", model_path=Path("m.pt"), device="cuda", language=None)
