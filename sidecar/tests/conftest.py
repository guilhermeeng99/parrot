"""Shared test fixtures.

Every test runs against a fresh tmp `parrot_data/` and a fresh tmp HF cache, with
the model boundary replaced by a deterministic fake backend — so the engine suite
needs no GPU, no torch, and no model download (synthesis.md). The TestClient is
given a 127.0.0.1 peer so the loopback-gated endpoints are reachable.
"""

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import create_app
from app.core import db, device
from app.services import hf_token, model_manager, setup_manager
from app.services import transcribe as transcribe_svc

# TestClient's default peer is "testclient", which the loopback gate rejects.
LOOPBACK_PEER = ("127.0.0.1", 50000)


class FakeBackend:
    """A stand-in for the OmniVoice model. Deterministic, fast, no torch."""

    sampling_rate = 24000

    def synthesize(self, text, *, seed=None, **kw):
        # Drive the real begin→report→finish progress plumbing the way the engine
        # does: one progress_cb call per diffusion step, so tests exercise the bus
        # instead of a no-op stub.
        progress_cb = kw.get("progress_cb")
        num_step = int(kw.get("num_step", 16) or 16)
        if progress_cb is not None:
            for i in range(num_step):
                progress_cb(i + 1, num_step)
        n = max(2400, min(24000, len(text) * 240))  # length scales with text
        t = np.linspace(0.0, 1.0, n, dtype=np.float32)
        return (np.sin(2 * np.pi * 220 * t) * 0.2).astype(np.float32)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("PARROT_DATA_DIR", str(tmp_path / "parrot_data"))
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    monkeypatch.delenv("HF_HUB_CACHE", raising=False)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    db._reset_for_tests()
    device._reset_cache()
    setup_manager._reset_for_tests()
    transcribe_svc._reset_for_tests()
    hf_token._invalidate_cache()
    model_manager._set_for_tests(FakeBackend())
    yield tmp_path
    model_manager.flush()


@pytest.fixture()
def client(env):
    with TestClient(create_app(), client=LOOPBACK_PEER) as c:
        yield c


def make_profile(client, name="Alex", text="this is the reference", language="Auto"):
    """Create a profile via the API and return its id."""
    res = client.post(
        "/profiles",
        data={"name": name, "ref_text": text, "language": language},
        files={"ref_audio": ("ref.wav", b"RIFFfake-wav-bytes", "audio/wav")},
    )
    assert res.status_code == 200, res.text
    return res.json()["id"]
