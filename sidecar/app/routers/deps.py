"""Shared router dependencies.

`require_loopback` enforces the loopback-only boundary on the settings + engine
endpoints (settings.md Rule 11): a non-loopback origin gets 403 before the
handler runs. The sidecar already binds 127.0.0.1 only, so this is defense in
depth, not the primary boundary.
"""

from fastapi import HTTPException, Request

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def require_loopback(request: Request) -> None:
    client = request.client
    host = client.host if client else None
    if host not in _LOOPBACK_HOSTS:
        raise HTTPException(status_code=403, detail="Loopback-only endpoint.")
