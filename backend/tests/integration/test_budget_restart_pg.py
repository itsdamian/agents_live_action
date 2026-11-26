"""AC6 on a real PostgreSQL through the whole runtime: a restart keeps the budget pause.

The in-memory version is ``test_a_restarted_runtime_has_the_same_usage_and_budget_pause`` in
``tests/test_usage_persistence.py``; this one builds each runtime from the environment the way the
container does (TASK_STORE=db, DATABASE_URL), each in its own event loop like two processes. A
pending task added while the backend is down must stay pending after the restart: the reloaded
spend pauses the project before anything is enqueued.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import psycopg
import pytest
import yaml

from core.runtime import FactoryRuntime
from models.task import Status
from tests.test_gate_restart import llm_calls  # noqa: F401  (fixture)

pytestmark = pytest.mark.integration

PROJECT = "demo"


@pytest.fixture
def pg_env(monkeypatch, migrated_db_sqlalchemy_url):
    monkeypatch.setenv("FORCE_MOCK", "true")
    monkeypatch.setenv("PIPELINE_ENGINE", "langgraph")
    monkeypatch.setenv("TASK_STORE", "db")
    monkeypatch.setenv("PIPELINE_CHECKPOINTER", "postgres")
    monkeypatch.setenv("DATABASE_URL", migrated_db_sqlalchemy_url)
    monkeypatch.setenv("PROJECT_BUDGET_USD", "0.01")
    monkeypatch.delenv("DAILY_BUDGET_USD", raising=False)


def _write_tasks(project: Path, entries: list[dict]) -> None:
    (project / "tasks.yaml").write_text(yaml.safe_dump({"tasks": entries}, sort_keys=False), encoding="utf-8")


def _usage_rows(plain_url: str) -> int:
    with psycopg.connect(plain_url) as conn:
        return conn.execute("SELECT count(*) FROM usage_records").fetchone()[0]


def test_a_restarted_runtime_on_postgres_keeps_the_usage_and_the_budget_pause(tmp_path, pg_env, migrated_db_url, llm_calls):
    project = tmp_path / PROJECT
    project.mkdir()
    _write_tasks(project, [])

    async def first_life():
        runtime = FactoryRuntime(projects_dir=tmp_path)
        await runtime.startup()
        try:
            # Real-priced spend past the 0.01 budget, recorded through the live tracker (and so
            # written through to usage_records).
            runtime.usage_tracker.record(PROJECT, "t1", "openai", "gpt-4o", 1000, 1000)
            orchestrator = runtime.orchestrators[PROJECT]
            return runtime.usage_summary(PROJECT), orchestrator.is_budget_paused()
        finally:
            await runtime.shutdown()

    summary1, paused1 = asyncio.run(first_life())
    assert _usage_rows(migrated_db_url) == 1

    # While the backend is down a new task arrives in tasks.yaml.
    _write_tasks(project, [{"id": "task-001", "title": "after restart", "description": "d",
                            "priority": "high", "status": "pending"}])

    async def second_life():
        runtime = FactoryRuntime(projects_dir=tmp_path)
        await runtime.startup()
        try:
            orchestrator = runtime.orchestrators[PROJECT]
            await asyncio.sleep(0.5)  # time a worker would need to pick the task up
            stored = await runtime.task_repo.get(PROJECT, "task-001")
            return (runtime.usage_summary(PROJECT), orchestrator.is_budget_paused(),
                    runtime.snapshot(PROJECT)["pipeline"]["budget_paused"], stored.status)
        finally:
            await runtime.shutdown()

    summary2, paused2, snapshot_paused, status = asyncio.run(second_life())

    assert paused1 is True and paused2 is True and snapshot_paused is True
    assert summary2 == summary1  # project usage, daily cost, budgets and pause flag are identical
    assert summary2["project"]["call_count"] == 1 and summary2["project"]["cost"] > 0.01
    assert status is Status.PENDING  # imported but not run: the pause held across the restart
    assert llm_calls == {}
    assert _usage_rows(migrated_db_url) == 1  # nothing new was spent
