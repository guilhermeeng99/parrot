"""Per-install secret encryption (core/crypto.py + the hf_token degrade path).

The Fernet key is derived per-install from (machine-id, salt) via scrypt. The
key property under test: a ciphertext is bound to *this* install — a different
machine or a different salt can't decrypt it, and the read path degrades to "no
token" rather than raising (so a parrot.db copied between machines never crashes
boot — crypto.py / hf_token.py docstrings).
"""

import base64
import time

import pytest
from cryptography.fernet import InvalidToken

from app.core import crypto, db
from app.services import hf_token


def test_derive_key_deterministic_for_fixed_salt():
    salt = b"0123456789abcdef"
    assert crypto.derive_key(salt) == crypto.derive_key(salt)


def test_derive_key_differs_when_machine_id_changes(monkeypatch):
    salt = b"0123456789abcdef"
    monkeypatch.setattr(crypto, "machine_id", lambda: b"machine-A")
    key_a = crypto.derive_key(salt)
    monkeypatch.setattr(crypto, "machine_id", lambda: b"machine-B")
    key_b = crypto.derive_key(salt)
    assert key_a != key_b  # same salt, different host → different key


def test_derive_key_differs_when_salt_changes():
    a = crypto.derive_key(crypto.new_salt())
    b = crypto.derive_key(crypto.new_salt())
    assert a != b


def test_ciphertext_from_one_salt_fails_under_another():
    salt_a, salt_b = crypto.new_salt(), crypto.new_salt()
    token = crypto.cipher(salt_a).encrypt(b"hf_secrettoken12345")
    # The right salt round-trips; the wrong salt raises InvalidToken (never plaintext).
    assert crypto.cipher(salt_a).decrypt(token) == b"hf_secrettoken12345"
    with pytest.raises(InvalidToken):
        crypto.cipher(salt_b).decrypt(token)


def _write_token_row(token: str, salt: bytes) -> None:
    """Persist an hf_token ciphertext + its salt the way hf_token._store_token
    does, but under a caller-chosen salt so the test can simulate a foreign db."""
    now = time.time()
    with db.connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
            ("_secret_key_salt", base64.b64encode(salt).decode("ascii"), now),
        )
        ciphertext = crypto.cipher(salt).encrypt(token.encode("utf-8")).decode("utf-8")
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
            ("hf_token", ciphertext, now),
        )


def test_token_from_other_machine_resolves_to_none(env, monkeypatch):
    """A token encrypted on one machine must resolve to None — not raise — when
    the machine-id changes (the 'parrot.db copied across machines' degrade path)."""
    monkeypatch.setattr(crypto, "machine_id", lambda: b"machine-original")
    _write_token_row("hf_secrettoken12345", crypto.new_salt())
    hf_token._invalidate_cache()

    # Same install still decrypts it.
    assert hf_token.resolve_token() == "hf_secrettoken12345"

    # Move the db to a different host: the per-install key no longer matches.
    monkeypatch.setattr(crypto, "machine_id", lambda: b"machine-foreign")
    hf_token._invalidate_cache()
    assert hf_token.resolve_token() is None  # graceful degrade, no InvalidToken
