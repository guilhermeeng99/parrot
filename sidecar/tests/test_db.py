"""DB layer + alembic migration: idempotent schema, tested upgrade path."""

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.core import db

SIDECAR_ROOT = Path(__file__).resolve().parents[1]
_EXPECTED = {"voice_profiles", "generation_history", "settings"}


def _table_names(db_file: Path) -> set[str]:
    con = sqlite3.connect(db_file)
    try:
        return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        con.close()


def _alembic_cfg(db_file: Path) -> Config:
    cfg = Config(str(SIDECAR_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(SIDECAR_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_file}")
    return cfg


def test_init_db_is_idempotent(env):
    db.init_db()
    db.init_db()  # second call must not raise
    assert _EXPECTED <= _table_names(env / "parrot_data" / "parrot.db")


def test_init_db_sets_wal_and_fk(env):
    with db.connection() as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_alembic_upgrade_creates_schema(tmp_path):
    db_file = tmp_path / "migrated.db"
    cfg = _alembic_cfg(db_file)
    command.upgrade(cfg, "head")
    assert _EXPECTED <= _table_names(db_file)
    # idempotent: running head again on an existing DB is a no-op (CREATE IF NOT EXISTS)
    command.upgrade(cfg, "head")
    assert _EXPECTED <= _table_names(db_file)


def test_alembic_downgrade_drops_schema(tmp_path):
    db_file = tmp_path / "down.db"
    cfg = _alembic_cfg(db_file)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    assert not (_EXPECTED & _table_names(db_file))
