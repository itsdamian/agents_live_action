"""tasks.yaml read-modify-write is mutually exclusive in file mode (Task 3).

``write_task_status`` and ``append_task`` both load the whole file, change it and
write it back. Atomic replacement (Task 12b) stops readers seeing half a file, but
without a lock two writers still overwrite each other's change ("lost update").
"""

from __future__ import annotations

import contextlib
import threading
from pathlib import Path

import pytest
import yaml

from core import task_watcher
from core.task_watcher import TASKS_FILENAME, append_task, write_task_status

APPEND_THREADS, APPENDS_EACH = 10, 5
STATUS_THREADS, STATUS_WRITES_EACH = 10, 50
EXISTING = ("task-001", "task-002", "task-003")
EXPECTED_TASKS = 3 + APPEND_THREADS * APPENDS_EACH  # 53
STATUS_CYCLE = ("in_progress", "awaiting_approval", "done", "failed", "pending")


@pytest.fixture(autouse=True)
def _no_fsync(monkeypatch):
    # fsync only slows the workload down; it does not change who races whom.
    monkeypatch.setattr(task_watcher.os, "fsync", lambda fd: None)


def _seed(project: Path) -> Path:
    project.mkdir(parents=True)
    path = project / TASKS_FILENAME
    path.write_text(
        yaml.safe_dump(
            {
                "tasks": [
                    {"id": task_id, "title": f"seed {task_id}", "description": "d",
                     "priority": "medium", "status": "pending"}
                    for task_id in EXISTING
                ]
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def _run_workload(project: Path):
    """Hammer one tasks.yaml from 20 threads; return what the file ends up as.

    Returns ``(tasks, last_written, errors)`` where ``last_written`` maps each
    pre-existing task to the status of the last write that *committed* (recorded
    while the writer still holds the lock, so its order is the commit order).
    """

    path = _seed(project)
    committed: list[tuple[str, str]] = []
    errors: list[BaseException] = []
    original = task_watcher._write_task_status_locked

    def recording(tasks_path, task_id, status_value):
        result = original(tasks_path, task_id, status_value)
        committed.append((task_id, status_value))
        return result

    start = threading.Barrier(APPEND_THREADS + STATUS_THREADS)

    def appender(index):
        try:
            start.wait()
            for n in range(APPENDS_EACH):
                append_task(project, f"appended {index}-{n}")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def status_writer(index):
        try:
            start.wait()
            for n in range(STATUS_WRITES_EACH):
                write_task_status(
                    project, EXISTING[(index + n) % 3], STATUS_CYCLE[(index * 7 + n) % 5]
                )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=appender, args=(i,)) for i in range(APPEND_THREADS)]
    threads += [threading.Thread(target=status_writer, args=(i,)) for i in range(STATUS_THREADS)]
    task_watcher._write_task_status_locked = recording
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        task_watcher._write_task_status_locked = original

    tasks = yaml.safe_load(path.read_text(encoding="utf-8"))["tasks"]
    last_written: dict[str, str] = {}
    for task_id, status in committed:
        last_written[task_id] = status
    return tasks, last_written, errors


def _lost_something(tasks, last_written) -> bool:
    if len(tasks) != EXPECTED_TASKS:
        return True
    final = {t["id"]: t["status"] for t in tasks}
    return any(final[task_id] != status for task_id, status in last_written.items())


def test_concurrent_appends_and_status_writes_lose_nothing(tmp_path):
    tasks, last_written, errors = _run_workload(tmp_path / "locked")

    assert errors == []
    assert len(tasks) == EXPECTED_TASKS
    assert [t["id"] for t in tasks] == [f"task-{i:03d}" for i in range(1, EXPECTED_TASKS + 1)]
    final = {t["id"]: t["status"] for t in tasks}
    # Each pre-existing task ends with the status of its last committed write.
    assert set(last_written) == set(EXISTING)
    for task_id in EXISTING:
        assert final[task_id] == last_written[task_id]
    # Every appended task survived the status writes (and is still pending).
    assert all(t["status"] == "pending" for t in tasks[3:])


def test_without_the_lock_updates_are_lost(tmp_path, monkeypatch):
    """Control group: swap the lock for a no-op and the same workload loses updates."""

    monkeypatch.setattr(task_watcher, "_tasks_file_lock", lambda path: contextlib.nullcontext())

    # One lost update is proof, so stop at the first one. Allowed up to 100 runs; the
    # measured rate while writing this test was 20 of 20 (every unlocked run lost
    # something), so in practice this finishes after a single run.
    runs = 0
    lost_runs = 0
    for attempt in range(100):
        runs += 1
        tasks, last_written, _errors = _run_workload(tmp_path / f"unlocked-{attempt}")
        if _lost_something(tasks, last_written):
            lost_runs += 1
            break

    assert lost_runs >= 1, f"no lost update in {runs} unlocked runs"


def test_lock_is_per_resolved_path(tmp_path):
    first = task_watcher._tasks_file_lock(tmp_path / "a" / TASKS_FILENAME)
    again = task_watcher._tasks_file_lock(tmp_path / "a" / ".." / "a" / TASKS_FILENAME)
    other = task_watcher._tasks_file_lock(tmp_path / "b" / TASKS_FILENAME)

    assert first is again  # two spellings of the same file share one lock
    assert first is not other  # another project does not contend
    assert isinstance(first, type(threading.Lock()))


def test_append_task_now_writes_atomically(tmp_path, monkeypatch):
    project = tmp_path / "p"
    _seed(project)
    calls = []
    real = task_watcher._atomic_dump_yaml

    def spy(path, data):
        calls.append(Path(path).name)
        return real(path, data)

    monkeypatch.setattr(task_watcher, "_atomic_dump_yaml", spy)

    append_task(project, "via api")

    assert calls == [TASKS_FILENAME]
    assert sorted(p.name for p in project.iterdir()) == [TASKS_FILENAME]  # no temp left
