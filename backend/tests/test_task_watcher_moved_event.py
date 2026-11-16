"""TaskWatcher reacts to a rename onto tasks.yaml / TODO.md (Task 12e).

On Linux (inotify) ``os.replace`` produces only a "moved" event. The tests call
the handler directly with watchdog's ``FileMovedEvent`` so they do not depend on
what the host OS actually emits.
"""

from __future__ import annotations

import yaml
from watchdog.events import DirMovedEvent, FileCreatedEvent, FileMovedEvent

from core.task_watcher import TASKS_FILENAME, TODO_FILENAME, TaskWatcher, write_task_status


def _tasks_yaml(statuses: dict[str, str]) -> str:
    return yaml.safe_dump(
        {
            "tasks": [
                {
                    "id": task_id,
                    "title": f"title {task_id}",
                    "description": "d",
                    "priority": "medium",
                    "status": status,
                }
                for task_id, status in statuses.items()
            ]
        },
        sort_keys=False,
    )


def _watcher(tmp_path) -> tuple[TaskWatcher, list]:
    dispatched: list = []
    return TaskWatcher(callback=dispatched.append, watch_dir=tmp_path), dispatched


def test_move_onto_tasks_yaml_dispatches_pending_tasks(tmp_path):
    watcher, dispatched = _watcher(tmp_path)
    tmp = tmp_path / ".tasks.yaml.abc123.tmp"
    dest = tmp_path / TASKS_FILENAME
    dest.write_text(_tasks_yaml({"task-001": "pending"}), encoding="utf-8")

    watcher.on_moved(FileMovedEvent(str(tmp), str(dest)))

    assert [t.id for t in dispatched] == ["task-001"]


def test_move_onto_todo_md_is_processed(tmp_path):
    watcher, dispatched = _watcher(tmp_path)
    dest = tmp_path / TODO_FILENAME
    dest.write_text("- [ ] write docs\n", encoding="utf-8")

    watcher.on_moved(FileMovedEvent(str(tmp_path / "x.swp"), str(dest)))

    assert len(dispatched) == 1


def test_move_between_other_names_is_ignored(tmp_path):
    watcher, dispatched = _watcher(tmp_path)
    # Even if the source is named tasks.yaml, only the destination matters.
    src = tmp_path / TASKS_FILENAME
    src.write_text(_tasks_yaml({"task-001": "pending"}), encoding="utf-8")
    other = tmp_path / "tasks.yaml.bak"
    other.write_text(_tasks_yaml({"task-001": "pending"}), encoding="utf-8")

    watcher.on_moved(FileMovedEvent(str(src), str(other)))

    assert dispatched == []


def test_temp_file_name_never_triggers(tmp_path):
    watcher, dispatched = _watcher(tmp_path)
    tmp = tmp_path / ".tasks.yaml.abc123.tmp"
    tmp.write_text(_tasks_yaml({"task-001": "pending"}), encoding="utf-8")
    moved_away = tmp_path / "gone.tmp"

    watcher.on_created(FileCreatedEvent(str(tmp)))
    watcher.on_moved(FileMovedEvent(str(tmp), str(moved_away)))

    assert dispatched == []


def test_directory_move_is_ignored(tmp_path):
    watcher, dispatched = _watcher(tmp_path)
    (tmp_path / TASKS_FILENAME).write_text(_tasks_yaml({"task-001": "pending"}), encoding="utf-8")

    watcher.on_moved(DirMovedEvent(str(tmp_path / "a"), str(tmp_path / TASKS_FILENAME)))

    assert dispatched == []


def test_own_status_writeback_does_not_redispatch(tmp_path):
    """The pipeline's own os.replace write-back must not enqueue a task twice."""

    watcher, dispatched = _watcher(tmp_path)
    path = tmp_path / TASKS_FILENAME
    path.write_text(_tasks_yaml({"task-001": "pending", "task-002": "pending"}), encoding="utf-8")
    watcher.on_moved(FileMovedEvent(str(tmp_path / ".t.tmp"), str(path)))
    assert [t.id for t in dispatched] == ["task-001", "task-002"]

    # The pipeline writes statuses back; each write is an os.replace -> moved event.
    for status in ("in_progress", "awaiting_approval", "in_progress", "done"):
        write_task_status(tmp_path, "task-001", status)
        watcher.on_moved(FileMovedEvent(str(tmp_path / ".t.tmp"), str(path)))
    write_task_status(tmp_path, "task-002", "failed")
    watcher.on_moved(FileMovedEvent(str(tmp_path / ".t.tmp"), str(path)))

    assert [t.id for t in dispatched] == ["task-001", "task-002"]  # no duplicates


def test_moved_edit_that_adds_a_new_pending_task_is_dispatched_once(tmp_path):
    watcher, dispatched = _watcher(tmp_path)
    path = tmp_path / TASKS_FILENAME
    path.write_text(_tasks_yaml({"task-001": "done"}), encoding="utf-8")
    watcher.on_moved(FileMovedEvent(str(tmp_path / ".a.tmp"), str(path)))
    assert dispatched == []

    path.write_text(_tasks_yaml({"task-001": "done", "task-002": "pending"}), encoding="utf-8")
    watcher.on_moved(FileMovedEvent(str(tmp_path / ".b.tmp"), str(path)))
    watcher.on_moved(FileMovedEvent(str(tmp_path / ".c.tmp"), str(path)))

    assert [t.id for t in dispatched] == ["task-002"]
