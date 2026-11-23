"""A shutdown a few event-loop turns after the gate event must not lose the gate (QA gate A, AC5).

``gate_enter`` writes the status, registers the gate and emits ``task:awaiting_approval``; its
pending writes are then saved and only after that the step's checkpoint. A shutdown between the
two leaves a checkpoint whose ``next`` is empty (LangGraph leaves tasks that already have writes
out of ``next``) while ``tasks`` still holds ``gate_enter``. That state must count as "waiting at
the gate". Each case stops the first runtime ``yields`` loop turns after the gate event, so one
of them lands in that window. In-memory stores and one InMemorySaver shared across the restart;
the Postgres version is ``tests/integration/test_gate_restart_race_pg.py``.
"""

from __future__ import annotations

import asyncio

import pytest

from models.task import Status
from tests.test_gate_restart import (  # noqa: F401  (llm_calls is a fixture)
    PROJECT,
    TASK,
    World,
    _answer,
    _status,
    _wait_status,
    _write_project,
    llm_calls,
)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("FORCE_MOCK", "true")
    monkeypatch.setenv("PIPELINE_ENGINE", "langgraph")
    monkeypatch.delenv("PIPELINE_CHECKPOINTER", raising=False)


async def _stop_after_gate_event(world: World, yields: int) -> None:
    runtime, events = world.runtime()
    gate_seen = asyncio.Event()
    record = runtime.ws_manager.broadcast

    async def watch(project_id, event, data):
        await record(project_id, event, data)
        if event == "task:awaiting_approval":
            gate_seen.set()

    runtime.ws_manager.broadcast = watch
    await runtime.startup()
    await asyncio.wait_for(gate_seen.wait(), 10)
    for _ in range(yields):
        await asyncio.sleep(0)
    await runtime.shutdown()


async def _scenario(root, yields: int) -> dict:
    world = World(root)
    _write_project(root, "after_swe")
    await _stop_after_gate_event(world, yields)
    status_between = await _status(world)

    runtime, events = world.runtime()
    await runtime.startup()
    try:
        gated = runtime.orchestrators[PROJECT].is_awaiting_approval(TASK)
        status_after_startup = await _status(world)
        if gated:
            await _answer(runtime, events, ("approve", ""), 0)
            await _wait_status(world, Status.DONE)
    finally:
        await runtime.shutdown()
    return {
        "status_between": status_between,
        "status_after_startup": status_after_startup,
        "gated": gated,
        "final": await _status(world),
        "events_after": [name for name, _ in events],
    }


@pytest.mark.parametrize("yields", range(6))
def test_a_shutdown_n_loop_turns_after_the_gate_event_keeps_the_gate(tmp_path, llm_calls, yields):
    result = asyncio.run(_scenario(tmp_path, yields))

    assert result["status_between"] is Status.AWAITING_APPROVAL
    assert result["status_after_startup"] is Status.AWAITING_APPROVAL  # not "gate lost"
    assert result["gated"] is True
    assert result["final"] is Status.DONE
    assert "task:awaiting_approval" not in result["events_after"]  # gate_enter not run again
    assert llm_calls == {"PM_agent": 1, "SWE_agent": 1, "QA_agent": 1}  # PM, SWE not rerun
