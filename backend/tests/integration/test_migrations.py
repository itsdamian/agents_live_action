"""Alembic migrations on a real PostgreSQL (Task 7, AC4)."""

from __future__ import annotations

import os
import subprocess
import sys

import psycopg
import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from db.migration_filters import CHECKPOINTER_TABLES, include_object
from db.models import Base
from tests.integration.conftest import BACKEND_DIR, alembic_config, to_sqlalchemy_url

pytestmark = pytest.mark.integration

OUR_TABLES = {"projects", "tasks", "task_runs", "task_run_steps", "usage_records", "agents", "pipelines"}


def _tables(plain_url: str) -> set[str]:
    with psycopg.connect(plain_url) as conn:
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        ).fetchall()
    return {r[0] for r in rows}


def _diff(sqlalchemy_url: str) -> list:
    """What ``alembic revision --autogenerate`` would put in a new revision (should be nothing)."""

    engine = create_engine(sqlalchemy_url)
    try:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(
                conn,
                opts={"include_object": include_object, "compare_type": True, "compare_server_default": True},
            )
            return compare_metadata(ctx, Base.metadata)
    finally:
        engine.dispose()


def test_upgrade_head_creates_the_phase_1a_tables(empty_db_url):
    command.upgrade(alembic_config(to_sqlalchemy_url(empty_db_url)), "head")

    assert _tables(empty_db_url) == OUR_TABLES | {"alembic_version"}
    with psycopg.connect(empty_db_url) as conn:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == ("0001",)


def test_downgrade_base_then_upgrade_again(empty_db_url):
    cfg = alembic_config(to_sqlalchemy_url(empty_db_url))

    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    assert _tables(empty_db_url) == {"alembic_version"}  # Alembic keeps its own bookkeeping table
    command.upgrade(cfg, "head")

    assert _tables(empty_db_url) == OUR_TABLES | {"alembic_version"}


def test_models_and_migration_agree_no_autogenerate_diff(migrated_db_sqlalchemy_url):
    assert _diff(migrated_db_sqlalchemy_url) == []


def test_autogenerate_does_notice_a_real_difference(migrated_db_url, migrated_db_sqlalchemy_url):
    """The comparison is meaningful: change the schema behind Alembic's back and it shows up."""

    with psycopg.connect(migrated_db_url, autocommit=True) as conn:
        conn.execute("ALTER TABLE tasks ADD COLUMN surprise text")

    assert _diff(migrated_db_sqlalchemy_url) != []


def test_checkpointer_tables_are_invisible_to_alembic(migrated_db_url, migrated_db_sqlalchemy_url):
    """LangGraph creates its own tables in the same database; Alembic must neither propose
    to drop them nor drop them on downgrade."""

    with psycopg.connect(migrated_db_url, autocommit=True) as conn:
        for name in sorted(CHECKPOINTER_TABLES):
            conn.execute(f'CREATE TABLE "{name}" (thread_id text, payload jsonb)')
            conn.execute(f'CREATE INDEX "ix_{name}_thread" ON "{name}" (thread_id)')

    assert _diff(migrated_db_sqlalchemy_url) == []
    command.downgrade(alembic_config(migrated_db_sqlalchemy_url), "base")
    assert CHECKPOINTER_TABLES <= _tables(migrated_db_url)
    assert not (OUR_TABLES & _tables(migrated_db_url))


def test_check_constraints_reject_bad_values(migrated_db_url):
    insert = (
        "INSERT INTO tasks (project_id, id, title, description, priority, status, source, created_at, updated_at) "
        "VALUES ('p', %s, 't', 'd', %s, %s, 'api', now(), now())"
    )
    with psycopg.connect(migrated_db_url, autocommit=True) as conn:
        conn.execute(insert, ("ok", "medium", "pending"))
        for args in (("a", "medium", "bogus"), ("b", "urgent", "pending")):
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(insert, args)
        with pytest.raises(psycopg.errors.UniqueViolation):  # primary key (project_id, id)
            conn.execute(insert, ("ok", "low", "done"))


def test_steps_cascade_with_their_run_and_one_default_pipeline_per_project(migrated_db_url):
    with psycopg.connect(migrated_db_url, autocommit=True) as conn:
        run_id = conn.execute(
            "INSERT INTO task_runs (project_id, task_id, run_no, started_at) VALUES ('p','t',1,now()) RETURNING id"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO task_run_steps (run_id, seq, kind, at) VALUES (%s, 1, 'station', now())", (run_id,)
        )
        with pytest.raises(psycopg.errors.UniqueViolation):  # (run_id, seq) is unique
            conn.execute(
                "INSERT INTO task_run_steps (run_id, seq, kind, at) VALUES (%s, 1, 'gate', now())", (run_id,)
            )
        conn.execute("DELETE FROM task_runs WHERE id = %s", (run_id,))
        assert conn.execute("SELECT count(*) FROM task_run_steps").fetchone() == (0,)

        pipeline = "INSERT INTO pipelines (project_id, id, name, is_default, seats, updated_at) VALUES ('p', %s, 'n', %s, '{pm}', now())"
        conn.execute(pipeline, ("main", True))
        conn.execute(pipeline, ("other", False))
        with pytest.raises(psycopg.errors.UniqueViolation):  # a second default in the same project
            conn.execute(pipeline, ("third", True))
        conn.execute(
            "INSERT INTO pipelines (project_id, id, name, is_default, seats, updated_at) "
            "VALUES ('q', 'main', 'n', true, '{pm}', now())"  # another project may have its own default
        )


def test_the_cli_and_the_container_path_upgrade_with_database_url(empty_db_url):
    """``alembic upgrade head`` from backend/ with DATABASE_URL set: what start.sh does."""

    env = {**os.environ, "DATABASE_URL": to_sqlalchemy_url(empty_db_url)}
    out = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR, env=env, capture_output=True, text=True,
    )

    assert out.returncode == 0, out.stderr
    assert _tables(empty_db_url) == OUR_TABLES | {"alembic_version"}
    # Running it again is a no-op, not an error (the container restarts often).
    again = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR, env=env, capture_output=True, text=True,
    )
    assert again.returncode == 0, again.stderr
