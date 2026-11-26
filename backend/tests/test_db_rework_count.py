"""DB mode stores ``tasks.rework_count`` (plan §4.1, D20; found while wiring the React stage, F12).

Before this fix only ``rerun_task`` ever wrote the column (to reset it), so the snapshot and
``GET /tasks`` always said 0 while ``GET /history`` said 1. Every rework now writes the counter
through the repository, so a page reload and a backend restart both see the real value.
File mode keeps no rework_count (tasks.yaml) and is unchanged.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml

from core.runtime import FactoryRuntime
from core.stores.memory import InMemoryRunRepository, InMemoryTaskRepository
from models.task import Approval, Status
from tests.hud_harness import make_orchestrator_with_repo
from tests.pipeline_harness import (
    QA_FAIL_REPORT,
    ScriptedClient,
    make_project,
    make_task,
    run_with_gate_actions,
)

ENGINES = ["legacy", "langgraph"]
PROJECT = "demo"


@pytest.fixture(autouse=True)
def _mock_only(monkeypatch):
    monkeypatch.setenv("FORCE_MOCK", "true")
    monkeypatch.delenv("TASK_STORE", raising=False)


def _run_in_db_mode(tmp_path, engine, task, qa_script, actions=None):
    project = make_project(tmp_path, [task])
    repo = InMemoryTaskRepository()
    asyncio.run(repo.insert_if_absent(project.name, task, source="yaml"))
    qa = ScriptedClient("Quality Assurance", "QA_agent", qa_script)
    orch, events = make_orchestrator_with_repo(project, engine=engine, task_repo=repo, qa_client=qa)
    if actions:
        result = asyncio.run(run_with_gate_actions(orch, task, actions, lambda o, t: None))
    else:
        result = asyncio.run(orch.process_task(task))
    stored = asyncio.run(repo.get(project.name, task.id))
    return result, stored, events


@pytest.mark.parametrize("engine", ENGINES)
def test_a_qa_rework_is_stored_with_the_task(tmp_path, engine):
    result, stored, events = _run_in_db_mode(tmp_path, engine, make_task(), [QA_FAIL_REPORT])

    assert result.status is Status.DONE and result.rework_count == 1
    assert stored.status is Status.DONE
    assert stored.rework_count == 1
    completed = next(d for e, d in events if e == "task:completed")
    assert completed["task"]["rework_count"] == stored.rework_count


@pytest.mark.parametrize("engine", ENGINES)
def test_a_task_failed_past_its_rework_ceiling_stores_the_same_count_as_its_event(tmp_path, engine):
    task = make_task(max_reworks=2)
    result, stored, events = _run_in_db_mode(tmp_path, engine, task, [QA_FAIL_REPORT] * 3)

    assert result.status is Status.FAILED
    failed = next(d for e, d in events if e == "task:failed")
    assert stored.status is Status.FAILED
    assert stored.rework_count == failed["task"]["rework_count"] == result.rework_count == 3


@pytest.mark.parametrize("engine", ENGINES)
def test_a_gate_rejection_rework_is_stored_too(tmp_path, engine):
    task = make_task(approval=Approval.AFTER_QA)
    result, stored, _ = _run_in_db_mode(tmp_path, engine, task, [], actions=[("reject", "please redo"), ("approve", "")])

    assert result.status is Status.DONE and result.rework_count == 1
    assert stored.rework_count == 1


# -- through the runtime: snapshot tasks[] after the run and after a restart ------------------------


def _write_project(root: Path) -> None:
    project = root / PROJECT
    project.mkdir(parents=True, exist_ok=True)
    (project / "tasks.yaml").write_text(yaml.safe_dump({"tasks": []}), encoding="utf-8")


def _runtime(root: Path, repo, runs) -> FactoryRuntime:
    return FactoryRuntime(projects_dir=root, task_store="db", task_repo=repo, run_repo=runs)


async def _wait_until(predicate, timeout=20.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if await predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition not reached in time")


def test_the_snapshot_shows_the_rework_count_after_the_run_and_after_a_restart(tmp_path):
    _write_project(tmp_path)
    repo, runs = InMemoryTaskRepository(), InMemoryRunRepository()

    async def done():
        tasks = await repo.list_tasks(PROJECT)
        return bool(tasks) and tasks[0].status is Status.DONE

    async def first_process():
        runtime = _runtime(tmp_path, repo, runs)
        await runtime.startup()
        try:
            await runtime.wait_ready(PROJECT)
            orch = runtime.orchestrators[PROJECT]
            orch._agents["qa"].llm_client = ScriptedClient("Quality Assurance", "QA_agent", [QA_FAIL_REPORT])
            await runtime.create_task(PROJECT, "needs one rework")
            await _wait_until(done)
            await asyncio.sleep(0.1)  # let the forwarder refresh the task cache
            return runtime.snapshot(PROJECT)["tasks"]
        finally:
            await runtime.shutdown()

    async def second_process():
        runtime = _runtime(tmp_path, repo, runs)
        await runtime.startup()
        try:
            await runtime.wait_ready(PROJECT)
            return runtime.snapshot(PROJECT)["tasks"], await runtime.list_tasks_db(PROJECT)
        finally:
            await runtime.shutdown()

    before = asyncio.run(first_process())
    after, listed = asyncio.run(second_process())

    assert [t["rework_count"] for t in before] == [1]
    assert [t["rework_count"] for t in after] == [1]
    assert [t.rework_count for t in listed] == [1]
