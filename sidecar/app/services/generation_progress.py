"""Per-step synthesis progress over SSE (synthesis.md §Progress).

`omnivoice` exposes no progress callback, but its diffusion sampler calls the
model's forward pass exactly `num_step` times per generation. `tts_backend`
counts those calls (via the adapter's `progress_cb`) and reports the fraction
here; the Speak UI subscribes to `GET /generate/progress-stream` and shows a real
%-complete bar instead of an indeterminate spinner.

Parrot is a single-user desktop app — one generation runs at a time (the Speak
button is disabled while busy) — so progress is broadcast to every subscriber
(the page's one progress bar). This mirrors `setup_manager`'s download
broadcaster: a worker thread publishes into the event loop via
`call_soon_threadsafe`, and an async generator fans events out as SSE.

The reported `pct` is clamped below 1.0 during stepping and only reaches 1.0 on
the explicit `done` event, so the bar never shows "100%" while the tail work
(token decode + DSP + WAV encode, which is not step-granular) is still running.
"""

import asyncio
import json
import logging
from collections import deque

log = logging.getLogger(__name__)

# The step loop can briefly overshoot num_step (a duration/prep forward pass or
# long-text chunking adds a few calls), so hold the bar just under full until the
# explicit done event. Keeps "Generating… 100%" from showing while still working.
_STEP_CEILING = 0.97


class _Broadcaster:
    """Worker-thread → async-SSE fan-out. Same shape as setup_manager's."""

    def __init__(self) -> None:
        self._subs: set[asyncio.Queue] = set()
        # Small replay buffer so a subscriber that connects a beat after `begin()`
        # still sees the current phase. Cleared on each new generation.
        self._recent: deque = deque(maxlen=4)
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def reset(self) -> None:
        # Drop stale events from a previous generation so a fresh subscriber can't
        # replay last run's progress.
        self._recent.clear()

    def publish(self, event: dict) -> None:
        self._recent.append(event)
        loop = self._loop
        if loop is None:
            return
        for q in list(self._subs):
            try:
                loop.call_soon_threadsafe(q.put_nowait, event)
            except RuntimeError:
                pass  # loop closed; subscriber is going away anyway

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        for event in list(self._recent):  # replay so a late client sees the phase
            q.put_nowait(event)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)


_bus = _Broadcaster()


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Called from the app lifespan so worker threads can publish into the loop."""
    _bus.bind_loop(loop)


def _event(phase: str, step: int = 0, total: int = 0, pct: float = 0.0) -> dict:
    return {"phase": phase, "step": step, "total": total, "pct": pct}


def begin(total_steps: int) -> None:
    """Start of a generation. Clears any stale events and publishes phase=start."""
    _bus.reset()
    _bus.publish(_event("start", step=0, total=max(0, int(total_steps)), pct=0.0))


def report(step: int, total: int) -> None:
    """One diffusion step done (called from the GPU worker thread). Publishes the
    clamped fraction so the bar advances with real model work."""
    total = max(1, int(total))
    pct = round(min(step / total, _STEP_CEILING), 4)
    _bus.publish(_event("step", step=int(step), total=total, pct=pct))


def finish() -> None:
    """Generation completed — the bar fills to 100%."""
    _bus.publish(_event("done", pct=1.0))


def fail() -> None:
    """Generation errored — let a subscribed bar stop spinning (the POST also 500s)."""
    _bus.publish(_event("error", pct=0.0))


async def progress_stream():
    """Async generator of SSE byte chunks: one `data:` line per event, `: keepalive`
    on idle (~30 s) so proxies don't drop the stream. Mirrors setup_manager."""
    q = _bus.subscribe()
    try:
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=30.0)
                yield f"data: {json.dumps(event)}\n\n".encode("utf-8")
            except asyncio.TimeoutError:
                yield b": keepalive\n\n"
    finally:
        _bus.unsubscribe(q)


def _reset_for_tests() -> None:
    _bus.reset()
