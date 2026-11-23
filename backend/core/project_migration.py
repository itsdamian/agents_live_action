"""Move an existing project's tasks into the database the first time it is seen (plan D3 (3)).

A project that was running in file mode has a ``tasks.yaml`` (and maybe ``TODO.md``) holding its
tasks and their statuses. In DB mode, when the ``projects`` table has no row for it, this module
reads those files ONCE and, in a single transaction, creates:

* the ``projects`` row (default LLM ``mock``),
* every task with its current status, ``source`` (``yaml`` / ``todo``) and ``source_hash``,
* one ``imported`` history run for each task that was done or failed,
* for tasks that were ``in_progress`` or ``awaiting_approval`` (the old in-memory checkpointer is
  gone, they can not continue): status ``failed``, reason ``interrupted before migration to
  database``, and an ``imported`` failed run.

Pending tasks stay pending; the startup sequence enqueues them. The files are only read.
Running it again does nothing (the project row exists). Agent configuration is NOT handled
here: importing ``agents/*.yaml`` is a separate, independent step (Task 18).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from core.stores import MigratedRun, MigratedTask, ProjectRepository
from core.task_importer import compute_source_hash
from core.task_watcher import TASKS_FILENAME, TODO_FILENAME, parse_tasks_yaml, parse_todo_md
from models.task import Status, Task

logger = logging.getLogger(__name__)

REASON_BEFORE_MIGRATION = "interrupted before migration to database"
DEFAULT_PROJECT_SETTINGS = {"llm": "mock"}


@dataclass
class MigrationResult:
    migrated: bool = False  # False when the project row already existed
    task_ids: list[str] = field(default_factory=list)
    pending_ids: list[str] = field(default_factory=list)
    interrupted_ids: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)


def _read_tasks(project_path: Path) -> list[tuple[Task, str]]:
    """``(task, source)`` for every task in tasks.yaml then TODO.md (blocking file reads)."""

    found: list[tuple[Task, str]] = []
    if (project_path / TASKS_FILENAME).is_file():
        found += [(t, "yaml") for t in parse_tasks_yaml(project_path / TASKS_FILENAME)]
    if (project_path / TODO_FILENAME).is_file():
        found += [(t, "todo") for t in parse_todo_md(project_path / TODO_FILENAME)]
    return found


async def migrate_project(
    project_id: str, project_path: Path, project_repo: ProjectRepository
) -> MigrationResult:
    """Migrate ``project_id`` if it has no ``projects`` row yet; return what was done.

    Raises whatever the repository raises; nothing is persisted in that case, so calling it
    again on the next start retries cleanly.
    """

    result = MigrationResult()
    if await project_repo.exists(project_id):
        return result

    entries = await asyncio.to_thread(_read_tasks, project_path)
    migrated: list[MigratedTask] = []
    seen: dict[str, str] = {}
    for task, source in entries:
        if task.id in seen:  # tasks.yaml was read first, so it wins over TODO.md
            logger.warning("Task id %s appears in %s and %s of project %s; keeping the first",
                           task.id, seen[task.id], source, project_id)
            result.conflicts.append(task.id)
            continue
        seen[task.id] = source
        migrated.append(_migrate_task(task, source, result))

    created = await project_repo.create_from_migration(
        project_id, settings=dict(DEFAULT_PROJECT_SETTINGS), tasks=migrated
    )
    result.migrated = created
    if created:
        logger.info("Migrated project %s to the database: %d task(s), %d pending, %d interrupted",
                    project_id, len(result.task_ids), len(result.pending_ids), len(result.interrupted_ids))
    return result


def _migrate_task(task: Task, source: str, result: MigrationResult) -> MigratedTask:
    digest = compute_source_hash(task)
    result.task_ids.append(task.id)
    moment = task.updated_at or datetime.now()
    if task.status is Status.PENDING:
        result.pending_ids.append(task.id)
        return MigratedTask(task, source, digest)
    if task.status in (Status.DONE, Status.FAILED):
        run = MigratedRun(outcome=task.status.value, started_at=moment, finished_at=moment)
        return MigratedTask(task, source, digest, run)
    # in_progress / awaiting_approval: it can not continue, so it is failed.
    result.interrupted_ids.append(task.id)
    failed = task.model_copy(update={"status": Status.FAILED})
    run = MigratedRun(outcome="failed", reason=REASON_BEFORE_MIGRATION, started_at=moment, finished_at=moment)
    return MigratedTask(failed, source, digest, run)
