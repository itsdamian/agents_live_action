"""DB mode: the snapshot's ``pipeline.done_count`` / ``failed_count`` survive a restart (plan D3).

Found while wiring the React stage (F12): with nine tasks done in the database a restarted
backend said ``done_count: 0``. The database is the source of truth in DB mode, so the
counters are counted back from it at startup (after the in-flight tasks were dealt with).
File mode keeps its in-memory counters starting at 0 (the characterization tests pin that).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml

from core.runtime import FactoryRuntime
from core.stores.memory import InMemoryRunRepository, InMemoryTaskRepository
from models.task import Status
from tests.stores.contract_tasks import make

PROJECT = "demo"


@pytest.fixture(autouse=True)
def _mock_only(monkeypatch):
    monkeypatch.setenv("FORCE_MOCK", "true")
    monkeypatch.delenv("TASK_STORE", raising=False)


def _write_project(root: Path, entries=()) -> None:
    project = root / PROJECT
    project.mkdir(parents=True, exist_ok=True)
    (project / "tasks.yaml").write_text(yaml.safe_dump({"tasks": list(entries)}), encoding="utf-8")


async def _wait_until(predicate, timeout=20.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if await predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition not reached in time")


def _counts(runtime) -> tuple[int, int]:
    pipeline = runtime.snapshot(PROJECT)["pipeline"]
    return pipeline["done_count"], pipeline["failed_count"]


def test_a_restarted_backend_counts_done_and_failed_tasks_from_the_database(tmp_path):
    _write_project(tmp_path)
    repo, runs = InMemoryTaskRepository(), InMemoryRunRepository()

    async def scenario():
        # What earlier processes left behind: 3 done, 2 failed, 1 cut off mid-run.
        for i in range(3):
            await repo.insert_if_absent(PROJECT, make(f"task-d{i}"), source="api")
            await repo.set_status(PROJECT, f"task-d{i}", Status.DONE)
        for i in range(2):
            await repo.insert_if_absent(PROJECT, make(f"task-f{i}"), source="api")
            await repo.set_status(PROJECT, f"task-f{i}", Status.FAILED)
        await repo.insert_if_absent(PROJECT, make("task-cut"), source="api")
        await repo.set_status(PROJECT, "task-cut", Status.IN_PROGRESS)

        runtime = FactoryRuntime(projects_dir=tmp_path, task_store="db", task_repo=repo, run_repo=runs)
        await runtime.startup()
        try:
            await runtime.wait_ready(PROJECT)
            return _counts(runtime)
        finally:
            await runtime.shutdown()

    # The interrupted task is failed by the startup recovery, so it counts as failed.
    assert asyncio.run(scenario()) == (3, 3)


def test_the_counters_keep_counting_from_the_database_value(tmp_path):
    _write_project(tmp_path)
    repo, runs = InMemoryTaskRepository(), InMemoryRunRepository()

    async def scenario():
        for i in range(2):
            await repo.insert_if_absent(PROJECT, make(f"task-d{i}"), source="api")
            await repo.set_status(PROJECT, f"task-d{i}", Status.DONE)
        runtime = FactoryRuntime(projects_dir=tmp_path, task_store="db", task_repo=repo, run_repo=runs)
        events: list[tuple[str, dict]] = []

        async def recording_broadcast(project_id, event, data):
            events.append((event, data))

        runtime.ws_manager.broadcast = recording_broadcast
        await runtime.startup()
        try:
            await runtime.wait_ready(PROJECT)
            task = await runtime.create_task(PROJECT, "one more")

            async def done():
                stored = await repo.get(PROJECT, task.id)
                return stored.status is Status.DONE

            await _wait_until(done)
            await asyncio.sleep(0.1)
            return _counts(runtime), events
        finally:
            await runtime.shutdown()

    counts, events = asyncio.run(scenario())
    assert counts == (3, 0)
    assert [d["done_count"] for e, d in events if e == "task:completed"] == [3]


def test_file_mode_counters_still_start_at_zero(tmp_path):
    _write_project(tmp_path, [{"id": "task-old", "title": "old", "description": "", "priority": "medium",
                               "status": "done"}])

    async def scenario():
        runtime = FactoryRuntime(projects_dir=tmp_path)  # file mode
        await runtime.startup()
        try:
            runtime.ensure_project(PROJECT)
            return _counts(runtime)
        finally:
            await runtime.shutdown()

    assert asyncio.run(scenario()) == (0, 0)
