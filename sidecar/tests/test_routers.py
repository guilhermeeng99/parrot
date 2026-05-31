"""IPC-contract tests for the always-on routers (health, engine) + port parsing.

These assert the exact shapes the Rust supervisor and Svelte UI depend on
(ipc-contract.md). The model is mocked (conftest), so they run without a GPU.
"""

import tomllib
from pathlib import Path

from fastapi.testclient import TestClient

from app import config, create_app


def test_healthz_returns_exact_contract(client):
    res = client.get("/healthz")
    assert res.status_code == 200
    # The supervisor polls for exactly this body — nothing more, nothing less.
    assert res.json() == {"status": "ok"}


def test_app_version_matches_project_metadata(client):
    with (Path(__file__).resolve().parents[1] / "pyproject.toml").open("rb") as f:
        expected = tomllib.load(f)["project"]["version"]
    assert client.app.version == expected


def test_engine_status_shape(client):
    res = client.get("/engine/status")
    assert res.status_code == 200
    body = res.json()
    assert body["active"] == "omnivoice"
    # Windows-only: CUDA (NVIDIA) or CPU. No mps/rocm.
    assert body["device"] in {"cuda", "cpu"}


def test_engine_status_rejects_non_loopback(env):
    # Built inside the env fixture so it runs against a fresh tmp data dir, not a
    # process-wide client created at import time (FIRST: independent/repeatable).
    remote = TestClient(create_app(), client=("10.0.0.5", 4444))
    assert remote.get("/engine/status").status_code == 403


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
