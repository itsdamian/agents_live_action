"""TaskWatcher's optional ``on_file_changed`` hook (Task 11b); default behaviour is unchanged."""

from __future__ import annotations

import yaml
from watchdog.events import DirModifiedEvent, FileCreatedEvent, FileModifiedEvent, FileMovedEvent

from core.task_watcher import TASKS_FILENAME, TODO_FILENAME, TaskWatcher


def _write(path, status="pending"):
    path.write_text(yaml.safe_dump({"tasks": [{"id": "task-001", "title": "t", "description": "d",
                                               "priority": "low", "status": status}]}), encoding="utf-8")


def test_with_the_hook_changed_files_go_to_it_and_not_to_the_task_callback(tmp_path):
    path = tmp_path / TASKS_FILENAME
    _write(path)
    dispatched, changed = [], []
    watcher = TaskWatcher(dispatched.append, watch_dir=tmp_path, on_file_changed=changed.append)

    watcher.on_created(FileCreatedEvent(str(path)))
    watcher.on_modified(FileModifiedEvent(str(path)))
    watcher.on_moved(FileMovedEvent(str(tmp_path / ".tasks.yaml.x.tmp"), str(path)))

    assert changed == [path, path, path]
    assert dispatched == [] and watcher.seen_task_ids == frozenset()  # no parsing happened here


def test_the_hook_sees_todo_md_too_but_nothing_else(tmp_path):
    changed = []
    watcher = TaskWatcher(lambda t: None, watch_dir=tmp_path, on_file_changed=changed.append)

    watcher.on_modified(FileModifiedEvent(str(tmp_path / TODO_FILENAME)))
    watcher.on_modified(FileModifiedEvent(str(tmp_path / "notes.txt")))
    watcher.on_created(FileCreatedEvent(str(tmp_path / ".tasks.yaml.abc.tmp")))  # our own temp file
    watcher.on_modified(DirModifiedEvent(str(tmp_path)))

    assert changed == [tmp_path / TODO_FILENAME]


def test_a_failing_hook_does_not_break_the_watcher(tmp_path):
    path = tmp_path / TASKS_FILENAME
    _write(path)
    calls = []

    def hook(p):
        calls.append(p)
        raise RuntimeError("boom")

    watcher = TaskWatcher(lambda t: None, watch_dir=tmp_path, on_file_changed=hook)

    watcher.on_modified(FileModifiedEvent(str(path)))
    watcher.on_modified(FileModifiedEvent(str(path)))

    assert len(calls) == 2


def test_without_the_hook_behaviour_is_the_original_dispatch(tmp_path):
    path = tmp_path / TASKS_FILENAME
    _write(path)
    dispatched = []
    watcher = TaskWatcher(dispatched.append, watch_dir=tmp_path)

    watcher.on_modified(FileModifiedEvent(str(path)))
    watcher.on_modified(FileModifiedEvent(str(path)))  # seen already

    assert [t.id for t in dispatched] == ["task-001"]


def test_existing_task_files_is_public(tmp_path):
    (tmp_path / "a").mkdir()
    _write(tmp_path / "a" / TASKS_FILENAME)
    (tmp_path / TODO_FILENAME).write_text("- [ ] x\n", encoding="utf-8")

    found = TaskWatcher(lambda t: None, watch_dir=tmp_path).existing_task_files()

    assert sorted(p.relative_to(tmp_path).as_posix() for p in found) == ["TODO.md", "a/tasks.yaml"]
