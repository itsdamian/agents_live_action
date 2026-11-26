"""A deferred lineup change applies when the running task ends (plan D17, AC18, 2026-11-24 ruling).

Right after ``task:completed`` / ``task:failed`` and with ``in_flight_task_id`` cleared, a removed
agent leaves (``agent:left``, ``pipeline:updated``) and ``pipelines.pending_seats`` is cleared,
also when no next task follows. Tasks resumed at their gate after a restart still hold it back
until the last one has finished (``tests/test_lineup_restart.py``).
"""

from __future__ import annotations

import asyncio

import pytest

import config
from core.stores import PipelineRecord
from core.stores.memory import InMemoryLineupRepository
from models.lineup import DEFAULT_LINEUP
from models.task import Approval
from tests.hud_harness import make_lineup, make_orchestrator_with_repo
from tests.pipeline_harness import QA_FAIL_REPORT, ScriptedClient, make_project, make_task

pytestmark = pytest.mark.skipif(
    config.get_pipeline_engine() != "langgraph", reason="custom lineups need the graph engine"
)

WITH_SECURITY = ("pm", "swe", "qa", "security")


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


def _world(tmp_path, task, **clients):
    """An orchestrator on (pm, swe, qa, security) with a lineup repo; one timeline of both channels."""

    timeline: list[tuple[str, dict]] = []
    orch, _ = make_orchestrator_with_repo(make_project(tmp_path, [task]), engine="langgraph",
                                          on_live_event=lambda e, d: timeline.append((e, d)), **clients)
    orch._on_event = lambda e, d: timeline.append((e, d))
    repo = InMemoryLineupRepository()
    asyncio.run(repo.import_initial(orch.project_id, [], [PipelineRecord("main", "主線", True, WITH_SECURITY)]))
    orch._lineup_repo = repo
    lineup, agents = make_lineup("pm", "swe", "qa", ("security", "check"))
    asyncio.run(orch.request_lineup(lineup, agents))
    timeline.clear()
    return orch, repo, timeline


async def _remove_security_at_the_gate(orch, repo, task):
    running = asyncio.create_task(orch.process_task(task))
    await _until(lambda: orch.is_awaiting_approval(task.id))
    assert await orch.request_lineup(DEFAULT_LINEUP, {}) == "next_task"
    assert (await repo.get_pipeline(orch.project_id, "main")).pending_seats == DEFAULT_LINEUP.ids
    assert orch.approve(task.id)
    await running
    return await repo.get_pipeline(orch.project_id, "main")


@pytest.mark.parametrize("outcome", ["task:completed", "task:failed"])
def test_a_removed_agent_leaves_as_soon_as_the_running_task_ends(tmp_path, outcome):
    if outcome == "task:completed":
        task = make_task(approval=Approval.AFTER_SWE)
        clients = {}
    else:  # QA fails with no rework left: the task fails after the gate
        task = make_task(approval=Approval.AFTER_SWE, max_reworks=0)
        clients = {"qa_client": ScriptedClient("Quality Assurance", "QA_agent", [QA_FAIL_REPORT])}
    orch, repo, timeline = _world(tmp_path, task, **clients)

    record = asyncio.run(_remove_security_at_the_gate(orch, repo, task))

    # No next task was started, yet the change is in place.
    names = [e for e, _ in timeline if e != "agent:phase"]
    end = names.index(outcome)
    assert names[end:] == [outcome, "agent:left", "pipeline:updated"]
    assert dict(timeline)["agent:left"] == {"agent_id": "security", "pipeline_id": "main"}
    assert dict(timeline)["pipeline:updated"]["pipeline"]["seats"] == ["pm", "swe", "qa"]
    assert (record.seats, record.pending_seats) == (DEFAULT_LINEUP.ids, None)
    assert orch.lineup == DEFAULT_LINEUP and orch.pending_lineup is None
    assert orch.in_flight_task_id is None
    assert list(orch.station_states) == ["pm", "swe", "qa"]


def test_the_running_task_still_finishes_on_the_lineup_it_started_with(tmp_path):
    task = make_task(approval=Approval.AFTER_SWE)
    orch, repo, timeline = _world(tmp_path, task)

    asyncio.run(_remove_security_at_the_gate(orch, repo, task))

    working = [d["station"] for e, d in timeline if e == "station:update" and d["status"] == "working"]
    assert working[-2:] == ["qa", "security"]  # after the gate: the old graph, security included


def test_the_next_task_start_applies_a_change_still_pending(tmp_path):
    """The safety net at the start of ``process_task`` stays: a change left pending is applied."""

    task = make_task()
    orch, repo, timeline = _world(tmp_path, task)
    orch._pending_lineup = (DEFAULT_LINEUP, {}, None)  # e.g. a task-end apply that could not run

    asyncio.run(orch.process_task(task))

    names = [e for e, _ in timeline if e != "agent:phase"]
    assert names[:3] == ["agent:left", "pipeline:updated", "task:created"]
    assert orch.lineup == DEFAULT_LINEUP and orch.pending_lineup is None
