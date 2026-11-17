"""Contract every TaskRepository must satisfy (plan D3, AC3).

Not collected by pytest (the file name has no ``test_`` prefix). A backend's test
module subclasses :class:`TaskRepositoryContract` (prefixed ``Test``), supplies
``make_repo`` and ``seed``, and lists operations it cannot offer in ``UNSUPPORTED``;
those tests ``pytest.skip``. The PostgreSQL implementation reuses it unchanged.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from core.stores import priority_rank
from models.task import Approval, Priority, Status, Task

T0 = datetime(2026, 1, 1, 12, 0, 0)


def make(task_id: str, **overrides) -> Task:
    base = dict(
        id=task_id, title=f"title {task_id}", description=f"desc {task_id}",
        priority=Priority.MEDIUM, status=Status.PENDING, created_at=T0, updated_at=T0,
    )
    base.update(overrides)
    return Task(**base)


class TaskRepositoryContract:
    """Subclass and provide ``make_repo(tmp_path)`` and ``seed(repo, project, task)``."""

    UNSUPPORTED: frozenset[str] = frozenset()
    PROJECT = "proj"

    # -- provided by the backend's test class ---------------------------------------------

    def make_repo(self, tmp_path):  # pragma: no cover - interface
        raise NotImplementedError

    async def seed(self, repo, project_id: str, task: Task) -> None:  # pragma: no cover
        raise NotImplementedError

    def run(self, coro):
        """Run a coroutine to completion; a backend may override (e.g. a shared loop)."""

        return asyncio.run(coro)

    def need(self, operation: str) -> None:
        if operation in self.UNSUPPORTED:
            pytest.skip(f"{operation} is not supported by this backend")

    @pytest.fixture
    def repo(self, tmp_path):
        return self.make_repo(tmp_path)

    # -- reading ----------------------------------------------------------------------------

    def test_get_unknown_task_and_project_return_none(self, repo):
        async def scenario():
            assert await repo.get(self.PROJECT, "nope") is None
            assert await repo.get("no-such-project", "task-001") is None

        self.run(scenario())

    def test_get_returns_the_seeded_fields(self, repo):
        async def scenario():
            await self.seed(
                repo, self.PROJECT,
                make("task-001", priority=Priority.HIGH, approval=Approval.AFTER_QA,
                     max_reworks=5, status=Status.DONE),
            )
            task = await repo.get(self.PROJECT, "task-001")
            assert (task.id, task.title, task.description) == ("task-001", "title task-001", "desc task-001")
            assert (task.priority, task.status) == (Priority.HIGH, Status.DONE)
            assert (task.approval, task.max_reworks) == (Approval.AFTER_QA, 5)
            assert task.created_at == T0

        self.run(scenario())

    def test_returned_tasks_are_copies(self, repo):
        async def scenario():
            await self.seed(repo, self.PROJECT, make("task-001"))
            first = await repo.get(self.PROJECT, "task-001")
            first.title = "mutated"
            listed = await repo.list_tasks(self.PROJECT)
            listed[0].status = Status.FAILED
            again = await repo.get(self.PROJECT, "task-001")
            assert (again.title, again.status) == ("title task-001", Status.PENDING)

        self.run(scenario())

    def test_list_tasks_all_and_filtered_by_status(self, repo):
        async def scenario():
            for task_id, status in (("task-001", Status.PENDING), ("task-002", Status.DONE),
                                    ("task-003", Status.FAILED), ("task-004", Status.PENDING)):
                await self.seed(repo, self.PROJECT, make(task_id, status=status))
            ids = lambda tasks: sorted(t.id for t in tasks)  # noqa: E731
            assert ids(await repo.list_tasks(self.PROJECT)) == ["task-001", "task-002", "task-003", "task-004"]
            assert ids(await repo.list_tasks(self.PROJECT, statuses=[Status.PENDING])) == ["task-001", "task-004"]
            assert ids(await repo.list_tasks(self.PROJECT, statuses=[Status.DONE, Status.FAILED])) == ["task-002", "task-003"]
            assert await repo.list_tasks(self.PROJECT, statuses=[Status.IN_PROGRESS]) == []
            assert await repo.list_tasks("no-such-project") == []

        self.run(scenario())

    def test_list_pending_orders_by_priority_then_age(self, repo):
        async def scenario():
            specs = [
                ("task-low-old", Priority.LOW, 0), ("task-med-new", Priority.MEDIUM, 5),
                ("task-crit", Priority.CRITICAL, 9), ("task-med-old", Priority.MEDIUM, 1),
                ("task-high", Priority.HIGH, 3),
            ]
            for task_id, priority, minutes in specs:
                await self.seed(repo, self.PROJECT, make(task_id, priority=priority,
                                                         created_at=T0 + timedelta(minutes=minutes)))
            await self.seed(repo, self.PROJECT, make("task-done", priority=Priority.CRITICAL, status=Status.DONE))
            pending = await repo.list_pending(self.PROJECT)
            assert [t.id for t in pending] == ["task-crit", "task-high", "task-med-old", "task-med-new", "task-low-old"]

        self.run(scenario())

    def test_count_by_status_has_every_status(self, repo):
        async def scenario():
            assert await repo.count_by_status(self.PROJECT) == {
                "pending": 0, "in_progress": 0, "awaiting_approval": 0, "done": 0, "failed": 0}
            for task_id, status in (("task-001", Status.PENDING), ("task-002", Status.PENDING),
                                    ("task-003", Status.DONE), ("task-004", Status.AWAITING_APPROVAL)):
                await self.seed(repo, self.PROJECT, make(task_id, status=status))
            assert await repo.count_by_status(self.PROJECT) == {
                "pending": 2, "in_progress": 0, "awaiting_approval": 1, "done": 1, "failed": 0}

        self.run(scenario())

    def test_projects_are_independent(self, repo, tmp_path):
        async def scenario():
            await self.seed(repo, "alpha", make("task-001", title="in alpha"))
            await self.seed(repo, "beta", make("task-001", title="in beta"))
            assert (await repo.get("alpha", "task-001")).title == "in alpha"
            assert (await repo.get("beta", "task-001")).title == "in beta"
            await repo.set_status("alpha", "task-001", Status.DONE)
            assert (await repo.get("beta", "task-001")).status is Status.PENDING

        self.run(scenario())

    # -- writing ----------------------------------------------------------------------------

    def test_create_assigns_sequential_ids_and_defaults(self, repo):
        async def scenario():
            first = await repo.create(self.PROJECT, "  Build login  ")
            second = await repo.create(self.PROJECT, "Second", "with description", Priority.HIGH)
            assert (first.id, first.title, first.description) == ("task-001", "Build login", "Build login")
            assert (first.status, first.priority, first.approval) == (Status.PENDING, Priority.MEDIUM, Approval.NONE)
            assert (second.id, second.description, second.priority) == ("task-002", "with description", Priority.HIGH)
            stored = await repo.get(self.PROJECT, "task-002")
            assert (stored.title, stored.priority, stored.status) == ("Second", Priority.HIGH, Status.PENDING)

        self.run(scenario())

    def test_create_continues_after_the_highest_existing_id(self, repo):
        async def scenario():
            await self.seed(repo, self.PROJECT, make("task-007"))
            await self.seed(repo, self.PROJECT, make("custom-id"))
            assert (await repo.create(self.PROJECT, "next")).id == "task-008"

        self.run(scenario())

    def test_create_rejects_an_empty_title(self, repo):
        async def scenario():
            with pytest.raises(ValueError):
                await repo.create(self.PROJECT, "   ")
            assert await repo.list_tasks(self.PROJECT) == []

        self.run(scenario())

    def test_set_status_persists_and_refreshes_updated_at(self, repo):
        async def scenario():
            await self.seed(repo, self.PROJECT, make("task-001"))
            before = await repo.get(self.PROJECT, "task-001")
            assert await repo.set_status(self.PROJECT, "task-001", Status.IN_PROGRESS) is True
            after = await repo.get(self.PROJECT, "task-001")
            assert after.status is Status.IN_PROGRESS
            assert after.updated_at >= before.updated_at
            assert after.title == before.title  # nothing else changed

        self.run(scenario())

    def test_set_status_walks_the_whole_lifecycle(self, repo):
        async def scenario():
            await self.seed(repo, self.PROJECT, make("task-001"))
            for status in (Status.IN_PROGRESS, Status.AWAITING_APPROVAL, Status.IN_PROGRESS, Status.DONE):
                assert await repo.set_status(self.PROJECT, "task-001", status) is True
                assert (await repo.get(self.PROJECT, "task-001")).status is status

        self.run(scenario())

    def test_set_status_on_an_unknown_task_is_false(self, repo):
        async def scenario():
            assert await repo.set_status(self.PROJECT, "nope", Status.DONE) is False
            assert await repo.set_status("no-such-project", "task-001", Status.DONE) is False

        self.run(scenario())

    # -- database-only operations (skipped for the file backend) ---------------------------------

    def test_insert_if_absent_inserts_once_and_never_overwrites(self, repo):
        self.need("insert_if_absent")

        async def scenario():
            original = make("task-001", title="original")
            assert await repo.insert_if_absent(self.PROJECT, original, source="yaml", source_hash="h1") is True
            changed = make("task-001", title="changed", priority=Priority.CRITICAL)
            assert await repo.insert_if_absent(self.PROJECT, changed, source="todo", source_hash="h2") is False
            stored = await repo.get(self.PROJECT, "task-001")
            assert (stored.title, stored.priority) == ("original", Priority.MEDIUM)
            meta = await repo.get_meta(self.PROJECT, "task-001")
            assert (meta.source, meta.source_hash) == ("yaml", "h1")

        self.run(scenario())

    def test_insert_if_absent_does_not_store_outputs(self, repo):
        self.need("insert_if_absent")

        async def scenario():
            task = make("task-001")
            task.pm_output = "in-process only"
            await repo.insert_if_absent(self.PROJECT, task, source="api")
            assert (await repo.get(self.PROJECT, "task-001")).pm_output is None

        self.run(scenario())

    def test_insert_if_absent_is_atomic_under_concurrency(self, repo):
        self.need("insert_if_absent")

        async def scenario():
            results = await asyncio.gather(*[
                repo.insert_if_absent(self.PROJECT, make("task-001", title=f"racer {i}"), source="yaml")
                for i in range(20)
            ])
            assert results.count(True) == 1 and results.count(False) == 19
            assert len(await repo.list_tasks(self.PROJECT)) == 1

        self.run(scenario())

    def test_get_meta_unknown_is_none(self, repo):
        self.need("get_meta")

        async def scenario():
            assert await repo.get_meta(self.PROJECT, "nope") is None

        self.run(scenario())

    def test_update_pending_content_changes_only_pending_tasks(self, repo):
        self.need("update_pending_content")
        self.need("insert_if_absent")

        async def scenario():
            await repo.insert_if_absent(self.PROJECT, make("task-001", status=Status.PENDING), source="yaml", source_hash="h1")
            await repo.insert_if_absent(self.PROJECT, make("task-002", status=Status.IN_PROGRESS), source="yaml", source_hash="h1")
            new = dict(title="new title", description="new desc", priority=Priority.CRITICAL,
                       approval=Approval.AFTER_SWE, max_reworks=7, source_hash="h2")
            assert await repo.update_pending_content(self.PROJECT, "task-001", **new) is True
            task = await repo.get(self.PROJECT, "task-001")
            assert (task.title, task.description, task.priority) == ("new title", "new desc", Priority.CRITICAL)
            assert (task.approval, task.max_reworks, task.status) == (Approval.AFTER_SWE, 7, Status.PENDING)
            assert (await repo.get_meta(self.PROJECT, "task-001")).source_hash == "h2"
            # Not pending any more -> untouched.
            assert await repo.update_pending_content(self.PROJECT, "task-002", **new) is False
            assert (await repo.get(self.PROJECT, "task-002")).title == "title task-002"
            assert (await repo.get_meta(self.PROJECT, "task-002")).source_hash == "h1"
            assert await repo.update_pending_content(self.PROJECT, "nope", **new) is False

        self.run(scenario())

    def test_set_rework_count_persists(self, repo):
        self.need("set_rework_count")

        async def scenario():
            await self.seed(repo, self.PROJECT, make("task-001"))
            assert await repo.set_rework_count(self.PROJECT, "task-001", 2) is True
            assert (await repo.get(self.PROJECT, "task-001")).rework_count == 2
            assert await repo.set_rework_count(self.PROJECT, "nope", 1) is False

        self.run(scenario())


def test_priority_rank_matches_the_pipeline_queue_order():
    """Guards against the two orderings drifting apart."""

    from core.pipeline import PRIORITY_ORDER

    assert {p: priority_rank(p) for p in Priority} == dict(PRIORITY_ORDER)
