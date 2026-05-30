"""Isolated tests for the shared `DownloadOrchestrator` state machine.

`setup_manager` (the OmniVoice first-run snapshot) and `transcribe` (the Whisper
reference checkpoints) both delegate their download dance to one
`DownloadOrchestrator`. The two services' suites (test_setup / test_transcribe)
exercise it only end-to-end and per-service; this suite drives the orchestrator
class directly — unknown-id gate, post-failure cooldown, already-present
short-circuit, retry/backoff, terminal events, replay-buffer reset, and the
concurrent-start lock — so a regression in the SHARED machine is caught here
regardless of which service triggers it.

No `env` fixture: the orchestrator is standalone (no DB, no paths, no torch). The
broadcaster has no bound asyncio loop in these tests, so `publish()` simply lands
each event in the replay buffer (`_bus._recent`) — which is exactly what we
assert against.
"""

import pytest

from app.services import download_orchestrator as mod
from app.services.download_orchestrator import DownloadOrchestrator
from app.services.errors import ServiceError


def make_orch(*, known=("m1", "m2"), present=False, fetch=None):
    """Build an orchestrator over in-memory fakes. `fetch` defaults to a no-op
    success; pass a callable to simulate failures/retries."""
    state = {"present": present}

    def _fetch(id_):
        if fetch is not None:
            fetch(id_)

    orch = DownloadOrchestrator(
        id_key="model",
        known_ids=lambda: set(known),
        is_present=lambda id_: state["present"],
        fetch=_fetch,
        unknown_message=lambda id_: f"Unknown model {id_!r}.",
    )
    return orch, state


def phases(orch):
    """Phases recorded in the (loop-less) broadcaster's replay buffer, in order."""
    return [e["phase"] for e in list(orch._bus._recent)]


class _DummyThread:
    """Stand-in for threading.Thread that records spawns but never runs the worker."""

    instances: list = []

    def __init__(self, *a, **k):
        self.kwargs = k
        _DummyThread.instances.append(self)

    def start(self):
        pass


@pytest.fixture(autouse=True)
def _reset_dummy_thread():
    _DummyThread.instances = []
    yield


# ---------------------------------------------------------------------------
# start(): gates + short-circuit + worker scheduling
# ---------------------------------------------------------------------------
def test_start_unknown_id_is_400():
    orch, _ = make_orch()
    with pytest.raises(ServiceError) as ei:
        orch.start("nope")
    assert ei.value.status_code == 400
    assert "nope" in ei.value.detail


def test_start_present_short_circuits_to_done(monkeypatch):
    orch, _ = make_orch(present=True)
    monkeypatch.setattr(mod.threading, "Thread", _DummyThread)

    res = orch.start("m1")

    assert res == {"status": "download_started", "model": "m1"}
    assert _DummyThread.instances == []  # no worker thread when already present
    assert phases(orch) == ["install_done"]  # reset, then a single terminal event
    assert "m1" not in orch._active


def test_start_absent_spawns_one_worker(monkeypatch):
    orch, _ = make_orch(present=False)
    monkeypatch.setattr(mod.threading, "Thread", _DummyThread)

    res = orch.start("m1")

    assert res == {"status": "download_started", "model": "m1"}
    assert len(_DummyThread.instances) == 1
    assert "m1" in orch._active


def test_start_duplicate_active_does_not_spawn_second(monkeypatch):
    orch, _ = make_orch(present=False)
    monkeypatch.setattr(mod.threading, "Thread", _DummyThread)
    orch._active.add("m1")  # a download is already in flight

    orch.start("m1")

    assert _DummyThread.instances == []  # the active-set lock blocks a 2nd worker


def test_start_resets_replay_buffer(monkeypatch):
    """A PRIOR model's terminal event lingering in the replay buffer must be
    cleared by the next start(), so the next subscriber can't replay it."""
    orch, _ = make_orch(present=True)
    orch._bus.publish({"model": "stale", "phase": "install_done", "pct": 1.0})
    assert any(e.get("model") == "stale" for e in list(orch._bus._recent))

    monkeypatch.setattr(mod.threading, "Thread", _DummyThread)
    orch.start("m2")

    assert all(e.get("model") != "stale" for e in list(orch._bus._recent))


# ---------------------------------------------------------------------------
# worker(): event sequence + retry/backoff + cooldown
# ---------------------------------------------------------------------------
def test_worker_success_event_sequence():
    orch, _ = make_orch()  # fetch is a no-op success
    orch.worker("m1")

    p = phases(orch)
    assert p[0] == "install_start"
    assert "resolving" in p
    assert p[-1] == "install_done"
    assert "install_error" not in p
    assert all(e["model"] == "m1" for e in list(orch._bus._recent))


def test_worker_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)  # skip the real backoff
    calls = {"n": 0}

    def flaky(id_):
        calls["n"] += 1
        if calls["n"] < 3:
            raise OSError("transient network blip")

    orch, _ = make_orch(fetch=flaky)
    orch.worker("m1")

    p = phases(orch)
    assert p.count("install_retry") == 2  # attempts 1 and 2 failed, then retried
    assert p[-1] == "install_done"  # attempt 3 succeeded
    assert "m1" not in orch._last_failure  # success arms no cooldown


def test_worker_exhausts_retries_and_arms_cooldown(monkeypatch):
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)

    def boom(id_):
        raise OSError("network down")

    orch, _ = make_orch(fetch=boom)
    orch.worker("m1")

    assert phases(orch)[-1] == "install_error"
    assert "m1" in orch._last_failure  # cooldown armed for the next start()

    # A start() inside the cooldown window is rejected 429.
    with pytest.raises(ServiceError) as ei:
        orch.start("m1")
    assert ei.value.status_code == 429


def test_cooldown_expires_allows_retry(monkeypatch):
    orch, _ = make_orch(present=False)
    orch._last_failure["m1"] = 1.0  # epoch ~1970 → far outside COOLDOWN_S
    monkeypatch.setattr(mod.threading, "Thread", _DummyThread)

    res = orch.start("m1")  # no ServiceError — the cooldown has elapsed

    assert res["status"] == "download_started"
    assert "m1" in orch._active  # worker was scheduled, not rejected


# ---------------------------------------------------------------------------
# event shaping + terminal predicate
# ---------------------------------------------------------------------------
def test_event_carries_the_configured_id_key():
    orch, _ = make_orch()
    ev = orch.event("m1", "progress", downloaded=5, total=10)
    assert ev["model"] == "m1"  # id_key is "model"
    assert ev["phase"] == "progress"
    assert ev["downloaded"] == 5 and ev["total"] == 10


def test_publish_progress_shape():
    orch, _ = make_orch()
    orch.publish_progress("m1", filename="x.bin", downloaded=3, total=12, pct=0.25)
    ev = list(orch._bus._recent)[-1]
    assert ev["phase"] == "progress" and ev["model"] == "m1"
    assert ev["filename"] == "x.bin" and ev["pct"] == 0.25


def test_is_terminal_predicate():
    orch, _ = make_orch()
    assert orch._is_terminal({"phase": "install_done"})
    assert orch._is_terminal({"phase": "install_error"})
    assert not orch._is_terminal({"phase": "progress"})
    assert not orch._is_terminal({"phase": "install_start"})
