"""Contract every ProjectRepository must satisfy (Task 11d)."""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from core.stores import MigratedRun, MigratedTask
from models.task import Status
from tests.stores.contract_tasks import make

T = datetime(2026, 2, 1, 10, 0, 0)


def migrated(task_id, status=Status.PENDING, source="yaml", run=None, **overrides):
    return MigratedTask(make(task_id, status=status, **overrides), source, f"hash-{task_id}", run)


class ProjectRepositoryContract:
    """Subclass (prefixed ``Test``); ``repos`` is a fixture returning ``(projects, tasks, runs)``."""

    def run(self, coro):
        return asyncio.run(coro)

    def test_a_new_project_does_not_exist_and_has_no_settings(self, repos):
        projects, _, _ = repos

        async def scenario():
            assert await projects.exists("p") is False
            assert await projects.get_settings("p") is None

        self.run(scenario())

    def test_create_stores_the_project_its_settings_tasks_and_imported_runs(self, repos):
        projects, tasks, runs = repos
        done_run = MigratedRun(outcome="done", started_at=T, finished_at=T)
        failed_run = MigratedRun(outcome="failed", reason="interrupted before migration to database", started_at=T, finished_at=T)

        async def scenario():
            created = await projects.create_from_migration(
                "p", settings={"llm": "mock"},
                tasks=[migrated("task-001"), migrated("task-002", Status.DONE, run=done_run),
                       migrated("task-003", Status.FAILED, source="todo", run=failed_run)])
            assert created is True
            assert await projects.exists("p") is True and await projects.get_settings("p") == {"llm": "mock"}
            assert {t.id: t.status for t in await tasks.list_tasks("p")} == {
                "task-001": Status.PENDING, "task-002": Status.DONE, "task-003": Status.FAILED}
            meta = await tasks.get_meta("p", "task-003")
            assert (meta.source, meta.source_hash) == ("todo", "hash-task-003")
            assert await runs.list_runs("p", "task-001") == []  # pending: no history yet
            (done,) = await runs.list_runs("p", "task-002")
            assert (done.run_no, done.outcome, done.imported, done.finished_at) == (1, "done", True, T)
            (failed,) = await runs.list_runs("p", "task-003")
            assert (failed.outcome, failed.reason, failed.imported) == ("failed", "interrupted before migration to database", True)

        self.run(scenario())

    def test_creating_twice_is_a_no_op_that_returns_false(self, repos):
        projects, tasks, _ = repos

        async def scenario():
            assert await projects.create_from_migration("p", settings={"llm": "mock"}, tasks=[migrated("task-001")]) is True
            again = await projects.create_from_migration(
                "p", settings={"llm": "other"}, tasks=[migrated("task-001", title="changed"), migrated("task-002")])
            assert again is False
            assert await projects.get_settings("p") == {"llm": "mock"}
            assert [t.id for t in await tasks.list_tasks("p")] == ["task-001"]
            assert (await tasks.get("p", "task-001")).title == "title task-001"

        self.run(scenario())

    def test_projects_are_independent(self, repos):
        projects, tasks, _ = repos

        async def scenario():
            await projects.create_from_migration("a", settings={}, tasks=[migrated("task-001")])
            assert await projects.exists("b") is False
            assert await projects.create_from_migration("b", settings={}, tasks=[migrated("task-001")]) is True
            assert len(await tasks.list_tasks("a")) == len(await tasks.list_tasks("b")) == 1

        self.run(scenario())

    def test_a_failure_halfway_rolls_everything_back_and_a_retry_succeeds(self, repos):
        projects, tasks, runs = repos
        run = MigratedRun(outcome="done", started_at=T, finished_at=T)

        async def scenario():
            with pytest.raises(Exception):
                await projects.create_from_migration(
                    "p", settings={"llm": "mock"},
                    tasks=[migrated("task-001", Status.DONE, run=run), migrated("task-002", source="bogus")])
            # Nothing at all was left behind: no project row, no tasks, no runs.
            assert await projects.exists("p") is False
            assert await tasks.list_tasks("p") == [] and await runs.list_runs("p", "task-001") == []
            # The next attempt (a fixed input) starts clean and numbers its run 1 again.
            assert await projects.create_from_migration(
                "p", settings={"llm": "mock"}, tasks=[migrated("task-001", Status.DONE, run=run), migrated("task-002")]) is True
            assert [r.run_no for r in await runs.list_runs("p", "task-001")] == [1]

        self.run(scenario())

    def test_tasks_and_runs_already_in_the_database_without_a_project_row(self, repos):
        # The database already has tasks and runs but no projects row (a development database
        # from before 11d). User ruling 2026-11-21: existing rows and their run history are kept
        # as they are and get NO imported run; only the tasks inserted by this migration do.
        projects, tasks, runs = repos
        done_run = MigratedRun(outcome="done", started_at=T, finished_at=T)

        async def scenario():
            await tasks.insert_if_absent("p", make("task-001"), source="yaml", source_hash="old-1")
            await tasks.insert_if_absent("p", make("task-002", status=Status.DONE), source="yaml", source_hash="old-2")
            earlier = await runs.open_run("p", "task-002", started_at=T)
            await runs.finish_run(earlier.id, outcome="done", finished_at=T)

            created = await projects.create_from_migration(
                "p", settings={"llm": "mock"},
                tasks=[migrated("task-001", Status.DONE, run=done_run),  # the file says done, the DB pending
                       migrated("task-002", Status.DONE, run=done_run),
                       migrated("task-003", Status.DONE, run=done_run)])  # new to the database

            assert created is True and await projects.exists("p") is True
            assert {t.id: t.status for t in await tasks.list_tasks("p")} == {
                "task-001": Status.PENDING, "task-002": Status.DONE, "task-003": Status.DONE}
            assert await runs.list_runs("p", "task-001") == []  # existing row: no imported run
            assert [(r.run_no, r.imported) for r in await runs.list_runs("p", "task-002")] == [(1, False)]
            assert [(r.run_no, r.imported) for r in await runs.list_runs("p", "task-003")] == [(1, True)]

        self.run(scenario())

    def test_an_existing_finished_row_without_runs_gets_no_imported_run(self, repos):
        # User ruling 2026-11-21: whatever the existing row's status (done, failed, even one left
        # in progress) and even with no history at all, it is kept untouched and gets no run.
        projects, tasks, runs = repos
        done_run = MigratedRun(outcome="done", started_at=T, finished_at=T)
        failed_run = MigratedRun(outcome="failed", reason="interrupted before migration to database", started_at=T, finished_at=T)

        async def scenario():
            await tasks.insert_if_absent("p", make("task-001", status=Status.FAILED, title="db title"),
                                         source="yaml", source_hash="old-1")
            await tasks.insert_if_absent("p", make("task-002", status=Status.IN_PROGRESS), source="todo", source_hash="old-2")

            created = await projects.create_from_migration(
                "p", settings={"llm": "mock"},
                tasks=[migrated("task-001", Status.DONE, run=done_run, title="file title"),
                       migrated("task-002", Status.FAILED, run=failed_run),
                       migrated("task-003", Status.FAILED, run=failed_run)])

            assert created is True
            kept = await tasks.get("p", "task-001")
            assert (kept.status, kept.title) == (Status.FAILED, "db title")
            assert (await tasks.get_meta("p", "task-001")).source_hash == "old-1"
            assert (await tasks.get("p", "task-002")).status is Status.IN_PROGRESS
            assert await runs.list_runs("p", "task-001") == [] and await runs.list_runs("p", "task-002") == []
            (new,) = await runs.list_runs("p", "task-003")
            assert (new.run_no, new.outcome, new.imported) == (1, "failed", True)

        self.run(scenario())

    def test_an_empty_project_is_still_marked_as_migrated(self, repos):
        projects, tasks, _ = repos

        async def scenario():
            assert await projects.create_from_migration("empty", settings={"llm": "mock"}, tasks=[]) is True
            assert await projects.exists("empty") is True and await tasks.list_tasks("empty") == []

        self.run(scenario())
