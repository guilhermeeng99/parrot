"""Compute-device detection + worker-pool sizing (device-detection.md).

Windows-only device set: **CUDA → CPU**. Detection never raises and never
returns None (worst case "cpu"). `torch` is imported lazily on first access (it
is a multi-second import) and the result is cached for the process lifetime, so
`/healthz` stays instant during cold start and `/engine/status` answers from the
cached value once detection has run.

Tuning constants are sidecar-internal (not user-facing). Power-user overrides
are explicit env opt-ins: PARROT_GPU_WORKERS, PARROT_CPU_POOL, CUDA_VISIBLE_DEVICES.
"""

import logging
import os

log = logging.getLogger(__name__)

GPU_VRAM_PER_JOB_GB = 2.5  # budgeted free VRAM per concurrent synthesize job
GPU_WORKER_CAP = 4  # hard ceiling on GPU workers regardless of VRAM

_state: dict | None = None


def _import_torch():
    """Import torch lazily. Returns the module or None if unavailable.

    Indirected so tests can monkeypatch a fake torch without installing it (the
    `engine` extra carries the real torch; the test venv does not)."""
    try:
        import torch  # type: ignore

        return torch
    except Exception:  # ImportError, or a broken partial install
        return None


def _detect_device(torch) -> tuple[str, str | None]:
    """(device, device_label). Fail-safe: any probe error degrades to cpu."""
    if torch is None:
        return "cpu", "CPU — slower but works"
    try:
        if torch.cuda.is_available():
            label = "GPU (CUDA)"
            try:
                name = torch.cuda.get_device_name(0)
                if name:
                    label = f"GPU (CUDA) — {name}"
            except Exception:
                pass
            _warn_on_arch_mismatch(torch)
            return "cuda", label
    except Exception as e:  # driver crash / missing symbol → CPU fallback
        log.warning("CUDA probe failed, falling back to CPU: %s", e)
    return "cpu", "CPU — slower but works"


def _warn_on_arch_mismatch(torch) -> None:
    """Compute-capability check (Rule 7): a mismatch logs a warning but does NOT
    block loading — the user gets an actionable message, not a mystery slowdown."""
    try:
        major, minor = torch.cuda.get_device_capability(0)
        sm = f"sm_{major}{minor}"
        arches = torch.cuda.get_arch_list()  # e.g. ['sm_70', 'sm_80', ...]
        if arches and sm not in arches:
            log.warning(
                "GPU compute capability %s not in this PyTorch build's arch list "
                "%s — attempting anyway; a load failure here means the wheel "
                "doesn't support your GPU.",
                sm,
                arches,
            )
    except Exception:
        pass  # introspection is best-effort, never fatal


def _size_gpu_workers(torch, device: str) -> int:
    """Resolve GPU worker count (Rule 4): env override → VRAM heuristic → 1."""
    override = os.environ.get("PARROT_GPU_WORKERS")
    if override is not None:
        try:
            return max(1, min(16, int(override)))
        except ValueError:
            log.warning("PARROT_GPU_WORKERS=%r is not an integer; ignoring.", override)
    if device != "cuda" or torch is None:
        return 1  # CPU pool exists but runs single-threaded
    try:
        free_bytes, _total = torch.cuda.mem_get_info()
        free_gb = free_bytes / (1024**3)
        workers = int(free_gb // GPU_VRAM_PER_JOB_GB)
        return max(1, min(GPU_WORKER_CAP, workers))
    except Exception as e:
        log.warning("mem_get_info() failed; using 1 GPU worker: %s", e)
        return 1


def _resolve() -> dict:
    global _state
    if _state is not None:
        return _state
    torch = _import_torch()
    device, label = _detect_device(torch)
    _state = {
        "device": device,
        "device_label": label,
        "gpu_workers": _size_gpu_workers(torch, device),
        "cpu_workers": _cpu_workers(),
    }
    log.info(
        "Device resolved: %s (%s), gpu_workers=%d, cpu_workers=%d",
        _state["device"],
        _state["device_label"],
        _state["gpu_workers"],
        _state["cpu_workers"],
    )
    return _state


def _cpu_workers() -> int:
    override = os.environ.get("PARROT_CPU_POOL")
    if override is not None:
        try:
            return max(1, min(16, int(override)))
        except ValueError:
            log.warning("PARROT_CPU_POOL=%r is not an integer; ignoring.", override)
    return min(8, os.cpu_count() or 4)


def detect_device() -> str:
    """The torch device string the model loads onto: 'cuda' | 'cpu'."""
    return _resolve()["device"]


def device_label() -> str | None:
    """Optional human label for the device, e.g. 'GPU (CUDA)'."""
    return _resolve()["device_label"]


def gpu_workers() -> int:
    return _resolve()["gpu_workers"]


def cpu_workers() -> int:
    return _resolve()["cpu_workers"]


def engine_status() -> dict:
    """The `/engine/status` payload. Never raises — on any internal error it
    reports a safe `cpu` default (device-detection.md / settings.md Rule)."""
    try:
        st = _resolve()
        out = {"active": "omnivoice", "device": st["device"]}
        if st["device_label"]:
            out["device_label"] = st["device_label"]
        return out
    except Exception:
        return {"active": "omnivoice", "device": "cpu"}


def _reset_cache() -> None:
    """Test-only: drop the cached resolution so the next call re-detects."""
    global _state
    _state = None
