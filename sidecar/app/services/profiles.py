"""Voice-profile CRUD + lock/unlock + usage + synthesis resolution.

Owns the `voice_profiles` table (voice-profiles.md) and the profile-resolution
order consumed by `/generate` (synthesis.md §Profile Resolution). All `*_path`
columns store a bare filename; absolute paths are re-derived at read time, so
parrot_data/ stays portable across machines.
"""

import logging
import os
import shutil
import time

from ..core import db, paths
from .errors import ServiceError, new_id

log = logging.getLogger(__name__)

_NOT_FOUND = "That voice profile doesn't exist. It may have been deleted from another tab."

# Reference-audio containers the capture UI offers (voice-cloning.md §4 / EDGE-5).
# The actual decode happens later in the engine (torchaudio + an ffmpeg fallback),
# which the light create path does not load — so create only rejects an obviously
# unsupported *extension* here, letting a wrong file fail fast at upload instead of
# as an opaque 500 at synthesis. Decodability + silence are validated at synthesis.
_SUPPORTED_AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".webm"}
_AUDIO_MIME_BY_EXT = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".webm": "audio/webm",
}


def _row_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "ref_audio_path": row["ref_audio_path"] or "",
        "ref_text": row["ref_text"] or "",
        "language": row["language"] or "Auto",
        "instruct": row["instruct"] or "",
        "locked_audio_path": row["locked_audio_path"] or "",
        "seed": row["seed"],
        "is_locked": 1 if row["is_locked"] else 0,
        "created_at": row["created_at"],
    }


def list_profiles() -> list[dict]:
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM voice_profiles ORDER BY created_at DESC"
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_profile(profile_id: str) -> dict | None:
    with db.connection() as conn:
        row = conn.execute(
            "SELECT * FROM voice_profiles WHERE id = ?", (profile_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def get_profile_or_404(profile_id: str) -> dict:
    profile = get_profile(profile_id)
    if profile is None:
        raise ServiceError(404, _NOT_FOUND)
    return profile


def create_profile(
    name: str,
    audio_bytes: bytes,
    original_filename: str | None,
    ref_text: str = "",
    instruct: str = "",
    language: str = "Auto",
    seed: int | None = None,
) -> dict:
    """Write the reference clip + insert the row atomically (BR-1/BR-2). On a DB
    failure the just-written audio file is removed so no orphan is left behind."""
    name = (name or "").strip()
    if not name:
        raise ServiceError(400, "A voice profile needs a name.")

    # A missing filename defaults to .wav (BR-2) — recorded clips always carry one.
    ext = os.path.splitext(original_filename or "")[1].lower() or ".wav"
    if ext not in _SUPPORTED_AUDIO_EXTS:
        raise ServiceError(
            415,
            f"Unsupported audio format '{ext}'. Use one of: "
            f"{', '.join(sorted(_SUPPORTED_AUDIO_EXTS))}.",
        )

    profile_id = new_id()
    filename = f"{profile_id}{ext}"
    dest = paths.voices_dir() / filename
    dest.write_bytes(audio_bytes)

    try:
        with db.connection() as conn:
            conn.execute(
                """INSERT INTO voice_profiles
                   (id, name, ref_audio_path, ref_text, language, instruct,
                    locked_audio_path, seed, is_locked, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, '', ?, 0, ?)""",
                (
                    profile_id,
                    name,
                    filename,
                    ref_text or "",
                    (language or "Auto").strip() or "Auto",
                    instruct or "",
                    seed,
                    time.time(),
                ),
            )
    except Exception as e:
        dest.unlink(missing_ok=True)  # no orphan file on a failed insert
        raise ServiceError(500, f"Couldn't save the voice profile: {e}")

    return {"id": profile_id, "name": name}


def update_profile(
    profile_id: str,
    name: str | None = None,
    ref_text: str | None = None,
    instruct: str | None = None,
    language: str | None = None,
) -> dict:
    """Partial patch (Rule 4). Only present fields change; an all-null body is a
    400, and a whitespace name is rejected with the existing name preserved."""
    get_profile_or_404(profile_id)

    sets: list[str] = []
    args: list = []
    if name is not None:
        trimmed = name.strip()
        if not trimmed:
            raise ServiceError(400, "A voice profile needs a name.")
        sets.append("name = ?")
        args.append(trimmed)
    if ref_text is not None:
        sets.append("ref_text = ?")
        args.append(ref_text)
    if instruct is not None:
        sets.append("instruct = ?")
        args.append(instruct)
    if language is not None:
        sets.append("language = ?")
        args.append(language.strip() or "Auto")

    if not sets:
        raise ServiceError(400, "Nothing to update — provide a name, ref_text, instruct, or language.")

    args.append(profile_id)
    with db.connection() as conn:
        conn.execute(f"UPDATE voice_profiles SET {', '.join(sets)} WHERE id = ?", args)
    return get_profile_or_404(profile_id)


def delete_profile(profile_id: str) -> dict:
    """Remove files, null-out dependent history (never cascade), delete the row.
    Deleting a non-existent id is a no-op success (Rule 11 / idempotent)."""
    profile = get_profile(profile_id)
    if profile is not None:
        for fname in (profile["ref_audio_path"], profile["locked_audio_path"]):
            p = paths.safe_voice_path(fname) if fname else None
            if p is not None:
                p.unlink(missing_ok=True)
        with db.connection() as conn:
            conn.execute(
                "UPDATE generation_history SET profile_id = NULL WHERE profile_id = ?",
                (profile_id,),
            )
            conn.execute("DELETE FROM voice_profiles WHERE id = ?", (profile_id,))
    return {"deleted": profile_id}


def lock_profile(profile_id: str, history_id: str, seed: int | None = None) -> dict:
    """Pin a generated take as the deterministic reference (Rule 8)."""
    get_profile_or_404(profile_id)
    with db.connection() as conn:
        hist = conn.execute(
            "SELECT text, audio_path FROM generation_history WHERE id = ?", (history_id,)
        ).fetchone()
    if hist is None or not hist["audio_path"]:
        raise ServiceError(404, "History item not found or has no audio.")

    source = paths.safe_output_path(hist["audio_path"])
    if source is None or not source.exists():
        raise ServiceError(404, "Audio file not found on disk.")

    locked_name = f"{profile_id}_locked.wav"
    shutil.copyfile(source, paths.voices_dir() / locked_name)

    with db.connection() as conn:
        conn.execute(
            """UPDATE voice_profiles
               SET locked_audio_path = ?, seed = ?, is_locked = 1, ref_text = ?
               WHERE id = ?""",
            (locked_name, seed, (hist["text"] or "")[:100], profile_id),
        )
    return {"locked": True, "profile_id": profile_id, "locked_audio_path": locked_name}


def unlock_profile(profile_id: str) -> dict:
    """Revert to the original clone (Rule 9, idempotent)."""
    profile = get_profile_or_404(profile_id)
    locked = paths.safe_voice_path(profile["locked_audio_path"]) if profile["locked_audio_path"] else None
    if locked is not None:
        locked.unlink(missing_ok=True)
    with db.connection() as conn:
        conn.execute(
            "UPDATE voice_profiles SET locked_audio_path = '', seed = NULL, is_locked = 0 WHERE id = ?",
            (profile_id,),
        )
    return {"unlocked": True, "profile_id": profile_id}


def usage(profile_id: str) -> dict:
    """Recent generations for this profile (≤20, newest first) + total (Rule 13)."""
    with db.connection() as conn:
        rows = conn.execute(
            """SELECT id, text, audio_path, created_at, generation_time
               FROM generation_history WHERE profile_id = ?
               ORDER BY created_at DESC LIMIT 20""",
            (profile_id,),
        ).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM generation_history WHERE profile_id = ?",
            (profile_id,),
        ).fetchone()["n"]
    recent = [
        {
            "id": r["id"],
            "text": r["text"],
            "audio_path": r["audio_path"],
            "created_at": r["created_at"],
            "generation_time": r["generation_time"],
        }
        for r in rows
    ]
    return {"synth_recent": recent, "synth_total": total}


def audio_path_for(profile_id: str):
    """Resolve the profile's representative audio file (locked preferred), with
    distinct 404s for missing profile / no audio / file gone (Rule 7)."""
    profile = get_profile(profile_id)
    if profile is None:
        raise ServiceError(404, "Profile not found.")
    chosen = (
        profile["locked_audio_path"]
        if profile["is_locked"] and profile["locked_audio_path"]
        else profile["ref_audio_path"]
    )
    if not chosen:
        raise ServiceError(404, "No audio available for this profile.")
    path = paths.safe_voice_path(chosen)
    if path is None or not path.exists():
        raise ServiceError(404, "Audio file missing on disk.")
    return path


def original_audio_path_for(profile_id: str):
    """Resolve the profile's original uploaded reference clip."""
    profile = get_profile(profile_id)
    if profile is None:
        raise ServiceError(404, "Profile not found.")
    chosen = profile["ref_audio_path"]
    if not chosen:
        raise ServiceError(404, "No original audio available for this profile.")
    path = paths.safe_voice_path(chosen)
    if path is None or not path.exists():
        raise ServiceError(404, "Audio file missing on disk.")
    return path


def audio_mime_for(path) -> str:
    """Best-effort MIME type for a stored profile audio file."""
    return _AUDIO_MIME_BY_EXT.get(path.suffix.lower(), "application/octet-stream")


def _empty(value) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def resolve_for_generate(
    profile_id: str | None,
    ref_audio_path: str | None,
    ref_text: str | None,
    instruct: str | None,
    seed: int | None,
    language: str | None,
) -> dict:
    """Decide the reference/ref_text/instruct/seed/language passed to the model.

    Implements the testable resolution order in synthesis.md. An explicit request
    field always wins; the profile fills only what the request left empty.
    Returns resolved values plus `resolved_profile_id` (None when no profile
    resolved — including a `profile_id` that wasn't found)."""
    resolved = {
        "ref_audio_path": ref_audio_path,
        "ref_text": ref_text,
        "instruct": instruct,
        "seed": seed,
        "language": language,
        "resolved_profile_id": None,
    }

    if not profile_id:
        # Cases 1 & 2: inline reference or default voice — request values as-is.
        return resolved

    profile = get_profile(profile_id)
    if profile is None:
        # Case 3: unknown profile → default-voice fallback, resolved id stays None.
        return resolved

    resolved["resolved_profile_id"] = profile_id
    if _empty(resolved["ref_text"]):
        resolved["ref_text"] = profile["ref_text"]
    if _empty(resolved["instruct"]):
        resolved["instruct"] = profile["instruct"]
    if resolved["seed"] is None:
        resolved["seed"] = profile["seed"]

    if profile["is_locked"] and profile["locked_audio_path"]:
        # Case 4: locked reference wins.
        resolved["ref_audio_path"] = _voice_ref(profile["locked_audio_path"])
    elif not _empty(profile["instruct"]):
        # Case 5: instruct-style path — no reference audio.
        resolved["ref_audio_path"] = None
    elif profile["ref_audio_path"]:
        # Case 6: the original clone reference.
        resolved["ref_audio_path"] = _voice_ref(profile["ref_audio_path"])

    # Case 7: a resolved profile with language "Auto" → let the model auto-detect.
    if resolved["language"] == "Auto":
        resolved["language"] = None
    return resolved


def _voice_ref(filename: str) -> str | None:
    """A voices-dir reference filename → absolute path the model can read, through
    the shared traversal guard. None if the stored name fails validation."""
    safe = paths.safe_voice_path(filename)
    return str(safe) if safe is not None else None
