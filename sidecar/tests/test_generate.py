"""Synthesis path: headers, history row, validation, presets, OOM, WS."""

import io
import json
import threading
import wave

import numpy as np

from app.services import generation_progress as gp
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
    from tests.conftest import FakeBackend

    class OOMBackend:
        sampling_rate = 24000

        def synthesize(self, text, **kw):
            raise RuntimeError("CUDA out of memory")

    # Spy on flush so we can prove the OOM path reloads the model (synthesis.md
    # Rule 10), then chain through to the real flush so _model is actually cleared.
    flushed: list[bool] = []
    real_flush = model_manager.flush
    monkeypatch.setattr(
        model_manager, "flush", lambda: (flushed.append(True), real_flush())[1]
    )

    model_manager._set_for_tests(OOMBackend())
    res = client.post("/generate", data={"text": "boom"})
    assert res.status_code == 500
    assert "out of memory" in res.json()["detail"].lower()
    assert flushed == [True]  # the model was flushed during OOM

    # Recovery: with a healthy backend reinstalled, the next /generate succeeds.
    model_manager._set_for_tests(FakeBackend())
    ok = client.post("/generate", data={"text": "recovered"})
    assert ok.status_code == 200
    assert ok.headers["content-type"].startswith("audio/wav")


def test_generate_drives_progress_bus_start_step_done(client, monkeypatch):
    """A real POST /generate must drive the progress bus through start → step(s) →
    done (A9): the FakeBackend now calls progress_cb once per diffusion step, so
    begin/report/finish fan out as real events."""
    events: list[dict] = []
    real_publish = gp._bus.publish
    monkeypatch.setattr(gp._bus, "publish", lambda e: (events.append(e), real_publish(e))[1])

    res = client.post("/generate", data={"text": "progress please", "num_step": 4})
    assert res.status_code == 200

    phases = [e["phase"] for e in events]
    assert phases[0] == "start"
    assert phases[-1] == "done"
    assert phases.count("step") == 4  # one per diffusion step
    # start carries the total; done carries it too (A6), never resetting to 0.
    assert events[0]["total"] == 4
    assert events[-1]["total"] == 4 and events[-1]["pct"] == 1.0


def test_overlapping_generations_do_not_corrupt_each_others_progress(client, monkeypatch):
    """A1 regression: two generations racing on the single shared progress bus must
    NOT interleave. tts_backend serializes inference under _run_lock, so each run's
    begin→steps→done is a contiguous, uninterrupted block on the bus.

    To actually exercise the race we (a) give the GPU executor 2 workers (a CUDA
    box gets up to 4; the CPU default is 1, which would mask the bug by serializing
    in the pool itself) and (b) use a backend that sleeps between progress steps so
    two concurrent runs genuinely interleave their report() calls on the one bus
    when unserialized. With _run_lock the recorded stream splits cleanly into two
    contiguous runs; without it the steps interleave and the per-run blocks break."""
    import time as _time
    from concurrent.futures import ThreadPoolExecutor

    from app.services import tts_backend

    pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="test-gpu")
    monkeypatch.setattr(tts_backend, "gpu_pool", lambda: pool)

    class SlowSteppingBackend:
        sampling_rate = 24000

        def synthesize(self, text, *, seed=None, **kw):
            progress_cb = kw.get("progress_cb")
            num_step = int(kw.get("num_step", 16) or 16)
            for i in range(num_step):
                _time.sleep(0.01)  # widen the window so unserialized runs interleave
                if progress_cb is not None:
                    progress_cb(i + 1, num_step)
            return np.zeros(2400, dtype=np.float32)

    model_manager._set_for_tests(SlowSteppingBackend())

    events: list[dict] = []
    lock = threading.Lock()
    real_publish = gp._bus.publish

    def _record(e: dict) -> None:
        with lock:
            events.append(dict(e))
        real_publish(e)

    monkeypatch.setattr(gp._bus, "publish", _record)

    def hammer_ws() -> None:
        with client.websocket_connect("/ws/tts") as ws:
            ws.send_json({"text": "concurrent stream", "num_step": 5})
            while True:
                m = ws.receive()
                if m.get("text") is not None:
                    if json.loads(m["text"])["type"] == "done":
                        return

    t = threading.Thread(target=hammer_ws)
    t.start()
    res = client.post("/generate", data={"text": "concurrent post", "num_step": 5})
    t.join()
    assert res.status_code == 200

    # Split the event stream at each `start`; with serialization each run is a
    # contiguous start → step* → done block (never start,start,…interleaved).
    runs: list[list[str]] = []
    for e in events:
        if e["phase"] == "start":
            runs.append([])
        if runs:
            runs[-1].append(e["phase"])
    assert len(runs) == 2  # two generations ran
    for run in runs:
        assert run[0] == "start"
        assert run[-1] == "done"
        assert run.count("start") == 1  # no second run's start bled into this block
        assert run.count("done") == 1
        assert run.count("step") == 5  # exactly this run's steps, no foreign ones


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


def test_ws_bad_typed_field_errors_and_keeps_socket_open(client):
    """A field with the wrong type fails pydantic validation, so the request is a
    recoverable inline {type:error} frame (not a socket-closing exception). The
    socket must stay open for the next, valid request (conversational mode)."""
    with client.websocket_connect("/ws/tts") as ws:
        # `seed` is `int | None`; a non-numeric string can't coerce → ValidationError.
        ws.send_json({"text": "typed wrong", "seed": "not-a-number"})
        err = ws.receive_json()
        assert err["type"] == "error"
        assert err["detail"]  # carries the field location/message
        # socket still usable after the validation error
        ws.send_json({"text": "now valid"})
        assert ws.receive_json()["type"] == "start"
