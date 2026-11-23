"""DB mode must behave exactly like file mode (Task 10, AC1, plan D2/D3).

Every scenario S1-S12 is run twice: in file mode (tasks.yaml write-back, the behaviour the
existing characterization tests pin with hard-coded values) and in DB mode with an injected
InMemory TaskRepository. Event signatures, the sequence of status writes, deliverables, the
final task fields, counters and what a paused gate looks like must be identical, and DB mode
must leave tasks.yaml alone.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from core.stores.memory import InMemoryTaskRepository
from models.task import Approval, Status
from tests.hud_harness import (
    SCENARIOS,
    make_orchestrator_with_repo,
    record_repo_statuses,
    run_scenario,
)
from tests.pipeline_harness import make_project, make_task

ENGINES = ["legacy", "langgraph"]


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("scenario_id", list(SCENARIOS))
def test_db_mode_is_observably_identical_to_file_mode(tmp_path, monkeypatch, engine, scenario_id):
    spec = SCENARIOS[scenario_id]

    file_obs, file_extras = run_scenario(
        spec, mode="file", engine=engine, workdir=tmp_path / "file", monkeypatch=monkeypatch
    )
    db_obs, db_extras = run_scenario(
        spec, mode="db", engine=engine, workdir=tmp_path / "db", monkeypatch=monkeypatch,
        repo=InMemoryTaskRepository(),
    )

    assert db_obs == file_obs
    # DB mode never touches tasks.yaml: no write-back call, file bytes unchanged.
    assert db_extras["file_writes"] == []
    assert db_extras["yaml_unchanged"] is True
    # ...while file mode really does write (so the comparison above is not vacuous).
    assert file_extras["file_writes"] == file_obs["status_writes"] and file_obs["status_writes"]
    assert file_extras["yaml_unchanged"] is False


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize(
    "scenario_id, writes, final",
    [
        ("S1", ["in_progress", "done"], "done"),
        ("S4", ["in_progress", "failed"], "failed"),
        ("S6", ["in_progress", "awaiting_approval", "in_progress", "done"], "done"),
        ("S9", ["in_progress", "awaiting_approval", "in_progress", "awaiting_approval", "in_progress", "failed"], "failed"),
    ],
)
def test_the_repository_receives_the_expected_status_sequence(tmp_path, monkeypatch, engine, scenario_id, writes, final):
    repo = InMemoryTaskRepository()

    obs, _ = run_scenario(
        SCENARIOS[scenario_id], mode="db", engine=engine, workdir=tmp_path, monkeypatch=monkeypatch, repo=repo
    )

    assert obs["status_writes"] == writes
    assert obs["stored_status"] == final


@pytest.mark.parametrize("engine", ENGINES)
def test_gate_becomes_visible_only_after_awaiting_approval_is_stored(tmp_path, engine):
    """The 12c rule in DB mode: persist the status first, register the gate second."""

    task = make_task(approval=Approval.AFTER_SWE)
    project = make_project(tmp_path, [task])
    repo = InMemoryTaskRepository()
    asyncio.run(repo.insert_if_absent(project.name, task, source="yaml"))
    orch, events = make_orchestrator_with_repo(project, engine=engine, task_repo=repo)
    during_write: list[dict] = []
    real_set_status = repo.set_status

    async def spying_set_status(project_id, task_id, status):
        if status is Status.AWAITING_APPROVAL:
            during_write.append({
                "gate_visible": orch.is_awaiting_approval(task_id),
                "snapshot": orch.snapshot()["awaiting_approval"],
                "approve": orch.approve(task_id),
            })
            await asyncio.sleep(0.02)  # widen the window
        return await real_set_status(project_id, task_id, status)

    repo.set_status = spying_set_status

    async def run():
        proc = asyncio.create_task(orch.process_task(task))
        for _ in range(500):
            if orch.is_awaiting_approval(task.id) and any(n == "task:awaiting_approval" for n, _ in events):
                break
            await asyncio.sleep(0.005)
        # By the time the gate is visible the repository already says awaiting_approval.
        assert (await repo.get(project.name, task.id)).status is Status.AWAITING_APPROVAL
        assert orch.approve(task.id) is True
        return await asyncio.wait_for(proc, timeout=10)

    result = asyncio.run(run())

    assert during_write == [{"gate_visible": False, "snapshot": [], "approve": False}]
    assert result.status is Status.DONE


@pytest.mark.parametrize("engine", ENGINES)
def test_a_task_the_repository_does_not_know_still_runs_to_the_end(tmp_path, engine):
    """set_status on an unknown id returns False (like a TODO.md task in file mode): not an error."""

    task = make_task()
    project = make_project(tmp_path, [task])
    repo = InMemoryTaskRepository()  # the task is NOT inserted
    recorded = record_repo_statuses(repo)
    orch, events = make_orchestrator_with_repo(project, engine=engine, task_repo=repo)

    result = asyncio.run(orch.process_task(task))

    assert result.status is Status.DONE
    assert recorded == ["in_progress", "done"]  # attempted, quietly ignored
    assert asyncio.run(repo.get(project.name, task.id)) is None


@pytest.mark.parametrize("engine", ENGINES)
def test_an_internal_pipeline_error_is_stored_as_failed(tmp_path, engine):
    """The worker loop's last-resort path (_fail_task) goes through the repository too."""

    task = make_task()
    project = make_project(tmp_path, [task])
    repo = InMemoryTaskRepository()
    asyncio.run(repo.insert_if_absent(project.name, task, source="yaml"))
    recorded = record_repo_statuses(repo)
    orch, events = make_orchestrator_with_repo(project, engine=engine, task_repo=repo)

    async def exploding(*args, **kwargs):
        raise RuntimeError("boom")

    orch._run_station = exploding

    async def run():
        orch.start()
        orch.enqueue(task)
        await asyncio.wait_for(orch._queue.join(), 10)
        await orch.stop()

    asyncio.run(run())

    assert recorded[-1] == "failed"
    assert asyncio.run(repo.get(project.name, task.id)).status is Status.FAILED
    assert [d["reason"] for n, d in events if n == "task:failed"] == ["internal pipeline error"]


def test_without_a_repository_the_orchestrator_is_in_file_mode(tmp_path):
    project = make_project(tmp_path, [make_task()])
    orch, _ = make_orchestrator_with_repo(project, engine="langgraph")

    assert orch._task_repo is None


@pytest.mark.parametrize("engine", ENGINES)
def test_updated_at_is_still_refreshed_on_the_live_task_in_db_mode(tmp_path, engine):
    task = make_task()
    before = task.updated_at
    project = make_project(tmp_path, [task])
    repo = InMemoryTaskRepository(clock=lambda: datetime(2030, 1, 1))
    asyncio.run(repo.insert_if_absent(project.name, task, source="yaml"))
    orch, _ = make_orchestrator_with_repo(project, engine=engine, task_repo=repo)

    asyncio.run(orch.process_task(task))

    assert task.updated_at > before
    assert asyncio.run(repo.get(project.name, task.id)).updated_at == datetime(2030, 1, 1)
