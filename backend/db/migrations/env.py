"""Alembic environment: synchronous psycopg 3 connection, model metadata from db.models.

The database URL is, in order: ``sqlalchemy.url`` set programmatically on the Alembic config
(tests), else ``DATABASE_URL`` through ``config.get_database_url`` (CLI and the container).
Migrations are never run on a request path (plan section 14).
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

import config as app_config  # our config.py, not Alembic's ``config`` object below
from db.migration_filters import include_object
from db.models import Base

alembic_config = context.config

# Logging is configured for the CLI only; tests set this attribute to False so pytest's
# logging setup is not replaced.
if alembic_config.config_file_name is not None and alembic_config.attributes.get(
    "configure_logger", True
):
    fileConfig(alembic_config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    return alembic_config.get_main_option("sqlalchemy.url") or app_config.get_database_url()


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(_database_url(), poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
