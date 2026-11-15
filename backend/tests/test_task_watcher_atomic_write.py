"""write_task_status must replace tasks.yaml atomically (Task 12b).

Readers (tests, the TaskWatcher, API handlers) must never observe a truncated or
half-written file while the pipeline records status changes.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest
import yaml
from watchdog.events import FileCreatedEvent, FileModifiedEvent

from core import task_watcher
from core.task_watcher import (
    TASKS_FILENAME,
    TODO_FILENAME,
    TaskWatcher,
    write_task_status,
)

SAMPLE = {
    "tasks": [
        {
            "id": "task-001",
            "title": "登入頁",
            "description": "Users should log in.",
            "priority": "high",
            "status": "pending",
        },
        {
            "id": "task-002",
            "title": "Second",
            "description": "Untouched.",
            "priority": "low",
            "status": "pending",
        },
    ]
}


def _write_sample(project: Path) -> Path:
    path = project / TASKS_FILENAME
    path.write_text(
        yaml.safe_dump(SAMPLE, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return path


def _names(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir())


# (a) same observable result as the old in-place write ---------------------------------


def test_write_updates_status_and_keeps_everything_else(tmp_path):
    path = _write_sample(tmp_path)

    assert write_task_status(tmp_path, "task-001", "in_progress") is True

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    first, second = raw["tasks"]
    assert first["status"] == "in_progress"
    assert "updated_at" in first
    assert {k: v for k, v in first.items() if k not in ("status", "updated_at")} == {
        k: v for k, v in SAMPLE["tasks"][0].items() if k != "status"
    }
    assert second == SAMPLE["tasks"][1]
    # Same dump settings as before: non-ASCII stays readable, key order kept.
    text = path.read_text(encoding="utf-8")
    assert "登入頁" in text
    assert list(first) == ["id", "title", "description", "priority", "status", "updated_at"]


def test_unknown_task_returns_false_and_leaves_file_alone(tmp_path):
    path = _write_sample(tmp_path)
    before = path.read_bytes()

    assert write_task_status(tmp_path, "nope", "done") is False

    assert path.read_bytes() == before
    assert _names(tmp_path) == [TASKS_FILENAME]


def test_permission_bits_are_preserved(tmp_path):
    path = _write_sample(tmp_path)
    os.chmod(path, 0o640)

    write_task_status(tmp_path, "task-001", "done")

    assert path.stat().st_mode & 0o777 == 0o640


# (b) failure mid-write leaves the original intact and no temp file ---------------------


def test_exception_while_dumping_keeps_original_and_cleans_temp(tmp_path, monkeypatch):
    path = _write_sample(tmp_path)
    before = path.read_bytes()

    def exploding_dump(data, stream, **kwargs):
        stream.write("tasks: [half-written")
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(yaml, "safe_dump", exploding_dump)

    with pytest.raises(RuntimeError, match="disk on fire"):
        write_task_status(tmp_path, "task-001", "done")

    assert path.read_bytes() == before
    assert _names(tmp_path) == [TASKS_FILENAME]


def test_exception_in_replace_keeps_original_and_cleans_temp(tmp_path, monkeypatch):
    path = _write_sample(tmp_path)
    before = path.read_bytes()

    def failing_replace(src, dst):
        raise OSError("cannot rename")

    monkeypatch.setattr(task_watcher.os, "replace", failing_replace)

    with pytest.raises(OSError, match="cannot rename"):
        write_task_status(tmp_path, "task-001", "done")

    assert path.read_bytes() == before
    assert _names(tmp_path) == [TASKS_FILENAME]


# (c) no temp file is left behind, and the watcher would not react to one ----------------


def test_no_temp_file_remains_after_success(tmp_path):
    _write_sample(tmp_path)

    write_task_status(tmp_path, "task-001", "done")
    write_task_status(tmp_path, "task-002", "failed")

    assert _names(tmp_path) == [TASKS_FILENAME]


def test_temp_file_name_is_ignored_by_the_watcher(tmp_path, monkeypatch):
    _write_sample(tmp_path)
    used_temp: list[str] = []
    real_replace = os.replace

    def spying_replace(src, dst):
        used_temp.append(str(src))
        return real_replace(src, dst)

    monkeypatch.setattr(task_watcher.os, "replace", spying_replace)
    write_task_status(tmp_path, "task-001", "done")

    (temp_path,) = used_temp
    assert Path(temp_path).parent == tmp_path  # same dir => atomic rename
    assert Path(temp_path).name not in (TASKS_FILENAME, TODO_FILENAME)

    dispatched: list = []
    watcher = TaskWatcher(callback=dispatched.append, watch_dir=tmp_path)
    # A pending task would be dispatched if the temp file were parsed as tasks.yaml.
    Path(temp_path).write_text(
        yaml.safe_dump(SAMPLE, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    watcher.on_created(FileCreatedEvent(temp_path))
    watcher.on_modified(FileModifiedEvent(temp_path))
    assert dispatched == []


# (d) concurrent readers never see a partial file -----------------------------------------


def test_concurrent_readers_always_see_complete_yaml(tmp_path):
    path = _write_sample(tmp_path)
    stop = threading.Event()
    problems: list[str] = []

    def reader():
        while not stop.is_set():
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001 - any failure is a problem
                problems.append(f"unparseable: {exc!r}")
                return
            if not isinstance(raw, dict) or len(raw.get("tasks", [])) != 2:
                problems.append(f"incomplete: {raw!r}")
                return

    readers = [threading.Thread(target=reader) for _ in range(3)]
    for thread in readers:
        thread.start()
    try:
        for i in range(300):
            status = "in_progress" if i % 2 == 0 else "awaiting_approval"
            assert write_task_status(tmp_path, "task-001", status) is True
    finally:
        stop.set()
        for thread in readers:
            thread.join()

    assert problems == []
    assert _names(tmp_path) == [TASKS_FILENAME]
