"""Sidecar runtime config.

Host is loopback-only (the security boundary — see docs/specs/architecture.md).
Port defaults to 3900 and is overridable via PARROT_PORT so the Rust supervisor
and the sidecar always agree on the same value.
"""

import os

HOST = "127.0.0.1"


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
