"""Reusable worker-thread → async-SSE fan-out (the one progress-broadcast shape).

Both the synthesis-progress bus (`services/generation_progress`) and the
model-download bus (`services/setup_manager`) need the same thing: a blocking
worker thread publishes events, and one-or-more async SSE generators (the
WebView's progress bars) consume them. This module is that shared primitive so
the two services don't each carry a near-verbatim private copy.

Threading model (deliberate split, do not "simplify" away):
  - `publish()` runs on a worker thread (the GPU executor / the HF download
    thread). It only touches the loop via `loop.call_soon_threadsafe`, never
    the asyncio.Queue directly — that's the one thread-safe bridge into the loop.
  - `subscribe()` / `unsubscribe()` run on the asyncio loop (inside the SSE
    request handler), so mutating the subscriber set there needs no async lock.
  - The subscriber set + replay deque are guarded by a plain `threading.Lock`
    because `publish` (worker thread) and `subscribe` (loop thread) both read/
    mutate them and the GIL alone doesn't make the multi-step set/deque ops
    atomic.

Each subscriber queue is BOUNDED (`_QUEUE_MAXSIZE`) and coalesces on overflow by
dropping the OLDEST queued event — a stalled or slow SSE consumer therefore can't
grow its queue without bound, and the latest progress (the only %-that-matters)
always survives.
"""

import asyncio
import json
import logging
import threading
from collections import deque

log = logging.getLogger(__name__)

# Per-subscriber queue cap. Progress events are tiny and coalescible (latest pct
# wins), so a small cap is plenty; on overflow we drop the oldest, never block the
# publishing worker thread.
_QUEUE_MAXSIZE = 256

# Idle interval between SSE keepalive comment lines, so proxies/WebView don't drop
# an otherwise-silent stream.
_KEEPALIVE_S = 30.0


class Broadcaster:
    """Fan-out of worker-thread events to async SSE subscribers.

    `replay_maxlen` sizes the replay buffer a late subscriber receives on connect
    (so a bar that opens a beat after the run starts still sees the current phase).
    """

    def __init__(self, replay_maxlen: int) -> None:
        self._subs: set[asyncio.Queue] = set()
        self._recent: deque = deque(maxlen=replay_maxlen)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Bind the asyncio loop worker threads publish into (app lifespan)."""
        self._loop = loop

    def reset(self) -> None:
        """Drop replayable events from a previous run so a fresh subscriber can't
        replay stale progress."""
        with self._lock:
            self._recent.clear()

    def publish(self, event: dict) -> None:
        """Fan `event` out to every subscriber (called from a worker thread)."""
        with self._lock:
            self._recent.append(event)
            subs = list(self._subs)
        loop = self._loop
        if loop is None:
            return
        for q in subs:
            try:
                loop.call_soon_threadsafe(self._offer, q, event)
            except RuntimeError:
                pass  # loop closed; subscriber is going away anyway

    @staticmethod
    def _offer(q: asyncio.Queue, event: dict) -> None:
        """Enqueue on the loop thread, coalescing on overflow by dropping the
        oldest event — keeps a stalled consumer's queue bounded (latest pct wins)."""
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            try:
                q.get_nowait()  # drop oldest
            except asyncio.QueueEmpty:
                pass
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass  # racing consumer refilled it; the next publish will catch up

    def subscribe(self) -> asyncio.Queue:
        """Register a subscriber, pre-seeded with the replay buffer (loop thread)."""
        q: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        with self._lock:
            for event in list(self._recent):  # replay so a late client sees the phase
                self._offer(q, event)
            self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        """Drop a subscriber (loop thread; runs in the SSE generator's finally)."""
        with self._lock:
            self._subs.discard(q)


async def keepalive_stream(broadcaster: Broadcaster, is_terminal=None):
    """Async generator of SSE byte chunks for one subscriber: one `data: {json}`
    line per event, a `: keepalive` comment on idle (~30 s) so proxies don't drop
    the stream, and `unsubscribe` in `finally` so a closed client is cleaned up.

    If `is_terminal(event)` is provided and returns True for an event, the stream
    yields that event then RETURNS — so a leaked/abandoned client can't keep the
    generator (and its queue) alive forever after the run is already done.
    """
    q = broadcaster.subscribe()
    try:
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=_KEEPALIVE_S)
            except asyncio.TimeoutError:
                yield b": keepalive\n\n"
                continue
            yield f"data: {json.dumps(event)}\n\n".encode("utf-8")
            if is_terminal is not None and is_terminal(event):
                return
    finally:
        broadcaster.unsubscribe(q)
