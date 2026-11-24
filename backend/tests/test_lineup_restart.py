"""A lineup change and a task resumed at its gate after a restart (plan D17, 2026-11-23 ruling).

A task resumed from its gate keeps the checkpoint of the graph it started on, so until every
resumed task has finished the line counts as busy: ``request_lineup`` answers ``next_task`` and
keeps the change pending (also in ``pipelines.pending_seats``) and applies it as soon as the last
resumed task has ended (2026-11-24 ruling). Without a resumed task the change applies at once. The Postgres version is
``tests/integration/test_lineup_restart_pg.py``.
"""

from __future__ import annotations

import asyncio

import pytest
from langgraph.checkpoint.memory import InMemorySaver

import config
from core.stores import PipelineRecord
from core.stores.memory import InMemoryLineupRepository
from models.lineup import DEFAULT_LINEUP
from models.task import Approval, Status
from tests.hud_harness import make_lineup, make_orchestrator_with_repo
from tests.pipeline_harness import make_project, make_task

pytestmark = pytest.mark.skipif(
    config.get_pipeline_engine() != "langgraph", reason="restoring a gate and custom lineups need the graph engine"
)

DEFAULT_SEATS = ("pm", "swe", "qa")
NEW_SEATS = ("pm", "designer", "swe", "qa")


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MOCK_LLM_DELAY", "0")


async def _until(predicate, timeout=10.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.005)


async def _to_gate(orch, task):
    """Run ``task`` to its gate, then stop as a restart would."""

    proc = asyncio.create_task(orch.process_task(task))
    await _until(lambda: orch.is_awaiting_approval(task.id))
    await asyncio.sleep(0.05)  # let gate_wait reach interrupt()
    proc.cancel()
    with pytest.raises(asyncio.CancelledError):
        await proc


def _working_stations(events, task_id):
    return [d["station"] for e, d in events
            if e == "station:update" and d["task"]["id"] == task_id and d["status"] == "working"]


def _stations_seen(events, task_id):
    return {d.get("station") for _, d in events if d.get("task", {}).get("id") == task_id}


async def _repo(project_name, pending=None):
    repo = InMemoryLineupRepository()
    await repo.import_initial(project_name, [], [PipelineRecord("main", "主線", True, DEFAULT_SEATS)])
    if pending is not None:
        await repo.set_pending_seats(project_name, "main", pending)
    return repo


def _first_process(project, saver, task):
    first, _ = make_orchestrator_with_repo(project, engine="langgraph")
    first.use_checkpointer(saver)
    asyncio.run(_to_gate(first, task))


@pytest.mark.parametrize("pending_in_db", [False, True], ids=["asked-after-restore", "pending-seats-in-db"])
def test_a_lineup_change_waits_for_the_task_resumed_at_its_gate(tmp_path, pending_in_db):
    gated, later = make_task("task-001", approval=Approval.AFTER_SWE), make_task("task-002")
    project = make_project(tmp_path, [gated, later])
    saver = InMemorySaver()
    _first_process(project, saver, gated)

    second, events = make_orchestrator_with_repo(project, engine="langgraph")
    second.use_checkpointer(saver)
    stored = make_task("task-001", approval=Approval.AFTER_SWE, status=Status.AWAITING_APPROVAL)
    lineup, agents = make_lineup("pm", ("designer", "make"), "swe", "qa")

    async def scenario():
        repo = await _repo(project.name, NEW_SEATS if pending_in_db else None)
        second._lineup_repo = repo
        assert await second.restore_gate(stored) is True
        # Restored, the worker has not resumed it yet: the line is busy with the restored task.
        assert second.in_flight_task_id is None
        assert await second.request_lineup(lineup, agents) == "next_task"
        assert second.lineup == DEFAULT_LINEUP and second.pending_lineup == lineup
        record = await repo.get_pipeline(project.name, "main")
        assert (record.seats, record.pending_seats) == (DEFAULT_SEATS, NEW_SEATS)

        second.start()
        assert second.approve(stored.id)
        await _until(lambda: stored.status is Status.DONE)
        # The resumed task ran to the end on the graph it started on; the change applies as soon
        # as it has ended (AC18, 2026-11-24 ruling), before any next task.
        await _until(lambda: second.pending_lineup is None)
        assert "designer" not in _stations_seen(events, stored.id)
        assert second.lineup == lineup
        record = await repo.get_pipeline(project.name, "main")
        assert (record.seats, record.pending_seats) == (NEW_SEATS, None)

        second.enqueue(later)
        await _until(lambda: later.status is Status.DONE)
        record = await repo.get_pipeline(project.name, "main")
        await second.stop()
        return record

    record = asyncio.run(scenario())
    assert (record.seats, record.pending_seats) == (NEW_SEATS, None)
    assert second.lineup == lineup and second.pending_lineup is None
    assert _working_stations(events, "task-001") == ["qa"]
    assert _working_stations(events, "task-002") == list(NEW_SEATS)


def test_without_a_resumed_task_the_lineup_change_applies_at_once(tmp_path):
    project = make_project(tmp_path, [make_task()])
    orch, _ = make_orchestrator_with_repo(project, engine="langgraph")
    orch.use_checkpointer(InMemorySaver())
    stored = make_task(approval=Approval.AFTER_SWE, status=Status.AWAITING_APPROVAL)  # no checkpoint
    lineup, agents = make_lineup("pm", ("designer", "make"), "swe", "qa")

    async def scenario():
        repo = await _repo(project.name, NEW_SEATS)
        orch._lineup_repo = repo
        assert await orch.restore_gate(stored) is False
        assert await orch.request_lineup(lineup, agents) == "now"
        return await repo.get_pipeline(project.name, "main")

    record = asyncio.run(scenario())
    assert (record.seats, record.pending_seats) == (NEW_SEATS, None)
    assert orch.lineup == lineup and orch.pending_lineup is None


def test_the_line_stays_busy_until_every_resumed_task_has_finished(tmp_path):
    first_task = make_task("task-001", approval=Approval.AFTER_SWE)
    second_task = make_task("task-002", approval=Approval.AFTER_SWE)
    project = make_project(tmp_path, [first_task, second_task])
    saver = InMemorySaver()
    _first_process(project, saver, first_task)
    _first_process(project, saver, second_task)

    orch, events = make_orchestrator_with_repo(project, engine="langgraph")
    orch.use_checkpointer(saver)
    stored = [make_task(t.id, approval=Approval.AFTER_SWE, status=Status.AWAITING_APPROVAL)
              for t in (first_task, second_task)]
    lineup, agents = make_lineup("pm", ("designer", "make"), "swe", "qa")

    async def scenario():
        for task in stored:
            assert await orch.restore_gate(task) is True
        orch.start()
        assert orch.approve(stored[0].id)
        await _until(lambda: stored[0].status is Status.DONE)
        # One resumed task is still waiting at its gate.
        assert await orch.request_lineup(lineup, agents) == "next_task"
        assert orch.lineup == DEFAULT_LINEUP
        assert orch.approve(stored[1].id)
        await _until(lambda: stored[1].status is Status.DONE)
        await _until(lambda: orch.pending_lineup is None)
        await orch.stop()

    asyncio.run(scenario())
    assert "designer" not in _stations_seen(events, "task-002")
    # The last resumed task has ended: the change is applied (AC18, 2026-11-24 ruling).
    assert orch.lineup == lineup and orch.pending_lineup is None


def test_a_pending_change_is_not_applied_while_another_resumed_task_still_waits(tmp_path):
    """The first resumed task ending does not apply the change: the second still needs the old graph."""

    first_task = make_task("task-001", approval=Approval.AFTER_SWE)
    second_task = make_task("task-002", approval=Approval.AFTER_SWE)
    project = make_project(tmp_path, [first_task, second_task])
    saver = InMemorySaver()
    _first_process(project, saver, first_task)
    _first_process(project, saver, second_task)

    orch, events = make_orchestrator_with_repo(project, engine="langgraph")
    orch.use_checkpointer(saver)
    stored = [make_task(t.id, approval=Approval.AFTER_SWE, status=Status.AWAITING_APPROVAL)
              for t in (first_task, second_task)]
    lineup, agents = make_lineup("pm", ("designer", "make"), "swe", "qa")

    async def scenario():
        repo = await _repo(project.name, NEW_SEATS)
        orch._lineup_repo = repo
        for task in stored:
            assert await orch.restore_gate(task) is True
        assert await orch.request_lineup(lineup, agents) == "next_task"
        orch.start()
        assert orch.approve(stored[0].id)
        await _until(lambda: stored[0].status is Status.DONE)
        await _until(lambda: orch.in_flight_task_id == stored[1].id)  # the worker moved on to the second
        assert orch.lineup == DEFAULT_LINEUP and orch.pending_lineup == lineup
        assert (await repo.get_pipeline(project.name, "main")).pending_seats == NEW_SEATS
        assert orch.approve(stored[1].id)
        await _until(lambda: stored[1].status is Status.DONE)
        await _until(lambda: orch.pending_lineup is None)
        record = await repo.get_pipeline(project.name, "main")
        await orch.stop()
        return record

    record = asyncio.run(scenario())
    assert "designer" not in _stations_seen(events, "task-002")
    assert _working_stations(events, "task-002") == ["qa"]
    assert (record.seats, record.pending_seats) == (NEW_SEATS, None)
    assert orch.lineup == lineup
