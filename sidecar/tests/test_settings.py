"""HF token store: masking, encrypt round-trip, env override, plaintext guard."""

import pytest

from app.services import hf_token, settings_store
from app.services.errors import ServiceError
from tests.conftest import LOOPBACK_PEER


@pytest.fixture()
def no_network(monkeypatch):
    monkeypatch.setattr(hf_token, "_whoami", lambda token: (True, "alice"))
    monkeypatch.setattr(hf_token, "_login", lambda token: None)


def test_get_state_no_token(client):
    state = client.get("/settings/hf-token").json()
    assert state["active"] is None
    app_src = next(s for s in state["sources"] if s["source"] == "app")
    assert app_src["set"] is False and app_src["masked"] is None


def test_set_token_masks_and_validates(client, no_network):
    res = client.post("/settings/hf-token", json={"token": "hf_abcdefghijklmnop"})
    assert res.status_code == 200
    state = res.json()
    app_src = next(s for s in state["sources"] if s["source"] == "app")
    assert app_src["set"] is True
    assert app_src["masked"] == "hf_…nop"  # last 3 only
    assert app_src["whoami_ok"] is True and app_src["whoami_user"] == "alice"
    assert state["active"] == "app"


def test_token_round_trips_through_db(client, no_network):
    client.post("/settings/hf-token", json={"token": "hf_secrettoken12345"})
    # a fresh read decrypts from disk
    assert hf_token.resolve_token() == "hf_secrettoken12345"
    state = client.get("/settings/hf-token").json()
    assert next(s for s in state["sources"] if s["source"] == "app")["set"] is True


def test_clear_token(client, no_network):
    client.post("/settings/hf-token", json={"token": "hf_tobedeleted0000"})
    client.delete("/settings/hf-token")
    assert hf_token.resolve_token() is None


def test_empty_token_is_400(client):
    assert client.post("/settings/hf-token", json={"token": "   "}).status_code == 400


def test_env_token_is_an_override(client, monkeypatch, no_network):
    monkeypatch.setenv("HF_TOKEN", "hf_envtoken9999")
    state = client.get("/settings/hf-token").json()
    env_src = next(s for s in state["sources"] if s["source"] == "env")
    assert env_src["set"] is True and env_src["masked"] == "hf_…999"
    assert hf_token.resolve_token() == "hf_envtoken9999"


def test_app_token_wins_over_env(client, monkeypatch, no_network):
    monkeypatch.setenv("HF_TOKEN", "hf_envtoken9999")
    client.post("/settings/hf-token", json={"token": "hf_apptoken1111"})
    assert hf_token.resolve_token() == "hf_apptoken1111"


def test_plaintext_write_to_secret_key_rejected(client):
    with pytest.raises(ServiceError):
        settings_store.write("hf_token", "plaintext-leak")


def test_token_endpoints_loopback_gated(client):
    from fastapi.testclient import TestClient

    from app import create_app

    remote = TestClient(create_app(), client=("10.0.0.9", 1))
    assert remote.get("/settings/hf-token").status_code == 403


def test_redaction_keeps_token_out_of_errors(client):
    from app.core.logging import redact

    msg = "failed with token hf_abcdefghijklmnopqrstuvwxyz012345"
    assert "hf_abcdef" not in redact(msg)
    assert "REDACTED" in redact(msg)
