"""On-disk layout for Parrot's durable state.

The Python sidecar is the *sole* owner of `parrot_data/` (architecture.md §7).
Everything here resolves the data dir fresh from the environment on each call so
tests can point `PARROT_DATA_DIR` at a tmp path per-case without import-time
caching getting in the way.

Default location is `%APPDATA%\\Parrot\\parrot_data` on Windows (overridable via
`PARROT_DATA_DIR`); a cross-platform fallback keeps `uv run`/tests working on a
dev box. Paths are joined with `pathlib` for correctness, not portability
ambition — Parrot is Windows-only (CLAUDE.md §Platform Scope).
"""

import os
from pathlib import Path


def data_dir() -> Path:
    """The root of all durable state. Created on demand."""
    override = os.environ.get("PARROT_DATA_DIR")
    if override:
        root = Path(override)
    else:
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / ".local" / "share"
        root = base / "Parrot" / "parrot_data"
    root.mkdir(parents=True, exist_ok=True)
    return root


def voices_dir() -> Path:
    """Reference + locked audio for voice profiles."""
    d = data_dir() / "voices"
    d.mkdir(parents=True, exist_ok=True)
    return d


def outputs_dir() -> Path:
    """Generated audio (24 kHz WAV)."""
    d = data_dir() / "outputs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def whisper_models_dir() -> Path:
    """Single-file Whisper `.pt` checkpoints for reference transcription
    (transcription.md §7). Kept beside — not inside — the HF snapshot cache the
    OmniVoice gate uses, because openai-whisper ships its own checkpoint format."""
    d = data_dir() / "whisper_models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    """The single SQLite database file."""
    return data_dir() / "parrot.db"


def _safe_path_in(root: Path, audio_path: str) -> Path | None:
    """Resolve a stored bare filename to a file inside `root`, rejecting path
    traversal (synthesis.md edge case "Path traversal in history file ops").

    Returns `None` for any name that escapes `root` (or is empty), so callers
    ignore it rather than acting on an out-of-tree path.
    """
    if not audio_path:
        return None
    # Only a bare basename is ever stored; anything with a directory component
    # (or `..`) is rejected outright.
    name = os.path.basename(audio_path)
    if name != audio_path or name in ("", ".", ".."):
        return None
    base = root.resolve()
    candidate = (base / name).resolve()
    if candidate.parent != base:
        return None
    return candidate


def safe_output_path(audio_path: str) -> Path | None:
    """A stored output filename → a file inside the outputs dir, or None."""
    return _safe_path_in(outputs_dir(), audio_path)


def safe_voice_path(audio_path: str) -> Path | None:
    """A stored profile-audio filename → a file inside the voices dir, or None."""
    return _safe_path_in(voices_dir(), audio_path)
