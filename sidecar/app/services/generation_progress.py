"""Per-step synthesis progress over SSE (synthesis.md §Progress).

`omnivoice` exposes no progress callback, but its diffusion sampler calls the
model's forward pass exactly `num_step` times per generation. `tts_backend`
counts those calls (via the adapter's `progress_cb`) and reports the fraction
here; the Speak UI subscribes to `GET /generate/progress-stream` and shows a real
%-complete bar instead of an indeterminate spinner.

Parrot is a single-user desktop app — one generation runs at a time (the Speak
button is disabled while busy, and `tts_backend.run` serializes inference) — so
progress is broadcast to every subscriber (the page's one progress bar). The
fan-out itself lives in the shared `core.sse_broadcast.Broadcaster`, also used by
`setup_manager`'s download bus: a worker thread publishes into the event loop via
`call_soon_threadsafe`, and an async generator fans events out as SSE.

The reported `pct` is clamped below 1.0 during stepping and only reaches 1.0 on
the explicit `done` event, so the bar never shows "100%" while the tail work
(token decode + DSP + WAV encode, which is not step-granular) is still running.
"""

import logging

from ..core.sse_broadcast import Broadcaster, keepalive_stream

log = logging.getLogger(__name__)

# The step loop can briefly overshoot num_step (a duration/prep forward pass or
# long-text chunking adds a few calls), so hold the bar just under full until the
# explicit done event. Keeps "Generating… 100%" from showing while still working.
_STEP_CEILING = 0.97

# Small replay buffer so a subscriber that connects a beat after `begin()` still
# sees the current phase; cleared on each new generation via `begin()` → reset().
_bus = Broadcaster(replay_maxlen=4)

# Last total/step a subscriber could have seen (set on begin/report). finish()/
# fail() publish these instead of 0,0 so a terminal event keeps the bar's total.
_last_total = 0
_last_step = 0


def bind_loop(loop) -> None:
    """Called from the app lifespan so worker threads can publish into the loop."""
    _bus.bind_loop(loop)


def _event(phase: str, step: int = 0, total: int = 0, pct: float = 0.0) -> dict:
    return {"phase": phase, "step": step, "total": total, "pct": pct}


def begin(total_steps: int) -> None:
    """Start of a generation. Clears any stale events and publishes phase=start."""
    global _last_total, _last_step
    _bus.reset()
    _last_total = max(0, int(total_steps))
    _last_step = 0
    _bus.publish(_event("start", step=0, total=_last_total, pct=0.0))


def report(step: int, total: int) -> None:
    """One diffusion step done (called from the GPU worker thread). Publishes the
    clamped fraction so the bar advances with real model work."""
    global _last_total, _last_step
    total = max(1, int(total))
    _last_total = total
    _last_step = int(step)
    pct = round(min(step / total, _STEP_CEILING), 4)
    _bus.publish(_event("step", step=_last_step, total=total, pct=pct))


def finish() -> None:
    """Generation completed — the bar fills to 100%, carrying the last known total."""
    _bus.publish(_event("done", step=_last_step, total=_last_total, pct=1.0))


def fail() -> None:
    """Generation errored — let a subscribed bar stop spinning (the POST also 500s).
    Carries the last known total so the terminal event doesn't reset it to 0."""
    _bus.publish(_event("error", step=_last_step, total=_last_total, pct=0.0))


def _is_terminal(event: dict) -> bool:
    # done/error are terminal — the stream closes after one so a leaked client can't
    # keep the generator + its queue alive forever past the end of the generation.
    return event.get("phase") in ("done", "error")


def progress_stream():
    """Async generator of SSE byte chunks for the in-flight generation: one `data:`
    line per event, `: keepalive` on idle (~30 s), and STOP after a terminal
    `done`/`error`. Cleanup (unsubscribe) is handled by the shared helper."""
    return keepalive_stream(_bus, is_terminal=_is_terminal)


def _reset_for_tests() -> None:
    global _last_total, _last_step
    _last_total = 0
    _last_step = 0
    _bus.reset()
