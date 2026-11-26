"""AC5 on a real PostgreSQL: a shutdown a few loop turns after the gate event keeps the gate.

QA gate A found that stopping right after ``gate_enter``'s pending writes were stored, but before
that step's checkpoint, made the restart mark the task failed ("gate lost after restart"). Each
case stops the first runtime ``yields`` event-loop turns after ``task:awaiting_approval``; two
turns hit that window every time before the fix. Each runtime has its own event loop and its own
connections, like two processes one after the other.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from models.task import Status
from tests.integration.test_gate_restart import (
    TASK,
    _decide,
    _runtime,
    _stored_status,
    _write_project,
    pg_env,  # noqa: F401  (fixture)
)
from tests.test_gate_restart import llm_calls  # noqa: F401  (fixture)

pytestmark = pytest.mark.integration


def _stop_after_gate_event(root: Path, yields: int) -> None:
    async def scenario():
        runtime, events = _runtime(root)
        gate_seen = asyncio.Event()
        record = runtime.ws_manager.broadcast

        async def watch(project_id, event, data):
            await record(project_id, event, data)
            if event == "task:awaiting_approval":
                gate_seen.set()

        runtime.ws_manager.broadcast = watch
        await runtime.startup()
        await asyncio.wait_for(gate_seen.wait(), 30)
        for _ in range(yields):
            await asyncio.sleep(0)
        await runtime.shutdown()

    asyncio.run(scenario())


def _restart_and_approve(root: Path) -> dict:
    out: dict = {}

    async def scenario():
        runtime, events = _runtime(root)
        await runtime.startup()
        try:
            out["status_after_startup"] = (await runtime.task_repo.get("demo", TASK)).status
            out["gated"] = runtime.orchestrators["demo"].is_awaiting_approval(TASK)
            if out["gated"]:
                await _decide(runtime, events, "demo", [("approve", "")], entries_offset=0)
            out["final"] = (await runtime.task_repo.get("demo", TASK)).status
            out["events_after"] = [name for name, _ in events]
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())
    return out


def _run(root: Path, plain_url: str, yields: int) -> dict:
    _write_project(root, "demo", "after_swe")
    _stop_after_gate_event(root, yields)
    status_between = asyncio.run(_stored_status(plain_url))
    return {"status_between": status_between, **_restart_and_approve(root)}


def _assert_gate_kept(result: dict, llm_calls: dict) -> None:
    assert result["status_between"] == "awaiting_approval"
    assert result["status_after_startup"] is Status.AWAITING_APPROVAL  # not "gate lost"
    assert result["gated"] is True
    assert result["final"] is Status.DONE
    assert "task:awaiting_approval" not in result["events_after"]  # gate_enter not run again
    assert llm_calls == {"PM_agent": 1, "SWE_agent": 1, "QA_agent": 1}  # PM, SWE not rerun


def test_a_shutdown_two_loop_turns_after_the_gate_event_keeps_the_gate(tmp_path, pg_env, migrated_db_url, llm_calls):
    _assert_gate_kept(_run(tmp_path, migrated_db_url, 2), llm_calls)


@pytest.mark.parametrize("yields", range(6))
def test_a_shutdown_n_loop_turns_after_the_gate_event_keeps_the_gate(tmp_path, pg_env, migrated_db_url, llm_calls, yields):
    _assert_gate_kept(_run(tmp_path, migrated_db_url, yields), llm_calls)
