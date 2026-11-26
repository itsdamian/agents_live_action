"""File-mode task repository: a thin wrapper around the existing tasks.yaml helpers.

It adds no behaviour of its own. Reading uses the same parsers as
``FactoryRuntime.load_tasks`` (``tasks.yaml`` plus ``TODO.md``); writing calls
``write_task_status`` and ``append_task`` from ``core.task_watcher`` (so the
Task 3 file lock and atomic replace apply). Operations that need columns
``tasks.yaml`` does not have raise :class:`~core.stores.NotSupportedInFileMode`.

The orchestrator does not use this class yet: in file mode it still calls
``core.pipeline.write_task_status`` directly, which the characterization tests
monkeypatch (plan D2; the repository is wired in by a later task).
"""

from __future__ import annotations

import asyncio
from collections.abc import Collection
from pathlib import Path

from core.stores import NotSupportedInFileMode, TaskMeta, priority_rank
from core.task_watcher import (
    TASKS_FILENAME,
    TODO_FILENAME,
    append_task,
    parse_task_file,
    write_task_status,
)
from models.task import Approval, Priority, Status, Task


class FileTaskRepository:
    """Tasks of every project folder under ``projects_dir``."""

    def __init__(self, projects_dir: str | Path) -> None:
        self._projects_dir = Path(projects_dir)

    def _project_path(self, project_id: str) -> Path:
        return self._projects_dir / project_id

    def _load(self, project_id: str) -> list[Task]:
        path = self._project_path(project_id)
        tasks: list[Task] = []
        for name in (TASKS_FILENAME, TODO_FILENAME):
            if (path / name).is_file():
                tasks.extend(parse_task_file(path / name))
        return tasks

    async def get(self, project_id: str, task_id: str) -> Task | None:
        for task in await self.list_tasks(project_id):
            if task.id == task_id:
                return task
        return None

    async def list_tasks(
        self, project_id: str, *, statuses: Collection[Status] | None = None
    ) -> list[Task]:
        tasks = await asyncio.to_thread(self._load, project_id)
        if statuses is None:
            return tasks
        wanted = {Status(s) for s in statuses}
        return [t for t in tasks if t.status in wanted]

    async def list_pending(self, project_id: str) -> list[Task]:
        pending = await self.list_tasks(project_id, statuses=[Status.PENDING])
        # sorted() is stable: file order breaks ties.
        return sorted(pending, key=lambda t: (priority_rank(t.priority), t.created_at))

    async def count_by_status(self, project_id: str) -> dict[str, int]:
        counts = {status.value: 0 for status in Status}
        for task in await self.list_tasks(project_id):
            counts[task.status.value] += 1
        return counts

    async def create(
        self,
        project_id: str,
        title: str,
        description: str = "",
        priority: Priority = Priority.MEDIUM,
        approval: Approval = Approval.NONE,
    ) -> Task:
        return await asyncio.to_thread(
            append_task, self._project_path(project_id), title, description, priority, approval
        )

    async def set_status(self, project_id: str, task_id: str, status: Status) -> bool:
        return await asyncio.to_thread(
            write_task_status, self._project_path(project_id), task_id, status
        )

    # -- operations tasks.yaml cannot offer -------------------------------------------

    async def insert_if_absent(
        self, project_id: str, task: Task, *, source: str, source_hash: str | None = None
    ) -> bool:
        raise NotSupportedInFileMode("insert_if_absent needs the database (task importer)")

    async def get_meta(self, project_id: str, task_id: str) -> TaskMeta | None:
        raise NotSupportedInFileMode("tasks.yaml keeps no source or source_hash")

    async def set_rework_count(self, project_id: str, task_id: str, rework_count: int) -> bool:
        raise NotSupportedInFileMode("tasks.yaml keeps no rework_count")

    async def update_pending_content(
        self,
        project_id: str,
        task_id: str,
        *,
        title: str,
        description: str,
        priority: Priority,
        approval: Approval,
        max_reworks: int,
        source_hash: str | None = None,
    ) -> bool:
        raise NotSupportedInFileMode("editing pending content is a database-mode operation")
