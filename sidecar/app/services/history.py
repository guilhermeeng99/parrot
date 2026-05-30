"""Generation history (synthesis.md §/history).

One row per successful `/generate`. Reads are newest-first, capped at 50. Deletes
remove the on-disk WAV best-effort (a missing file never fails the request) and
always remove the row. `audio_path` is path-validated to the outputs dir before
any file op (no path traversal).
"""

import logging

from ..core import db, paths
from . import audio_io
from .errors import ServiceError

log = logging.getLogger(__name__)


def audio_path_for(history_id: str):
    """Resolve a history row's generated WAV for playback, with distinct 404s for
    missing row / missing file. Mirrors profiles.audio_path_for."""
    with db.connection() as conn:
        row = conn.execute(
            "SELECT audio_path FROM generation_history WHERE id = ?", (history_id,)
        ).fetchone()
    if row is None:
        raise ServiceError(404, "History item not found.")
    safe = paths.safe_output_path(row["audio_path"] or "")
    if safe is None or not safe.exists():
        raise ServiceError(404, "Audio file missing on disk.")
    return safe


def audio_mp3_bytes(history_id: str) -> bytes:
    """The generated clip re-encoded as MP3 (export-as-mp3). 404s like audio_path_for."""
    return audio_io.wav_file_to_mp3_bytes(audio_path_for(history_id))


def _row_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "text": row["text"],
        "language": row["language"],
        "profile_id": row["profile_id"],
        "audio_path": row["audio_path"],
        "duration_seconds": row["duration_seconds"],
        "generation_time": row["generation_time"],
        "seed": row["seed"],
        "created_at": row["created_at"],
    }


def list_history(limit: int = 50) -> list[dict]:
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM generation_history ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def insert(row: dict) -> None:
    with db.connection() as conn:
        conn.execute(
            """INSERT INTO generation_history
               (id, text, language, profile_id, audio_path, duration_seconds,
                generation_time, seed, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row["id"],
                row["text"],
                row["language"],
                row.get("profile_id"),
                row["audio_path"],
                row["duration_seconds"],
                row["generation_time"],
                row.get("seed"),
                row["created_at"],
            ),
        )


def _remove_audio(audio_path: str | None) -> None:
    safe = paths.safe_output_path(audio_path or "")
    if safe is not None:
        safe.unlink(missing_ok=True)


def delete_one(history_id: str) -> dict:
    with db.connection() as conn:
        row = conn.execute(
            "SELECT audio_path FROM generation_history WHERE id = ?", (history_id,)
        ).fetchone()
        if row is not None:
            _remove_audio(row["audio_path"])
            conn.execute("DELETE FROM generation_history WHERE id = ?", (history_id,))
    return {"deleted": True}


def clear_all() -> dict:
    with db.connection() as conn:
        rows = conn.execute("SELECT audio_path FROM generation_history").fetchall()
        for r in rows:
            _remove_audio(r["audio_path"])
        conn.execute("DELETE FROM generation_history")
    return {"cleared": True}
