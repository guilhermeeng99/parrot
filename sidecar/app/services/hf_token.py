"""Hugging Face token store + resolver (settings.md, first-run-setup.md §2).

The token is optional — only gated model downloads need it. It is stored Fernet-
encrypted in the `settings` table under a per-install key (see core/crypto.py),
with the `_secret_key_salt` written in the SAME transaction as the first token so
there is never ciphertext without its salt.

Resolution order (highest → lowest): the in-app encrypted setting, then the
`HF_TOKEN` env var (documented power-user override). Reads only ever expose a
masked form (`hf_…<last 3>`); the raw token never leaves this module except via
`resolve_token()`, which the download path uses internally.
"""

import base64
import hashlib
import logging
import os
import time

from cryptography.fernet import InvalidToken

from ..core import crypto, db
from .errors import ServiceError

log = logging.getLogger(__name__)

_SALT_KEY = "_secret_key_salt"
_TOKEN_KEY = "hf_token"
_WHOAMI_TTL_S = 300.0

# sha256(token) -> (timestamp, ok, username). Keyed by hash, never the raw token,
# so the secret itself is never held as a dict key. Invalidated on set/clear.
_whoami_cache: dict[str, tuple[float, bool, str | None]] = {}


def _token_key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Masking
# ---------------------------------------------------------------------------
def _mask(token: str | None) -> str | None:
    if not token:
        return None
    return f"hf_…{token[-3:]}"


# ---------------------------------------------------------------------------
# Encrypted storage (app source)
# ---------------------------------------------------------------------------
def _read_salt(conn) -> bytes | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (_SALT_KEY,)).fetchone()
    if not row:
        return None
    try:
        return base64.b64decode(row["value"])
    except Exception:
        return None


def _read_app_token() -> str | None:
    """Decrypt the stored token, or None. A db copied across machines fails to
    decrypt (per-install key): we log once and degrade to None, never raise."""
    with db.connection() as conn:
        trow = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (_TOKEN_KEY,)
        ).fetchone()
        if not trow:
            return None
        salt = _read_salt(conn)
        if salt is None:
            return None
    try:
        return crypto.cipher(salt).decrypt(trow["value"].encode("utf-8")).decode("utf-8")
    except InvalidToken:
        log.warning(
            "Stored HF token could not be decrypted (parrot.db likely copied from "
            "another machine); falling back to HF_TOKEN env if set."
        )
        return None
    except Exception as e:
        log.warning("HF token decrypt failed: %s", e)
        return None


def _store_token(token: str) -> None:
    """Encrypt + persist the token, creating the salt in the same transaction."""
    now = time.time()
    with db.connection() as conn:
        salt = _read_salt(conn)
        if salt is None:
            salt = crypto.new_salt()
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                (_SALT_KEY, base64.b64encode(salt).decode("ascii"), now),
            )
        ciphertext = crypto.cipher(salt).encrypt(token.encode("utf-8")).decode("utf-8")
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
            (_TOKEN_KEY, ciphertext, now),
        )


def _delete_token() -> None:
    """Clear the token row; PRESERVE the salt so a re-save round-trips."""
    with db.connection() as conn:
        conn.execute("DELETE FROM settings WHERE key = ?", (_TOKEN_KEY,))


def _env_token() -> str | None:
    return os.environ.get("HF_TOKEN") or None


# ---------------------------------------------------------------------------
# Validation (network — indirected so tests can monkeypatch)
# ---------------------------------------------------------------------------
def _whoami(token: str) -> tuple[bool, str | None]:
    """(ok, username) from huggingface_hub.whoami. Network failure → (False, None)."""
    try:
        from huggingface_hub import whoami

        info = whoami(token=token)
        return True, (info or {}).get("name")
    except Exception:
        return False, None


def _login(token: str) -> None:
    """Prime the canonical HF credential file; never write to git (Rule 9)."""
    try:
        from huggingface_hub import login

        login(token=token, add_to_git_credential=False)
    except Exception as e:  # best-effort: the encrypted store already holds it
        log.warning("huggingface_hub.login failed (non-fatal): %s", e)


def _validate_cached(token: str) -> tuple[bool, str | None]:
    now = time.time()
    # Prune expired entries on access so a token-rotation churn can't grow the
    # cache unbounded (the dict is keyed by hash, not the secret).
    expired = [k for k, v in _whoami_cache.items() if (now - v[0]) >= _WHOAMI_TTL_S]
    for k in expired:
        del _whoami_cache[k]

    key = _token_key(token)
    hit = _whoami_cache.get(key)
    if hit and (now - hit[0]) < _WHOAMI_TTL_S:
        return hit[1], hit[2]
    ok, user = _whoami(token)
    _whoami_cache[key] = (now, ok, user)
    return ok, user


def _invalidate_cache() -> None:
    _whoami_cache.clear()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def resolve_token() -> str | None:
    """The raw token to use for downloads: app first, then HF_TOKEN env."""
    return _read_app_token() or _env_token()


def get_state() -> dict:
    """The masked TokenState for the Settings panel. Never raises."""
    app_token = _read_app_token()
    env_token = _env_token()

    sources = []
    active: str | None = None

    for name, tok in (("app", app_token), ("env", env_token)):
        is_set = bool(tok)
        ok, user = (False, None)
        if is_set:
            ok, user = _validate_cached(tok)  # type: ignore[arg-type]
            if ok and active is None:
                active = name
        sources.append(
            {
                "source": name,
                "set": is_set,
                "masked": _mask(tok),
                "whoami_user": user,
                "whoami_ok": ok,
            }
        )
    return {"active": active, "sources": sources}


def set_token(token: str) -> dict:
    """Persist (encrypt) a token and return the refreshed, re-validated state."""
    token = (token or "").strip()
    if not token:
        raise ServiceError(400, "A Hugging Face token is required.")
    try:
        _store_token(token)
    except Exception as e:
        raise ServiceError(500, f"Couldn't save the token: {e}")
    _login(token)
    _invalidate_cache()
    return get_state()


def clear_token() -> dict:
    """Clear the in-app token (idempotent). Keeps the salt; returns fresh state."""
    try:
        _delete_token()
    except Exception as e:
        raise ServiceError(500, f"Couldn't clear the token: {e}")
    _invalidate_cache()
    return get_state()
