"""Reference transcription: catalog/status, download state machine, transcribe.

The ASR engine boundary (`asr_manager.transcribe`) is monkeypatched, so this
suite needs no torch/whisper/av and no model download — exactly like the rest of
the engine suite mocks the TTS boundary (transcription.md ENG-5).
"""

import pytest

from app.core import paths
from app.services import asr_manager
from app.services import transcribe as svc
from app.services.errors import ServiceError


def _make_model(model_id: str) -> None:
    """Drop a non-empty fake `.pt` so `_is_present(model_id)` is True."""
    paths.whisper_models_dir().joinpath(f"{model_id}.pt").write_bytes(b"fake-weights")


# ---------------------------------------------------------------------------
# Catalog / status / language mapping
# ---------------------------------------------------------------------------
def test_default_model_is_in_catalog(env):
    ids = {m["id"] for m in svc.MODELS}
    assert svc.DEFAULT_MODEL in ids
    assert svc.DEFAULT_MODEL == "large-v3"


def test_status_shape_nothing_downloaded(env):
    st = svc.status()
    assert st["default_model"] == "large-v3"
    assert st["device"] in ("cuda", "cpu")
    assert st["gpu"] == (st["device"] == "cuda")
    assert {m["id"] for m in st["models"]} == {m["id"] for m in svc.MODELS}
    assert all(m["downloaded"] is False for m in st["models"])  # fresh data dir


def test_status_marks_downloaded_model(env):
    _make_model("large-v3")
    st = svc.status()
    by_id = {m["id"]: m for m in st["models"]}
    assert by_id["large-v3"]["downloaded"] is True
    assert by_id["small"]["downloaded"] is False


def test_status_via_api(client):
    res = client.get("/transcribe/status")
    assert res.status_code == 200
    body = res.json()
    assert body["default_model"] == "large-v3"
    assert isinstance(body["models"], list) and body["models"]


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Auto", None),
        ("", None),
        (None, None),
        ("Portuguese", "pt"),
        ("english", "en"),
        ("Klingon", None),  # unknown → auto-detect
    ],
)
def test_lang_code_mapping(env, name, expected):
    assert svc._lang_code(name) == expected


# ---------------------------------------------------------------------------
# Download state machine (mirrors setup_manager tests; _fetch_model indirected)
# ---------------------------------------------------------------------------
def test_download_unknown_model_is_400(env):
    with pytest.raises(ServiceError) as ei:
        svc.start_download("whisper-9000")
    assert ei.value.status_code == 400


def test_download_noop_when_already_present(env, monkeypatch):
    _make_model("large-v3")

    spawned: list = []
    monkeypatch.setattr(svc.threading, "Thread", lambda *a, **k: spawned.append((a, k)))
    events: list[dict] = []
    monkeypatch.setattr(svc._bus, "publish", lambda e: events.append(e))

    res = svc.start_download("large-v3")

    assert res == {"status": "download_started", "model": "large-v3"}
    assert spawned == []  # no background thread
    assert "large-v3" not in svc._active
    assert [e["phase"] for e in events] == ["install_done"]
    assert events[0]["model"] == "large-v3" and events[0]["pct"] == 1.0


def test_download_worker_emits_event_sequence(env, monkeypatch):
    events: list[dict] = []
    monkeypatch.setattr(svc._bus, "publish", lambda e: events.append(e))
    monkeypatch.setattr(svc, "_fetch_model", lambda model_id: None)

    svc._download_worker("medium")

    phases = [e["phase"] for e in events]
    assert phases[0] == "install_start"
    assert "resolving" in phases
    assert phases[-1] == "install_done"
    assert all(e["model"] == "medium" for e in events)


def test_failed_download_sets_cooldown(env, monkeypatch):
    events: list[dict] = []
    monkeypatch.setattr(svc._bus, "publish", lambda e: events.append(e))
    monkeypatch.setattr(svc.time, "sleep", lambda *a: None)  # skip the backoff

    def boom(model_id):
        raise OSError("network down")

    monkeypatch.setattr(svc, "_fetch_model", boom)
    svc._download_worker("large-v3")

    assert any(e["phase"] == "install_error" for e in events)
    with pytest.raises(ServiceError) as ei:
        svc.start_download("large-v3")
    assert ei.value.status_code == 429


def test_cooldown_expires_allows_retry(env, monkeypatch):
    """After COOLDOWN_S has elapsed, a previously-failed model downloads again —
    the `remaining > 0` gate must flip back to spawning a worker (EDGE-T5)."""
    svc._last_failure["large-v3"] = 1.0  # epoch ~1970: far outside the window

    class _DummyThread:
        def __init__(self, *a, **k):
            pass

        def start(self):
            pass

    monkeypatch.setattr(svc.threading, "Thread", _DummyThread)
    res = svc.start_download("large-v3")  # no ServiceError — cooldown expired
    assert res == {"status": "download_started", "model": "large-v3"}
    assert "large-v3" in svc._active  # the worker was scheduled, not rejected


def test_download_via_api_present_model(client):
    _make_model("small")
    res = client.post("/transcribe/download", json={"model": "small"})
    assert res.status_code == 200
    assert res.json() == {"status": "download_started", "model": "small"}


def test_download_via_api_unknown_model_is_400(client):
    res = client.post("/transcribe/download", json={"model": "nope"})
    assert res.status_code == 400


# ---------------------------------------------------------------------------
# Transcribe (asr_manager boundary mocked)
# ---------------------------------------------------------------------------
def test_transcribe_unknown_model_is_400(env):
    with pytest.raises(ServiceError) as ei:
        svc.transcribe(b"bytes", "ref.wav", "nope", "Auto")
    assert ei.value.status_code == 400


def test_transcribe_unsupported_ext_is_415(env):
    _make_model("large-v3")
    with pytest.raises(ServiceError) as ei:
        svc.transcribe(b"bytes", "ref.txt", "large-v3", "Auto")
    assert ei.value.status_code == 415


def test_transcribe_model_not_downloaded_is_409(env):
    with pytest.raises(ServiceError) as ei:
        svc.transcribe(b"bytes", "ref.wav", "large-v3", "Auto")
    assert ei.value.status_code == 409


def test_transcribe_empty_audio_is_400(env):
    _make_model("large-v3")
    with pytest.raises(ServiceError) as ei:
        svc.transcribe(b"", "ref.wav", "large-v3", "Auto")
    assert ei.value.status_code == 400


def test_transcribe_happy_path(env, monkeypatch):
    _make_model("large-v3")
    seen: dict = {}

    def fake(audio_bytes, *, model_path, device, language):
        seen.update(audio_bytes=audio_bytes, model_path=model_path, device=device, language=language)
        return {"text": "olá mundo", "language": "pt"}

    monkeypatch.setattr(asr_manager, "transcribe", fake)
    out = svc.transcribe(b"webm-bytes", "ref.webm", "large-v3", "Portuguese")

    assert out == {"text": "olá mundo", "language": "pt", "model": "large-v3"}
    assert seen["audio_bytes"] == b"webm-bytes"
    assert seen["language"] == "pt"  # name → ISO code resolved before the boundary
    assert seen["model_path"].name == "large-v3.pt"


def test_transcribe_empty_result_is_not_an_error(env, monkeypatch):
    _make_model("large-v3")
    monkeypatch.setattr(asr_manager, "transcribe", lambda *a, **k: {"text": "", "language": ""})
    out = svc.transcribe(b"silence", "ref.wav", "large-v3", "Auto")
    assert out["text"] == ""  # EDGE-T1: no-speech is a valid result, not a 500


def test_transcribe_missing_engine_is_500(env, monkeypatch):
    _make_model("large-v3")

    def no_engine(*a, **k):
        raise ModuleNotFoundError("No module named 'whisper'")

    monkeypatch.setattr(asr_manager, "transcribe", no_engine)
    with pytest.raises(ServiceError) as ei:
        svc.transcribe(b"bytes", "ref.wav", "large-v3", "Auto")
    assert ei.value.status_code == 500
    assert "engine is not installed" in ei.value.detail.lower()


def test_transcribe_decode_failure_is_500(env, monkeypatch):
    _make_model("large-v3")

    def bad(*a, **k):
        raise ValueError("no audio stream in the file")

    monkeypatch.setattr(asr_manager, "transcribe", bad)
    with pytest.raises(ServiceError) as ei:
        svc.transcribe(b"bytes", "ref.wav", "large-v3", "Auto")
    assert ei.value.status_code == 500
    assert "couldn't read" in ei.value.detail.lower()


def test_transcribe_via_api(client, monkeypatch):
    _make_model("large-v3")
    monkeypatch.setattr(
        asr_manager, "transcribe", lambda *a, **k: {"text": "hello there", "language": "en"}
    )
    res = client.post(
        "/transcribe",
        data={"model": "large-v3", "language": "English"},
        files={"ref_audio": ("ref.wav", b"RIFFfake", "audio/wav")},
    )
    assert res.status_code == 200, res.text
    assert res.json() == {"text": "hello there", "language": "en", "model": "large-v3"}
