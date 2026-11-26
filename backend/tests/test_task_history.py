"""HistoryRecorder: events -> task_runs / task_run_steps (Task 15a, plan D20), in memory.

The recorder is fed the very events a real orchestrator emits (mock LLM), so the history is
checked against the pipeline as it behaves, not against a hand-written event list. Cost and
token totals are checked with hand-made usage records, since the mock provider costs nothing.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from core.stores.memory import InMemoryRunRepository, InMemoryTaskRepository
from core.task_history import DEFAULT_SEATS, REASON_SUPERSEDED, HistoryRecorder, TrackerUsage
from core.usage_tracker import UsageTracker
from models.task import Approval
from tests.hud_harness import make_orchestrator_with_repo
from tests.pipeline_harness import QA_FAIL_REPORT, ScriptedClient, make_project, make_task, run_with_gate_actions

PROJECT = "demo-project"  # make_project's folder name


@pytest.fixture(autouse=True)
def _mock_only(monkeypatch):
    monkeypatch.setenv("FORCE_MOCK", "true")


def _recorder(runs=None, tracker=None, seats=None) -> HistoryRecorder:
    # Real clock: the tracker stamps its records with datetime.now too, and "this run's usage"
    # means records at or after the run's start.
    return HistoryRecorder(
        runs or InMemoryRunRepository(),
        usage=TrackerUsage(tracker or UsageTracker()),
        seats=seats,
    )


def _run_pipeline(tmp_path, recorder: HistoryRecorder, *, task_kw=None, actions=None, swe=None, qa=None,
                  engine="langgraph", tracker=None):
    """Run one task through a real orchestrator and feed every event to ``recorder``."""

    task = make_task(**(task_kw or {}))
    project = make_project(tmp_path, [task])
    orch, events = make_orchestrator_with_repo(
        project, engine=engine, task_repo=InMemoryTaskRepository(), usage_tracker=tracker,
        swe_client=ScriptedClient("Software Engineer", "SWE_agent", swe or []),
        qa_client=ScriptedClient("Quality Assurance", "QA_agent", qa or []),
    )
    asyncio.run(orch._task_repo.insert_if_absent(PROJECT, task, source="yaml"))

    async def forward(event, data):
        events.append((event, data))
        await recorder.handle(PROJECT, event, data)

    orch._on_event = forward
    if actions:
        asyncio.run(run_with_gate_actions(orch, task, actions))
    else:
        asyncio.run(orch.process_task(task))
    return task, events


async def _only_run(runs, task_id="task-001"):
    listed = await runs.list_runs(PROJECT, task_id)
    assert len(listed) == 1, listed
    return listed[0], await runs.list_steps(listed[0].id)


def _steps(steps):
    return [(s.seq, s.kind, s.station, s.status) for s in steps]


@pytest.fixture(params=["langgraph", "legacy"])
def engine(request):
    return request.param  # the recorder only listens to events: both engines give the same history


# -- outcomes and steps -----------------------------------------------------------------------------


def test_a_task_that_completes_has_one_done_run_with_a_step_per_station(tmp_path, engine):
    runs = InMemoryRunRepository()
    _run_pipeline(tmp_path, _recorder(runs), engine=engine)

    run, steps = asyncio.run(_only_run(runs))
    assert (run.run_no, run.outcome, run.reason, run.rework_count, run.imported) == (1, "done", None, 0, False)
    assert run.finished_at is not None and run.finished_at > run.started_at
    assert run.seats == DEFAULT_SEATS
    assert _steps(steps) == [(1, "station", "pm", "done"), (2, "station", "swe", "done"), (3, "station", "qa", "done")]


def test_a_station_error_fails_the_run_with_the_reason(tmp_path, engine):
    runs = InMemoryRunRepository()
    _run_pipeline(tmp_path, _recorder(runs), swe=[RuntimeError("provider unreachable")], engine=engine)

    run, steps = asyncio.run(_only_run(runs))
    assert run.outcome == "failed" and "provider unreachable" in run.reason
    assert _steps(steps) == [(1, "station", "pm", "done"), (2, "station", "swe", "error")]
    assert "provider unreachable" in steps[1].note


def test_a_qa_rework_is_a_step_and_the_run_counts_it(tmp_path, engine):
    runs = InMemoryRunRepository()
    _run_pipeline(tmp_path, _recorder(runs), qa=[QA_FAIL_REPORT], engine=engine)

    run, steps = asyncio.run(_only_run(runs))
    assert (run.outcome, run.rework_count) == ("done", 1)
    assert _steps(steps) == [
        (1, "station", "pm", "done"), (2, "station", "swe", "done"), (3, "station", "qa", "done"),
        (4, "rework", "swe", "rework"), (5, "station", "swe", "done"), (6, "station", "qa", "done"),
    ]


def test_gate_decisions_are_steps_and_a_final_reject_fails_the_run(tmp_path, engine):
    runs = InMemoryRunRepository()
    _run_pipeline(tmp_path, _recorder(runs), engine=engine,
                  task_kw={"approval": Approval.AFTER_SWE, "max_reworks": 1},
                  actions=[("reject", "nope"), ("reject", "still nope")])

    run, steps = asyncio.run(_only_run(runs))
    assert run.outcome == "failed" and run.reason == "Rejected at approval gate (after SWE)"
    assert _steps(steps) == [
        (1, "station", "pm", "done"), (2, "station", "swe", "done"), (3, "gate", "swe", "rejected"),
        (4, "rework", "swe", "rework"), (5, "station", "swe", "done"), (6, "gate", "swe", "rejected"),
    ]
    assert [s.note for s in steps if s.kind == "gate"] == ["nope", "still nope"]


def test_an_approval_is_a_gate_step(tmp_path, engine):
    runs = InMemoryRunRepository()
    _run_pipeline(tmp_path, _recorder(runs), engine=engine,
                  task_kw={"approval": Approval.AFTER_QA}, actions=[("approve", "")])

    run, steps = asyncio.run(_only_run(runs))
    assert run.outcome == "done"
    assert _steps(steps)[-1] == (4, "gate", "qa", "approved")


# -- cost: re-summed from the usage records, never accumulated from events -----------------------------


def test_cost_and_tokens_equal_the_usage_records_of_the_run_period():
    runs, tracker = InMemoryRunRepository(), UsageTracker()
    recorder = _recorder(runs, tracker)
    task = {"id": "task-001", "title": "t", "priority": "high", "status": "pending", "rework_count": 0}

    async def scenario():
        tracker.record(PROJECT, "task-001", "openai", "gpt-4o", 5000, 5000)  # an earlier run: not this one
        await asyncio.sleep(0.002)
        await recorder.handle(PROJECT, "task:created", {"task": task, "queue_size": 0})
        first = tracker.record(PROJECT, "task-001", "openai", "gpt-4o", 1000, 2000)
        tracker.record(PROJECT, "other-task", "openai", "gpt-4o", 9000, 9000)
        await recorder.handle(PROJECT, "usage:update", {"task_id": "task-001", "record": first.to_dict()})
        await recorder.handle(PROJECT, "usage:update", {"task_id": "task-001", "record": first.to_dict()})  # again
        second = tracker.record(PROJECT, "task-001", "openai", "gpt-4o", 10, 20)
        await recorder.handle(PROJECT, "usage:update", {"task_id": "task-001", "record": second.to_dict()})
        return (await runs.list_runs(PROJECT, "task-001"))[0], first, second

    run, first, second = asyncio.run(scenario())
    assert (run.input_tokens, run.output_tokens) == (1010, 2020)
    assert run.cost_usd == pytest.approx(first.cost + second.cost)


def test_the_final_totals_are_summed_again_when_the_run_ends():
    runs, tracker = InMemoryRunRepository(), UsageTracker()
    recorder = _recorder(runs, tracker)
    task = {"id": "task-001", "title": "t", "priority": "high", "status": "pending", "rework_count": 0}

    async def scenario():
        await recorder.handle(PROJECT, "task:created", {"task": task, "queue_size": 0})
        tracker.record(PROJECT, "task-001", "openai", "gpt-4o", 100, 100)  # no usage:update seen
        await recorder.handle(PROJECT, "task:completed", {"task": task, "done_count": 1, "deliverables": []})
        return (await runs.list_runs(PROJECT, "task-001"))[0]

    run = asyncio.run(scenario())
    assert (run.outcome, run.input_tokens, run.output_tokens) == ("done", 100, 100)


# -- across restarts and reruns ----------------------------------------------------------------------


def test_a_new_recorder_continues_the_open_run_with_the_next_seq():
    runs, tracker = InMemoryRunRepository(), UsageTracker()
    task = {"id": "task-001", "title": "t", "priority": "high", "status": "in_progress", "rework_count": 0}

    async def scenario():
        before = _recorder(runs, tracker)
        await before.handle(PROJECT, "task:created", {"task": task, "queue_size": 0})
        await before.handle(PROJECT, "station:update", {"station": "pm", "status": "done", "task": task,
                                                        "success": True, "error": None, "execution_time": 1})
        tracker.record(PROJECT, "task-001", "openai", "gpt-4o", 1000, 1000)
        await before.handle(PROJECT, "station:update", {"station": "swe", "status": "done", "task": task,
                                                        "success": True, "error": None, "execution_time": 1})
        after = _recorder(runs, tracker)  # the restart: nothing in memory, no task:created
        await after.handle(PROJECT, "task:approved", {"task": task, "station": "swe"})
        tracker.record(PROJECT, "task-001", "openai", "gpt-4o", 10, 10)
        await after.handle(PROJECT, "usage:update", {"task_id": "task-001"})
        await after.handle(PROJECT, "task:completed", {"task": task, "done_count": 1, "deliverables": []})
        listed = await runs.list_runs(PROJECT, "task-001")
        return listed, await runs.list_steps(listed[0].id)

    listed, steps = asyncio.run(scenario())
    assert len(listed) == 1 and listed[0].outcome == "done"
    assert [s.seq for s in steps] == [1, 2, 3]
    assert _steps(steps)[-1] == (3, "gate", "swe", "approved")
    assert (listed[0].input_tokens, listed[0].output_tokens) == (1010, 1010)  # includes before the restart


def test_an_event_for_a_task_without_any_run_opens_one():
    runs = InMemoryRunRepository()
    task = {"id": "task-001", "title": "t", "priority": "high", "status": "in_progress", "rework_count": 0}

    asyncio.run(_recorder(runs).handle(PROJECT, "station:update", {"station": "pm", "status": "done", "task": task}))

    run, steps = asyncio.run(_only_run(runs))
    assert run.outcome is None and _steps(steps) == [(1, "station", "pm", "done")]


def test_task_created_supersedes_an_unfinished_run_and_opens_the_next_one():
    runs = InMemoryRunRepository()
    task = {"id": "task-001", "title": "t", "priority": "high", "status": "pending", "rework_count": 0}

    async def scenario():
        old = await runs.open_run(PROJECT, "task-001", started_at=datetime(2026, 1, 1))
        await _recorder(runs).handle(PROJECT, "task:created", {"task": task, "queue_size": 0})
        return old, await runs.list_runs(PROJECT, "task-001")

    old, listed = asyncio.run(scenario())
    newest, previous = listed
    assert (previous.id, previous.outcome, previous.reason) == (old.id, "failed", REASON_SUPERSEDED)
    assert REASON_SUPERSEDED == "superseded"
    assert (newest.run_no, newest.outcome) == (2, None)


def test_the_seat_provider_gives_the_snapshot_of_the_moment():
    runs = InMemoryRunRepository()
    seats = [{"id": "pm", "display_name": "PM", "look": "glasses", "kind": "make"},
             {"id": "designer", "display_name": "設計師", "look": "beret", "kind": "make"}]
    task = {"id": "task-001", "title": "t", "priority": "high", "status": "pending", "rework_count": 0}

    asyncio.run(_recorder(runs, seats=lambda project_id: seats).handle(PROJECT, "task:created", {"task": task}))

    assert asyncio.run(runs.list_runs(PROJECT, "task-001"))[0].seats == seats


def test_the_default_seats_are_the_three_builtin_stations():
    assert DEFAULT_SEATS == [
        {"id": "pm", "display_name": "PM", "look": "glasses", "kind": "make"},
        {"id": "swe", "display_name": "SWE", "look": "headset", "kind": "make"},
        {"id": "qa", "display_name": "QA", "look": "cap", "kind": "check"},
    ]


def test_events_that_are_not_about_a_task_are_ignored_and_failures_never_propagate(caplog):
    class Broken(InMemoryRunRepository):
        async def find_open_run(self, *a, **k):
            raise RuntimeError("database down")

    recorder = _recorder(Broken())
    task = {"id": "task-001", "title": "t", "priority": "high", "status": "in_progress", "rework_count": 0}

    async def scenario():
        await recorder.handle(PROJECT, "devops:alert", {"level": "critical", "message": "x"})
        await recorder.handle(PROJECT, "station:update", {"station": "pm", "status": "working", "task": task})
        await recorder.handle(PROJECT, "station:update", {"station": "pm", "status": "done", "task": task})

    asyncio.run(scenario())  # no exception
    assert "database down" in caplog.text


# -- wired into the runtime (DB mode) --------------------------------------------------------------


def _usage_sum(store, task_id, since):
    records = [r for r in store.list_records("demo") if r.task_id == task_id and r.timestamp >= since]
    return sum(r.input_tokens for r in records), sum(r.output_tokens for r in records), round(sum(r.cost for r in records), 6)


def test_the_runtime_records_a_run_for_every_task_it_runs(tmp_path, monkeypatch):
    from tests.test_gate_restart import World, _wait_status, _write_project
    from models.task import Status

    monkeypatch.setenv("PIPELINE_ENGINE", "langgraph")
    world = World(tmp_path)
    _write_project(tmp_path, "none")

    async def scenario():
        runtime, _ = world.runtime()
        await runtime.startup()
        try:
            await _wait_status(world, Status.DONE)
            await asyncio.sleep(0.05)
        finally:
            await runtime.shutdown()
        listed = await world.runs.list_runs("demo", "task-g")
        return listed, await world.runs.list_steps(listed[0].id)

    listed, steps = asyncio.run(scenario())
    assert [(r.run_no, r.outcome) for r in listed] == [(1, "done")]
    assert _steps(steps) == [(1, "station", "pm", "done"), (2, "station", "swe", "done"), (3, "station", "qa", "done")]
    assert (listed[0].input_tokens, listed[0].output_tokens, listed[0].cost_usd) == _usage_sum(
        world.usage, "task-g", listed[0].started_at)
    assert listed[0].input_tokens > 0  # the mock reports token usage


def test_ac5_history_a_gate_resumed_after_a_restart_keeps_one_run(tmp_path, monkeypatch):
    """Only one run, seq without gaps across the restart, totals include the usage before it."""

    from tests.test_gate_restart import _restart_scenario

    monkeypatch.setenv("PIPELINE_ENGINE", "langgraph")
    captured = {}
    import tests.test_gate_restart as gate_tests

    original_world = gate_tests.World

    class KeepWorld(original_world):
        def __init__(self, root):
            super().__init__(root)
            captured["world"] = self

    monkeypatch.setattr(gate_tests, "World", KeepWorld)
    asyncio.run(_restart_scenario(tmp_path, "after_swe", [("approve", "")]))
    world = captured["world"]

    async def history():
        listed = await world.runs.list_runs("demo", "task-g")
        return listed, await world.runs.list_steps(listed[0].id)

    listed, steps = asyncio.run(history())
    assert [(r.run_no, r.outcome) for r in listed] == [(1, "done")]
    assert _steps(steps) == [
        (1, "station", "pm", "done"), (2, "station", "swe", "done"),  # before the restart
        (3, "gate", "swe", "approved"), (4, "station", "qa", "done"),  # after it
    ]
    run = listed[0]
    assert (run.input_tokens, run.output_tokens, run.cost_usd) == _usage_sum(world.usage, "task-g", run.started_at)
    calls = [r for r in world.usage.list_records("demo") if r.task_id == "task-g"]
    assert len(calls) == 3  # PM and SWE before the restart, QA after it
    assert run.input_tokens == sum(r.input_tokens for r in calls) > calls[2].input_tokens


def test_a_rerun_opens_the_next_run(tmp_path, monkeypatch):
    from tests.test_gate_restart import World, _wait_status, _write_project
    from models.task import Status

    monkeypatch.setenv("PIPELINE_ENGINE", "langgraph")
    world = World(tmp_path)
    _write_project(tmp_path, "none")

    async def scenario():
        runtime, _ = world.runtime()
        await runtime.startup()
        try:
            await _wait_status(world, Status.DONE)
            await runtime.rerun_task("demo", "task-g")
            for _ in range(2000):
                runs = await world.runs.list_runs("demo", "task-g")
                if len(runs) == 2 and runs[0].outcome == "done":
                    break
                await asyncio.sleep(0.005)
        finally:
            await runtime.shutdown()
        return await world.runs.list_runs("demo", "task-g")

    listed = asyncio.run(scenario())
    assert [(r.run_no, r.outcome) for r in listed] == [(2, "done"), (1, "done")]
