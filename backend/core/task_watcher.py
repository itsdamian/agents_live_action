"""Task file watcher.

Watches the ``projects/`` tree for changes to task sources and turns newly
appearing work into :class:`~models.task.Task` objects that can be pushed into
the pipeline queue. Two task sources are supported:

* ``<project>/tasks.yaml`` — the primary task definition format
  (Requirement 5.1). A ``tasks:`` list of mappings, each carrying
  ``id`` / ``title`` / ``description`` / ``priority`` / ``status`` and,
  optionally, ``created_at`` / ``updated_at`` timestamps.
* ``<project>/TODO.md`` — a supplementary source (Requirement 5.3). Markdown
  checkbox items (``- [ ]`` pending, ``- [x]`` done) are parsed into tasks.

When a new ``status: pending`` task appears in either source, the watcher
invokes a caller-supplied callback so the task can be enqueued into the
pipeline (Requirement 5.4). Already-seen task ids are tracked so a task is only
handed off once, even though watchdog fires an event on every file save.

The reverse direction is handled by :func:`write_task_status`, which writes an
updated task status back into ``tasks.yaml`` so the file always reflects the
latest pipeline state (Requirement 5.5).

Parsing is tolerant: a YAML syntax error logs a warning and yields no tasks,
and individual malformed task entries are logged and skipped so one bad entry
never hides the rest of the file (see design "Task 文件解析失敗").

Requirements:
    5.1 (find tasks.yaml / tasks YAML as the primary task source),
    5.3 (parse TODO.md ``- [ ]`` items as a supplementary task source),
    5.4 (trigger a callback to enqueue newly appearing pending tasks),
    5.5 (write task status updates back to tasks.yaml).
"""

from __future__ import annotations

import logging
import os
import re
import stat
import tempfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import yaml
from pydantic import ValidationError
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from core.project_scanner import DEFAULT_PROJECTS_DIR
from models.task import Priority, Status, Task

logger = logging.getLogger(__name__)

# Filenames the watcher reacts to. Kept as module constants so tests and other
# modules can reference them without hardcoding strings.
TASKS_FILENAME = "tasks.yaml"
TODO_FILENAME = "TODO.md"

# Callback invoked with each newly-detected pending task.
TaskCallback = Callable[[Task], None]

# Matches a Markdown checkbox list item, e.g. "- [ ] do the thing" or
# "* [x] finished". Capture group ``mark`` is the checkbox contents and
# ``title`` is the trimmed item text.
_TODO_ITEM_RE = re.compile(r"^\s*[-*]\s*\[(?P<mark>[ xX])\]\s+(?P<title>.+?)\s*$")

# Non-alphanumeric runs are collapsed to a single hyphen when building a stable
# id from a TODO item's title.
_SLUG_RE = re.compile(r"[^a-z0-9]+")

# Defaults applied to TODO-sourced tasks, which carry no structured metadata.
_TODO_DEFAULT_PRIORITY = Priority.MEDIUM


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def parse_tasks_yaml(path: str | Path) -> list[Task]:
    """Parse ``tasks.yaml`` into a list of :class:`Task` objects.

    The file is expected to hold a top-level mapping with a ``tasks`` key whose
    value is a list of task mappings. Missing ``created_at`` / ``updated_at``
    timestamps are populated with :func:`datetime.now`.

    Malformed input is tolerated (design "Task 文件解析失敗"):

    * a YAML syntax error logs a warning and returns an empty list;
    * a non-mapping top level, or a missing/invalid ``tasks`` list, logs a
      warning and returns an empty list;
    * an individual task entry that fails validation logs a warning and is
      skipped, leaving the remaining valid tasks intact.

    Args:
        path: Path to the ``tasks.yaml`` file.

    Returns:
        The successfully parsed tasks, in file order.
    """

    path = Path(path)
    try:
        with path.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
    except FileNotFoundError:
        logger.warning("tasks.yaml not found: %s", path)
        return []
    except yaml.YAMLError as exc:
        logger.warning("Failed to parse %s (YAML syntax error: %s); skipping.", path, exc)
        return []

    if raw is None:
        # Empty file — no tasks, not an error.
        return []

    if not isinstance(raw, dict):
        logger.warning(
            "Unexpected %s structure (expected a mapping, got %s); skipping.",
            path,
            type(raw).__name__,
        )
        return []

    entries = raw.get("tasks")
    if entries is None:
        return []
    if not isinstance(entries, list):
        logger.warning(
            "'tasks' in %s is not a list (got %s); skipping.",
            path,
            type(entries).__name__,
        )
        return []

    tasks: list[Task] = []
    for index, entry in enumerate(entries):
        task = _build_task_from_mapping(entry, source=path, index=index)
        if task is not None:
            tasks.append(task)
    return tasks


def _build_task_from_mapping(
    entry: object, *, source: Path, index: int
) -> Task | None:
    """Turn one ``tasks.yaml`` entry into a :class:`Task`, or skip it.

    Returns ``None`` (after logging a warning) when the entry is not a mapping
    or fails Pydantic validation.
    """

    if not isinstance(entry, dict):
        logger.warning(
            "Skipping malformed task #%d in %s (expected a mapping, got %s).",
            index,
            source,
            type(entry).__name__,
        )
        return None

    data = dict(entry)
    # created_at / updated_at are required by the model but optional in the
    # file; default them to now so hand-written task files stay terse.
    now = datetime.now()
    data.setdefault("created_at", now)
    data.setdefault("updated_at", now)

    try:
        return Task(**data)
    except ValidationError as exc:
        logger.warning(
            "Skipping malformed task #%d in %s (invalid fields: %s).",
            index,
            source,
            exc,
        )
        return None


def _slugify(title: str) -> str:
    """Build a filesystem/id-friendly slug from a TODO item's title."""

    slug = _SLUG_RE.sub("-", title.lower()).strip("-")
    return slug


def parse_todo_md(path: str | Path) -> list[Task]:
    """Parse ``- [ ]`` / ``- [x]`` checkbox items from ``TODO.md`` into tasks.

    Each checkbox line becomes a :class:`Task`. Because TODO items carry no
    structured metadata, sensible defaults are applied:

    * ``id`` — ``todo-<slug>`` derived from the item title (stable across
      re-parses so the same item is not enqueued twice); falls back to
      ``todo-<line-number>`` when the title has no slug-able characters.
    * ``description`` — the item title (there is no separate description).
    * ``priority`` — ``medium``.
    * ``status`` — ``pending`` for ``[ ]``, ``done`` for ``[x]``/``[X]``.
    * ``created_at`` / ``updated_at`` — :func:`datetime.now`.

    Args:
        path: Path to the ``TODO.md`` file.

    Returns:
        The parsed tasks, in file order. Returns an empty list when the file is
        missing or contains no checkbox items.
    """

    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("TODO.md not found: %s", path)
        return []

    tasks: list[Task] = []
    seen_ids: set[str] = set()
    for line_no, line in enumerate(text.splitlines(), start=1):
        match = _TODO_ITEM_RE.match(line)
        if match is None:
            continue

        title = match.group("title").strip()
        if not title:
            continue

        done = match.group("mark").lower() == "x"
        slug = _slugify(title)
        task_id = f"todo-{slug}" if slug else f"todo-{line_no}"
        # Guard against duplicate titles within the same file producing
        # colliding ids.
        if task_id in seen_ids:
            task_id = f"{task_id}-{line_no}"
        seen_ids.add(task_id)

        now = datetime.now()
        tasks.append(
            Task(
                id=task_id,
                title=title,
                description=title,
                priority=_TODO_DEFAULT_PRIORITY,
                status=Status.DONE if done else Status.PENDING,
                created_at=now,
                updated_at=now,
            )
        )
    return tasks


def parse_task_file(path: str | Path) -> list[Task]:
    """Dispatch to the right parser based on a path's filename.

    Recognizes ``tasks.yaml`` and ``TODO.md``; any other filename yields an
    empty list.
    """

    path = Path(path)
    if path.name == TASKS_FILENAME:
        return parse_tasks_yaml(path)
    if path.name == TODO_FILENAME:
        return parse_todo_md(path)
    return []


# ---------------------------------------------------------------------------
# Status write-back
# ---------------------------------------------------------------------------


def write_task_status(
    project_path: str | Path,
    task_id: str,
    status: Status | str,
) -> bool:
    """Write an updated task status back into ``<project>/tasks.yaml``.

    Keeps the on-disk task file in sync with the pipeline so it always reflects
    the latest state (Requirement 5.5). The matching task's ``status`` field is
    updated in place and its ``updated_at`` timestamp is refreshed; every other
    field and the overall file structure are preserved, so hand-authored
    ordering, comments-free layout, and unrelated tasks stay intact.

    The update is done by re-reading the current file rather than serializing a
    cached model, so concurrent edits made between reads are respected.

    Args:
        project_path: Path to the project root directory (the folder that
            contains ``tasks.yaml``).
        task_id: The ``id`` of the task to update.
        status: The new status, either a :class:`~models.task.Status` member or
            its string value (e.g. ``"in_progress"``).

    Returns:
        ``True`` if a matching task was found and the file was rewritten;
        ``False`` if the file is missing, unparseable, structurally invalid, or
        contains no task with ``task_id``.

    Raises:
        ValueError: If ``status`` is not a valid :class:`Status` value.
    """

    # Validate/normalize the status up front so an invalid value fails loudly
    # rather than silently writing garbage into the file.
    status_value = Status(status).value

    tasks_path = Path(project_path) / TASKS_FILENAME
    try:
        with tasks_path.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
    except FileNotFoundError:
        logger.warning(
            "Cannot write task status: %s not found (task %s).",
            tasks_path,
            task_id,
        )
        return False
    except yaml.YAMLError as exc:
        logger.warning(
            "Cannot write task status: %s is unparseable (%s); leaving as-is.",
            tasks_path,
            exc,
        )
        return False

    if not isinstance(raw, dict):
        logger.warning(
            "Cannot write task status: unexpected %s structure (expected a "
            "mapping, got %s).",
            tasks_path,
            type(raw).__name__,
        )
        return False

    entries = raw.get("tasks")
    if not isinstance(entries, list):
        logger.warning(
            "Cannot write task status: 'tasks' in %s is not a list (got %s).",
            tasks_path,
            type(entries).__name__ if entries is not None else "missing",
        )
        return False

    target = None
    for entry in entries:
        if isinstance(entry, dict) and entry.get("id") == task_id:
            target = entry
            break

    if target is None:
        logger.warning(
            "Cannot write task status: no task with id %r in %s.",
            task_id,
            tasks_path,
        )
        return False

    target["status"] = status_value
    # Refresh the timestamp so the file records when the state last changed.
    target["updated_at"] = datetime.now().isoformat()

    _atomic_dump_yaml(tasks_path, raw)

    logger.info(
        "Wrote status %s for task %s to %s", status_value, task_id, tasks_path
    )
    return True


def _atomic_dump_yaml(path: Path, data: object) -> None:
    """Write ``data`` as YAML to ``path`` so readers never see a partial file.

    The YAML is written to a temp file in the same directory (same filesystem,
    so the final rename is atomic), flushed and fsynced, then moved over
    ``path`` with :func:`os.replace`. The temp file is named
    ``.<name>.<random>.tmp`` -- never ``tasks.yaml`` / ``TODO.md`` -- so the
    :class:`TaskWatcher` ignores it. The original file's permission bits are
    kept. On any failure the temp file is removed and ``path`` is untouched.
    """

    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        mode = 0o644

    tmp = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    try:
        with tmp as fh:
            yaml.safe_dump(
                data,
                fh,
                allow_unicode=True,
                sort_keys=False,
                default_flow_style=False,
            )
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp.name, mode)
        os.replace(tmp.name, path)
    except BaseException:
        try:
            os.unlink(tmp.name)
        except FileNotFoundError:
            pass
        raise


def append_task(
    project_path: str | Path,
    title: str,
    description: str = "",
    priority: Priority | str = Priority.MEDIUM,
) -> Task:
    """Append a new pending task to ``<project>/tasks.yaml`` and return it.

    Used by the ``POST /api/projects/{id}/tasks`` endpoint so users can create
    tasks from the UI instead of hand-editing YAML. The file remains the single
    source of truth: this only writes the file, and the running
    :class:`TaskWatcher` picks the new task up and enqueues it exactly as if it
    had been added by hand (Requirement 5.4).

    A unique id of the form ``task-NNN`` is generated from the highest existing
    numeric suffix. The file is created (with a ``tasks:`` list) when missing.

    Args:
        project_path: Path to the project root directory.
        title: Task title (must be non-empty after stripping).
        description: Task description; defaults to the title when empty.
        priority: Task priority (enum member or its string value).

    Returns:
        The created :class:`Task`.

    Raises:
        ValueError: If ``title`` is empty or ``priority`` is invalid.
        yaml.YAMLError: If an existing tasks.yaml cannot be parsed (the file is
            left untouched rather than clobbered).
    """

    title = title.strip()
    if not title:
        raise ValueError("Task title must not be empty.")
    priority = Priority(priority)

    tasks_path = Path(project_path) / TASKS_FILENAME
    raw: dict = {"tasks": []}
    if tasks_path.is_file():
        with tasks_path.open("r", encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh)
        if loaded is not None:
            if not isinstance(loaded, dict) or not isinstance(
                loaded.get("tasks", []), list
            ):
                raise ValueError(f"{tasks_path} has an unexpected structure.")
            raw = loaded
            raw.setdefault("tasks", [])

    entries: list = raw["tasks"]
    highest = 0
    for entry in entries:
        if isinstance(entry, dict):
            match = re.fullmatch(r"task-(\d+)", str(entry.get("id", "")))
            if match:
                highest = max(highest, int(match.group(1)))

    now = datetime.now()
    task = Task(
        id=f"task-{highest + 1:03d}",
        title=title,
        description=description.strip() or title,
        priority=priority,
        status=Status.PENDING,
        created_at=now,
        updated_at=now,
    )
    entries.append(
        {
            "id": task.id,
            "title": task.title,
            "description": task.description,
            "priority": task.priority.value,
            "status": task.status.value,
            "created_at": task.created_at.isoformat(),
            "updated_at": task.updated_at.isoformat(),
        }
    )

    with tasks_path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(
            raw,
            fh,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )

    logger.info("Appended task %s (%s) to %s", task.id, task.title, tasks_path)
    return task


# ---------------------------------------------------------------------------
# Watcher
# ---------------------------------------------------------------------------


class TaskWatcher(FileSystemEventHandler):
    """Watch a projects tree and fire a callback for new pending tasks.

    The watcher subclasses :class:`~watchdog.events.FileSystemEventHandler` so
    it can be scheduled directly with a watchdog ``Observer``. On every create
    or modify event for a ``tasks.yaml`` or ``TODO.md`` file it re-parses that
    file and invokes ``callback`` once for each ``status: pending`` task whose
    id has not been seen before (Requirement 5.4).

    Args:
        callback: Called with each newly-detected pending :class:`Task`.
        watch_dir: Directory tree to watch. Defaults to the repo-root
            ``projects/`` directory; tests override it with a tmp dir.

    Example::

        watcher = TaskWatcher(pipeline.enqueue, watch_dir=projects_dir)
        watcher.start()
        ...
        watcher.stop()
    """

    def __init__(
        self,
        callback: TaskCallback,
        watch_dir: str | Path | None = None,
    ) -> None:
        super().__init__()
        self._callback = callback
        self._watch_dir = (
            Path(watch_dir) if watch_dir is not None else DEFAULT_PROJECTS_DIR
        )
        self._seen_ids: set[str] = set()
        self._observer: Observer | None = None

    @property
    def watch_dir(self) -> Path:
        """The directory tree this watcher observes."""

        return self._watch_dir

    @property
    def seen_task_ids(self) -> frozenset[str]:
        """Ids of tasks already handed to the callback."""

        return frozenset(self._seen_ids)

    # -- watchdog event hooks ------------------------------------------------

    def on_created(self, event: FileSystemEvent) -> None:  # noqa: D102 - see base
        self._handle_event(event)

    def on_modified(self, event: FileSystemEvent) -> None:  # noqa: D102 - see base
        self._handle_event(event)

    def on_moved(self, event: FileSystemEvent) -> None:  # noqa: D102 - see base
        # A rename onto tasks.yaml / TODO.md (atomic saves, including our own
        # write_task_status via os.replace) arrives on Linux (inotify) only as a
        # "moved" event, with the real file as ``dest_path``. The temp file name
        # (``src_path``) is never looked at, so it cannot trigger anything.
        if event.is_directory:
            return
        dest = getattr(event, "dest_path", "")
        if not dest:
            return
        self._process_file_if_watched(Path(dest))

    def _handle_event(self, event: FileSystemEvent) -> None:
        """Parse a changed task file and dispatch any new pending tasks."""

        if event.is_directory:
            return
        self._process_file_if_watched(Path(event.src_path))

    def _process_file_if_watched(self, path: Path) -> None:
        if path.name not in (TASKS_FILENAME, TODO_FILENAME):
            return
        self._process_file(path)

    # -- core logic ----------------------------------------------------------

    def _process_file(self, path: str | Path) -> list[Task]:
        """Parse ``path`` and fire the callback for each new pending task.

        Returns the list of tasks that were newly dispatched (useful in tests).
        """

        dispatched: list[Task] = []
        for task in parse_task_file(path):
            if task.status is not Status.PENDING:
                continue
            if task.id in self._seen_ids:
                continue
            self._seen_ids.add(task.id)
            try:
                self._callback(task)
            except Exception:  # pragma: no cover - defensive
                logger.exception(
                    "Task callback raised for task %s from %s", task.id, path
                )
            dispatched.append(task)
        return dispatched

    def prime(self) -> None:
        """Mark tasks currently on disk as seen without firing the callback.

        Call this after :meth:`start` (or before) when the watcher should only
        react to tasks added *after* startup, treating everything already
        present as known.
        """

        for path in self._existing_task_files():
            for task in parse_task_file(path):
                self._seen_ids.add(task.id)

    def scan_now(self) -> list[Task]:
        """Parse every task file currently on disk and dispatch new tasks.

        Useful at startup to enqueue pending tasks that already exist before
        the observer began watching. Returns the tasks dispatched this call.
        """

        dispatched: list[Task] = []
        for path in self._existing_task_files():
            dispatched.extend(self._process_file(path))
        return dispatched

    def _existing_task_files(self) -> list[Path]:
        """Return existing tasks.yaml / TODO.md files under the watch dir."""

        if not self._watch_dir.is_dir():
            return []
        found: list[Path] = []
        for name in (TASKS_FILENAME, TODO_FILENAME):
            found.extend(sorted(self._watch_dir.rglob(name)))
        return found

    # -- observer lifecycle --------------------------------------------------

    def start(self) -> None:
        """Begin watching ``watch_dir`` recursively for task file changes."""

        if self._observer is not None:
            logger.debug("TaskWatcher already started for %s", self._watch_dir)
            return
        self._watch_dir.mkdir(parents=True, exist_ok=True)
        observer = Observer()
        observer.schedule(self, str(self._watch_dir), recursive=True)
        observer.start()
        self._observer = observer
        logger.info("TaskWatcher started, watching %s", self._watch_dir)

    def stop(self) -> None:
        """Stop the observer and release its resources."""

        if self._observer is None:
            return
        self._observer.stop()
        self._observer.join()
        self._observer = None
        logger.info("TaskWatcher stopped for %s", self._watch_dir)
