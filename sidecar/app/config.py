"""Sidecar runtime config + environment bootstrap.

Host is loopback-only (the security boundary — see docs/specs/architecture.md).
Port defaults to 3900 and is overridable via PARROT_PORT so the Rust supervisor
and the sidecar always agree on the same value.

This module also owns the Windows HF-cache path-length workaround
(docs/specs/first-run-setup.md §7): it must run before any huggingface_hub
import reads the cache location, so `prepare_environment()` is called at the very
top of `app.create_app()` / `main.py`.
"""

import os
import sys
from pathlib import Path

HOST = "127.0.0.1"

# Minimum free disk (GB) the first-run model download needs (first-run-setup §2).
MIN_FREE_GB = 10

# The model repos Parrot knows how to download. `repo_id` is the only thing the
# download endpoint accepts (validated against this catalog). The concrete repo
# is configuration, not contract (first-run-setup §2). Override the default repo
# with PARROT_MODEL_REPO for testing against a different/ungated mirror.
DEFAULT_MODEL_REPO = os.environ.get("PARROT_MODEL_REPO", "k2-fsa/OmniVoice")


def known_models() -> list[dict[str, str]]:
    """Catalog of downloadable models. Single entry — Parrot ships one engine."""
    return [{"repo_id": DEFAULT_MODEL_REPO, "label": "OmniVoice voice model"}]


def port() -> int:
    """The port to bind. Env override keeps the supervisor + sidecar in sync.

    Out-of-range values fall back to 3900 to match the Rust supervisor's u16
    parse (`resolve_port`), so the two processes never bind different ports.
    """
    raw = os.environ.get("PARROT_PORT", "3900")
    try:
        value = int(raw)
    except ValueError:
        return 3900
    return value if 1 <= value <= 65535 else 3900


# Origins allowed to call the sidecar: the Vite dev server and the Tauri WebView.
CORS_ORIGINS = [
    "http://localhost:3901",
    "http://127.0.0.1:3901",
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
]


def _local_app_data() -> Path:
    """Best-effort %LOCALAPPDATA% (Windows) with a cross-platform fallback so the
    sidecar still runs under `uv run` / tests on a dev box without that env var."""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if base:
        return Path(base)
    return Path.home() / ".local" / "share"


def prepare_environment() -> None:
    """Set process env that MUST be in place before huggingface_hub is imported.

    Windows HF-cache path-length fix (first-run-setup §7): the default HF layout
    (`models--org--name/snapshots/<hash>/<file>`) routinely blows past the legacy
    260-char MAX_PATH on NTFS. Redirect the cache to a short path
    (`%LOCALAPPDATA%\\Parrot\\hf_cache`, ~40 chars) and disable symlinks so it
    works on accounts without the symlink privilege.

    Respects explicit overrides: if the user already set HF_HOME / HF_HUB_CACHE,
    the redirect is a no-op (first-run-setup Rule 7).
    """
    # Symlink-free cache works on filesystems/accounts without symlink rights.
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")

    if os.environ.get("HF_HOME") or os.environ.get("HF_HUB_CACHE"):
        return  # explicit user override wins — do not redirect.

    if sys.platform != "win32":
        return  # the path-length limit is a Windows-only problem.

    cache = _local_app_data() / "Parrot" / "hf_cache"
    try:
        cache.mkdir(parents=True, exist_ok=True)
    except OSError:
        return  # can't create it — leave HF defaults rather than breaking boot.
    os.environ["HF_HOME"] = str(cache)
    os.environ["HF_HUB_CACHE"] = str(cache)


def hf_cache_dir() -> str:
    """The absolute path model weights download into, resolved the same way the HF
    library resolves it: HF_HUB_CACHE → HF_HOME/hub → ~/.cache/huggingface/hub."""
    explicit = os.environ.get("HF_HUB_CACHE")
    if explicit:
        return str(Path(explicit))
    home = os.environ.get("HF_HOME")
    if home:
        return str(Path(home) / "hub")
    return str(Path.home() / ".cache" / "huggingface" / "hub")
