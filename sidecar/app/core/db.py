"""SQLite access for the sidecar.

One database under `parrot_data/parrot.db`, WAL mode, `foreign_keys = ON`,
`row_factory = sqlite3.Row` so rows read like dicts. The schema is created
idempotently from `schema.CREATE_STATEMENTS` on first connection; versioned
evolution rides on alembic against the same DDL (see alembic/). User data must
survive upgrades with no manual migration (CLAUDE.md).

Connections are short-lived (open → use → close) rather than a long-lived global,
which keeps WAL writers from stepping on each other across the thread pool.
"""

import sqlite3
import threading
from contextlib import contextmanager
from typing import Iterator

from . import paths
from .schema import CREATE_STATEMENTS

_initialized = False
# Guards the init latch: connections open across the GPU/HTTP thread pool, so the
# first concurrent callers could otherwise run init_db() simultaneously.
_init_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(paths.db_path(), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def init_db() -> None:
    """Create the schema idempotently. Cheap to call repeatedly; runs once.

    Double-checked under `_init_lock` so concurrent first-use (multiple pool
    threads hitting `connection()` at once) initializes exactly once."""
    global _initialized
    if _initialized:
        return
    with _init_lock:
        if _initialized:
            return
        conn = _connect()
        try:
            for stmt in CREATE_STATEMENTS:
                conn.execute(stmt)
            conn.commit()
            _initialized = True
        finally:
            conn.close()


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    """A ready-to-use connection with the schema ensured. Commits on clean exit,
    rolls back on exception, and always closes."""
    if not _initialized:
        init_db()
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _reset_for_tests() -> None:
    """Test-only: forget the init latch so a fresh tmp DB re-initializes."""
    global _initialized
    with _init_lock:
        _initialized = False
