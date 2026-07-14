"""Unit tests for the task watcher.

Covers the two parsers (tasks.yaml, TODO.md), malformed-input tolerance, and
the new-pending-task detection that drives the pipeline callback. File change
events are simulated by invoking the watchdog event handler directly with
synthetic :class:`FileSystemEvent` objects, so the tests neither depend on
real filesystem-event timing nor touch the repo-root ``projects/`` folder.

Requirements: 5.1 (tasks.yaml primary source), 5.3 (TODO.md supplementary
source), 5.4 (callback fires for new pending tasks).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from watchdog.events import FileCreatedEvent, FileModifiedEvent

from core.task_watcher import (
    TaskWatcher,
    parse_task_file,
    parse_tasks_yaml,
    parse_todo_md,
    write_task_status,
)
from models.task import Approval, Priority, Status


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_tasks_yaml(project: Path, body: str) -> Path:
    project.mkdir(parents=True, exist_ok=True)
    target = project / "tasks.yaml"
    target.write_text(body, encoding="utf-8")
    return target


def _write_todo_md(project: Path, body: str) -> Path:
    project.mkdir(parents=True, exist_ok=True)
    target = project / "TODO.md"
    target.write_text(body, encoding="utf-8")
    return target


_VALID_TASKS_YAML = """
tasks:
  - id: task-001
    title: "Implement auth"
    description: "Add JWT-based auth"
    priority: high
    status: pending
    created_at: "2026-06-13T10:00:00"
    updated_at: "2026-06-13T10:00:00"
  - id: task-002
    title: "Write docs"
    description: "Document the API"
    priority: low
    status: done
    created_at: "2026-06-13T11:00:00"
    updated_at: "2026-06-13T11:00:00"
"""


# ---------------------------------------------------------------------------
# parse_tasks_yaml (Requirement 5.1)
# ---------------------------------------------------------------------------


def test_parse_tasks_yaml_reads_all_fields(tmp_path):
    path = _write_tasks_yaml(tmp_path / "proj", _VALID_TASKS_YAML)

    tasks = parse_tasks_yaml(path)

    assert [t.id for t in tasks] == ["task-001", "task-002"]
    first = tasks[0]
    assert first.title == "Implement auth"
    assert first.description == "Add JWT-based auth"
    assert first.priority is Priority.HIGH
    assert first.status is Status.PENDING
    assert tasks[1].status is Status.DONE


def test_parse_tasks_yaml_defaults_missing_timestamps(tmp_path):
    body = """
tasks:
  - id: task-010
    title: "No timestamps"
    description: "created_at/updated_at omitted"
    priority: medium
    status: pending
"""
    path = _write_tasks_yaml(tmp_path / "proj", body)

    tasks = parse_tasks_yaml(path)

    assert len(tasks) == 1
    # Missing timestamps are populated (defaulted to now) rather than rejected.
    assert tasks[0].created_at is not None
    assert tasks[0].updated_at is not None


def test_parse_tasks_yaml_missing_file_returns_empty(tmp_path):
    assert parse_tasks_yaml(tmp_path / "proj" / "tasks.yaml") == []


def test_parse_tasks_yaml_empty_file_returns_empty(tmp_path):
    path = _write_tasks_yaml(tmp_path / "proj", "")
    assert parse_tasks_yaml(path) == []


def test_parse_tasks_yaml_no_tasks_key_returns_empty(tmp_path):
    path = _write_tasks_yaml(tmp_path / "proj", "other: value\n")
    assert parse_tasks_yaml(path) == []


# ---------------------------------------------------------------------------
# Malformed input tolerance
# ---------------------------------------------------------------------------


def test_parse_tasks_yaml_syntax_error_logs_and_skips(tmp_path, caplog):
    # Unbalanced bracket -> YAML syntax error.
    path = _write_tasks_yaml(tmp_path / "proj", "tasks: [ { id: x ")

    with caplog.at_level("WARNING"):
        tasks = parse_tasks_yaml(path)

    assert tasks == []
    assert any("YAML syntax error" in rec.message for rec in caplog.records)


def test_parse_tasks_yaml_skips_only_malformed_entry(tmp_path, caplog):
    body = """
tasks:
  - id: good-1
    title: "Valid"
    description: "ok"
    priority: high
    status: pending
    created_at: "2026-06-13T10:00:00"
    updated_at: "2026-06-13T10:00:00"
  - id: bad-1
    title: "Missing required fields and bad priority"
    priority: not-a-real-priority
    status: pending
"""
    path = _write_tasks_yaml(tmp_path / "proj", body)

    with caplog.at_level("WARNING"):
        tasks = parse_tasks_yaml(path)

    # The valid task survives; the malformed one is skipped with a warning.
    assert [t.id for t in tasks] == ["good-1"]
    assert any("malformed task" in rec.message.lower() for rec in caplog.records)


def test_parse_tasks_yaml_non_mapping_top_level(tmp_path, caplog):
    path = _write_tasks_yaml(tmp_path / "proj", "- just\n- a\n- list\n")
    with caplog.at_level("WARNING"):
        assert parse_tasks_yaml(path) == []


# ---------------------------------------------------------------------------
# parse_todo_md (Requirement 5.3)
# ---------------------------------------------------------------------------


def test_parse_todo_md_parses_checkbox_items(tmp_path):
    body = """# TODO

Some intro text that is not a task.

- [ ] Set up CI pipeline
- [x] Initialise repository
* [ ] Add linting

Regular bullet - [ ] not really since no space handling below:
- not a checkbox item
"""
    path = _write_todo_md(tmp_path / "proj", body)

    tasks = parse_todo_md(path)

    titles = [t.title for t in tasks]
    assert "Set up CI pipeline" in titles
    assert "Initialise repository" in titles
    assert "Add linting" in titles
    assert "not a checkbox item" not in titles


def test_parse_todo_md_status_and_defaults(tmp_path):
    body = "- [ ] Pending item\n- [x] Done item\n"
    path = _write_todo_md(tmp_path / "proj", body)

    tasks = parse_todo_md(path)
    by_title = {t.title: t for t in tasks}

    pending = by_title["Pending item"]
    assert pending.status is Status.PENDING
    assert pending.priority is Priority.MEDIUM
    assert pending.description == "Pending item"
    assert pending.id == "todo-pending-item"
    assert pending.created_at is not None

    assert by_title["Done item"].status is Status.DONE


def test_parse_todo_md_missing_file_returns_empty(tmp_path):
    assert parse_todo_md(tmp_path / "proj" / "TODO.md") == []


def test_parse_todo_md_stable_ids_for_duplicate_titles(tmp_path):
    body = "- [ ] Same title\n- [ ] Same title\n"
    path = _write_todo_md(tmp_path / "proj", body)

    ids = [t.id for t in parse_todo_md(path)]

    assert len(ids) == len(set(ids)), "duplicate titles must produce unique ids"


# ---------------------------------------------------------------------------
# parse_task_file dispatch
# ---------------------------------------------------------------------------


def test_parse_task_file_dispatches_by_name(tmp_path):
    yaml_path = _write_tasks_yaml(tmp_path / "proj", _VALID_TASKS_YAML)
    todo_path = _write_todo_md(tmp_path / "proj", "- [ ] a todo\n")

    assert len(parse_task_file(yaml_path)) == 2
    assert len(parse_task_file(todo_path)) == 1
    # Unrelated filenames yield nothing.
    other = tmp_path / "proj" / "notes.txt"
    other.write_text("- [ ] ignored\n", encoding="utf-8")
    assert parse_task_file(other) == []


# ---------------------------------------------------------------------------
# TaskWatcher event handling (Requirement 5.4)
# ---------------------------------------------------------------------------


def test_new_pending_task_fires_callback(tmp_path):
    project = tmp_path / "proj"
    path = _write_tasks_yaml(project, _VALID_TASKS_YAML)

    received = []
    watcher = TaskWatcher(received.append, watch_dir=tmp_path)

    # Simulate a modify event on tasks.yaml.
    watcher.on_modified(FileModifiedEvent(str(path)))

    # Only the pending task (task-001) is dispatched; the done task is not.
    assert [t.id for t in received] == ["task-001"]
    assert "task-001" in watcher.seen_task_ids


def test_callback_not_fired_twice_for_same_task(tmp_path):
    project = tmp_path / "proj"
    path = _write_tasks_yaml(project, _VALID_TASKS_YAML)

    received = []
    watcher = TaskWatcher(received.append, watch_dir=tmp_path)

    watcher.on_modified(FileModifiedEvent(str(path)))
    # A second save of the same file must not re-dispatch the known task.
    watcher.on_modified(FileModifiedEvent(str(path)))

    assert [t.id for t in received] == ["task-001"]


def test_newly_added_pending_task_fires_on_next_event(tmp_path):
    project = tmp_path / "proj"
    path = _write_tasks_yaml(project, _VALID_TASKS_YAML)

    received = []
    watcher = TaskWatcher(received.append, watch_dir=tmp_path)
    watcher.on_modified(FileModifiedEvent(str(path)))
    assert [t.id for t in received] == ["task-001"]

    # Append a brand new pending task and fire another event.
    path.write_text(
        _VALID_TASKS_YAML
        + """
  - id: task-003
    title: "New work"
    description: "added later"
    priority: critical
    status: pending
    created_at: "2026-06-13T12:00:00"
    updated_at: "2026-06-13T12:00:00"
""",
        encoding="utf-8",
    )
    watcher.on_modified(FileModifiedEvent(str(path)))

    assert [t.id for t in received] == ["task-001", "task-003"]


def test_todo_pending_item_fires_callback_on_create(tmp_path):
    project = tmp_path / "proj"
    path = _write_todo_md(project, "- [ ] Ship it\n- [x] Already done\n")

    received = []
    watcher = TaskWatcher(received.append, watch_dir=tmp_path)

    watcher.on_created(FileCreatedEvent(str(path)))

    assert [t.title for t in received] == ["Ship it"]


def test_directory_events_are_ignored(tmp_path):
    received = []
    watcher = TaskWatcher(received.append, watch_dir=tmp_path)

    dir_event = FileModifiedEvent(str(tmp_path / "proj"))
    dir_event.is_directory = True
    watcher.on_modified(dir_event)

    assert received == []


def test_unrelated_file_events_are_ignored(tmp_path):
    project = tmp_path / "proj"
    project.mkdir(parents=True)
    other = project / "README.md"
    other.write_text("- [ ] not a task source\n", encoding="utf-8")

    received = []
    watcher = TaskWatcher(received.append, watch_dir=tmp_path)
    watcher.on_modified(FileModifiedEvent(str(other)))

    assert received == []


def test_malformed_yaml_event_fires_nothing(tmp_path):
    project = tmp_path / "proj"
    path = _write_tasks_yaml(project, "tasks: [ { id: x ")

    received = []
    watcher = TaskWatcher(received.append, watch_dir=tmp_path)
    watcher.on_modified(FileModifiedEvent(str(path)))

    assert received == []


def test_scan_now_dispatches_existing_tasks(tmp_path):
    _write_tasks_yaml(tmp_path / "proj-a", _VALID_TASKS_YAML)
    _write_todo_md(tmp_path / "proj-b", "- [ ] From todo\n")

    received = []
    watcher = TaskWatcher(received.append, watch_dir=tmp_path)
    dispatched = watcher.scan_now()

    ids = {t.id for t in dispatched}
    assert "task-001" in ids
    assert "todo-from-todo" in ids
    # scan_now and the callback agree.
    assert {t.id for t in received} == ids


def test_prime_marks_existing_tasks_seen_without_firing(tmp_path):
    project = tmp_path / "proj"
    path = _write_tasks_yaml(project, _VALID_TASKS_YAML)

    received = []
    watcher = TaskWatcher(received.append, watch_dir=tmp_path)
    watcher.prime()

    # Nothing dispatched by priming.
    assert received == []
    assert "task-001" in watcher.seen_task_ids

    # A subsequent event for the already-primed task does not fire.
    watcher.on_modified(FileModifiedEvent(str(path)))
    assert received == []


def test_start_and_stop_lifecycle(tmp_path):
    watcher = TaskWatcher(lambda _t: None, watch_dir=tmp_path / "projects")
    watcher.start()
    try:
        assert watcher.watch_dir.is_dir()
    finally:
        watcher.stop()
    # Stopping again is a no-op.
    watcher.stop()


# ---------------------------------------------------------------------------
# write_task_status (Requirement 5.5)
# ---------------------------------------------------------------------------


def test_write_task_status_updates_matching_task(tmp_path):
    project = tmp_path / "proj"
    _write_tasks_yaml(project, _VALID_TASKS_YAML)

    result = write_task_status(project, "task-001", Status.IN_PROGRESS)

    assert result is True
    # Re-parse from disk to confirm the change was persisted.
    tasks = {t.id: t for t in parse_tasks_yaml(project / "tasks.yaml")}
    assert tasks["task-001"].status is Status.IN_PROGRESS
    # The other task is left untouched.
    assert tasks["task-002"].status is Status.DONE


def test_write_task_status_accepts_string_status(tmp_path):
    project = tmp_path / "proj"
    _write_tasks_yaml(project, _VALID_TASKS_YAML)

    assert write_task_status(project, "task-001", "done") is True

    tasks = {t.id: t for t in parse_tasks_yaml(project / "tasks.yaml")}
    assert tasks["task-001"].status is Status.DONE


def test_write_task_status_refreshes_updated_at(tmp_path):
    project = tmp_path / "proj"
    _write_tasks_yaml(project, _VALID_TASKS_YAML)

    before = {t.id: t for t in parse_tasks_yaml(project / "tasks.yaml")}
    original_updated_at = before["task-001"].updated_at

    write_task_status(project, "task-001", Status.FAILED)

    after = {t.id: t for t in parse_tasks_yaml(project / "tasks.yaml")}
    assert after["task-001"].updated_at >= original_updated_at
    # created_at must be preserved, not overwritten.
    assert after["task-001"].created_at == before["task-001"].created_at


def test_write_task_status_preserves_other_fields(tmp_path):
    project = tmp_path / "proj"
    _write_tasks_yaml(project, _VALID_TASKS_YAML)

    write_task_status(project, "task-001", Status.IN_PROGRESS)

    task = {t.id: t for t in parse_tasks_yaml(project / "tasks.yaml")}["task-001"]
    assert task.title == "Implement auth"
    assert task.description == "Add JWT-based auth"
    assert task.priority is Priority.HIGH


def test_write_task_status_unknown_task_returns_false(tmp_path):
    project = tmp_path / "proj"
    _write_tasks_yaml(project, _VALID_TASKS_YAML)

    assert write_task_status(project, "does-not-exist", Status.DONE) is False
    # File is unchanged: both original tasks remain with original statuses.
    tasks = {t.id: t for t in parse_tasks_yaml(project / "tasks.yaml")}
    assert tasks["task-001"].status is Status.PENDING
    assert tasks["task-002"].status is Status.DONE


def test_write_task_status_missing_file_returns_false(tmp_path):
    # No tasks.yaml written.
    assert write_task_status(tmp_path / "proj", "task-001", Status.DONE) is False


def test_write_task_status_malformed_file_returns_false(tmp_path, caplog):
    project = tmp_path / "proj"
    _write_tasks_yaml(project, "tasks: [ { id: x ")

    with caplog.at_level("WARNING"):
        assert write_task_status(project, "x", Status.DONE) is False


def test_write_task_status_invalid_status_raises(tmp_path):
    project = tmp_path / "proj"
    _write_tasks_yaml(project, _VALID_TASKS_YAML)

    with pytest.raises(ValueError):
        write_task_status(project, "task-001", "not-a-status")


def test_parse_tasks_yaml_reads_approval_field(tmp_path):
    """The approval gate field round-trips from tasks.yaml (task 17)."""

    body = """
tasks:
  - id: task-gate
    title: "Gated task"
    description: "Needs sign-off"
    priority: high
    status: pending
    approval: after_qa
    created_at: "2026-06-13T10:00:00"
    updated_at: "2026-06-13T10:00:00"
"""
    path = _write_tasks_yaml(tmp_path / "proj", body)

    tasks = parse_tasks_yaml(path)

    assert len(tasks) == 1
    assert tasks[0].approval is Approval.AFTER_QA


def test_parse_tasks_yaml_defaults_missing_approval_to_none(tmp_path):
    """Backward compatibility: tasks without approval default to no gate."""

    path = _write_tasks_yaml(tmp_path / "proj", _VALID_TASKS_YAML)

    tasks = parse_tasks_yaml(path)

    assert all(t.approval is Approval.NONE for t in tasks)


def test_write_task_status_preserves_approval_field(tmp_path):
    """A status write-back must not drop the approval gate field (task 17)."""

    body = """
tasks:
  - id: task-gate
    title: "Gated task"
    description: "Needs sign-off"
    priority: high
    status: pending
    approval: after_swe
    created_at: "2026-06-13T10:00:00"
    updated_at: "2026-06-13T10:00:00"
"""
    project = tmp_path / "proj"
    _write_tasks_yaml(project, body)

    write_task_status(project, "task-gate", Status.AWAITING_APPROVAL)

    task = parse_tasks_yaml(project / "tasks.yaml")[0]
    assert task.status is Status.AWAITING_APPROVAL
    assert task.approval is Approval.AFTER_SWE


def test_write_task_status_roundtrip_reparses_cleanly(tmp_path):
    # After a write-back the file must still be fully parseable (no structural
    # corruption), so a subsequent watcher pass keeps working.
    project = tmp_path / "proj"
    _write_tasks_yaml(project, _VALID_TASKS_YAML)

    write_task_status(project, "task-001", Status.IN_PROGRESS)
    write_task_status(project, "task-002", Status.PENDING)

    tasks = parse_tasks_yaml(project / "tasks.yaml")
    assert [t.id for t in tasks] == ["task-001", "task-002"]
    assert tasks[0].status is Status.IN_PROGRESS
    assert tasks[1].status is Status.PENDING
