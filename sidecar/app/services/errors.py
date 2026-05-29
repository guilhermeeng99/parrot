"""Domain errors + id generation.

Services raise `ServiceError(status_code, detail)` for expected failures; the app
factory registers one handler that serializes them as the IPC error envelope
`{"detail": ...}` with the right status and CORS headers, so a 4xx/5xx is never a
bare CORS error in the WebView (ipc-contract.md §2). `detail` is always a string
and is redacted before it leaves the process.
"""

import uuid

from ..core.logging import redact


class ServiceError(Exception):
    """An expected failure with an HTTP status and a user-safe, redacted detail."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = redact(str(detail))
        super().__init__(self.detail)


def new_id() -> str:
    """An 8-char hex id (`uuid4()[:8]`) — the convention for profile + history ids."""
    return uuid.uuid4().hex[:8]
