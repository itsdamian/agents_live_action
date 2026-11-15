"""Gate robustness: cross-thread reject, stop() windows, SWE context (AC5, AC6, S12).

Complements tests/test_pipeline_characterization.py. Tests parametrized with
``ENGINES`` must behave the same on both engines; the two ``langgraph``-only
tests cover cleanup windows that exist only because the langgraph driver
suspends between ``gate_enter`` and ``gate_wait``.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from models.task import Approval, Status
from tests.pipeline_harness import (
    QA_FAIL_REPORT,
    ScriptedClient,
    make_orchestrator,
    make_project,
    make_task,
    run_with_gate_actions,
)

ENGINES = ["legacy", "langgraph"]


def _count_gate_events(events) -> int:
    return sum(1 for name, _ in events if name == "task:awaiting_approval")


async def _wait_for_gate(orch, events, task_id, entries=1):
    """Wait until the ``entries``-th gate entry is registered *and* announced."""

    for _ in range(500):
        if orch.is_awaiting_approval(task_id) and _count_gate_events(events) >= entries:
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"task {task_id} never reached gate entry #{entries}")


def _assert_gate_fully_cleared(orch, task):
    assert orch.is_awaiting_approval(task.id) is False
    assert orch.snapshot()["awaiting_approval"] == []
    assert orch._approval_gates == {}
    assert orch._awaiting == {}
    assert orch._gate_events == {}
    assert orch._approval_decisions == {}
    # A dead gate must not accept a decision.
    assert orch.approve(task.id) is False
    assert orch.reject(task.id, "late") is False


# -- AC5: reject() from another thread ----------------------------------------------


@pytest.mark.parametrize("engine", ENGINES)
def test_reject_from_other_thread(tmp_path, engine):
    """REST runs ``reject`` in a threadpool; the worker must pick it up and rework."""

    task = make_task(approval=Approval.AFTER_SWE)
    project = make_project(tmp_path, [task])
    swe_client = ScriptedClient("Software Engineer", "SWE_agent")
    orch, events = make_orchestrator(project, engine=engine, swe_client=swe_client)
    returned: list[bool] = []

    async def run():
        orch.start()
        orch.enqueue(task)
        await _wait_for_gate(orch, events, task.id, 1)
        thread = threading.Thread(
            target=lambda: returned.append(orch.reject(task.id, "needs validation"))
        )
        thread.start()
        thread.join()
        # The rework pass re-runs SWE and pauses at a second gate.
        await _wait_for_gate(orch, events, task.id, 2)
        assert orch.approve(task.id) is True
        await asyncio.wait_for(orch._queue.join(), 10)
        await orch.stop()

    asyncio.run(run())

    assert returned == [True]
    assert task.status is Status.DONE
    assert task.rework_count == 1
    assert (orch.done_count, orch.failed_count) == (1, 0)
    assert len(swe_client.prompts) == 2
    assert "needs validation" in swe_client.prompts[1]
    assert [e for e, _ in events if e == "task:rejected"] == ["task:rejected"]


# -- AC6: stop() while the gate is registered but not yet announced ------------------


@pytest.mark.parametrize("engine", ENGINES)
def test_stop_before_gate_event_is_emitted_clears_gate(tmp_path, engine):
    task = make_task(approval=Approval.AFTER_SWE)
    project = make_project(tmp_path, [task])
    orch, events = make_orchestrator(project, engine=engine)
    reached = asyncio.Event
    original_on_event = orch._on_event

    async def run():
        entered = reached()

        # Block inside the ``task:awaiting_approval`` emit: at that moment the gate
        # is registered (is_awaiting_approval is True) but nobody waits on it yet.
        async def blocking(event, data):
            original_on_event(event, data)
            if event == "task:awaiting_approval":
                entered.set()
                await asyncio.Event().wait()

        orch._on_event = blocking
        orch.start()
        orch.enqueue(task)
        await asyncio.wait_for(entered.wait(), 10)
        assert orch.is_awaiting_approval(task.id) is True  # precondition
        await orch.stop()

    asyncio.run(run())

    _assert_gate_fully_cleared(orch, task)


# -- S12: does an empty reject note reach SWE's context? ------------------------------


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize(
    "note, expect_feedback",
    [("", False), ("fix the validation", True)],
)
def test_reject_note_reaches_swe_context_only_when_non_empty(
    tmp_path, engine, note, expect_feedback
):
    task = make_task(approval=Approval.AFTER_SWE)
    project = make_project(tmp_path, [task])
    orch, _events = make_orchestrator(project, engine=engine)
    swe_agent = orch._agents["swe"]
    real_run = swe_agent.run
    contexts: list[dict] = []

    def recording_run(task_arg, context=None, project_path=None):
        contexts.append(dict(context))
        return real_run(task_arg, context, project_path)

    swe_agent.run = recording_run

    asyncio.run(
        run_with_gate_actions(orch, task, [("reject", note), ("approve", "")])
    )

    assert len(contexts) == 2
    assert set(contexts[0]) == {"pm_output"}
    if expect_feedback:
        assert contexts[1]["approval_feedback"] == note
    else:
        assert "approval_feedback" not in contexts[1]
    # after_swe: QA has not run yet, so no qa_output is passed along.
    assert "qa_output" not in contexts[1]


@pytest.mark.parametrize("engine", ENGINES)
def test_reject_note_is_not_repeated_after_swe_succeeds(tmp_path, engine):
    """after_qa: reject -> SWE redo -> QA fails -> rework: the old note must be gone.

    The reviewer's note is only meant for the SWE run that follows the rejection.
    A rework triggered later by a QA failure must not carry it again.
    """

    note = "tighten tests"
    task = make_task(approval=Approval.AFTER_QA)
    project = make_project(tmp_path, [task])
    # QA: pass (-> gate, rejected), then FAIL (-> rework), then pass (-> gate, approved).
    qa_client = ScriptedClient("Quality Assurance", "QA_agent", [None, QA_FAIL_REPORT])
    orch, _events = make_orchestrator(project, engine=engine, qa_client=qa_client)
    swe_agent = orch._agents["swe"]
    real_run = swe_agent.run
    contexts: list[dict] = []

    def recording_run(task_arg, context=None, project_path=None):
        contexts.append(dict(context))
        return real_run(task_arg, context, project_path)

    swe_agent.run = recording_run

    result = asyncio.run(
        run_with_gate_actions(orch, task, [("reject", note), ("approve", "")])
    )

    assert result.status is Status.DONE
    assert result.rework_count == 2  # one rejection + one QA failure
    assert len(qa_client.prompts) == 3
    assert len(contexts) == 3
    assert set(contexts[0]) == {"pm_output"}
    # Rework after the rejection: QA's first report and the reviewer's note.
    assert set(contexts[1]) == {"pm_output", "qa_output", "approval_feedback"}
    assert contexts[1]["approval_feedback"] == note
    assert "Result: PASS" in contexts[1]["qa_output"]
    # Rework after the QA failure: the failure report only, never the old note.
    assert set(contexts[2]) == {"pm_output", "qa_output"}
    assert "Result: FAIL" in contexts[2]["qa_output"]


# -- langgraph-only windows ------------------------------------------------------------


def test_langgraph_stop_between_gate_enter_and_gate_wait_clears_gate(tmp_path):
    """T5: cancelled after gate_enter finished but before _gate_wait started."""

    task = make_task(approval=Approval.AFTER_SWE)
    project = make_project(tmp_path, [task])
    orch, _events = make_orchestrator(project, engine="langgraph")

    async def run():
        reached = asyncio.Event()

        # Replace the driver's wait so the real _gate_wait (and its finally) never
        # runs: only the runner's own cleanup can clear the gate.
        async def stuck(task_arg):
            reached.set()
            await asyncio.sleep(3600)

        orch._gate_wait = stuck
        orch.start()
        orch.enqueue(task)
        await asyncio.wait_for(reached.wait(), 10)
        assert orch.is_awaiting_approval(task.id) is True  # precondition
        await orch.stop()

    asyncio.run(run())

    _assert_gate_fully_cleared(orch, task)


def test_langgraph_approve_then_cancel_leaves_no_stale_decision(tmp_path):
    """A decision recorded but never consumed must not outlive the cancelled run."""

    task = make_task(approval=Approval.AFTER_SWE)
    project = make_project(tmp_path, [task])
    orch, events = make_orchestrator(project, engine="langgraph")

    async def run():
        orch.start()
        orch.enqueue(task)
        await _wait_for_gate(orch, events, task.id, 1)
        assert orch.approve(task.id) is True
        # Cancel before the worker gets a chance to consume the decision.
        await orch.stop()

    asyncio.run(run())

    _assert_gate_fully_cleared(orch, task)
