"""Unit tests for the synthesis-progress broadcaster (services/generation_progress).

The sync tests bind no event loop, so events land in the replay buffer only — we
read them back via a subscriber's replay queue. This exercises the event shapes,
the sub-1.0 clamp during stepping, and the per-generation reset, with no model.

The async tests (bottom) bind a real running loop to the shared Broadcaster and
drive publish() from a worker thread — the production wiring — to prove the
worker-thread→loop fan-out, SSE framing, terminal close, multi-subscriber
delivery, the no-subscriber path, and bounded-queue overflow. They use
`asyncio.run()` directly so no pytest-asyncio plugin is required.
"""

import asyncio
import json
import threading

from app.core.sse_broadcast import Broadcaster
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
    # A6: the terminal `done` carries the last-known total (16), not 0 — so a
    # subscriber's bar keeps the total it saw on start instead of resetting it.
    assert _drain_recent()[-1] == {"phase": "done", "step": 0, "total": 16, "pct": 1.0}


def test_fail_emits_error():
    gp._reset_for_tests()
    gp.begin(16)
    gp.fail()
    last = _drain_recent()[-1]
    assert last["phase"] == "error"
    assert last["total"] == 16  # A6: carries the last-known total, not 0


# ---------------------------------------------------------------------------
# Async fan-out tests — bind a real loop and publish from a worker thread.
# ---------------------------------------------------------------------------
async def _publish_from_thread(bus: Broadcaster, events: list[dict]) -> None:
    """Publish `events` from a separate thread, the way the GPU worker does."""
    bus.bind_loop(asyncio.get_running_loop())

    def worker() -> None:
        for e in events:
            bus.publish(e)

    t = threading.Thread(target=worker)
    t.start()
    await asyncio.to_thread(t.join)


def test_worker_thread_publish_reaches_subscriber_queue():
    async def scenario():
        bus = Broadcaster(replay_maxlen=4)
        bus.bind_loop(asyncio.get_running_loop())
        q = bus.subscribe()
        await _publish_from_thread(bus, [{"phase": "step", "pct": 0.5}])
        got = await asyncio.wait_for(q.get(), timeout=1.0)
        assert got == {"phase": "step", "pct": 0.5}

    asyncio.run(scenario())


def test_progress_stream_emits_sse_framing_and_unsubscribes_on_close():
    async def scenario():
        gp._reset_for_tests()
        gp.bind_loop(asyncio.get_running_loop())
        stream = gp.progress_stream()
        gp.begin(16)
        gp.report(8, 16)
        gp.finish()  # terminal → the stream returns after emitting it

        chunks: list[bytes] = []
        async for chunk in stream:
            chunks.append(chunk)
            if len(chunks) > 10:  # guard against a hang if terminal-close regresses
                break

        text = b"".join(chunks).decode("utf-8")
        assert text.startswith("data: ")  # SSE framing
        assert "\n\n" in text
        first = json.loads(text.splitlines()[0][len("data: ") :])
        assert first["phase"] == "start"
        assert '"phase": "done"' in text
        # The generator stopped on the terminal event → unsubscribe ran in finally.
        assert not gp._bus._subs

    asyncio.run(scenario())


def test_two_subscribers_both_receive():
    async def scenario():
        bus = Broadcaster(replay_maxlen=4)
        bus.bind_loop(asyncio.get_running_loop())
        q1 = bus.subscribe()
        q2 = bus.subscribe()
        await _publish_from_thread(bus, [{"phase": "step", "pct": 0.25}])
        e1 = await asyncio.wait_for(q1.get(), timeout=1.0)
        e2 = await asyncio.wait_for(q2.get(), timeout=1.0)
        assert e1 == e2 == {"phase": "step", "pct": 0.25}

    asyncio.run(scenario())


def test_publish_with_zero_subscribers_does_not_raise():
    async def scenario():
        bus = Broadcaster(replay_maxlen=4)
        bus.bind_loop(asyncio.get_running_loop())
        await _publish_from_thread(bus, [{"phase": "step", "pct": 0.1}])
        # No subscriber; the event only lands in the replay buffer. A late
        # subscriber then still catches it (replay-on-connect).
        q = bus.subscribe()
        assert q.get_nowait() == {"phase": "step", "pct": 0.1}

    asyncio.run(scenario())


def test_bounded_queue_overflow_drops_oldest_without_raising():
    async def scenario():
        bus = Broadcaster(replay_maxlen=4)
        bus.bind_loop(asyncio.get_running_loop())
        q = bus.subscribe()
        # Publish far more than the queue cap; a stalled consumer (we never drain)
        # must not make publish() raise, and the queue stays bounded by coalescing
        # the oldest away. The LATEST event survives (the pct that matters).
        total = 256 + 50
        await _publish_from_thread(bus, [{"phase": "step", "i": i} for i in range(total)])
        assert q.qsize() <= 256
        drained = [q.get_nowait() for _ in range(q.qsize())]
        assert drained[-1] == {"phase": "step", "i": total - 1}  # newest kept

    asyncio.run(scenario())
