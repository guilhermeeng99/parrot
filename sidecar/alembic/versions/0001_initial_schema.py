"""initial schema: voice_profiles, generation_history, settings

Applies the canonical DDL shared with the idempotent app-boot path
(app.core.schema.CREATE_STATEMENTS), so a migrated DB and a freshly-booted DB
are byte-for-byte the same schema. This is revision 0001 — the baseline an
existing parrot_data/ stamps up from.

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-29
"""

from typing import Sequence, Union

from alembic import op

from app.core.schema import CREATE_STATEMENTS

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # CREATE TABLE IF NOT EXISTS — safe to run against a DB the app already
    # created idempotently (an existing install just stamps the revision).
    for stmt in CREATE_STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_profiles_created")
    op.execute("DROP INDEX IF EXISTS ix_history_profile")
    op.execute("DROP INDEX IF EXISTS ix_history_created")
    op.execute("DROP TABLE IF EXISTS generation_history")
    op.execute("DROP TABLE IF EXISTS settings")
    op.execute("DROP TABLE IF EXISTS voice_profiles")
