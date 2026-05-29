"""Logging with secret redaction.

Any Hugging Face token (`hf_[A-Za-z0-9]{8,}`) is scrubbed to
`hf_***REDACTED***` before any handler formats a record (first-run-setup §2), so
a token never lands in `backend.log`, the splash log panel, an SSE event, or an
error surfaced to the UI. Generic bearer-token-looking values are masked too.

`redact(text)` is the single scrub function; the logging filter and every code
path that puts an exception message on the wire route through it.
"""

import logging
import re

# {8,} (not {30,}): real HF tokens are long, but short/truncated or test tokens
# must still be scrubbed — over-redacting a non-secret `hf_*` string is harmless.
_HF_TOKEN = re.compile(r"hf_[A-Za-z0-9]{8,}")
# Generic "key=value" secrets (TOKEN/KEY/SECRET/PASSWORD) — defensive cover for
# anything that isn't an HF token but still shouldn't be logged (CLAUDE.md).
_KV_SECRET = re.compile(
    r"((?:token|key|secret|password|authorization)\s*[=:]\s*)(\S+)",
    re.IGNORECASE,
)


def redact(text: str) -> str:
    """Scrub secrets from a string. Safe to call on any user/engine-facing text."""
    if not text:
        return text
    text = _HF_TOKEN.sub("hf_***REDACTED***", text)
    text = _KV_SECRET.sub(r"\1***REDACTED***", text)
    return text


class RedactingFilter(logging.Filter):
    """Logging filter that redacts the formatted message of every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact(record.getMessage())
            record.args = ()
        except Exception:  # never let logging hygiene break logging itself
            pass
        return True


def configure_logging(level: int = logging.INFO) -> None:
    """Attach the redacting filter to the root logger's handlers (idempotent)."""
    root = logging.getLogger()
    root.setLevel(level)
    if not root.handlers:
        logging.basicConfig(level=level)
    flt = RedactingFilter()
    for handler in root.handlers:
        if not any(isinstance(f, RedactingFilter) for f in handler.filters):
            handler.addFilter(flt)
