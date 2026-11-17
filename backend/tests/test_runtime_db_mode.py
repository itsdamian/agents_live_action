"""FactoryRuntime in DB mode with in-memory repositories (Task 11b).

Plan D3 (1) startup order, AC2 (a)-(f), AC7d. Everything runs on the mock LLM; the project
folders live in tmp_path, never in the repo's ``projects/``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from core.runtime import REASON_GATE_LOST, REASON_INTERRUPTED, FactoryRuntime
from core.stores.memory import InMemoryRunRepository, InMemoryTaskRepository
from models.task import Priority, Status
from tests.hud_harness import make_orchestrator_with_repo
from tests.pipeline_harness import ScriptedClient, make_project, make_task
from tests.stores.contract_tasks import make

PROJECT = "demo"


@pytest.fixture(autouse=True)
def _mock_only(monkeypatch):
    monkeypatch.setenv("FORCE_MOCK", "true")  # never a real LLM, whatever the environment says
    monkeypatch.delenv("TASK_STORE", raising=False)


def _entry(task_id, title=None, priority="medium", status="pending", **extra):
    return {"id": task_id, "title": title or f"title {task_id}", "description": f"desc {task_id}",
            "priority": priority, "status": status, **extra}


def _write_project(root: Path, entries, name=PROJECT, todo: str | None = None) -> Path:
    project = root / name
    project.mkdir(parents=True, exist_ok=True)
    (project / "tasks.yaml").write_text(yaml.safe_dump({"tasks": entries}, allow_unicode=True, sort_keys=False), encoding="utf-8")
    if todo is not None:
        (project / "TODO.md").write_text(todo, encoding="utf-8")
    return project


def _runtime(root: Path, repo=None, runs=None) -> FactoryRuntime:
    return FactoryRuntime(
        projects_dir=root, task_store="db",
        task_repo=repo or InMemoryTaskRepository(), run_repo=runs or InMemoryRunRepository(),
    )


def _record_events(runtime: FactoryRuntime) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []

    async def recording_broadcast(project_id, event, data):
        events.append((event, data))

    runtime.ws_manager.broadcast = recording_broadcast
    return events


async def _wait_until(predicate, timeout=20.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if await predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition not reached in time")


def _all_in(repo, statuses: dict[str, Status], project=PROJECT):
    async def check():
        for tid, status in statuses.items():
            task = await repo.get(project, tid)
            if task is None or task.status is not status:
                return False
        return True

    return check


def _created_ids(events):
    return [d["task"]["id"] for e, d in events if e == "task:created"]


# -- AC2 (a), (b): import, run once, files untouched ---------------------------------------------------


def test_startup_imports_enqueues_once_in_priority_order_and_never_touches_the_files(tmp_path):
    project = _write_project(
        tmp_path,
        [_entry("task-low", priority="low"), _entry("task-crit", priority="critical"),
         _entry("task-med", priority="medium"), _entry("task-done", status="done")],
        todo="- [ ] write docs\n",
    )
    yaml_before = (project / "tasks.yaml").read_bytes()
    todo_before = (project / "TODO.md").read_bytes()
    mtimes = {f: (project / f).stat().st_mtime_ns for f in ("tasks.yaml", "TODO.md")}
    repo = InMemoryTaskRepository()

    async def scenario():
        runtime = _runtime(tmp_path, repo)
        events = _record_events(runtime)
        await runtime.startup()
        try:
            todo_id = next(t.id for t in await repo.list_tasks(PROJECT) if t.title == "write docs")
            await _wait_until(_all_in(repo, {"task-low": Status.DONE, "task-crit": Status.DONE,
                                             "task-med": Status.DONE, todo_id: Status.DONE}))
        finally:
            await runtime.shutdown()
        return events

    events = asyncio.run(scenario())

    started = _created_ids(events)
    assert started[0] == "task-crit" and started.index("task-med") < started.index("task-low")  # urgency order
    assert sorted(_created_ids(events)) == sorted(set(_created_ids(events)))  # each started exactly once
    assert "task-done" not in _created_ids(events)  # already done in the file: stored, not run
    assert asyncio.run(repo.get(PROJECT, "task-done")).status is Status.DONE
    # The pipeline wrote nothing back: bytes and mtimes are as they were.
    assert (project / "tasks.yaml").read_bytes() == yaml_before
    assert (project / "TODO.md").read_bytes() == todo_before
    assert {f: (project / f).stat().st_mtime_ns for f in ("tasks.yaml", "TODO.md")} == mtimes


# -- AC2 (c), (f): a restart does not dispatch twice and marks what was in flight failed -----------------


def test_restart_enqueues_pending_once_marks_in_flight_failed_and_never_reruns_finished_work(tmp_path):
    _write_project(tmp_path, [
        _entry("task-a"), _entry("task-b", status="in_progress"),
        _entry("task-c", status="awaiting_approval"), _entry("task-d", status="done"),
    ])
    repo, runs = InMemoryTaskRepository(), InMemoryRunRepository()

    async def seed():
        # What a previous process left behind: the same tasks, B and C mid-flight with open runs.
        for tid, status in (("task-a", Status.PENDING), ("task-b", Status.IN_PROGRESS),
                            ("task-c", Status.AWAITING_APPROVAL), ("task-d", Status.DONE)):
            await repo.insert_if_absent(PROJECT, make(tid, status=status, title=f"title {tid}", description=f"desc {tid}"), source="yaml")
        await runs.open_run(PROJECT, "task-b", started_at=datetime(2026, 1, 1))
        await runs.open_run(PROJECT, "task-c", started_at=datetime(2026, 1, 1))

    async def start_and_wait(wait_for_a: bool):
        runtime = _runtime(tmp_path, repo, runs)
        events = _record_events(runtime)
        await runtime.startup()
        try:
            if wait_for_a:
                await _wait_until(_all_in(repo, {"task-a": Status.DONE}))
            await asyncio.sleep(0.3)
        finally:
            await runtime.shutdown()
        return events

    asyncio.run(seed())
    first = asyncio.run(start_and_wait(True))
    second = asyncio.run(start_and_wait(False))  # a second restart on the same database

    assert _created_ids(first) == ["task-a"]  # exactly once, and nothing else
    assert _created_ids(second) == []  # nothing is dispatched again
    statuses = {t.id: t.status for t in asyncio.run(repo.list_tasks(PROJECT))}
    assert statuses == {"task-a": Status.DONE, "task-b": Status.FAILED, "task-c": Status.FAILED, "task-d": Status.DONE}
    reasons = {tid: asyncio.run(runs.list_runs(PROJECT, tid))[0].reason for tid in ("task-b", "task-c")}
    assert (REASON_INTERRUPTED, REASON_GATE_LOST) == ("interrupted by restart", "gate lost after restart")
    assert reasons == {"task-b": "interrupted by restart", "task-c": "gate lost after restart"}
    assert all(r.outcome == "failed" for tid in ("task-b", "task-c") for r in asyncio.run(runs.list_runs(PROJECT, tid)))


# -- live changes through the watcher ---------------------------------------------------------------------


def test_a_task_added_to_the_file_later_is_imported_and_run_and_edits_to_finished_ones_are_ignored(tmp_path):
    project = _write_project(tmp_path, [_entry("task-001")])
    repo = InMemoryTaskRepository()

    async def scenario():
        runtime = _runtime(tmp_path, repo)
        events = _record_events(runtime)
        await runtime.startup()
        try:
            await _wait_until(_all_in(repo, {"task-001": Status.DONE}))
            # A person edits the file: a new task, and a new title + a "re-opened" status on the finished one.
            (project / "tasks.yaml").write_text(yaml.safe_dump({"tasks": [
                _entry("task-001", title="edited too late", status="pending"), _entry("task-002"),
            ]}, allow_unicode=True, sort_keys=False), encoding="utf-8")
            await _wait_until(_all_in(repo, {"task-002": Status.DONE}))
            await asyncio.sleep(0.5)  # let any (wrong) re-run of task-001 show up
        finally:
            await runtime.shutdown()
        return events

    events = asyncio.run(scenario())

    assert _created_ids(events) == ["task-001", "task-002"]  # task-001 did not run again
    first = asyncio.run(repo.get(PROJECT, "task-001"))
    assert (first.title, first.status) == ("title task-001", Status.DONE)


def test_ensure_project_from_a_worker_thread_initialises_the_project_on_the_loop(tmp_path):
    _write_project(tmp_path, [_entry("task-001")], name="first")
    repo = InMemoryTaskRepository()

    async def scenario():
        runtime = _runtime(tmp_path, repo)
        await runtime.startup()
        try:
            _write_project(tmp_path, [_entry("task-009")], name="late")  # appears after startup
            orchestrator = await asyncio.to_thread(runtime.ensure_project, "late")  # like a sync REST handler
            assert orchestrator is not None
            await runtime.wait_ready("late")
            await _wait_until(_all_in(repo, {"task-009": Status.DONE}, project="late"))
        finally:
            await runtime.shutdown()

    asyncio.run(scenario())


# -- the screen reads the database ---------------------------------------------------------------------------


def test_load_tasks_and_snapshot_show_the_database_not_the_file(tmp_path):
    _write_project(tmp_path, [_entry("task-001")])
    repo = InMemoryTaskRepository()

    async def scenario():
        runtime = _runtime(tmp_path, repo)
        await runtime.startup()
        try:
            await _wait_until(_all_in(repo, {"task-001": Status.DONE}))
            await asyncio.sleep(0.3)  # the forwarder refreshes the cache on task:completed
            return runtime.load_tasks(PROJECT), runtime.snapshot(PROJECT)["tasks"]
        finally:
            await runtime.shutdown()

    loaded, snapshot_tasks = asyncio.run(scenario())

    # tasks.yaml still says "pending"; the database (and so the screen) says done.
    assert [t.status for t in loaded] == [Status.DONE]
    assert [t["status"] for t in snapshot_tasks] == ["done"]


def test_file_mode_is_the_default_and_builds_no_repository(tmp_path):
    _write_project(tmp_path, [_entry("task-001")])

    runtime = FactoryRuntime(projects_dir=tmp_path)
    orchestrator = runtime.ensure_project(PROJECT)

    assert runtime.task_store == "file" and runtime.task_repo is None
    assert orchestrator._task_repo is None
    assert [t.id for t in runtime.load_tasks(PROJECT)] == ["task-001"]  # straight from the file


def test_shutdown_while_a_project_is_still_initialising_is_clean(tmp_path):
    _write_project(tmp_path, [_entry("task-001")])

    async def scenario():
        runtime = _runtime(tmp_path)
        await runtime.startup()
        await runtime.shutdown()
        await runtime.shutdown()  # twice is fine

    asyncio.run(scenario())


# -- AC7d / D24: a queued task is re-read just before it starts --------------------------------------------------


def _orchestrator_with_repo(tmp_path, repo, pm_client=None):
    project = make_project(tmp_path, [make_task()])
    orch, events = make_orchestrator_with_repo(project, engine="langgraph", task_repo=repo, pm_client=pm_client)
    return project, orch, events


def test_a_queued_task_runs_with_the_content_it_has_when_it_starts(tmp_path):
    repo = InMemoryTaskRepository()
    pm = ScriptedClient("Project Manager", "PM_agent")
    _, orch, events = _orchestrator_with_repo(tmp_path, repo, pm)
    original = make_task(title="old title", description="old description")
    queued_object = original.model_copy()

    async def scenario():
        await repo.insert_if_absent("demo-project", original, source="yaml")
        orch.enqueue(queued_object)  # sits in the queue with the OLD title
        # tasks.yaml was edited and imported meanwhile:
        assert await repo.update_pending_content(
            "demo-project", "task-001", title="new title", description="new description",
            priority=Priority.CRITICAL, approval=original.approval, max_reworks=4)
        orch.start()
        await asyncio.wait_for(orch._queue.join(), 15)
        await orch.stop()

    asyncio.run(scenario())

    created = [d for e, d in events if e == "task:created"]
    assert [d["task"]["title"] for d in created] == ["new title"]
    assert "new title" in pm.prompts[0] and "old title" not in pm.prompts[0]
    assert "new description" in pm.prompts[0]
    assert asyncio.run(repo.get("demo-project", "task-001")).status is Status.DONE


@pytest.mark.parametrize("status", [Status.IN_PROGRESS, Status.DONE, Status.FAILED, Status.AWAITING_APPROVAL])
def test_a_queued_task_that_is_no_longer_pending_in_the_database_is_skipped(tmp_path, status, caplog):
    repo = InMemoryTaskRepository()
    pm = ScriptedClient("Project Manager", "PM_agent")
    _, orch, events = _orchestrator_with_repo(tmp_path, repo, pm)
    task = make_task()
    caplog.set_level(logging.INFO)

    async def scenario():
        await repo.insert_if_absent("demo-project", task, source="yaml")
        orch.enqueue(task)
        await repo.set_status("demo-project", "task-001", status)  # handled elsewhere in the meantime
        orch.start()
        await asyncio.wait_for(orch._queue.join(), 15)  # the skipped item is still marked done
        await orch.stop()

    asyncio.run(scenario())

    assert events == [] and pm.prompts == []  # no event, no agent call
    assert (orch.done_count, orch.failed_count) == (0, 0)
    assert asyncio.run(repo.get("demo-project", "task-001")).status is status
    assert "not pending" in caplog.text


def test_a_queued_task_that_vanished_from_the_database_is_skipped(tmp_path):
    repo = InMemoryTaskRepository()
    pm = ScriptedClient("Project Manager", "PM_agent")
    _, orch, events = _orchestrator_with_repo(tmp_path, repo, pm)

    async def scenario():
        orch.enqueue(make_task())  # never inserted into the repository
        orch.start()
        await asyncio.wait_for(orch._queue.join(), 15)
        await orch.stop()

    asyncio.run(scenario())

    assert events == [] and pm.prompts == []


def test_process_task_called_directly_does_not_consult_the_repository(tmp_path):
    """Only the queue worker refreshes (plan D24); direct calls keep working unchanged."""

    repo = InMemoryTaskRepository()
    _, orch, events = _orchestrator_with_repo(tmp_path, repo)

    result = asyncio.run(orch.process_task(make_task()))  # not in the repo, still runs

    assert result.status is Status.DONE
