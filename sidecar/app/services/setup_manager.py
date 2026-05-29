"""First-run model gate: presence check + download with SSE progress.

`setup_status()` is a stateless snapshot (readiness is derived from the HF cache,
never persisted — first-run-setup Rule 2). `start_download()` kicks a background
download whose progress flows over `download_stream()` as SSE events. A failed
repo is put in a 60 s cooldown so a button-masher can't stampede the network.

The download itself (`_run_snapshot`) and the cache scan (`_scan_cached_repos`)
are indirected so tests exercise the status/cooldown/event logic without a real
network or multi-GB download.
"""

import logging
import shutil
import threading
import time
from pathlib import Path

from .. import config
from ..core.logging import redact
from ..core.sse_broadcast import Broadcaster, keepalive_stream
from .errors import ServiceError

log = logging.getLogger(__name__)

COOLDOWN_S = 60.0
_MAX_RETRIES = 3

# repo_id -> epoch seconds of last failure (cooldown source).
_last_failure: dict[str, float] = {}
_active: set[str] = set()
_active_lock = threading.Lock()


# Background download thread → async SSE generator. A wider replay buffer than the
# synthesis bus: a download emits many byte-progress events, and a late splash
# should still catch the current phase. (Shared fan-out lives in core.sse_broadcast.)
_bus = Broadcaster(replay_maxlen=50)


def bind_loop(loop) -> None:
    """Called from the app lifespan so the worker thread can publish into the loop."""
    _bus.bind_loop(loop)


def _event(repo_id: str, phase: str, **extra) -> dict:
    base = {"repo_id": repo_id, "filename": "", "downloaded": 0, "total": 0, "pct": 0.0, "phase": phase}
    base.update(extra)
    return base


# ---------------------------------------------------------------------------
# Cache presence + disk
# ---------------------------------------------------------------------------
def _scan_cached_repos() -> dict[str, int]:
    """Map repo_id -> size_on_disk from the HF cache. {} if the cache is empty/absent."""
    try:
        from huggingface_hub import scan_cache_dir

        info = scan_cache_dir()
        return {r.repo_id: int(r.size_on_disk) for r in info.repos}
    except Exception:
        return {}


def _is_cached(repo_id: str, sizes: dict[str, int]) -> bool:
    # Cached only when on-disk size > 0 — a torn/zero-byte snapshot is NOT ready.
    return sizes.get(repo_id, 0) > 0


def _disk_free_gb(start: Path) -> float:
    """Free GB on the volume holding `start`, walking up to the nearest existing
    ancestor. 0.0 on any probe error (so enough_disk is False — fail safe)."""
    probe = start
    for _ in range(40):
        if probe.exists():
            try:
                return round(shutil.disk_usage(str(probe)).free / (1024**3), 2)
            except OSError:
                return 0.0
        if probe.parent == probe:
            break
        probe = probe.parent
    return 0.0


def setup_status() -> dict:
    cache_dir = config.hf_cache_dir()
    sizes = _scan_cached_repos()
    missing = [
        {"repo_id": m["repo_id"], "label": m["label"]}
        for m in config.known_models()
        if not _is_cached(m["repo_id"], sizes)
    ]
    free_gb = _disk_free_gb(Path(cache_dir))
    return {
        "models_ready": len(missing) == 0,
        "missing": missing,
        "hf_cache_dir": cache_dir,
        "disk_free_gb": free_gb,
        "min_free_gb": config.MIN_FREE_GB,
        "enough_disk": free_gb >= config.MIN_FREE_GB,
    }


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------
def _known_repo_ids() -> set[str]:
    return {m["repo_id"] for m in config.known_models()}


def _progress_tqdm_class(repo_id: str):
    """A tqdm-compatible class that re-publishes HF's download progress as our
    DownloadEvents (downloaded/total/pct populated). Returns None if tqdm isn't
    importable, so `_run_snapshot` degrades to phase-only events."""
    try:
        from tqdm.auto import tqdm as _tqdm
    except Exception:
        return None

    class _PublishingTqdm(_tqdm):  # type: ignore[misc, valid-type]
        # HF instantiates one bar per file; we forward absolute byte counts on each
        # update so the splash can show real progress, not just "resolving".
        def update(self, n=1):
            ret = super().update(n)
            total = int(self.total or 0)
            done = int(self.n or 0)
            pct = round(done / total, 4) if total > 0 else 0.0
            _bus.publish(
                _event(
                    repo_id,
                    "progress",
                    filename=str(getattr(self, "desc", "") or ""),
                    downloaded=done,
                    total=total,
                    pct=pct,
                )
            )
            return ret

    return _PublishingTqdm


def _run_snapshot(repo_id: str) -> None:
    """Blocking HF snapshot download (indirected for tests). Symlink-free on
    Windows so it works without the symlink privilege (first-run-setup §7).

    Wires a publishing tqdm_class so progress flows as incremental DownloadEvents;
    if the hook is unavailable the download still completes (phase events only)."""
    from huggingface_hub import snapshot_download

    from . import hf_token

    kwargs: dict = {
        "repo_id": repo_id,
        "token": hf_token.resolve_token(),
        "local_dir_use_symlinks": False,
    }
    tqdm_cls = _progress_tqdm_class(repo_id)
    if tqdm_cls is not None:
        kwargs["tqdm_class"] = tqdm_cls
    snapshot_download(**kwargs)


def _download_worker(repo_id: str) -> None:
    _bus.publish(_event(repo_id, "install_start"))
    _bus.publish(_event(repo_id, "resolving"))
    try:
        last_error: Exception | None = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                _run_snapshot(repo_id)
                last_error = None
                break
            except Exception as e:  # transient network/OSError → backoff + retry
                last_error = e
                if attempt < _MAX_RETRIES:
                    _bus.publish(
                        _event(repo_id, "install_retry", attempt=attempt, error=redact(str(e)))
                    )
                    time.sleep(min(2**attempt, 8))
        if last_error is not None:
            raise last_error
        _bus.publish(_event(repo_id, "progress", pct=1.0))
        _bus.publish(_event(repo_id, "install_done", pct=1.0))
    except Exception as e:
        _last_failure[repo_id] = time.time()
        _bus.publish(_event(repo_id, "install_error", error=redact(str(e))))
        log.warning("Model download failed for %s: %s", repo_id, redact(str(e)))
    finally:
        with _active_lock:
            _active.discard(repo_id)


def start_download(repo_id: str) -> dict:
    if repo_id not in _known_repo_ids():
        raise ServiceError(400, f"Unknown model repo: '{repo_id}'.")

    # Cooldown after a recent failure (Rule 8).
    last = _last_failure.get(repo_id)
    if last is not None:
        remaining = COOLDOWN_S - (time.time() - last)
        if remaining > 0:
            raise ServiceError(429, f"That download just failed — retry in {int(remaining) + 1}s.")

    # Already fully cached → no-op that immediately reports done (Rule 4).
    if _is_cached(repo_id, _scan_cached_repos()):
        _bus.publish(_event(repo_id, "install_done", pct=1.0))
        return {"status": "download_started", "repo_id": repo_id}

    with _active_lock:
        if repo_id not in _active:
            _active.add(repo_id)
            threading.Thread(
                target=_download_worker, args=(repo_id,), daemon=True
            ).start()
    return {"status": "download_started", "repo_id": repo_id}


def _is_terminal(event: dict) -> bool:
    # A download ends on install_done/install_error; close the stream after one so a
    # leaked splash client can't keep the generator + queue alive past the download.
    return event.get("phase") in ("install_done", "install_error")


def download_stream():
    """Async generator of SSE byte chunks: one `data:` line per progress event,
    `: keepalive` on idle (~30 s) so proxies don't drop the stream, and STOP after
    a terminal `install_done`/`install_error`. (Shared fan-out + cleanup helper.)"""
    return keepalive_stream(_bus, is_terminal=_is_terminal)


def _reset_for_tests() -> None:
    _last_failure.clear()
    with _active_lock:
        _active.clear()
