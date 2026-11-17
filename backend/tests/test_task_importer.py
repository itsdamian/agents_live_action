"""TaskImporter against the idempotency table of plan D3 (1); AC2 (a), (c), (d), (e)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import pytest
import yaml

from core.stores.memory import InMemoryTaskRepository
from core.task_importer import TaskImporter, compute_source_hash
from models.task import Approval, Priority, Status
from tests.stores.contract_tasks import make

P = "proj"


def run(coro):
    return asyncio.run(coro)


def _importer(repo=None, project=P):
    repo = repo or InMemoryTaskRepository()
    return repo, TaskImporter(repo, project)


# -- row: unique key --------------------------------------------------------------------------------


def test_the_unique_key_is_project_and_task_id():
    repo = InMemoryTaskRepository()

    async def scenario():
        a = await TaskImporter(repo, "alpha").import_tasks([make("task-001", title="alpha one")], source="yaml")
        b = await TaskImporter(repo, "beta").import_tasks([make("task-001", title="beta one")], source="yaml")
        assert len(a.to_enqueue) == 1 and len(b.to_enqueue) == 1  # same id, different projects
        assert (await repo.get("alpha", "task-001")).title == "alpha one"
        assert (await repo.get("beta", "task-001")).title == "beta one"

    run(scenario())


# -- row: new id -------------------------------------------------------------------------------------


def test_a_new_pending_task_is_inserted_with_source_and_hash_and_is_to_be_enqueued():
    repo, importer = _importer()
    task = make("task-001")

    result = run(importer.import_tasks([task], source="yaml"))

    assert [t.id for t in result.inserted] == ["task-001"] == [t.id for t in result.to_enqueue]
    meta = run(repo.get_meta(P, "task-001"))
    assert (meta.source, meta.source_hash) == ("yaml", compute_source_hash(task))
    assert run(repo.get(P, "task-001")).status is Status.PENDING


@pytest.mark.parametrize("status", [Status.DONE, Status.FAILED, Status.IN_PROGRESS, Status.AWAITING_APPROVAL])
def test_a_new_task_that_is_not_pending_is_stored_but_never_enqueued(status):
    repo, importer = _importer()

    result = run(importer.import_tasks([make("task-001", status=status)], source="yaml"))

    assert [t.id for t in result.inserted] == ["task-001"] and result.to_enqueue == []
    assert run(repo.get(P, "task-001")).status is status


# -- row: same id again (AC2 c) ----------------------------------------------------------------------


def test_importing_the_same_content_again_enqueues_nothing_and_changes_nothing():
    repo, importer = _importer()
    task = make("task-001")
    run(importer.import_tasks([task], source="yaml"))
    before = run(repo.get(P, "task-001"))

    again = run(importer.import_tasks([make("task-001")], source="yaml"))

    assert again.inserted == [] and again.to_enqueue == [] and again.unchanged == ["task-001"]
    assert run(repo.get(P, "task-001")) == before


def test_two_concurrent_imports_of_one_batch_enqueue_each_task_once():
    repo, importer = _importer()
    batch = [make("task-001"), make("task-002")]

    async def scenario():
        results = await asyncio.gather(importer.import_tasks(batch, source="yaml"), importer.import_tasks(batch, source="yaml"))
        return sorted(t.id for r in results for t in r.to_enqueue)

    assert run(scenario()) == ["task-001", "task-002"]


# -- row: content changed (AC2 d) ----------------------------------------------------------------------


def test_changed_content_of_a_pending_task_updates_it_and_enqueues_nothing_new():
    repo, importer = _importer()
    run(importer.import_tasks([make("task-001")], source="yaml"))
    edited = make("task-001", title="new title", description="new desc", priority=Priority.CRITICAL,
                  approval=Approval.AFTER_QA, max_reworks=9)

    result = run(importer.import_tasks([edited], source="yaml"))

    assert result.updated == ["task-001"] and result.to_enqueue == []
    stored = run(repo.get(P, "task-001"))
    assert (stored.title, stored.description, stored.priority) == ("new title", "new desc", Priority.CRITICAL)
    assert (stored.approval, stored.max_reworks, stored.status) == (Approval.AFTER_QA, 9, Status.PENDING)
    assert run(repo.get_meta(P, "task-001")).source_hash == compute_source_hash(edited)
    # ...and importing that edit once more is a no-op.
    assert run(importer.import_tasks([edited], source="yaml")).unchanged == ["task-001"]


@pytest.mark.parametrize("status", [Status.IN_PROGRESS, Status.AWAITING_APPROVAL, Status.DONE, Status.FAILED])
def test_changed_content_of_a_started_or_finished_task_is_ignored(status, caplog):
    repo, importer = _importer()
    run(importer.import_tasks([make("task-001")], source="yaml"))
    run(repo.set_status(P, "task-001", status))
    caplog.set_level(logging.INFO)

    result = run(importer.import_tasks([make("task-001", title="too late")], source="yaml"))

    assert result.ignored_started == ["task-001"] and result.updated == []
    assert run(repo.get(P, "task-001")).title == "title task-001"
    assert "already started" in caplog.text


def test_the_status_written_in_the_file_is_always_ignored():
    repo, importer = _importer()
    run(importer.import_tasks([make("task-001"), make("task-002")], source="yaml"))
    run(repo.set_status(P, "task-001", Status.DONE))

    # File says pending again (a person "re-opened" it by editing the status): ignored.
    run(importer.import_tasks([make("task-001", status=Status.PENDING)], source="yaml"))
    # File says done for a pending task whose content did not change: ignored too.
    run(importer.import_tasks([make("task-002", status=Status.DONE)], source="yaml"))

    assert run(repo.get(P, "task-001")).status is Status.DONE
    assert run(repo.get(P, "task-002")).status is Status.PENDING


def test_a_status_only_edit_with_other_changes_still_only_updates_content_of_pending_tasks():
    repo, importer = _importer()
    run(importer.import_tasks([make("task-001")], source="yaml"))

    result = run(importer.import_tasks([make("task-001", title="renamed", status=Status.FAILED)], source="yaml"))

    stored = run(repo.get(P, "task-001"))
    assert result.updated == ["task-001"]
    assert (stored.title, stored.status) == ("renamed", Status.PENDING)  # status from the file ignored


# -- row: removed from the file (AC2 e) -----------------------------------------------------------------


def test_removing_a_task_from_the_file_changes_nothing_in_the_database():
    repo, importer = _importer()
    run(importer.import_tasks([make("task-001"), make("task-002")], source="yaml"))

    result = run(importer.import_tasks([make("task-002")], source="yaml"))  # task-001 is gone from the file

    assert result.unchanged == ["task-002"]
    assert sorted(t.id for t in run(repo.list_tasks(P))) == ["task-001", "task-002"]


# -- row: the same id in tasks.yaml and TODO.md ------------------------------------------------------------


@pytest.mark.parametrize("first, second", [("yaml", "todo"), ("todo", "yaml")])
def test_first_writer_wins_when_two_sources_use_the_same_id(first, second, caplog):
    repo, importer = _importer()
    run(importer.import_tasks([make("task-001", title=f"from {first}")], source=first))
    caplog.set_level(logging.WARNING)

    result = run(importer.import_tasks([make("task-001", title=f"from {second}")], source=second))

    assert result.conflicts == ["task-001"] and result.to_enqueue == [] and result.updated == []
    assert run(repo.get(P, "task-001")).title == f"from {first}"
    assert run(repo.get_meta(P, "task-001")).source == first
    assert "collides" in caplog.text


def test_a_file_entry_cannot_take_over_a_task_created_from_the_ui(caplog):
    repo, importer = _importer()
    ui_task = run(repo.create(P, "made in the UI"))
    caplog.set_level(logging.WARNING)

    result = run(importer.import_tasks([make(ui_task.id, title="hand-written")], source="yaml"))

    assert result.conflicts == [ui_task.id]
    assert run(repo.get(P, ui_task.id)).title == "made in the UI"


# -- the hash -----------------------------------------------------------------------------------------------------


def test_the_hash_ignores_status_and_timestamps_but_not_content():
    base = make("task-001")
    same = make("task-001", status=Status.DONE, created_at=datetime(2030, 1, 1), updated_at=datetime(2031, 1, 1))

    assert compute_source_hash(base) == compute_source_hash(same)
    assert len(compute_source_hash(base)) == 64
    for change in ({"title": "x"}, {"description": "x"}, {"priority": Priority.LOW},
                   {"approval": Approval.AFTER_SWE}, {"max_reworks": 7}):
        assert compute_source_hash(make("task-001", **change)) != compute_source_hash(base), change
    assert compute_source_hash(make("task-002")) != compute_source_hash(base)


# -- reading real files -------------------------------------------------------------------------------------------


def test_import_file_reads_tasks_yaml_and_todo_md(tmp_path):
    (tmp_path / "tasks.yaml").write_text(
        yaml.safe_dump({"tasks": [
            {"id": "task-001", "title": "yaml one", "description": "d", "priority": "high", "status": "pending"},
            {"id": "task-002", "title": "yaml done", "description": "d", "priority": "low", "status": "done"},
        ]}), encoding="utf-8")
    (tmp_path / "TODO.md").write_text("- [ ] write docs\n- [x] ship it\n", encoding="utf-8")
    repo, importer = _importer()

    from_yaml = run(importer.import_file(tmp_path / "tasks.yaml"))
    from_todo = run(importer.import_file(tmp_path / "TODO.md"))

    assert [t.id for t in from_yaml.to_enqueue] == ["task-001"]
    assert [t.title for t in from_todo.to_enqueue] == ["write docs"]  # the checked item is stored, not enqueued
    assert len(run(repo.list_tasks(P))) == 4
    assert {run(repo.get_meta(P, t.id)).source for t in run(repo.list_tasks(P))} == {"yaml", "todo"}
    assert run(importer.import_file(tmp_path / "tasks.yaml")).unchanged == ["task-001", "task-002"]


def test_an_unparseable_file_imports_nothing_and_does_not_raise(tmp_path):
    (tmp_path / "tasks.yaml").write_text("tasks: [unclosed", encoding="utf-8")
    repo, importer = _importer()

    result = run(importer.import_file(tmp_path / "tasks.yaml"))

    assert result.inserted == [] and run(repo.list_tasks(P)) == []
