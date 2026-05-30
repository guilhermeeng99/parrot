"""First-run model gate: presence check + download with SSE progress.

`setup_status()` is a stateless snapshot (readiness is derived from the HF cache,
never persisted — first-run-setup Rule 2). `start_download()` kicks a background
download whose progress flows over `download_stream()` as SSE events. A failed
repo is put in a 60 s cooldown so a button-masher can't stampede the network.

The download choreography (cooldown, retry/backoff, the active set, the SSE bus,
terminal events) is the shared `DownloadOrchestrator` — also used by `transcribe`
for the Whisper checkpoints. Only the OmniVoice-specific bits live here: the HF
cache scan, the disk-space probe, and the snapshot download with its tqdm hook.
The download itself (`_run_snapshot`) and the cache scan (`_scan_cached_repos`)
are indirected so tests exercise the status/cooldown/event logic without a real
network or multi-GB download.
"""

import logging
import shutil

# `threading` / `time` are imported so tests can patch threading.Thread / time.sleep
# on this module to reach the shared worker (the patch is module-global; see
# download_orchestrator). They are otherwise exercised through the orchestrator.
import threading  # noqa: F401  (patched by tests)
import time  # noqa: F401  (patched by tests)
from pathlib import Path

from .. import config
from .download_orchestrator import DownloadOrchestrator

log = logging.getLogger(__name__)


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
# Download (OmniVoice-specific fetch; choreography is the shared orchestrator)
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
            _downloads.publish_progress(
                repo_id,
                filename=str(getattr(self, "desc", "") or ""),
                downloaded=done,
                total=total,
                pct=pct,
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


# The shared download state machine, wired to the OmniVoice repo specifics. The
# fetch/known/present callables are late-bound thunks so tests that monkeypatch
# `_run_snapshot` / `_scan_cached_repos` still take effect inside the worker.
_downloads = DownloadOrchestrator(
    id_key="repo_id",
    known_ids=lambda: _known_repo_ids(),
    is_present=lambda repo_id: _is_cached(repo_id, _scan_cached_repos()),
    fetch=lambda repo_id: _run_snapshot(repo_id),
    unknown_message=lambda repo_id: f"Unknown model repo: '{repo_id}'.",
)

# Module-level aliases preserve the public surface (routers) and the test surface
# (which patches `_bus.publish` and reads `_active` / `_last_failure`). The set/dict
# are the SAME objects the orchestrator mutates, so reads and writes stay in sync.
_bus = _downloads._bus
_active = _downloads._active
_last_failure = _downloads._last_failure
_download_worker = _downloads.worker


def bind_loop(loop) -> None:
    """Called from the app lifespan so the worker thread can publish into the loop."""
    _downloads.bind_loop(loop)


def start_download(repo_id: str) -> dict:
    return _downloads.start(repo_id)


def download_stream():
    """Async generator of SSE byte chunks for the first-run model download."""
    return _downloads.stream()


def _reset_for_tests() -> None:
    _downloads.reset_for_tests()
