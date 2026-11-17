"""Migrating an existing file-mode project into the database (Task 11d, AC2b, plan D3 (3))."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from core.project_migration import REASON_BEFORE_MIGRATION, migrate_project
from core.runtime import FactoryRuntime
from core.stores.memory import (
    InMemoryProjectRepository,
    InMemoryRunRepository,
    InMemoryTaskRepository,
)
from core.task_importer import TaskImporter, compute_source_hash
from core.task_watcher import parse_todo_md
from models.task import Status

PROJECT = "legacy-proj"


@pytest.fixture(autouse=True)
def _mock_only(monkeypatch):
    monkeypatch.setenv("FORCE_MOCK", "true")
    monkeypatch.delenv("TASK_STORE", raising=False)


def _entry(task_id, status, priority="medium", updated="2026-03-04T05:06:07"):
    return {"id": task_id, "title": f"title {task_id}", "description": f"desc {task_id}", "priority": priority,
            "status": status, "created_at": "2026-03-01T00:00:00", "updated_at": updated}


def _write_project(root: Path, entries, todo=None, agents=True) -> Path:
    project = root / PROJECT
    project.mkdir(parents=True, exist_ok=True)
    (project / "tasks.yaml").write_text(yaml.safe_dump({"tasks": entries}, allow_unicode=True, sort_keys=False), encoding="utf-8")
    if todo is not None:
        (project / "TODO.md").write_text(todo, encoding="utf-8")
    if agents:
        (project / "agents").mkdir(exist_ok=True)
        (project / "agents" / "PM_agent.yaml").write_text(
            "name: PM_agent\nrole: Project Manager\ndescription: d\nllm_provider: anthropic\nllm_model: claude-opus-5-5\n"
            "api_key_env: ANTHROPIC_API_KEY\nmax_retries: 2\nsystem_prompt: custom\n", encoding="utf-8")
    return project


def _mtimes(project: Path):
    """Content and mtime of the files a person owns: tasks.yaml, TODO.md and agents/*.yaml.

    (Running a task legitimately adds deliverables/ and logs/; those are not inputs.)"""

    inputs = [project / "tasks.yaml", project / "TODO.md", *sorted((project / "agents").glob("*.yaml"))]
    return {p.relative_to(project).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns) for p in inputs if p.is_file()}


def _repos():
    tasks, runs = InMemoryTaskRepository(), InMemoryRunRepository()
    return InMemoryProjectRepository(tasks, runs), tasks, runs


def run(coro):
    return asyncio.run(coro)


STATES = [_entry("task-pending", "pending"), _entry("task-done", "done", updated="2026-03-04T05:06:07"),
          _entry("task-failed", "failed", updated="2026-03-05T05:06:07"),
          _entry("task-progress", "in_progress"), _entry("task-gate", "awaiting_approval")]


# -- AC2b: the four (five) states --------------------------------------------------------------------


def test_every_task_and_status_moves_into_the_database(tmp_path):
    project = _write_project(tmp_path, STATES)
    projects, tasks, runs = _repos()

    result = run(migrate_project(PROJECT, project, projects))

    assert result.migrated is True
    assert sorted(result.task_ids) == sorted(e["id"] for e in STATES)
    assert result.pending_ids == ["task-pending"] and sorted(result.interrupted_ids) == ["task-gate", "task-progress"]
    statuses = {t.id: t.status for t in run(tasks.list_tasks(PROJECT))}
    assert statuses == {"task-pending": Status.PENDING, "task-done": Status.DONE, "task-failed": Status.FAILED,
                        "task-progress": Status.FAILED, "task-gate": Status.FAILED}
    assert run(projects.get_settings(PROJECT)) == {"llm": "mock"}  # the default model is mock (plan D21)


def test_done_and_failed_tasks_get_one_imported_run_each_and_interrupted_ones_a_failed_run_with_the_reason(tmp_path):
    project = _write_project(tmp_path, STATES)
    projects, tasks, runs = _repos()
    run(migrate_project(PROJECT, project, projects))

    def only_run(task_id):
        (record,) = run(runs.list_runs(PROJECT, task_id))
        return record

    done, failed = only_run("task-done"), only_run("task-failed")
    assert (done.outcome, done.reason, done.imported, done.run_no) == ("done", None, True, 1)
    assert done.finished_at == datetime(2026, 3, 4, 5, 6, 7)  # finished_at = the task's updated_at
    assert (failed.outcome, failed.finished_at) == ("failed", datetime(2026, 3, 5, 5, 6, 7))
    for task_id in ("task-progress", "task-gate"):
        record = only_run(task_id)
        assert (record.outcome, record.reason, record.imported) == ("failed", REASON_BEFORE_MIGRATION, True)
    assert REASON_BEFORE_MIGRATION == "interrupted before migration to database"
    assert run(runs.list_runs(PROJECT, "task-pending")) == []  # not started: no history


def test_todo_md_tasks_are_migrated_and_a_clashing_id_keeps_the_first(tmp_path, caplog):
    project = _write_project(tmp_path, [_entry("task-a", "pending")], todo="- [ ] write the docs\n")
    todo_ids = [t.id for t in parse_todo_md(project / "TODO.md")]
    projects, tasks, _ = _repos()
    caplog.set_level(logging.WARNING)

    result = run(migrate_project(PROJECT, project, projects))

    stored = run(tasks.list_tasks(PROJECT))
    assert sorted(t.id for t in stored) == sorted(["task-a", todo_ids[0]])
    assert {t.id: run(tasks.get_meta(PROJECT, t.id)).source for t in stored} == {"task-a": "yaml", todo_ids[0]: "todo"}

    # Now an id that exists in both files: tasks.yaml was read first and wins.
    other = _write_project(tmp_path / "other", [_entry(todo_ids[0], "done")], todo="- [ ] write the docs\n")
    projects2, tasks2, _ = _repos()
    result2 = run(migrate_project(PROJECT, other, projects2))
    assert result2.conflicts == [todo_ids[0]]
    assert run(tasks2.get(PROJECT, todo_ids[0])).status is Status.DONE
    assert "keeping the first" in caplog.text


# -- idempotency and rollback -------------------------------------------------------------------------------


def test_a_second_call_does_not_migrate_again(tmp_path):
    project = _write_project(tmp_path, STATES)
    projects, tasks, runs = _repos()
    run(migrate_project(PROJECT, project, projects))
    run(tasks.set_status(PROJECT, "task-pending", Status.IN_PROGRESS))  # the system moved on since

    again = run(migrate_project(PROJECT, project, projects))

    assert again.migrated is False and again.task_ids == []
    assert run(tasks.get(PROJECT, "task-pending")).status is Status.IN_PROGRESS  # untouched
    assert len(run(runs.list_runs(PROJECT, "task-done"))) == 1


def test_a_failed_transaction_leaves_nothing_and_the_next_start_retries(tmp_path):
    project = _write_project(tmp_path, STATES)
    projects, tasks, runs = _repos()
    real = projects.create_from_migration
    calls = {"n": 0}

    async def flaky(project_id, *, settings, tasks):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("connection lost mid-transaction")
        return await real(project_id, settings=settings, tasks=tasks)

    projects.create_from_migration = flaky

    with pytest.raises(RuntimeError, match="connection lost"):
        run(migrate_project(PROJECT, project, projects))
    assert run(projects.exists(PROJECT)) is False and run(tasks.list_tasks(PROJECT)) == []

    retry = run(migrate_project(PROJECT, project, projects))  # "the next start"

    assert retry.migrated is True and len(run(tasks.list_tasks(PROJECT))) == 5
    assert len(run(runs.list_runs(PROJECT, "task-done"))) == 1


def test_files_are_never_modified_content_and_mtime(tmp_path):
    project = _write_project(tmp_path, STATES, todo="- [ ] from todo\n")
    before = _mtimes(project)
    projects, _, _ = _repos()

    run(migrate_project(PROJECT, project, projects))
    run(migrate_project(PROJECT, project, projects))

    assert _mtimes(project) == before  # tasks.yaml, TODO.md and agents/*.yaml: bytes and mtimes


def test_the_stored_hash_matches_the_importer_so_the_first_rescan_changes_nothing(tmp_path):
    project = _write_project(tmp_path, [_entry("task-pending", "pending"), _entry("task-done", "done")])
    projects, tasks, _ = _repos()
    run(migrate_project(PROJECT, project, projects))

    again = run(TaskImporter(tasks, PROJECT).import_file(project / "tasks.yaml"))

    assert sorted(again.unchanged) == ["task-done", "task-pending"]
    assert again.inserted == [] and again.updated == [] and again.ignored_started == []
    stored = run(tasks.get(PROJECT, "task-pending"))
    assert run(tasks.get_meta(PROJECT, "task-pending")).source_hash == compute_source_hash(stored)


def test_agents_are_not_part_of_this_step(tmp_path):
    """Importing agents/*.yaml is Task 18: migration here only handles tasks and statuses."""

    project = _write_project(tmp_path, [_entry("task-a", "pending")])
    projects, tasks, runs = _repos()
    run(migrate_project(PROJECT, project, projects))

    assert not hasattr(projects, "agents") and run(projects.get_settings(PROJECT)) == {"llm": "mock"}  # no migration_notes yet
    assert (project / "agents" / "PM_agent.yaml").exists()


# -- through the runtime --------------------------------------------------------------------------------------------


def _runtime(root, projects, tasks, runs):
    return FactoryRuntime(projects_dir=root, task_store="db", task_repo=tasks, run_repo=runs, project_repo=projects)


async def _wait_status(tasks, task_id, status, timeout=20):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        task = await tasks.get(PROJECT, task_id)
        if task is not None and task.status is status:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"{task_id} never became {status}")


def test_the_first_start_migrates_and_runs_the_pending_task_and_the_second_start_does_not_migrate_again(tmp_path):
    project = _write_project(tmp_path, STATES)
    before = _mtimes(project)
    projects, tasks, runs = _repos()
    created: list[str] = []

    async def start(wait_for_pending: bool):
        runtime = _runtime(tmp_path, projects, tasks, runs)

        async def record(project_id, event, data):
            if event == "task:created":
                created.append(data["task"]["id"])

        runtime.ws_manager.broadcast = record
        await runtime.startup()
        try:
            if wait_for_pending:
                await _wait_status(tasks, "task-pending", Status.DONE)
            await asyncio.sleep(0.2)
        finally:
            await runtime.shutdown()

    asyncio.run(start(True))
    runs_after_first = {t: len(run(runs.list_runs(PROJECT, t))) for t in ("task-done", "task-failed", "task-progress", "task-gate", "task-pending")}
    asyncio.run(start(False))

    assert created == ["task-pending"]  # only the pending task ran, once, across both starts
    statuses = {t.id: t.status for t in run(tasks.list_tasks(PROJECT))}
    assert statuses["task-pending"] is Status.DONE and statuses["task-progress"] is Status.FAILED
    assert runs_after_first == {"task-done": 1, "task-failed": 1, "task-progress": 1, "task-gate": 1, "task-pending": 0}
    # the second start migrated nothing more (history counts unchanged) and touched no file
    assert {t: len(run(runs.list_runs(PROJECT, t))) for t in runs_after_first} == runs_after_first
    assert _mtimes(project) == before


def test_a_failing_migration_leaves_the_project_idle_and_the_next_start_recovers(tmp_path, caplog):
    _write_project(tmp_path, STATES)
    projects, tasks, runs = _repos()
    real = projects.create_from_migration
    state = {"fail": True}

    async def sometimes(project_id, *, settings, tasks):
        if state["fail"]:
            raise RuntimeError("database hiccup")
        return await real(project_id, settings=settings, tasks=tasks)

    projects.create_from_migration = sometimes
    caplog.set_level(logging.ERROR)

    async def first():
        runtime = _runtime(tmp_path, projects, tasks, runs)
        await runtime.startup()  # must not raise: one project's failure does not stop the app
        try:
            assert runtime.orchestrators[PROJECT].queue_size == 0 and run_started(runtime) is False
        finally:
            await runtime.shutdown()

    def run_started(runtime):
        return runtime.orchestrators[PROJECT]._worker is not None

    asyncio.run(first())
    assert "Could not initialise project" in caplog.text and run(tasks.list_tasks(PROJECT)) == []

    state["fail"] = False

    async def second():
        runtime = _runtime(tmp_path, projects, tasks, runs)
        await runtime.startup()
        try:
            await _wait_status(tasks, "task-pending", Status.DONE)
        finally:
            await runtime.shutdown()

    asyncio.run(second())
    assert run(projects.exists(PROJECT)) is True
