"""Phase-1 IPC-contract tests for the sidecar routers.

These assert the exact shapes the Rust supervisor and Svelte UI depend on
(docs/specs/ipc-contract.md). Model loading is not involved — the engine status
is a fixed stub in Phase 1 — so these run without a GPU or torch.
"""

from fastapi.testclient import TestClient

from app import config, create_app

client = TestClient(create_app())


def test_healthz_returns_exact_contract():
    res = client.get("/healthz")
    assert res.status_code == 200
    # The supervisor polls for exactly this body — nothing more, nothing less.
    assert res.json() == {"status": "ok"}


def test_engine_status_shape():
    res = client.get("/engine/status")
    assert res.status_code == 200
    body = res.json()
    assert body["active"] == "omnivoice"
    # ROCm reports as "cuda"; "rocm" is never a reported value.
    assert body["device"] in {"cuda", "mps", "cpu"}


def test_port_defaults_to_3900(monkeypatch):
    monkeypatch.delenv("PARROT_PORT", raising=False)
    assert config.port() == 3900


def test_port_honors_env_override(monkeypatch):
    monkeypatch.setenv("PARROT_PORT", "4123")
    assert config.port() == 4123


def test_port_falls_back_on_unparseable_value(monkeypatch):
    monkeypatch.setenv("PARROT_PORT", "not-a-port")
    assert config.port() == 3900


def test_port_rejects_out_of_range(monkeypatch):
    # Must mirror the Rust resolve_port() u16 parse, or the supervisor and the
    # sidecar would bind different ports for the same PARROT_PORT.
    monkeypatch.setenv("PARROT_PORT", "70000")
    assert config.port() == 3900
    monkeypatch.setenv("PARROT_PORT", "-5")
    assert config.port() == 3900
    monkeypatch.setenv("PARROT_PORT", "0")
    assert config.port() == 3900
