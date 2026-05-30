"""Shared model-download choreography (OmniVoice first-run snapshot + Whisper ASR).

`setup_manager` (the OmniVoice first-run snapshot) and `transcribe` (the Whisper
reference-ASR checkpoints) need the identical download dance: a 60 s post-failure
cooldown, bounded retry/backoff on a daemon worker thread, an "already present →
immediate done" short-circuit, and SSE progress/terminal events fanned out via
`core.sse_broadcast`. This is that one orchestration, parameterized by an id-key,
a presence check, and a blocking fetch callable — so the two services no longer
each carry a near-verbatim private copy (which had already drifted: one emitted a
trailing `progress` event before `install_done` that the other did not).

Each `start()` first RESETS the replay buffer. A previous model's terminal
`install_done` must never be replayed to the NEXT model's SSE subscriber: the
Whisper picker downloads several checkpoints in one session, and without the reset
the second download's stream replayed the first's `install_done`, driving the UI
to a spurious "model file is missing" failure while the real download ran
invisibly. The synthesis bus already resets per run in `generation_progress.begin()`;
this brings downloads in line with that pattern.
"""

import logging
import threading
import time
from collections.abc import Callable

from ..core.logging import redact
from ..core.sse_broadcast import Broadcaster, keepalive_stream
from .errors import ServiceError

log = logging.getLogger(__name__)

COOLDOWN_S = 60.0
_MAX_RETRIES = 3


class DownloadOrchestrator:
    """One download state machine, reused by `setup_manager` and `transcribe`.

    The fetch/presence/known callables are stored as late-bound thunks by the
    callers (e.g. ``fetch=lambda i: _run_snapshot(i)``) so a test that
    monkeypatches the module-level fetch still takes effect here.
    """

    def __init__(
        self,
        *,
        id_key: str,
        known_ids: Callable[[], set[str]],
        is_present: Callable[[str], bool],
        fetch: Callable[[str], None],
        unknown_message: Callable[[str], str],
        replay_maxlen: int = 50,
    ) -> None:
        self._id_key = id_key
        self._known_ids = known_ids
        self._is_present = is_present
        self._fetch = fetch
        self._unknown_message = unknown_message
        self._last_failure: dict[str, float] = {}  # id -> epoch seconds of last failure
        self._active: set[str] = set()
        self._active_lock = threading.Lock()
        self._bus = Broadcaster(replay_maxlen=replay_maxlen)

    # -- SSE plumbing ------------------------------------------------------
    def bind_loop(self, loop) -> None:
        """Called from the app lifespan so the worker thread can publish into the loop."""
        self._bus.bind_loop(loop)

    def event(self, id_: str, phase: str, **extra) -> dict:
        base = {self._id_key: id_, "filename": "", "downloaded": 0, "total": 0, "pct": 0.0, "phase": phase}
        base.update(extra)
        return base

    def publish_progress(
        self, id_: str, *, filename: str = "", downloaded: int = 0, total: int = 0, pct: float = 0.0
    ) -> None:
        """Re-publish a fetch's byte progress as a DownloadEvent — used by the HF
        tqdm hook (snapshot) and the single-file Whisper streamer."""
        self._bus.publish(
            self.event(id_, "progress", filename=filename, downloaded=downloaded, total=total, pct=pct)
        )

    @staticmethod
    def _is_terminal(event: dict) -> bool:
        # A download ends on install_done/install_error; close the stream after one so
        # a leaked client can't keep the generator + queue alive past the download.
        return event.get("phase") in ("install_done", "install_error")

    def stream(self):
        """Async SSE generator: one `data:` line per event, `: keepalive` on idle,
        STOP after a terminal install_done/install_error (shared fan-out helper)."""
        return keepalive_stream(self._bus, is_terminal=self._is_terminal)

    # -- worker ------------------------------------------------------------
    def worker(self, id_: str) -> None:
        self._bus.publish(self.event(id_, "install_start"))
        self._bus.publish(self.event(id_, "resolving"))
        try:
            last_error: Exception | None = None
            for attempt in range(1, _MAX_RETRIES + 1):
                try:
                    self._fetch(id_)
                    last_error = None
                    break
                except Exception as e:  # transient network/OSError → backoff + retry
                    last_error = e
                    if attempt < _MAX_RETRIES:
                        self._bus.publish(
                            self.event(id_, "install_retry", attempt=attempt, error=redact(str(e)))
                        )
                        time.sleep(min(2**attempt, 8))
            if last_error is not None:
                raise last_error
            self._bus.publish(self.event(id_, "progress", pct=1.0))
            self._bus.publish(self.event(id_, "install_done", pct=1.0))
        except Exception as e:
            self._last_failure[id_] = time.time()
            self._bus.publish(self.event(id_, "install_error", error=redact(str(e))))
            log.warning("Model download failed for %s: %s", id_, redact(str(e)))
        finally:
            with self._active_lock:
                self._active.discard(id_)

    # -- start -------------------------------------------------------------
    def start(self, id_: str) -> dict:
        if id_ not in self._known_ids():
            raise ServiceError(400, self._unknown_message(id_))

        last = self._last_failure.get(id_)
        if last is not None:
            remaining = COOLDOWN_S - (time.time() - last)
            if remaining > 0:
                raise ServiceError(429, f"That download just failed — retry in {int(remaining) + 1}s.")

        # Fresh replay buffer per download: a PRIOR model's terminal event must not
        # replay to THIS download's SSE subscriber (would spuriously fail it).
        self._bus.reset()

        if self._is_present(id_):  # already downloaded → immediate done (no thread)
            self._bus.publish(self.event(id_, "install_done", pct=1.0))
            return {"status": "download_started", self._id_key: id_}

        with self._active_lock:
            if id_ not in self._active:
                self._active.add(id_)
                threading.Thread(target=self.worker, args=(id_,), daemon=True).start()
        return {"status": "download_started", self._id_key: id_}

    def reset_for_tests(self) -> None:
        self._last_failure.clear()
        with self._active_lock:
            self._active.clear()
        self._bus.reset()
