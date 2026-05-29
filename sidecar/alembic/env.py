"""Alembic environment for Parrot's SQLite DB.

The URL is resolved at runtime (not hardcoded in alembic.ini) so migrations
target the real `parrot_data/parrot.db` — honoring PARROT_DATA_DIR — without a
second source of truth. Tests pass a temp DB via `-x db_url=sqlite:///<path>`.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config

if config.config_file_name is not None:
    try:
        fileConfig(config.config_file_name)
    except Exception:
        pass  # logging config is best-effort; never block a migration on it


def _resolve_url() -> str:
    # 1. `-x db_url=...` (used by tests). 2. an explicit alembic.ini value.
    # 3. the runtime data-dir DB path.
    x_args = context.get_x_argument(as_dictionary=True)
    if x_args.get("db_url"):
        return x_args["db_url"]
    ini_url = config.get_main_option("sqlalchemy.url")
    if ini_url:
        return ini_url
    from app.core import paths

    return f"sqlite:///{paths.db_path()}"


target_metadata = None  # raw-DDL migrations; no SQLAlchemy model metadata


def run_migrations_offline() -> None:
    context.configure(
        url=_resolve_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _resolve_url()
    connectable = engine_from_config(
        section, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
