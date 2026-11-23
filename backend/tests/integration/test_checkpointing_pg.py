"""The Postgres checkpointer on a real PostgreSQL (Task 13, plan D7, AC4, AC23).

Records what ``AsyncPostgresSaver.setup()`` really creates, that running it again is harmless,
and that Alembic leaves those tables alone.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import psycopg
import pytest
import yaml
from alembic import command
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.postgres.base import MIGRATIONS

from core.checkpointing import open_postgres_checkpointer
from core.runtime import FactoryRuntime
from db.migration_filters import CHECKPOINTER_TABLES
from tests.integration.conftest import alembic_config
from tests.integration.test_migrations import OUR_TABLES, _diff, _tables

pytestmark = pytest.mark.integration

# What setup() of langgraph-checkpoint-postgres 3.2.1 creates (read from base.py MIGRATIONS and
# confirmed by the first test below).
EXPECTED_CHECKPOINTER_TABLES = {"checkpoint_migrations", "checkpoints", "checkpoint_blobs", "checkpoint_writes"}


def _indexes(plain_url: str) -> set[str]:
    with psycopg.connect(plain_url) as conn:
        rows = conn.execute("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'").fetchall()
    return {r[0] for r in rows}


async def _setup_once(url: str) -> None:
    async with open_postgres_checkpointer(url) as saver:
        assert isinstance(saver, AsyncPostgresSaver)


def test_setup_creates_exactly_the_tables_alembic_excludes(migrated_db_url, migrated_db_sqlalchemy_url):
    asyncio.run(_setup_once(migrated_db_sqlalchemy_url))

    created = _tables(migrated_db_url) - OUR_TABLES - {"alembic_version"}
    print("checkpointer tables:", sorted(created))  # recorded in the Task 13 report
    assert created == EXPECTED_CHECKPOINTER_TABLES
    assert set(CHECKPOINTER_TABLES) == created
    assert {"checkpoints_thread_id_idx", "checkpoint_blobs_thread_id_idx",
            "checkpoint_writes_thread_id_idx"} <= _indexes(migrated_db_url)


def test_setup_twice_is_idempotent(migrated_db_url, migrated_db_sqlalchemy_url):
    asyncio.run(_setup_once(migrated_db_sqlalchemy_url))
    tables, indexes = _tables(migrated_db_url), _indexes(migrated_db_url)

    asyncio.run(_setup_once(migrated_db_sqlalchemy_url))  # a restart: no error, nothing new

    assert (_tables(migrated_db_url), _indexes(migrated_db_url)) == (tables, indexes)
    with psycopg.connect(migrated_db_url) as conn:
        versions = [r[0] for r in conn.execute("SELECT v FROM checkpoint_migrations ORDER BY v")]
    assert versions == list(range(len(MIGRATIONS)))


def test_alembic_ignores_the_real_checkpointer_tables(migrated_db_url, migrated_db_sqlalchemy_url):
    asyncio.run(_setup_once(migrated_db_sqlalchemy_url))

    assert _diff(migrated_db_sqlalchemy_url) == []
    command.downgrade(alembic_config(migrated_db_sqlalchemy_url), "base")
    assert EXPECTED_CHECKPOINTER_TABLES <= _tables(migrated_db_url)


def test_the_runtime_opens_the_checkpointer_runs_tasks_on_it_and_closes_it(
    tmp_path: Path, monkeypatch, migrated_db_url, migrated_db_sqlalchemy_url
):
    monkeypatch.setenv("FORCE_MOCK", "true")
    monkeypatch.setenv("PIPELINE_ENGINE", "langgraph")  # the only engine with a checkpointer (D23)
    monkeypatch.setenv("TASK_STORE", "db")
    monkeypatch.setenv("PIPELINE_CHECKPOINTER", "postgres")
    monkeypatch.setenv("DATABASE_URL", migrated_db_sqlalchemy_url)
    project = tmp_path / "demo"
    project.mkdir()
    (project / "tasks.yaml").write_text(yaml.safe_dump({"tasks": [
        {"id": "task-a", "title": "a", "description": "d", "priority": "high", "status": "pending"},
    ]}), encoding="utf-8")
    seen: dict = {}

    async def scenario():
        runtime = FactoryRuntime(projects_dir=tmp_path)
        await runtime.startup()
        try:
            saver = runtime.checkpointer
            seen["saver"] = saver
            assert isinstance(saver, AsyncPostgresSaver)
            assert runtime.orchestrators["demo"]._graph_runner._checkpointer is saver
            for _ in range(300):
                task = await runtime.task_repo.get("demo", "task-a")
                if task.status.value == "done":
                    break
                await asyncio.sleep(0.1)
            seen["status"] = task.status.value
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())

    assert seen["status"] == "done"
    assert seen["saver"].conn.closed
    with psycopg.connect(migrated_db_url) as conn:  # a finished run leaves no thread behind
        assert conn.execute("SELECT count(*) FROM checkpoints").fetchone() == (0,)
