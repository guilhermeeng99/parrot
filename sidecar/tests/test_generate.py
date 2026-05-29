"""Synthesis path: headers, history row, validation, presets, OOM, WS."""

import io
import wave

from app.services import model_manager


def test_generate_streams_wav_with_headers(client):
    res = client.post("/generate", data={"text": "hello world"})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("audio/wav")
    for h in ("X-Audio-Id", "X-Gen-Time", "X-Audio-Path", "X-Audio-Duration"):
        assert h in res.headers
    assert res.headers["X-Audio-Path"] == f"{res.headers['X-Audio-Id']}.wav"
    assert int(res.headers["Content-Length"]) == len(res.content)
    # body is a real, parseable WAV at 24 kHz
    with wave.open(io.BytesIO(res.content)) as w:
        assert w.getframerate() == 24000


def test_generate_writes_one_history_row(client):
    client.post("/generate", data={"text": "row please"})
    hist = client.get("/history").json()
    assert len(hist) == 1
    assert hist[0]["text"] == "row please"
    assert hist[0]["language"] == "Auto"


def test_generate_text_truncated_to_200_in_history(client):
    long = "a" * 500
    client.post("/generate", data={"text": long})
    assert len(client.get("/history").json()[0]["text"]) == 200


def test_generate_missing_text_is_400(client):
    assert client.post("/generate", data={}).status_code == 400


def test_generate_unknown_preset_is_400(client):
    res = client.post("/generate", data={"text": "hi", "effect_preset": "bogus"})
    assert res.status_code == 400
    assert "preset" in res.json()["detail"].lower()


def test_generate_seed_echoed_in_header(client):
    res = client.post("/generate", data={"text": "seeded", "seed": 1234})
    assert res.headers["X-Seed"] == "1234"
    assert client.get("/history").json()[0]["seed"] == 1234


def test_generate_no_seed_empty_header(client):
    res = client.post("/generate", data={"text": "no seed"})
    assert res.headers["X-Seed"] == ""


def test_generate_oom_is_recoverable_500(client, monkeypatch):
    class OOMBackend:
        sampling_rate = 24000

        def synthesize(self, text, **kw):
            raise RuntimeError("CUDA out of memory")

    model_manager._set_for_tests(OOMBackend())
    res = client.post("/generate", data={"text": "boom"})
    assert res.status_code == 500
    assert "out of memory" in res.json()["detail"].lower()


def test_ws_streams_pcm(client):
    import json

    with client.websocket_connect("/ws/tts") as ws:
        ws.send_json({"text": "stream me"})
        start = ws.receive_json()
        assert start["type"] == "start" and start["sample_rate"] == 24000
        got_bytes = False
        while True:
            m = ws.receive()
            if m.get("bytes") is not None:
                got_bytes = True
            elif m.get("text") is not None:
                msg = json.loads(m["text"])
                assert msg["type"] == "done"
                assert msg["samples"] > 0
                break
        assert got_bytes


def test_ws_missing_text_keeps_socket_open(client):
    with client.websocket_connect("/ws/tts") as ws:
        ws.send_json({"text": "   "})
        err = ws.receive_json()
        assert err["type"] == "error"
        # socket still usable
        ws.send_json({"text": "now valid"})
        assert ws.receive_json()["type"] == "start"
