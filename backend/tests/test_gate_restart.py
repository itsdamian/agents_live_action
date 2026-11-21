"""A task stopped at the approval gate survives a restart (Task 14, plan D7, AC5 flow part).

The "restart" is two FactoryRuntime instances in a row that share the in-memory repositories and
one InMemorySaver (what Postgres gives the real thing; the Postgres version of these checks is
``tests/integration/test_gate_restart.py``). Everything runs on the mock LLM.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml
from langgraph.checkpoint.memory import InMemorySaver

from core.llm_client import MockLLMClient
from core.pipeline_graph import PipelineGraphRunner
from core.runtime import REASON_GATE_LOST, FactoryRuntime
from core.stores.memory import InMemoryRunRepository, InMemoryTaskRepository
from core.stores.usage_store import InMemoryUsageStore
from models.task import Approval, Status
from tests.hud_harness import make_orchestrator_with_repo
from tests.pipeline_harness import deliverable_names, make_project, make_task, signatures

PROJECT = "demo"
TASK = "task-g"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("FORCE_MOCK", "true")
    monkeypatch.setenv("PIPELINE_ENGINE", "langgraph")  # the gate resumes through the graph
    monkeypatch.delenv("PIPELINE_CHECKPOINTER", raising=False)


@pytest.fixture
def llm_calls(monkeypatch) -> dict[str, int]:
    """LLM calls per agent name, across every runtime of the test."""

    calls: dict[str, int] = {}
    original = MockLLMClient.call

    def counting(self, prompt, system_prompt=None):
        calls[self.name] = calls.get(self.name, 0) + 1
        return original(self, prompt, system_prompt)

    monkeypatch.setattr(MockLLMClient, "call", counting)
    return calls


def _write_project(root: Path, approval: str) -> Path:
    project = root / PROJECT
    project.mkdir(parents=True, exist_ok=True)
    entry = {"id": TASK, "title": "Gate me", "description": "d", "priority": "high",
             "status": "pending", "approval": approval}
    (project / "tasks.yaml").write_text(yaml.safe_dump({"tasks": [entry]}, sort_keys=False), encoding="utf-8")
    return project


class World:
    """What survives a restart: the stores and the checkpointer."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.tasks = InMemoryTaskRepository()
        self.runs = InMemoryRunRepository()
        self.usage = InMemoryUsageStore()
        self.saver = InMemorySaver()

    def runtime(self) -> tuple[FactoryRuntime, list[tuple[str, dict]]]:
        runtime = FactoryRuntime(projects_dir=self.root, task_store="db", task_repo=self.tasks,
                                 run_repo=self.runs, usage_store=self.usage, checkpointer=self.saver)
        events: list[tuple[str, dict]] = []

        async def record(project_id, event, data):
            events.append((event, data))

        runtime.ws_manager.broadcast = record
        return runtime, events


async def _until(predicate, timeout=10.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition never became true")
        await asyncio.sleep(0.005)


def _gate_entries(events) -> int:
    return sum(1 for name, _ in events if name == "task:awaiting_approval")


async def _status(world: World) -> Status:
    return (await world.tasks.get(PROJECT, TASK)).status


async def _wait_status(world: World, status: Status) -> None:
    for _ in range(2000):
        if await _status(world) is status:
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"task never became {status.value}; is {(await _status(world)).value}")


async def _answer(runtime, events, action, entries_before):
    """Wait for the next gate entry (or a restored gate) and answer it."""

    orch = runtime.orchestrators[PROJECT]
    await _until(lambda: orch.is_awaiting_approval(TASK) and _gate_entries(events) >= entries_before)
    if action[0] == "approve":
        assert orch.approve(TASK) is True
    else:
        assert orch.reject(TASK, action[1]) is True


async def _reference(root: Path, approval: str, actions) -> dict:
    """The same task without a restart: events from the first decision on, deliverables."""

    world = World(root)
    project = _write_project(root, approval)
    runtime, events = world.runtime()
    await runtime.startup()
    try:
        first_decision = None
        for n, action in enumerate(actions, start=1):
            await _answer(runtime, events, action, n)
            if first_decision is None:
                first_decision = len(events)
        await _wait_status(world, Status.DONE)
        await asyncio.sleep(0.05)
    finally:
        await runtime.shutdown()
    return {"after": signatures(events[first_decision:]), "deliverables": deliverable_names(project, TASK)}


async def _restart_scenario(root: Path, approval: str, actions, *, stop_at_gate_event=False) -> dict:
    world = World(root)
    project = _write_project(root, approval)

    first, first_events = world.runtime()
    await first.startup()
    orch = first.orchestrators[PROJECT]
    if stop_at_gate_event:  # as early as possible: maybe before gate_wait has even run
        await _until(lambda: _gate_entries(first_events) >= 1)
    else:
        await _until(lambda: orch.is_awaiting_approval(TASK) and _gate_entries(first_events) >= 1)
    await first.shutdown()  # immediately, while the task waits at the gate
    assert await _status(world) is Status.AWAITING_APPROVAL
    deliverables_at_gate = deliverable_names(project, TASK)

    second, events = world.runtime()
    await second.startup()
    try:
        snap = second.snapshot(PROJECT)
        restored = {
            "awaiting": [(a["task"]["id"], a["station"], a["approval"]) for a in snap["pipeline"]["awaiting_approval"]],
            "station": snap["pipeline"]["stations"],
            "gated": second.orchestrators[PROJECT].is_awaiting_approval(TASK),
        }
        for n, action in enumerate(actions):
            await _answer(second, events, action, n)  # the restored gate has no new entry event
        await _wait_status(world, Status.DONE)
        await asyncio.sleep(0.05)
    finally:
        await second.shutdown()
    return {
        "restored": restored,
        "events": events,
        "after": signatures(events),
        "deliverables_at_gate": deliverables_at_gate,
        "deliverables": deliverable_names(project, TASK),
        "first_events": first_events,
    }


# -- AC5: after_swe, approve after the restart ---------------------------------------------------


def test_an_after_swe_gate_is_resumed_after_a_restart_without_rerunning_pm_or_swe(tmp_path, llm_calls):
    reference = asyncio.run(_reference(tmp_path / "ref", "after_swe", [("approve", "")]))
    reference_calls = dict(llm_calls)
    llm_calls.clear()

    result = asyncio.run(_restart_scenario(tmp_path / "run", "after_swe", [("approve", "")]))

    assert result["restored"]["awaiting"] == [(TASK, "swe", "after_swe")]
    assert result["restored"]["station"]["swe"]["status"] == "awaiting_approval"
    assert result["restored"]["gated"] is True
    names = [name for name, _ in result["events"]]
    assert "task:awaiting_approval" not in names and "task:created" not in names  # E5: not re-sent
    assert names[0] == "task:approved"
    assert result["after"] == reference["after"]  # from task:approved on, as if nothing happened
    assert llm_calls == reference_calls == {"PM_agent": 1, "SWE_agent": 1, "QA_agent": 1}
    assert result["deliverables"] == reference["deliverables"]  # nothing written twice
    assert len(result["deliverables"]) == len(set(result["deliverables"]))


def test_a_restart_right_at_the_gate_event_still_resumes(tmp_path, llm_calls):
    """Stop as soon as task:awaiting_approval is out, possibly before gate_wait ran (Task 14)."""

    reference = asyncio.run(_reference(tmp_path / "ref", "after_swe", [("approve", "")]))
    llm_calls.clear()

    result = asyncio.run(_restart_scenario(tmp_path / "run", "after_swe", [("approve", "")],
                                           stop_at_gate_event=True))

    assert result["restored"]["awaiting"] == [(TASK, "swe", "after_swe")]
    assert result["after"] == reference["after"]
    assert llm_calls == {"PM_agent": 1, "SWE_agent": 1, "QA_agent": 1}


def test_an_after_qa_gate_rejected_after_the_restart_reworks_then_completes(tmp_path, llm_calls):
    actions = [("reject", "tighten tests"), ("approve", "")]
    reference = asyncio.run(_reference(tmp_path / "ref", "after_qa", actions))
    reference_calls = dict(llm_calls)
    llm_calls.clear()

    result = asyncio.run(_restart_scenario(tmp_path / "run", "after_qa", actions))

    assert result["restored"]["awaiting"] == [(TASK, "qa", "after_qa")]
    names = [name for name, _ in result["events"]]
    assert names[0] == "task:rejected"
    assert names.count("task:awaiting_approval") == 1  # only the NEW gate after the rework
    assert result["after"] == reference["after"]
    assert llm_calls == reference_calls == {"PM_agent": 1, "SWE_agent": 2, "QA_agent": 2}
    assert result["deliverables"] == reference["deliverables"]


def test_without_a_shared_checkpointer_the_gate_is_lost_and_the_task_fails(tmp_path):
    """Per-runner in-memory savers die with the process: nothing to resume (plan D7)."""

    _write_project(tmp_path, "after_swe")
    tasks, runs = InMemoryTaskRepository(), InMemoryRunRepository()

    async def scenario():
        first = FactoryRuntime(projects_dir=tmp_path, task_store="db", task_repo=tasks, run_repo=runs)
        await first.startup()
        await _until(lambda: first.orchestrators[PROJECT].is_awaiting_approval(TASK))
        await first.shutdown()
        second = FactoryRuntime(projects_dir=tmp_path, task_store="db", task_repo=tasks, run_repo=runs)
        failed: list[dict] = []

        async def record(project_id, event, data):
            failed.append((event, data))

        second.ws_manager.broadcast = record
        await second.startup()
        try:
            return (await tasks.get(PROJECT, TASK)).status, second.snapshot(PROJECT)["pipeline"]["awaiting_approval"]
        finally:
            await second.shutdown()

    status, awaiting = asyncio.run(scenario())
    assert status is Status.FAILED and awaiting == []
    assert REASON_GATE_LOST == "gate lost after restart"


# -- the runner on its own: run() decides between resuming and starting over -----------------------


async def _to_gate(orch, task):
    proc = asyncio.create_task(orch.process_task(task))
    await _until(lambda: orch.is_awaiting_approval(task.id))
    await asyncio.sleep(0.05)  # let gate_wait reach interrupt()
    proc.cancel()
    with pytest.raises(asyncio.CancelledError):
        await proc


def test_run_resumes_a_thread_that_waits_at_the_gate_instead_of_starting_over(tmp_path, llm_calls):
    task = make_task(approval=Approval.AFTER_SWE)
    project = make_project(tmp_path, [task])
    saver = InMemorySaver()
    first, _ = make_orchestrator_with_repo(project, engine="langgraph")
    first.use_checkpointer(saver)
    asyncio.run(_to_gate(first, task))
    assert first.is_awaiting_approval(task.id) is False  # cancellation cleared the old gate

    second, events = make_orchestrator_with_repo(project, engine="langgraph")
    second.use_checkpointer(saver)

    async def resume():
        proc = asyncio.create_task(second.process_task(make_task(approval=Approval.AFTER_SWE)))
        await _until(lambda: second.is_awaiting_approval(task.id))
        assert second.approve(task.id)
        return await asyncio.wait_for(proc, 10)

    result = asyncio.run(resume())

    assert result.status is Status.DONE
    assert [name for name, _ in events][0] == "task:approved"
    assert llm_calls == {"PM_agent": 1, "SWE_agent": 1, "QA_agent": 1}
    assert result.pm_output and result.swe_output  # restored from the checkpoint


def test_run_starts_over_when_the_old_thread_is_not_at_the_gate(tmp_path, llm_calls):
    task = make_task()
    project = make_project(tmp_path, [task])
    saver = InMemorySaver()
    orch, events = make_orchestrator_with_repo(project, engine="langgraph")
    orch.use_checkpointer(saver)

    asyncio.run(orch.process_task(task))
    asyncio.run(orch.process_task(make_task()))

    assert [name for name, _ in events].count("task:created") == 2
    assert llm_calls == {"PM_agent": 2, "SWE_agent": 2, "QA_agent": 2}


def test_a_thread_stopped_just_before_gate_wait_is_resumed_too(tmp_path, llm_calls, monkeypatch):
    """Checkpoint after gate_enter, interrupt() never reached: gate_wait has no side effects
    (rule I1), so resuming runs it and then waits for the decision."""

    task = make_task(approval=Approval.AFTER_SWE)
    project = make_project(tmp_path, [task])
    saver = InMemorySaver()

    async def never_returns(self, state):
        await asyncio.Event().wait()

    with monkeypatch.context() as mp:
        mp.setattr(PipelineGraphRunner, "gate_wait", never_returns)
        first, _ = make_orchestrator_with_repo(project, engine="langgraph")
        first.use_checkpointer(saver)  # (re)compiled while gate_wait is the blocking one
    asyncio.run(_to_gate(first, task))

    second, events = make_orchestrator_with_repo(project, engine="langgraph")
    second.use_checkpointer(saver)

    async def resume():
        proc = asyncio.create_task(second.process_task(make_task(approval=Approval.AFTER_SWE)))
        await _until(lambda: second.is_awaiting_approval(task.id))
        assert second.approve(task.id)
        return await asyncio.wait_for(proc, 10)

    assert asyncio.run(resume()).status is Status.DONE
    assert "task:awaiting_approval" not in [name for name, _ in events]
    assert llm_calls == {"PM_agent": 1, "SWE_agent": 1, "QA_agent": 1}
