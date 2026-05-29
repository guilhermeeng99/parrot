"""Device detection: CUDA/CPU selection, worker sizing, fail-safe fallbacks."""

import pytest

from app.core import device


@pytest.fixture(autouse=True)
def reset_device():
    device._reset_cache()
    yield
    device._reset_cache()


class FakeCuda:
    def __init__(self, free_gb=10.0, raise_mem=False):
        self._free = int(free_gb * 1024**3)
        self._raise_mem = raise_mem

    def is_available(self):
        return True

    def get_device_name(self, i):
        return "RTX 4090"

    def get_device_capability(self, i):
        return (9, 0)

    def get_arch_list(self):
        return ["sm_90"]

    def mem_get_info(self):
        if self._raise_mem:
            raise RuntimeError("driver hiccup")
        return (self._free, 24 * 1024**3)


class FakeTorch:
    def __init__(self, **kw):
        self.cuda = FakeCuda(**kw)


def test_no_torch_is_cpu(monkeypatch):
    monkeypatch.setattr(device, "_import_torch", lambda: None)
    assert device.detect_device() == "cpu"
    assert device.gpu_workers() == 1
    assert device.engine_status()["device"] == "cpu"


def test_cuda_detected(monkeypatch):
    monkeypatch.setattr(device, "_import_torch", lambda: FakeTorch(free_gb=10.0))
    assert device.detect_device() == "cuda"
    # 10 GB free / 2.5 = 4, clamped to the cap
    assert device.gpu_workers() == 4
    assert "CUDA" in device.engine_status()["device_label"]


def test_low_vram_clamps_to_one(monkeypatch):
    monkeypatch.setattr(device, "_import_torch", lambda: FakeTorch(free_gb=1.0))
    assert device.detect_device() == "cuda"
    assert device.gpu_workers() == 1  # floor(1/2.5)=0 → clamp to 1


def test_mem_get_info_failure_degrades_workers(monkeypatch):
    monkeypatch.setattr(device, "_import_torch", lambda: FakeTorch(raise_mem=True))
    assert device.detect_device() == "cuda"  # device stays cuda
    assert device.gpu_workers() == 1


def test_gpu_worker_env_override(monkeypatch):
    monkeypatch.setenv("PARROT_GPU_WORKERS", "3")
    monkeypatch.setattr(device, "_import_torch", lambda: FakeTorch())
    assert device.gpu_workers() == 3


def test_gpu_worker_env_override_clamped(monkeypatch):
    monkeypatch.setenv("PARROT_GPU_WORKERS", "999")
    monkeypatch.setattr(device, "_import_torch", lambda: FakeTorch())
    assert device.gpu_workers() == 16


def test_bad_worker_override_ignored(monkeypatch):
    monkeypatch.setenv("PARROT_GPU_WORKERS", "foo")
    monkeypatch.setattr(device, "_import_torch", lambda: FakeTorch(free_gb=10.0))
    assert device.gpu_workers() == 4  # falls through to VRAM heuristic


def test_engine_status_never_raises(monkeypatch):
    def boom():
        raise RuntimeError("kaboom")

    monkeypatch.setattr(device, "_resolve", boom)
    assert device.engine_status() == {"active": "omnivoice", "device": "cpu"}
