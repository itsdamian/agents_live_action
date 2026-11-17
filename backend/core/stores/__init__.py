"""Repository interfaces (plan D2).

Three Protocols describe what the pipeline needs from storage, independent of
where the data lives:

* :class:`TaskRepository`  -- tasks and their status (``tasks.yaml`` in file mode,
  PostgreSQL in db mode),
* :class:`UsageRepository` -- one row per LLM call (the source of truth for spend),
* :class:`RunRepository`   -- task run history ("sticky note" history, plan D20).

Implementations: ``memory.py`` (in-process, used by unit tests), ``file_tasks.py``
(a thin wrapper around the existing ``tasks.yaml`` helpers) and, later, ``pg.py``.

Conventions
-----------
* Every method is ``async``; implementations that block (files) use
  ``asyncio.to_thread``.
* Methods return copies: mutating a returned object never changes the store.
* ``bool`` results mean "did anything match": ``False`` for an unknown task/run.
* An operation a backend cannot offer raises :class:`NotSupportedInFileMode`; the
  contract tests skip those operations for that backend.
* Task *outputs* (``pm_output`` ...) are not persisted: they live in the running
  pipeline and in the deliverable files.
* The Protocols may grow in later tasks, additively only.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from core.usage_tracker import UsageRecord
from models.task import Approval, Priority, Status, Task

# Where a task came from (``tasks.source`` column).
TASK_SOURCES = ("yaml", "todo", "api")

# Task scheduling order: lower rank is served first (same order as the pipeline's
# PriorityQueue; a test keeps the two in step).
_PRIORITY_RANK = {
    Priority.CRITICAL: 0,
    Priority.HIGH: 1,
    Priority.MEDIUM: 2,
    Priority.LOW: 3,
}

RUN_STEP_KINDS = ("station", "rework", "gate")
RUN_OUTCOMES = ("done", "failed")


def priority_rank(priority: Priority) -> int:
    """Scheduling rank of a priority (0 = most urgent)."""

    return _PRIORITY_RANK[Priority(priority)]


class NotSupportedInFileMode(NotImplementedError):
    """The file-backed repository cannot offer this operation."""


# -- data carried by the repositories -----------------------------------------------


@dataclass(frozen=True)
class TaskMeta:
    """Where a stored task came from, and the hash of that source entry."""

    source: str
    source_hash: str | None


@dataclass(frozen=True)
class UsageTotals:
    """Summed usage over a set of usage records."""

    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    call_count: int = 0


@dataclass
class RunRecord:
    """One execution of a task through the pipeline (``task_runs`` row)."""

    id: int
    project_id: str
    task_id: str
    run_no: int
    pipeline_id: str | None
    seats: list[dict]
    outcome: str | None
    reason: str | None
    rework_count: int
    started_at: datetime
    finished_at: datetime | None
    cost_usd: float
    input_tokens: int
    output_tokens: int
    imported: bool


@dataclass(frozen=True)
class StepRecord:
    """One step of a run (``task_run_steps`` row); ``seq`` counts 1, 2, 3 ... per run."""

    run_id: int
    seq: int
    kind: str
    station: str | None
    status: str | None
    note: str | None
    at: datetime


# -- the Protocols ------------------------------------------------------------------------


@runtime_checkable
class TaskRepository(Protocol):
    """Tasks of all projects, addressed by ``(project_id, task_id)``."""

    async def insert_if_absent(
        self, project_id: str, task: Task, *, source: str, source_hash: str | None = None
    ) -> bool:
        """Insert ``task`` unless ``(project_id, task.id)`` exists; ``True`` if inserted.

        Atomic: of several concurrent inserts of one key exactly one returns ``True``.
        An existing task is never modified.
        """

    async def get(self, project_id: str, task_id: str) -> Task | None: ...

    async def get_meta(self, project_id: str, task_id: str) -> TaskMeta | None: ...

    async def list_tasks(
        self, project_id: str, *, statuses: Collection[Status] | None = None
    ) -> list[Task]:
        """All tasks of a project (optionally only those in ``statuses``), unspecified order."""

    async def list_pending(self, project_id: str) -> list[Task]:
        """Pending tasks, most urgent first, then oldest first."""

    async def count_by_status(self, project_id: str) -> dict[str, int]:
        """Task count per status; every status value is present (0 when none)."""

    async def create(
        self,
        project_id: str,
        title: str,
        description: str = "",
        priority: Priority = Priority.MEDIUM,
    ) -> Task:
        """Create a pending task with the next free ``task-NNN`` id (source ``api``).

        Raises ``ValueError`` for an empty title. ``description`` defaults to the title.
        """

    async def set_status(self, project_id: str, task_id: str, status: Status) -> bool:
        """Set the status and refresh ``updated_at``; ``False`` if the task is unknown."""

    async def set_rework_count(self, project_id: str, task_id: str, rework_count: int) -> bool: ...

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
        """Replace the content of a task that is still ``pending``; ``False`` otherwise."""


@runtime_checkable
class UsageRepository(Protocol):
    """One record per LLM call; the source of truth for spend."""

    async def add(self, record: UsageRecord) -> None: ...

    async def list_records(self, project_id: str | None = None) -> list[UsageRecord]:
        """Records of one project (all when ``None``), oldest first."""

    async def totals(
        self, project_id: str, task_id: str, *, since: datetime | None = None
    ) -> UsageTotals:
        """Sum of one task's records, optionally only those at or after ``since``."""


@runtime_checkable
class RunRepository(Protocol):
    """Task run history."""

    async def open_run(
        self,
        project_id: str,
        task_id: str,
        *,
        started_at: datetime,
        pipeline_id: str | None = None,
        seats: Sequence[dict] = (),
        imported: bool = False,
    ) -> RunRecord:
        """Start a run; ``run_no`` is the task's highest run number plus one (from 1)."""

    async def get_run(self, run_id: int) -> RunRecord | None: ...

    async def find_open_run(self, project_id: str, task_id: str) -> RunRecord | None:
        """The task's newest run that has no outcome yet."""

    async def list_runs(self, project_id: str, task_id: str) -> list[RunRecord]:
        """A task's runs, newest ``run_no`` first."""

    async def add_step(
        self,
        run_id: int,
        *,
        kind: str,
        at: datetime,
        station: str | None = None,
        status: str | None = None,
        note: str | None = None,
    ) -> StepRecord:
        """Append a step; ``seq`` is the run's highest seq plus one (from 1, no gaps)."""

    async def list_steps(self, run_id: int) -> list[StepRecord]: ...

    async def update_totals(
        self, run_id: int, *, cost_usd: float, input_tokens: int, output_tokens: int
    ) -> bool: ...

    async def finish_run(
        self,
        run_id: int,
        *,
        outcome: str,
        finished_at: datetime,
        reason: str | None = None,
        rework_count: int = 0,
    ) -> bool: ...

    async def supersede_open_runs(
        self, project_id: str, task_id: str, *, reason: str, finished_at: datetime
    ) -> int:
        """Close the task's unfinished runs as ``failed`` with ``reason``; return how many."""
