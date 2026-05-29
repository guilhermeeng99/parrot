"""Per-install secret encryption for the HF token (settings.md §1).

The Fernet key is derived per-install via scrypt over `(OS machine-id,
_secret_key_salt)`. It is deliberately **not** at-rest portable: a `parrot.db`
copied to another machine yields a key that can't decrypt `hf_token`, so the
read path degrades to "no token" rather than leaking or crashing.

This module is pure (no DB access): it provides the machine id, the KDF, and a
cipher factory. The salt lifecycle (create-with-first-write, preserve-on-clear)
lives in `services/hf_token.py`, which owns the DB transaction.
"""

import base64
import hashlib
import os
import sys

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

SALT_BYTES = 16


def machine_id() -> bytes:
    """A stable per-machine identifier. Best-effort; never raises.

    Windows: the registry `MachineGuid`. Fallback (and other OSes for dev/test):
    a hash of the node id so the derived key is still stable on that host.
    """
    if sys.platform == "win32":
        try:  # pragma: no cover - exercised only on Windows
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Cryptography",
            ) as key:
                guid, _ = winreg.QueryValueEx(key, "MachineGuid")
                if guid:
                    return str(guid).encode("utf-8")
        except OSError:
            pass
    # Cross-platform fallback (also the dev/test path on this box).
    import uuid

    node = uuid.getnode()
    return hashlib.sha256(f"parrot-{node}".encode("utf-8")).digest()


def new_salt() -> bytes:
    """A fresh random KDF salt (stored base64 in the `_secret_key_salt` row)."""
    return os.urandom(SALT_BYTES)


def derive_key(salt: bytes) -> bytes:
    """Derive a urlsafe-base64 Fernet key from (machine-id, salt) via scrypt."""
    kdf = Scrypt(salt=salt, length=32, n=2**14, r=8, p=1)
    raw = kdf.derive(machine_id())
    return base64.urlsafe_b64encode(raw)


def cipher(salt: bytes) -> Fernet:
    """A Fernet cipher for this install's derived key."""
    return Fernet(derive_key(salt))
