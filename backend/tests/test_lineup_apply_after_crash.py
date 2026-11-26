"""The worker's exception paths also apply a deferred lineup change (plan D17, AC18, 2026-11-24).

When ``process_task`` raises, the worker fails the task itself (``internal pipeline error``) and
then applies the pending change, both for a queued task and for one resumed at its gate after a
restart. The normal path is ``tests/test_lineup_apply_at_task_end.py``.
"""

from __future__ import annotations

import asyncio

import pytest

import config
from core.stores import PipelineRecord
from core.stores.memory import InMemoryLineupRepository
from models.lineup import DEFAULT_LINEUP
from models.task import Status
from tests.hud_harness import make_lineup, make_orchestrator_with_repo
from tests.pipeline_harness import make_project, make_task

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


@pytest.mark.parametrize("path", ["queued", "resumed-at-gate"])
def test_a_crashing_task_still_lets_the_pending_change_apply(tmp_path, path):
    task = make_task()
    timeline: list[tuple[str, dict]] = []
    orch, _ = make_orchestrator_with_repo(make_project(tmp_path, [task]), engine="langgraph",
                                          on_live_event=lambda e, d: timeline.append((e, d)))
    orch._on_event = lambda e, d: timeline.append((e, d))
    repo = InMemoryLineupRepository()

    async def crash(running_task):
        # The line is busy with this task: the change waits, then the run blows up.
        assert await orch.request_lineup(DEFAULT_LINEUP, {}) == "next_task"
        assert (await repo.get_pipeline(orch.project_id, "main")).pending_seats == DEFAULT_LINEUP.ids
        raise RuntimeError("boom")

    async def scenario():
        await repo.import_initial(orch.project_id, [], [PipelineRecord("main", "主線", True, WITH_SECURITY)])
        orch._lineup_repo = repo
        lineup, agents = make_lineup("pm", "swe", "qa", ("security", "check"))
        await orch.request_lineup(lineup, agents)
        timeline.clear()
        orch._graph_runner.run = crash
        if path == "queued":
            orch.enqueue(task)
        else:
            orch._resume_first.append(task)  # what restore_gate leaves for the worker
            orch._gate_events[task.id] = asyncio.Event()
        orch.start()
        await _until(lambda: task.status is Status.FAILED)
        await _until(lambda: orch.pending_lineup is None)
        record = await repo.get_pipeline(orch.project_id, "main")
        await orch.stop()
        return record

    record = asyncio.run(scenario())

    names = [e for e, _ in timeline if e != "agent:phase"]
    failed = names.index("task:failed")
    assert names[failed:] == ["task:failed", "agent:left", "pipeline:updated"]
    assert dict(timeline)["task:failed"]["reason"] == "internal pipeline error"
    assert dict(timeline)["agent:left"] == {"agent_id": "security", "pipeline_id": "main"}
    assert (record.seats, record.pending_seats) == (DEFAULT_LINEUP.ids, None)
    assert orch.lineup == DEFAULT_LINEUP and orch.in_flight_task_id is None
