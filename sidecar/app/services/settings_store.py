"""Low-level key/value access to the `settings` table.

Secrets (the HF token) are owned by `hf_token.py`, which encrypts before writing
and manages the salt in one transaction. This module is the generic helper for
non-secret rows and guards against a misrouted plaintext write to a secret key
(settings.md edge: "the encrypted row can never be overwritten with plaintext").
"""

import time

from ..core import db
from .errors import ServiceError

# Keys that must only ever be written through their encrypting owner.
SECRET_KEYS = {"hf_token"}


def read(key: str) -> str | None:
    with db.connection() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None


def write(key: str, value: str) -> None:
    """Write a NON-secret setting. Rejects secret keys (use their owner instead)."""
    if key in SECRET_KEYS:
        raise ServiceError(400, f"'{key}' is a secret and cannot be stored as plaintext.")
    _put(key, value)


def delete(key: str) -> None:
    with db.connection() as conn:
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))


def _put(key: str, value: str) -> None:
    """INSERT-OR-REPLACE a row, stamping updated_at. Internal — no secret guard."""
    with db.connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
            (key, value, time.time()),
        )
