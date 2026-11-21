"""Import tasks from ``tasks.yaml`` / ``TODO.md`` into the task repository (plan D3 (1)).

In DB mode the database is the source of truth. The files are only one way to *add*
tasks: whenever a file is read at startup or after a change, this importer turns what it
finds into repository rows following the idempotency table of plan D3 (1):

========================  =================================================================
situation                 rule
========================  =================================================================
unique key                ``(project_id, task_id)``; ids come from the file / the TODO slug
new id                    insert as ``pending`` (the file's status is ignored) with ``source``
                          and ``source_hash``; enqueue it only if it was really inserted
same id, content changed  DB task still ``pending`` -> update title, description, priority,
                          approval, max_reworks and the hash; started or finished -> ignore
                          (log). The ``status`` in the file is always ignored.
removed from the file     nothing happens (the importer never deletes)
id used by two sources    the first writer wins; the other is logged and skipped
id repeated in one file   the first entry wins; the later ones are logged and skipped
========================  =================================================================

The importer enqueues nothing itself: it returns the tasks the caller should enqueue.
It is pure logic over a :class:`~core.stores.TaskRepository`, so it is tested with the
in-memory repository.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from core.stores import TaskRepository
from core.task_watcher import TASKS_FILENAME, TODO_FILENAME, parse_task_file
from models.task import Status, Task

logger = logging.getLogger(__name__)

SOURCE_FOR_FILENAME = {TASKS_FILENAME: "yaml", TODO_FILENAME: "todo"}


def compute_source_hash(task: Task) -> str:
    """SHA-256 of a task's *content*: everything a person writes, nothing the system changes.

    Status and timestamps are excluded (the system rewrites them); so are outputs. Two file
    entries that mean the same thing hash equal even if one omits defaulted fields.
    """

    content = {
        "id": task.id,
        "title": task.title,
        "description": task.description,
        "priority": task.priority.value,
        "approval": task.approval.value,
        "max_reworks": task.max_reworks,
    }
    payload = json.dumps(content, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class ImportResult:
    """What one import pass did, by task id."""

    inserted: list[Task] = field(default_factory=list)  # new rows (always stored as pending)
    to_enqueue: list[Task] = field(default_factory=list)  # the new rows, to be enqueued by the caller
    updated: list[str] = field(default_factory=list)  # pending rows whose content was refreshed
    unchanged: list[str] = field(default_factory=list)
    ignored_started: list[str] = field(default_factory=list)  # changed in the file, but already begun
    conflicts: list[str] = field(default_factory=list)  # id owned by another source


class TaskImporter:
    """Imports the tasks of one project."""

    def __init__(self, repo: TaskRepository, project_id: str) -> None:
        self._repo = repo
        self._project_id = project_id

    async def import_file(self, path: str | Path) -> ImportResult:
        """Parse ``tasks.yaml`` or ``TODO.md`` and import its tasks."""

        path = Path(path)
        source = SOURCE_FOR_FILENAME[path.name]
        tasks = await _parse(path)
        return await self.import_tasks(tasks, source=source)

    async def import_tasks(self, tasks: Sequence[Task], *, source: str) -> ImportResult:
        result = ImportResult()
        seen: set[str] = set()
        for task in tasks:
            if task.id in seen:
                logger.warning(
                    "Task id %s appears more than once in %s of project %s; keeping the first entry",
                    task.id, source, self._project_id,
                )
                continue
            seen.add(task.id)
            await self._import_one(task, source, result)
        return result

    async def _import_one(self, task: Task, source: str, result: ImportResult) -> None:
        digest = compute_source_hash(task)
        # The status written in the file is never trusted, not even for a new id: a new task
        # always starts pending (it can not be in_progress or at a gate without a worker on it).
        task = task.model_copy(update={"status": Status.PENDING})
        inserted = await self._repo.insert_if_absent(
            self._project_id, task, source=source, source_hash=digest
        )
        if inserted:
            result.inserted.append(task)
            result.to_enqueue.append(task)
            return

        meta = await self._repo.get_meta(self._project_id, task.id)
        stored = await self._repo.get(self._project_id, task.id)
        if meta is None or stored is None:  # vanished between the two calls: next pass handles it
            return
        if meta.source != source:
            logger.warning(
                "Task id %s in %s collides with a task already imported from %s; skipping it",
                task.id, source, meta.source,
            )
            result.conflicts.append(task.id)
            return
        if meta.source_hash == digest:
            result.unchanged.append(task.id)
            return
        if stored.status is not Status.PENDING:
            logger.info(
                "Task %s changed in %s but has already started (%s); ignoring the change",
                task.id, source, stored.status.value,
            )
            result.ignored_started.append(task.id)
            return
        updated = await self._repo.update_pending_content(
            self._project_id, task.id,
            title=task.title, description=task.description, priority=task.priority,
            approval=task.approval, max_reworks=task.max_reworks, source_hash=digest,
        )
        # False means it started in the meantime: same as the "already started" case.
        (result.updated if updated else result.ignored_started).append(task.id)


async def _parse(path: Path) -> list[Task]:
    import asyncio

    return await asyncio.to_thread(parse_task_file, path)
