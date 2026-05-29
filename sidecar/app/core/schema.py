"""Canonical SQLite DDL — the single source of the schema.

Both the idempotent app-boot path (`db.init_db`) and the alembic initial
migration apply *these exact statements*, so the runtime schema and the migrated
schema can never drift. Schema changes add a new alembic revision; this list is
the head schema (CLAUDE.md §Data Model, voice-profiles.md, synthesis.md).

All tables: WAL mode, `foreign_keys = ON`. Paths in `*_path` columns are bare
filenames (joined with parrot_data/ at read time) — keeps parrot_data portable.
"""

CREATE_STATEMENTS: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS voice_profiles (
        id                TEXT PRIMARY KEY,
        name              TEXT NOT NULL,
        ref_audio_path    TEXT,
        ref_text          TEXT DEFAULT '',
        language          TEXT DEFAULT 'Auto',
        instruct          TEXT DEFAULT '',
        locked_audio_path TEXT DEFAULT '',
        seed              INTEGER DEFAULT NULL,
        is_locked         INTEGER DEFAULT 0,
        created_at        REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS generation_history (
        id               TEXT PRIMARY KEY,
        text             TEXT,
        language         TEXT,
        profile_id       TEXT REFERENCES voice_profiles(id) ON DELETE SET NULL,
        audio_path       TEXT,
        duration_seconds REAL,
        generation_time  REAL,
        seed             INTEGER DEFAULT NULL,
        created_at       REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS settings (
        key        TEXT PRIMARY KEY,
        value      TEXT NOT NULL,
        updated_at REAL NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_history_created ON generation_history(created_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_history_profile ON generation_history(profile_id)",
    "CREATE INDEX IF NOT EXISTS ix_profiles_created ON voice_profiles(created_at DESC)",
]
