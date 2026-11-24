"""In-process repository implementations, used by unit tests (plan D9).

Not thread-safe: they are meant to be used from one event loop, like the real
repositories. They follow the same contract tests as the PostgreSQL versions.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Sequence
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime

from core.stores import (
    AGENT_KINDS,
    AGENT_UPDATABLE_FIELDS,
    TASK_SOURCES,
    AgentRecord,
    PipelineRecord,
    agent_sort_key,
    MigratedTask,
    RunRecord,
    StepRecord,
    TaskMeta,
    UsageTotals,
    priority_rank,
)
from core.usage_tracker import UsageRecord
from models.task import Approval, Priority, Status, Task

Clock = Callable[[], datetime]


def _persisted(task: Task) -> Task:
    """Copy of ``task`` as a database would keep it: outputs are not stored."""

    return task.model_copy(
        deep=True, update={"pm_output": None, "swe_output": None, "qa_output": None}
    )


@dataclass
class _TaskRow:
    task: Task
    meta: TaskMeta
    seq: int


class InMemoryTaskRepository:
    def __init__(self, *, clock: Clock = datetime.now) -> None:
        self._clock = clock
        self._rows: dict[tuple[str, str], _TaskRow] = {}
        self._seq = 0

    def _row(self, project_id: str, task_id: str) -> _TaskRow | None:
        return self._rows.get((project_id, task_id))

    async def insert_if_absent(
        self, project_id: str, task: Task, *, source: str, source_hash: str | None = None
    ) -> bool:
        key = (project_id, task.id)
        if key in self._rows:
            return False
        self._seq += 1
        self._rows[key] = _TaskRow(_persisted(task), TaskMeta(source, source_hash), self._seq)
        return True

    async def get(self, project_id: str, task_id: str) -> Task | None:
        row = self._row(project_id, task_id)
        return row.task.model_copy(deep=True) if row else None

    async def get_meta(self, project_id: str, task_id: str) -> TaskMeta | None:
        row = self._row(project_id, task_id)
        return row.meta if row else None

    def _project_rows(self, project_id: str) -> list[_TaskRow]:
        rows = [r for (p, _), r in self._rows.items() if p == project_id]
        return sorted(rows, key=lambda r: r.seq)

    async def list_tasks(
        self, project_id: str, *, statuses: Collection[Status] | None = None
    ) -> list[Task]:
        wanted = None if statuses is None else {Status(s) for s in statuses}
        return [
            r.task.model_copy(deep=True)
            for r in self._project_rows(project_id)
            if wanted is None or r.task.status in wanted
        ]

    async def list_pending(self, project_id: str) -> list[Task]:
        pending = [r for r in self._project_rows(project_id) if r.task.status is Status.PENDING]
        pending.sort(key=lambda r: (priority_rank(r.task.priority), r.task.created_at, r.seq))
        return [r.task.model_copy(deep=True) for r in pending]

    async def count_by_status(self, project_id: str) -> dict[str, int]:
        counts = {status.value: 0 for status in Status}
        for row in self._project_rows(project_id):
            counts[row.task.status.value] += 1
        return counts

    async def create(
        self,
        project_id: str,
        title: str,
        description: str = "",
        priority: Priority = Priority.MEDIUM,
        approval: Approval = Approval.NONE,
    ) -> Task:
        title = title.strip()
        if not title:
            raise ValueError("Task title must not be empty.")
        highest = 0
        for row in self._project_rows(project_id):
            match = re.fullmatch(r"task-(\d+)", row.task.id)
            if match:
                highest = max(highest, int(match.group(1)))
        now = self._clock()
        task = Task(
            id=f"task-{highest + 1:03d}",
            title=title,
            description=description.strip() or title,
            priority=Priority(priority),
            approval=Approval(approval),
            status=Status.PENDING,
            created_at=now,
            updated_at=now,
        )
        await self.insert_if_absent(project_id, task, source="api")
        return task

    async def set_status(self, project_id: str, task_id: str, status: Status) -> bool:
        row = self._row(project_id, task_id)
        if row is None:
            return False
        row.task.status = Status(status)
        row.task.updated_at = self._clock()
        return True

    async def set_rework_count(self, project_id: str, task_id: str, rework_count: int) -> bool:
        row = self._row(project_id, task_id)
        if row is None:
            return False
        row.task.rework_count = rework_count
        return True

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
        row = self._row(project_id, task_id)
        if row is None or row.task.status is not Status.PENDING:
            return False
        row.task.title = title
        row.task.description = description
        row.task.priority = Priority(priority)
        row.task.approval = Approval(approval)
        row.task.max_reworks = max_reworks
        row.task.updated_at = self._clock()
        row.meta = TaskMeta(row.meta.source, source_hash)
        return True


class InMemoryUsageRepository:
    def __init__(self) -> None:
        self._records: list[UsageRecord] = []

    async def add(self, record: UsageRecord) -> None:
        self._records.append(record)

    async def list_records(self, project_id: str | None = None) -> list[UsageRecord]:
        return [r for r in self._records if project_id is None or r.project_id == project_id]

    async def totals(
        self, project_id: str, task_id: str, *, since: datetime | None = None
    ) -> UsageTotals:
        chosen = [
            r
            for r in self._records
            if r.project_id == project_id
            and r.task_id == task_id
            and (since is None or r.timestamp >= since)
        ]
        return UsageTotals(
            input_tokens=sum(r.input_tokens for r in chosen),
            output_tokens=sum(r.output_tokens for r in chosen),
            cost=round(sum(r.cost for r in chosen), 6),
            call_count=len(chosen),
        )


class InMemoryRunRepository:
    def __init__(self) -> None:
        self._runs: dict[int, RunRecord] = {}
        self._steps: dict[int, list[StepRecord]] = {}
        self._next_id = 0

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
        self._next_id += 1
        run_no = 1 + max(
            (r.run_no for r in self._runs.values()
             if (r.project_id, r.task_id) == (project_id, task_id)),
            default=0,
        )
        run = RunRecord(
            id=self._next_id, project_id=project_id, task_id=task_id, run_no=run_no,
            pipeline_id=pipeline_id, seats=[dict(s) for s in seats], outcome=None, reason=None,
            rework_count=0, started_at=started_at, finished_at=None, cost_usd=0.0,
            input_tokens=0, output_tokens=0, imported=imported,
        )
        self._runs[run.id] = run
        self._steps[run.id] = []
        return self._copy(run)

    @staticmethod
    def _copy(run: RunRecord) -> RunRecord:
        return replace(run, seats=[dict(s) for s in run.seats])

    async def get_run(self, run_id: int) -> RunRecord | None:
        run = self._runs.get(run_id)
        return self._copy(run) if run else None

    async def find_open_run(self, project_id: str, task_id: str) -> RunRecord | None:
        open_runs = [
            r for r in self._runs.values()
            if (r.project_id, r.task_id) == (project_id, task_id) and r.outcome is None
        ]
        return self._copy(max(open_runs, key=lambda r: r.run_no)) if open_runs else None

    async def list_runs(self, project_id: str, task_id: str) -> list[RunRecord]:
        runs = [
            r for r in self._runs.values() if (r.project_id, r.task_id) == (project_id, task_id)
        ]
        return [self._copy(r) for r in sorted(runs, key=lambda r: r.run_no, reverse=True)]

    async def list_project_runs(self, project_id: str) -> list[RunRecord]:
        runs = [r for r in self._runs.values() if r.project_id == project_id]
        return [self._copy(r) for r in sorted(runs, key=lambda r: (r.task_id, -r.run_no))]

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
        steps = self._steps[run_id]  # KeyError for an unknown run, like a foreign-key error
        step = StepRecord(run_id, len(steps) + 1, kind, station, status, note, at)
        steps.append(step)
        return step

    async def list_steps(self, run_id: int) -> list[StepRecord]:
        return list(self._steps.get(run_id, []))

    async def update_totals(
        self, run_id: int, *, cost_usd: float, input_tokens: int, output_tokens: int
    ) -> bool:
        run = self._runs.get(run_id)
        if run is None:
            return False
        run.cost_usd, run.input_tokens, run.output_tokens = cost_usd, input_tokens, output_tokens
        return True

    async def finish_run(
        self,
        run_id: int,
        *,
        outcome: str,
        finished_at: datetime,
        reason: str | None = None,
        rework_count: int = 0,
    ) -> bool:
        run = self._runs.get(run_id)
        if run is None:
            return False
        run.outcome, run.reason = outcome, reason
        run.finished_at, run.rework_count = finished_at, rework_count
        return True

    async def supersede_open_runs(
        self, project_id: str, task_id: str, *, reason: str, finished_at: datetime
    ) -> int:
        count = 0
        for run in self._runs.values():
            if (run.project_id, run.task_id) == (project_id, task_id) and run.outcome is None:
                run.outcome, run.reason, run.finished_at = "failed", reason, finished_at
                count += 1
        return count


class InMemoryProjectRepository:
    """Project rows over the in-memory task and run repositories (so a migration is visible in both)."""

    def __init__(self, tasks: InMemoryTaskRepository, runs: InMemoryRunRepository) -> None:
        self._tasks = tasks
        self._runs = runs
        self._projects: dict[str, dict] = {}

    async def exists(self, project_id: str) -> bool:
        return project_id in self._projects

    async def get_settings(self, project_id: str) -> dict | None:
        settings = self._projects.get(project_id)
        return None if settings is None else deepcopy(settings)

    async def set_llm(self, project_id: str, llm) -> bool:
        if project_id not in self._projects:
            return False
        self._projects[project_id]["llm"] = deepcopy(llm)
        return True

    async def create_from_migration(
        self, project_id: str, *, settings: dict, tasks: Sequence[MigratedTask]
    ) -> bool:
        if project_id in self._projects:
            return False
        # All or nothing: remember the state, restore it if anything fails.
        saved = (
            dict(self._tasks._rows), self._tasks._seq,
            dict(self._runs._runs), {k: list(v) for k, v in self._runs._steps.items()}, self._runs._next_id,
        )
        try:
            for item in tasks:
                if item.source not in TASK_SOURCES:  # what the database CHECK constraint would say
                    raise ValueError(f"invalid task source {item.source!r}")
                inserted = await self._tasks.insert_if_absent(
                    project_id, item.task, source=item.source, source_hash=item.source_hash
                )
                # A row already in the store wins (insert_if_absent kept it): it keeps its status
                # and run history and gets no imported run (user ruling 2026-11-21). Only tasks
                # inserted here get one; open_run numbers it after any existing runs of the id.
                if item.run is not None and inserted:
                    run = await self._runs.open_run(
                        project_id, item.task.id, started_at=item.run.started_at, imported=True
                    )
                    await self._runs.finish_run(
                        run.id, outcome=item.run.outcome, finished_at=item.run.finished_at,
                        reason=item.run.reason, rework_count=item.run.rework_count,
                    )
            self._projects[project_id] = {**settings}
        except BaseException:
            (self._tasks._rows, self._tasks._seq, self._runs._runs, self._runs._steps,
             self._runs._next_id) = saved
            raise
        return True


class InMemoryLineupRepository:
    """Agents and pipelines; patches project settings through an optional project repository."""

    def __init__(self, projects: InMemoryProjectRepository | None = None, *, clock: Clock = datetime.now) -> None:
        self._projects = projects
        self._clock = clock
        self._agents: dict[str, dict[str, AgentRecord]] = {}
        self._pipelines: dict[str, dict[str, PipelineRecord]] = {}

    @staticmethod
    def _check(record: AgentRecord) -> None:
        if record.kind not in AGENT_KINDS:  # what the database CHECK constraint would say
            raise ValueError(f"invalid agent kind {record.kind!r}")

    def _stamped(self, record: AgentRecord) -> AgentRecord:
        now = self._clock()
        return replace(record, llm=deepcopy(record.llm), created_at=record.created_at or now, updated_at=now)

    async def has_agents(self, project_id: str) -> bool:
        return bool(self._agents.get(project_id))

    async def list_agents(self, project_id: str) -> list[AgentRecord]:
        records = list(self._agents.get(project_id, {}).values())
        ordered = sorted(enumerate(records), key=lambda item: agent_sort_key(item[1], item[0]))
        return [replace(r, llm=deepcopy(r.llm)) for _, r in ordered]

    async def get_agent(self, project_id: str, agent_id: str) -> AgentRecord | None:
        record = self._agents.get(project_id, {}).get(agent_id)
        return None if record is None else replace(record, llm=deepcopy(record.llm))

    async def insert_agent(self, project_id: str, record: AgentRecord) -> bool:
        self._check(record)
        agents = self._agents.setdefault(project_id, {})
        if record.id in agents:
            return False
        agents[record.id] = self._stamped(record)
        return True

    async def update_agent(self, project_id: str, agent_id: str, **fields) -> bool:
        unknown = set(fields) - AGENT_UPDATABLE_FIELDS
        if unknown:
            raise ValueError(f"agent fields can not be updated: {sorted(unknown)}")
        record = self._agents.get(project_id, {}).get(agent_id)
        if record is None:
            return False
        updated = replace(record, **{k: deepcopy(v) for k, v in fields.items()}, updated_at=self._clock())
        self._check(updated)
        self._agents[project_id][agent_id] = updated
        return True

    async def delete_agent(self, project_id: str, agent_id: str) -> bool:
        return self._agents.get(project_id, {}).pop(agent_id, None) is not None

    async def list_pipelines(self, project_id: str) -> list[PipelineRecord]:
        records = self._pipelines.get(project_id, {}).values()
        return sorted(records, key=lambda p: (not p.is_default, p.id))

    async def get_pipeline(self, project_id: str, pipeline_id: str) -> PipelineRecord | None:
        return self._pipelines.get(project_id, {}).get(pipeline_id)

    def _pipeline(self, record: PipelineRecord) -> PipelineRecord:
        pending = None if record.pending_seats is None else tuple(record.pending_seats)
        return replace(record, seats=tuple(record.seats), pending_seats=pending, updated_at=self._clock())

    async def save_pipeline(self, project_id: str, record: PipelineRecord) -> None:
        self._pipelines.setdefault(project_id, {})[record.id] = self._pipeline(record)

    async def set_pending_seats(self, project_id: str, pipeline_id: str, seats) -> bool:
        record = self._pipelines.get(project_id, {}).get(pipeline_id)
        if record is None:
            return False
        pending = None if seats is None else tuple(seats)
        self._pipelines[project_id][pipeline_id] = replace(record, pending_seats=pending, updated_at=self._clock())
        return True

    def _settings(self, project_id: str) -> dict | None:
        return None if self._projects is None else self._projects._projects.get(project_id)

    async def list_leaving(self, project_id: str) -> tuple[str, ...]:
        settings = self._settings(project_id) or {}
        return tuple(settings.get("leaving_agents", ()))

    async def set_leaving(self, project_id: str, agent_ids) -> bool:
        settings = self._settings(project_id)
        if settings is None:
            return False
        if agent_ids:
            settings["leaving_agents"] = list(agent_ids)
        else:
            settings.pop("leaving_agents", None)
        return True

    def _write(self, project_id: str, agents, pipelines) -> None:
        for record in agents:
            self._check(record)
        self._agents[project_id] = {r.id: self._stamped(r) for r in agents}
        self._pipelines[project_id] = {p.id: self._pipeline(p) for p in pipelines}

    async def import_initial(self, project_id: str, agents, pipelines, *, migration_notes=()) -> bool:
        if self._agents.get(project_id):
            return False
        self._write(project_id, agents, pipelines)
        settings = self._settings(project_id)
        if settings is not None and migration_notes:
            settings["migration_notes"] = [*settings.get("migration_notes", []), *deepcopy(list(migration_notes))]
        return True

    async def replace_all(self, project_id: str, agents, pipelines, *, project_llm=None) -> None:
        saved = (dict(self._agents.get(project_id, {})), dict(self._pipelines.get(project_id, {})))
        try:
            self._write(project_id, agents, pipelines)
        except BaseException:
            self._agents[project_id], self._pipelines[project_id] = saved
            raise
        settings = self._settings(project_id)
        if settings is not None and project_llm is not None:
            settings["llm"] = deepcopy(project_llm)
