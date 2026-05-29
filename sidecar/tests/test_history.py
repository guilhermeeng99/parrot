"""History list/delete/clear + on-disk WAV cleanup."""

from app.core import paths


def _gen(client, text="hi"):
    return client.post("/generate", data={"text": text}).headers["X-Audio-Id"]


def test_history_newest_first(client):
    _gen(client, "first")
    _gen(client, "second")
    rows = client.get("/history").json()
    assert [r["text"] for r in rows] == ["second", "first"]


def test_delete_one_removes_row_and_file(client, env):
    aid = _gen(client, "delete me")
    wav = paths.outputs_dir() / f"{aid}.wav"
    assert wav.exists()
    assert client.delete(f"/history/{aid}").json() == {"deleted": True}
    assert not wav.exists()
    assert client.get("/history").json() == []


def test_delete_missing_is_ok(client):
    assert client.delete("/history/ffffffff").json() == {"deleted": True}


def test_history_audio_serves_wav(client):
    aid = _gen(client, "play me back")
    res = client.get(f"/history/{aid}/audio")
    assert res.status_code == 200
    assert res.headers["content-type"] == "audio/wav"


def test_history_audio_missing_row_is_404(client):
    assert client.get("/history/ffffffff/audio").status_code == 404


def test_clear_all(client):
    _gen(client, "a")
    _gen(client, "b")
    assert client.delete("/history").json() == {"cleared": True}
    assert client.get("/history").json() == []
