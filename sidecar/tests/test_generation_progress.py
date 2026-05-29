"""Unit tests for the synthesis-progress broadcaster (services/generation_progress).

No event loop is bound here, so events land in the replay buffer only — we read
them back via a subscriber's replay queue. This exercises the event shapes, the
sub-1.0 clamp during stepping, and the per-generation reset, with no model.
"""

from app.services import generation_progress as gp


def _drain_recent() -> list[dict]:
    """Subscribe (which replays the recent buffer) and drain it synchronously."""
    q = gp._bus.subscribe()
    out: list[dict] = []
    while not q.empty():
        out.append(q.get_nowait())
    gp._bus.unsubscribe(q)
    return out


def test_begin_resets_stale_events_and_emits_start():
    gp._reset_for_tests()
    gp.report(5, 10)  # stale event from a "previous" generation
    gp.begin(16)
    events = _drain_recent()
    assert events[-1] == {"phase": "start", "step": 0, "total": 16, "pct": 0.0}
    assert all(e["phase"] != "step" for e in events)  # begin() cleared the stale step


def test_report_emits_fraction_and_clamps_below_full():
    gp._reset_for_tests()
    gp.begin(16)
    gp.report(8, 16)
    gp.report(100, 16)  # overshoot (prep/chunk passes) must not exceed the ceiling
    steps = [e for e in _drain_recent() if e["phase"] == "step"]
    assert steps[0]["pct"] == 0.5
    assert steps[-1]["pct"] == 0.97  # _STEP_CEILING — never 1.0 while still stepping


def test_report_guards_zero_total():
    gp._reset_for_tests()
    gp.begin(0)
    gp.report(1, 0)  # would divide by zero without the guard
    steps = [e for e in _drain_recent() if e["phase"] == "step"]
    assert steps[-1]["pct"] == 0.97  # min(1/1, ceiling) with total floored to 1


def test_finish_reaches_full():
    gp._reset_for_tests()
    gp.begin(16)
    gp.finish()
    assert _drain_recent()[-1] == {"phase": "done", "step": 0, "total": 0, "pct": 1.0}


def test_fail_emits_error():
    gp._reset_for_tests()
    gp.begin(16)
    gp.fail()
    assert _drain_recent()[-1]["phase"] == "error"
