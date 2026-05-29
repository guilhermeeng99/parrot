"""First-run setup: status snapshot, download validation, cooldown, event flow."""

import pytest

from app import config
from app.services import setup_manager as sm
from app.services.errors import ServiceError


def test_setup_status_shape(env, monkeypatch):
    monkeypatch.setattr(sm, "_scan_cached_repos", lambda: {})
    st = sm.setup_status()
    assert st["models_ready"] is False
    assert st["min_free_gb"] == 10
    assert "hf_cache_dir" in st and isinstance(st["disk_free_gb"], float)
    assert st["enough_disk"] == (st["disk_free_gb"] >= st["min_free_gb"])
    assert any(m["repo_id"] == config.DEFAULT_MODEL_REPO for m in st["missing"])


def test_setup_status_ready_when_cached(env, monkeypatch):
    monkeypatch.setattr(sm, "_scan_cached_repos", lambda: {config.DEFAULT_MODEL_REPO: 999})
    st = sm.setup_status()
    assert st["models_ready"] is True and st["missing"] == []


def test_setup_status_via_api(client, monkeypatch):
    monkeypatch.setattr(sm, "_scan_cached_repos", lambda: {})
    res = client.get("/setup/status")
    assert res.status_code == 200
    assert res.json()["models_ready"] is False


def test_download_unknown_repo_is_400(env):
    with pytest.raises(ServiceError) as ei:
        sm.start_download("not/a-known-repo")
    assert ei.value.status_code == 400


def test_download_noop_when_already_cached(env, monkeypatch):
    monkeypatch.setattr(sm, "_scan_cached_repos", lambda: {config.DEFAULT_MODEL_REPO: 123})
    res = sm.start_download(config.DEFAULT_MODEL_REPO)
    assert res["status"] == "download_started"


def test_worker_emits_event_sequence(env, monkeypatch):
    events: list[dict] = []
    monkeypatch.setattr(sm._bus, "publish", lambda e: events.append(e))
    monkeypatch.setattr(sm, "_run_snapshot", lambda repo: None)
    sm._download_worker(config.DEFAULT_MODEL_REPO)
    phases = [e["phase"] for e in events]
    assert phases[0] == "install_start"
    assert "resolving" in phases
    assert "install_done" in phases


def test_failed_download_sets_cooldown(env, monkeypatch):
    events: list[dict] = []
    monkeypatch.setattr(sm._bus, "publish", lambda e: events.append(e))
    monkeypatch.setattr(sm.time, "sleep", lambda *a: None)  # don't actually back off

    def boom(repo):
        raise OSError("network down")

    monkeypatch.setattr(sm, "_run_snapshot", boom)
    sm._download_worker(config.DEFAULT_MODEL_REPO)
    assert any(e["phase"] == "install_error" for e in events)
    # an immediate retry is rejected with 429 + remaining seconds
    with pytest.raises(ServiceError) as ei:
        sm.start_download(config.DEFAULT_MODEL_REPO)
    assert ei.value.status_code == 429
