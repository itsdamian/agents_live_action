"""UsageTracker write-through and reload (Task 12, AC6, plan D20)."""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta

import pytest

from core.runtime import FactoryRuntime
from core.stores.memory import InMemoryRunRepository, InMemoryTaskRepository
from core.stores.usage_store import InMemoryUsageStore
from core.usage_tracker import UsageTracker
from models.task import Status
from tests.hud_harness import make_orchestrator_with_repo
from tests.pipeline_harness import make_project, make_task

DAY1, DAY2 = datetime(2026, 6, 1, 23, 50), datetime(2026, 6, 2, 0, 10)


def _clock(times):
    """Returns the given times in turn, then keeps returning the last one."""

    remaining = list(times)
    return lambda: remaining.pop(0) if len(remaining) > 1 else remaining[0]


def _filled_tracker(store=None, **kwargs):
    tracker = UsageTracker(clock=_clock([DAY1, DAY1, DAY2, DAY2]), store=store, **kwargs)
    tracker.record("p1", "t1", "anthropic", "claude-opus-5-5", 1000, 500)
    tracker.record("p1", "t2", "openai", "gpt-4o", 200, 100)
    tracker.record("p2", "t1", "anthropic", "claude-sonnet-5-5", 300, 300)
    tracker.record("p1", "t1", "anthropic", "claude-opus-5-5", 50, 25)
    return tracker


# -- without a store nothing changes ----------------------------------------------------------------


def test_a_tracker_without_a_store_behaves_as_before():
    tracker = _filled_tracker()

    assert tracker.project_usage("p1").call_count == 3
    with pytest.raises(ValueError, match="needs a store"):
        tracker.load_from()


# -- write-through -------------------------------------------------------------------------------------


def test_every_record_is_in_the_store_by_the_time_record_returns():
    store = InMemoryUsageStore()
    tracker = UsageTracker(store=store)

    returned = tracker.record("p", "t", "anthropic", "claude-opus-5-5", 10, 5)

    assert store.list_records() == [returned]  # the very record that was returned, already stored


def test_the_store_has_the_row_when_the_usage_update_event_is_emitted(tmp_path):
    store = InMemoryUsageStore()
    tracker = UsageTracker(store=store)
    task = make_task()
    project = make_project(tmp_path, [task])
    orch, events = make_orchestrator_with_repo(project, engine="langgraph", usage_tracker=tracker)
    checks: list[tuple[int, int]] = []
    inner = orch._on_event

    def watching(event, data):
        if event == "usage:update":
            # At this moment the row of THIS record must already be stored.
            stored = {(r.task_id, r.timestamp.isoformat()) for r in store.list_records()}
            checks.append((len(store.list_records()), int(data["project_usage"]["call_count"])))
            assert (data["record"]["task_id"], data["record"]["timestamp"]) in stored
        return inner(event, data)

    orch._on_event = watching

    asyncio.run(orch.process_task(task))

    assert checks == [(1, 1), (2, 2), (3, 3)]  # PM, SWE, QA: store and tracker move together


def test_a_failing_store_is_logged_but_does_not_fail_the_call(caplog):
    class Broken:
        def add(self, record):
            raise RuntimeError("database is down")

        def list_records(self, project_id=None):
            return []

    tracker = UsageTracker(store=Broken())
    caplog.set_level(logging.ERROR)

    record = tracker.record("p", "t", "anthropic", "claude-opus-5-5", 100, 50)

    assert record.input_tokens == 100
    assert tracker.project_usage("p").call_count == 1  # still counted in memory
    assert "Could not store usage record" in caplog.text


# -- reload ----------------------------------------------------------------------------------------------


def test_a_new_tracker_rebuilds_every_total_from_the_store():
    store = InMemoryUsageStore()
    before = _filled_tracker(store)

    after = UsageTracker()
    assert after.load_from(store) == 4

    for project in ("p1", "p2", "never"):
        assert after.project_usage(project) == before.project_usage(project)
    for key in (("p1", "t1"), ("p1", "t2"), ("p2", "t1"), ("p9", "t9")):
        assert after.task_usage(*key) == before.task_usage(*key)
    for day in (date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3)):
        assert after.daily_usage(day) == before.daily_usage(day)  # records land on their own day
    assert after.records() == before.records()
    assert before.daily_usage(date(2026, 6, 1)).call_count == 2 and before.daily_usage(date(2026, 6, 2)).call_count == 2


def test_budget_status_is_the_same_after_a_restart():
    store = InMemoryUsageStore()
    before = _filled_tracker(store, project_budget_usd=0.01, daily_budget_usd=100.0)
    assert before.budget_status("p1").exceeded is True

    after = UsageTracker(project_budget_usd=0.01, daily_budget_usd=100.0, clock=lambda: DAY2)
    after.load_from(store)

    status = after.budget_status("p1")
    assert (status.exceeded, status.kind, round(status.spend, 6)) == (True, "project", round(before.project_cost("p1"), 6))
    assert after.budget_status("p2").exceeded == before.budget_status("p2").exceeded


def test_load_from_replaces_in_memory_state_and_does_not_write_back():
    store = InMemoryUsageStore()
    _filled_tracker(store)
    tracker = UsageTracker(store=store)
    tracker.record("stale", "t", "openai", "gpt-4o", 1, 1)  # also written to the store
    count_before = len(store.list_records())

    tracker.load_from(store)
    tracker.load_from(store)  # loading twice must not double anything

    assert len(store.list_records()) == count_before  # nothing written back
    assert tracker.project_usage("p1").call_count == 3 and tracker.project_usage("stale").call_count == 1
    assert len(tracker.records()) == count_before


def test_attach_store_loads_and_then_writes_through():
    store = InMemoryUsageStore()
    _filled_tracker(store)
    tracker = UsageTracker()

    assert tracker.attach_store(store) == 4
    tracker.record("p1", "t1", "anthropic", "claude-opus-5-5", 1, 1)

    assert tracker.project_usage("p1").call_count == 4 and len(store.list_records()) == 5


# -- through the runtime: a restart keeps the spend and the budget pause ----------------------------------------


def test_a_restarted_runtime_has_the_same_usage_and_budget_pause(tmp_path, monkeypatch):
    monkeypatch.setenv("FORCE_MOCK", "true")
    monkeypatch.setenv("PROJECT_BUDGET_USD", "0.01")
    (tmp_path / "demo").mkdir()
    store = InMemoryUsageStore()
    tasks = InMemoryTaskRepository()

    def make_runtime():
        return FactoryRuntime(projects_dir=tmp_path, task_store="db", task_repo=tasks,
                              run_repo=InMemoryRunRepository(), usage_store=store)

    async def first_life():
        runtime = make_runtime()
        await runtime.startup()
        try:
            # Real-priced spend past the 0.01 budget, recorded through the live tracker.
            runtime.usage_tracker.record("demo", "t1", "openai", "gpt-4o", 1000, 1000)
            return runtime.usage_summary("demo"), runtime.orchestrators["demo"].is_budget_paused()
        finally:
            await runtime.shutdown()

    async def second_life():
        runtime = make_runtime()
        await runtime.startup()
        try:
            orchestrator = runtime.orchestrators["demo"]
            return runtime.usage_summary("demo"), orchestrator.is_budget_paused(), runtime.snapshot("demo")["pipeline"]["budget_paused"]
        finally:
            await runtime.shutdown()

    summary1, paused1 = asyncio.run(first_life())
    summary2, paused2, snapshot_paused = asyncio.run(second_life())

    assert paused1 is True and paused2 is True and snapshot_paused is True
    assert summary2 == summary1  # project usage, daily cost, budgets and pause flag are identical
    assert summary2["project"]["call_count"] == 1 and summary2["project"]["cost"] > 0.01


class _CountingStore(InMemoryUsageStore):
    def __init__(self, fail: bool = False) -> None:
        super().__init__()
        self.loads = 0
        self.fail = fail

    def list_records(self, project_id=None):
        self.loads += 1
        if self.fail:
            raise RuntimeError("database is down")
        return super().list_records(project_id)


def _db_runtime(tmp_path, store):
    return FactoryRuntime(projects_dir=tmp_path, task_store="db", task_repo=InMemoryTaskRepository(),
                          run_repo=InMemoryRunRepository(), usage_store=store)


def _start_and_stop(runtime):
    async def life():
        await runtime.startup()
        await runtime.shutdown()

    asyncio.run(life())


def test_a_runtime_without_projects_does_not_read_the_usage_store(tmp_path, monkeypatch):
    monkeypatch.setenv("FORCE_MOCK", "true")
    store = _CountingStore()

    _start_and_stop(_db_runtime(tmp_path, store))

    assert store.loads == 0  # startup alone never touches the database


def test_stored_usage_is_loaded_once_however_many_projects_start(tmp_path, monkeypatch):
    monkeypatch.setenv("FORCE_MOCK", "true")
    for name in ("alpha", "beta", "gamma"):
        (tmp_path / name).mkdir()
    store = _CountingStore()
    store.add(_filled_tracker().records()[0])
    runtime = _db_runtime(tmp_path, store)

    _start_and_stop(runtime)

    assert store.loads == 1 and runtime.usage_tracker.project_usage("p1").call_count == 1


def test_if_loading_usage_fails_the_project_stays_idle(tmp_path, monkeypatch):
    monkeypatch.setenv("FORCE_MOCK", "true")
    monkeypatch.setenv("PROJECT_BUDGET_USD", "0.01")
    (tmp_path / "demo").mkdir()
    runtime = _db_runtime(tmp_path, _CountingStore(fail=True))

    async def life():
        await runtime.startup()
        try:
            return "demo" in runtime._init_failed, runtime.orchestrators["demo"]._worker
        finally:
            await runtime.shutdown()

    failed, worker = asyncio.run(life())

    # Without the past spend the budget guard could be wrong, so the project does not start.
    assert failed is True and worker is None
