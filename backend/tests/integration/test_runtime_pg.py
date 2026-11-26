"""FactoryRuntime in DB mode against a real PostgreSQL (Task 11b, AC2).

The runtime builds its own repositories from ``DATABASE_URL`` here, the way the container does.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml

from core.runtime import FactoryRuntime
from core.stores.pg import PgRunRepository, PgTaskRepository
from db.session import create_engine_for, session_factory
from models.task import Status

pytestmark = pytest.mark.integration

PROJECT = "demo"


def _entry(task_id, status="pending", priority="medium"):
    return {"id": task_id, "title": f"title {task_id}", "description": "d", "priority": priority, "status": status}


def _write(root: Path, entries) -> Path:
    project = root / PROJECT
    project.mkdir(parents=True, exist_ok=True)
    (project / "tasks.yaml").write_text(yaml.safe_dump({"tasks": entries}, sort_keys=False), encoding="utf-8")
    return project


@pytest.fixture
def db_env(monkeypatch, migrated_db_sqlalchemy_url):
    monkeypatch.setenv("FORCE_MOCK", "true")
    monkeypatch.setenv("TASK_STORE", "db")
    monkeypatch.setenv("PIPELINE_CHECKPOINTER", "memory")
    monkeypatch.setenv("DATABASE_URL", migrated_db_sqlalchemy_url)
    return migrated_db_sqlalchemy_url


async def _statuses(url: str) -> dict[str, Status]:
    engine = create_engine_for(url, null_pool=True)
    try:
        repo = PgTaskRepository(session_factory(engine))
        return {t.id: t.status for t in await repo.list_tasks(PROJECT)}
    finally:
        await engine.dispose()


async def _wait_statuses(url: str, expected: dict[str, Status], timeout=30.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if await _statuses(url) == expected:
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"database never reached {expected}; has {await _statuses(url)}")


def test_the_runtime_builds_its_repositories_from_database_url_and_runs_tasks(tmp_path, db_env):
    project = _write(tmp_path, [_entry("task-a", priority="high"), _entry("task-b"), _entry("task-c", status="done")])
    before = ((project / "tasks.yaml").read_bytes(), (project / "tasks.yaml").stat().st_mtime_ns)

    async def scenario():
        runtime = FactoryRuntime(projects_dir=tmp_path)  # task store, URL and repositories all from the environment
        assert runtime.task_store == "db" and runtime.task_repo is None  # built during startup
        await runtime.startup()
        try:
            assert isinstance(runtime.task_repo, PgTaskRepository) and isinstance(runtime.run_repo, PgRunRepository)
            await _wait_statuses(db_env, {"task-a": Status.DONE, "task-b": Status.DONE, "task-c": Status.DONE})
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())

    assert ((project / "tasks.yaml").read_bytes(), (project / "tasks.yaml").stat().st_mtime_ns) == before


def test_a_restart_on_the_same_database_dispatches_nothing_twice_and_fails_what_was_in_flight(tmp_path, db_env):
    _write(tmp_path, [_entry("task-a"), _entry("task-b", status="in_progress")])

    async def seed_in_flight():
        from tests.stores.contract_tasks import make

        engine = create_engine_for(db_env, null_pool=True)
        repo = PgTaskRepository(session_factory(engine))
        await repo.insert_if_absent(PROJECT, make("task-b", status=Status.IN_PROGRESS, title="title task-b", description="d"), source="yaml")
        await engine.dispose()

    async def one_run():
        runtime = FactoryRuntime(projects_dir=tmp_path)
        created: list[str] = []

        async def record(project_id, event, data):
            if event == "task:created":
                created.append(data["task"]["id"])

        runtime.ws_manager.broadcast = record
        await runtime.startup()
        try:
            await _wait_statuses(db_env, {"task-a": Status.DONE, "task-b": Status.FAILED})
            await asyncio.sleep(0.3)
        finally:
            await runtime.shutdown()
        return created

    asyncio.run(seed_in_flight())
    first = asyncio.run(one_run())
    second = asyncio.run(one_run())

    assert first == ["task-a"] and second == []
