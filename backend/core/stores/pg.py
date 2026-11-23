"""PostgreSQL repositories (plan D2, D3, D20): SQLAlchemy 2 async on psycopg 3.

They satisfy the same contract tests as the in-memory versions (tests/stores/contract_*.py,
run against a real database in tests/integration/test_stores_pg.py).

Each public method runs in one short transaction (``sessions.begin()``). Operations that must
not interleave (next task id, next run number, next step number) take a transaction-scoped
advisory lock keyed by what they number, so concurrent callers queue instead of colliding.
Timestamps are naive local times (``datetime.now()``), matching the rest of the app.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import case, delete, func, null, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from core.stores import (
    AGENT_UPDATABLE_FIELDS,
    AgentRecord,
    MigratedTask,
    PipelineRecord,
    RunRecord,
    StepRecord,
    TaskMeta,
    UsageTotals,
    agent_sort_key,
    priority_rank,
)
from core.usage_tracker import UsageRecord
from db.models import AgentRow, PipelineRow, Project, TaskRow, TaskRun, TaskRunStep, UsageRecordRow
from models.task import Approval, Priority, Status, Task

Clock = Callable[[], datetime]

_PRIORITY_RANK = case({p.value: priority_rank(p) for p in Priority}, value=TaskRow.priority)


def _task_from_row(row: TaskRow) -> Task:
    return Task(
        id=row.id,
        title=row.title,
        description=row.description,
        priority=Priority(row.priority),
        status=Status(row.status),
        approval=Approval(row.approval),
        rework_count=row.rework_count,
        max_reworks=row.max_reworks,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _lock(session, key: str) -> None:
    """Serialize concurrent callers on ``key`` until the current transaction ends."""

    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": key})


class PgTaskRepository:
    def __init__(self, sessions: async_sessionmaker, *, clock: Clock = datetime.now) -> None:
        self._sessions = sessions
        self._clock = clock

    async def insert_if_absent(
        self, project_id: str, task: Task, *, source: str, source_hash: str | None = None
    ) -> bool:
        stmt = (
            pg_insert(TaskRow)
            .values(
                project_id=project_id,
                id=task.id,
                title=task.title,
                description=task.description,
                priority=Priority(task.priority).value,
                status=Status(task.status).value,
                approval=Approval(task.approval).value,
                rework_count=task.rework_count,
                max_reworks=task.max_reworks,
                source=source,
                source_hash=source_hash,
                created_at=task.created_at,
                updated_at=task.updated_at,
            )
            .on_conflict_do_nothing(index_elements=["project_id", "id"])
            .returning(TaskRow.id)
        )
        async with self._sessions.begin() as session:
            return (await session.execute(stmt)).first() is not None

    async def _row(self, session, project_id: str, task_id: str) -> TaskRow | None:
        return await session.get(TaskRow, (project_id, task_id))

    async def get(self, project_id: str, task_id: str) -> Task | None:
        async with self._sessions() as session:
            row = await self._row(session, project_id, task_id)
            return _task_from_row(row) if row else None

    async def get_meta(self, project_id: str, task_id: str) -> TaskMeta | None:
        async with self._sessions() as session:
            row = await self._row(session, project_id, task_id)
            return TaskMeta(row.source, row.source_hash) if row else None

    async def list_tasks(
        self, project_id: str, *, statuses: Collection[Status] | None = None
    ) -> list[Task]:
        stmt = select(TaskRow).where(TaskRow.project_id == project_id)
        if statuses is not None:
            stmt = stmt.where(TaskRow.status.in_([Status(s).value for s in statuses]))
        stmt = stmt.order_by(TaskRow.created_at, TaskRow.id)
        async with self._sessions() as session:
            return [_task_from_row(r) for r in (await session.scalars(stmt)).all()]

    async def list_pending(self, project_id: str) -> list[Task]:
        stmt = (
            select(TaskRow)
            .where(TaskRow.project_id == project_id, TaskRow.status == Status.PENDING.value)
            .order_by(_PRIORITY_RANK, TaskRow.created_at, TaskRow.id)
        )
        async with self._sessions() as session:
            return [_task_from_row(r) for r in (await session.scalars(stmt)).all()]

    async def count_by_status(self, project_id: str) -> dict[str, int]:
        counts = {status.value: 0 for status in Status}
        stmt = (
            select(TaskRow.status, func.count())
            .where(TaskRow.project_id == project_id)
            .group_by(TaskRow.status)
        )
        async with self._sessions() as session:
            for status, number in (await session.execute(stmt)).all():
                counts[status] = number
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
        now = self._clock()
        async with self._sessions.begin() as session:
            await _lock(session, f"tasks:{project_id}")  # one id allocator per project
            highest = (
                await session.execute(
                    text(
                        "SELECT coalesce(max(substring(id from '^task-([0-9]+)$')::bigint), 0) "
                        "FROM tasks WHERE project_id = :p AND id ~ '^task-[0-9]+$'"
                    ),
                    {"p": project_id},
                )
            ).scalar_one()
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
            session.add(
                TaskRow(
                    project_id=project_id,
                    id=task.id,
                    title=task.title,
                    description=task.description,
                    priority=task.priority.value,
                    status=task.status.value,
                    approval=task.approval.value,
                    rework_count=0,
                    max_reworks=task.max_reworks,
                    source="api",
                    source_hash=None,
                    created_at=now,
                    updated_at=now,
                )
            )
        return task

    async def _update(self, project_id: str, task_id: str, values: dict, *where) -> bool:
        stmt = (
            update(TaskRow)
            .where(TaskRow.project_id == project_id, TaskRow.id == task_id, *where)
            .values(**values)
        )
        async with self._sessions.begin() as session:
            return (await session.execute(stmt)).rowcount > 0

    async def set_status(self, project_id: str, task_id: str, status: Status) -> bool:
        return await self._update(
            project_id, task_id, {"status": Status(status).value, "updated_at": self._clock()}
        )

    async def set_rework_count(self, project_id: str, task_id: str, rework_count: int) -> bool:
        return await self._update(project_id, task_id, {"rework_count": rework_count})

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
        return await self._update(
            project_id,
            task_id,
            {
                "title": title,
                "description": description,
                "priority": Priority(priority).value,
                "approval": Approval(approval).value,
                "max_reworks": max_reworks,
                "source_hash": source_hash,
                "updated_at": self._clock(),
            },
            TaskRow.status == Status.PENDING.value,  # only while still in the to-do tray
        )


class PgUsageRepository:
    def __init__(self, sessions: async_sessionmaker) -> None:
        self._sessions = sessions

    async def add(self, record: UsageRecord) -> None:
        async with self._sessions.begin() as session:
            session.add(
                UsageRecordRow(
                    project_id=record.project_id,
                    task_id=record.task_id,
                    provider=record.provider,
                    model=record.model,
                    input_tokens=record.input_tokens,
                    output_tokens=record.output_tokens,
                    cost=Decimal(str(record.cost)),
                    created_at=record.timestamp,
                )
            )

    async def list_records(self, project_id: str | None = None) -> list[UsageRecord]:
        stmt = select(UsageRecordRow).order_by(UsageRecordRow.id)
        if project_id is not None:
            stmt = stmt.where(UsageRecordRow.project_id == project_id)
        async with self._sessions() as session:
            return [
                UsageRecord(
                    project_id=r.project_id,
                    task_id=r.task_id,
                    provider=r.provider,
                    model=r.model,
                    input_tokens=r.input_tokens,
                    output_tokens=r.output_tokens,
                    cost=float(r.cost),
                    timestamp=r.created_at,
                )
                for r in (await session.scalars(stmt)).all()
            ]

    async def totals(
        self, project_id: str, task_id: str, *, since: datetime | None = None
    ) -> UsageTotals:
        stmt = select(
            func.coalesce(func.sum(UsageRecordRow.input_tokens), 0),
            func.coalesce(func.sum(UsageRecordRow.output_tokens), 0),
            func.coalesce(func.sum(UsageRecordRow.cost), 0),
            func.count(),
        ).where(UsageRecordRow.project_id == project_id, UsageRecordRow.task_id == task_id)
        if since is not None:
            stmt = stmt.where(UsageRecordRow.created_at >= since)
        async with self._sessions() as session:
            input_tokens, output_tokens, cost, count = (await session.execute(stmt)).one()
        return UsageTotals(int(input_tokens), int(output_tokens), round(float(cost), 6), int(count))


def _run_from_row(row: TaskRun) -> RunRecord:
    return RunRecord(
        id=row.id,
        project_id=row.project_id,
        task_id=row.task_id,
        run_no=row.run_no,
        pipeline_id=row.pipeline_id,
        seats=[dict(s) for s in row.seats],
        outcome=row.outcome,
        reason=row.reason,
        rework_count=row.rework_count,
        started_at=row.started_at,
        finished_at=row.finished_at,
        cost_usd=float(row.cost_usd),
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        imported=row.imported,
    )


class PgRunRepository:
    def __init__(self, sessions: async_sessionmaker) -> None:
        self._sessions = sessions

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
        async with self._sessions.begin() as session:
            await _lock(session, f"runs:{project_id}:{task_id}")
            highest = (
                await session.execute(
                    select(func.coalesce(func.max(TaskRun.run_no), 0)).where(
                        TaskRun.project_id == project_id, TaskRun.task_id == task_id
                    )
                )
            ).scalar_one()
            row = TaskRun(
                project_id=project_id,
                task_id=task_id,
                run_no=highest + 1,
                pipeline_id=pipeline_id,
                seats=[dict(s) for s in seats],
                started_at=started_at,
                rework_count=0,
                cost_usd=Decimal("0"),
                input_tokens=0,
                output_tokens=0,
                imported=imported,
            )
            session.add(row)
            await session.flush()
            return _run_from_row(row)

    async def get_run(self, run_id: int) -> RunRecord | None:
        async with self._sessions() as session:
            row = await session.get(TaskRun, run_id)
            return _run_from_row(row) if row else None

    async def find_open_run(self, project_id: str, task_id: str) -> RunRecord | None:
        stmt = (
            select(TaskRun)
            .where(
                TaskRun.project_id == project_id,
                TaskRun.task_id == task_id,
                TaskRun.outcome.is_(None),
            )
            .order_by(TaskRun.run_no.desc())
            .limit(1)
        )
        async with self._sessions() as session:
            row = (await session.scalars(stmt)).first()
            return _run_from_row(row) if row else None

    async def list_runs(self, project_id: str, task_id: str) -> list[RunRecord]:
        stmt = (
            select(TaskRun)
            .where(TaskRun.project_id == project_id, TaskRun.task_id == task_id)
            .order_by(TaskRun.run_no.desc())
        )
        async with self._sessions() as session:
            return [_run_from_row(r) for r in (await session.scalars(stmt)).all()]

    async def list_project_runs(self, project_id: str) -> list[RunRecord]:
        stmt = (
            select(TaskRun)
            .where(TaskRun.project_id == project_id)
            .order_by(TaskRun.task_id, TaskRun.run_no.desc())
        )
        async with self._sessions() as session:
            return [_run_from_row(r) for r in (await session.scalars(stmt)).all()]

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
        async with self._sessions.begin() as session:
            await _lock(session, f"steps:{run_id}")
            highest = (
                await session.execute(
                    select(func.coalesce(func.max(TaskRunStep.seq), 0)).where(
                        TaskRunStep.run_id == run_id
                    )
                )
            ).scalar_one()
            # An unknown run id fails here on the foreign key, like the in-memory KeyError.
            row = TaskRunStep(
                run_id=run_id, seq=highest + 1, kind=kind, station=station, status=status,
                note=note, at=at,
            )
            session.add(row)
            await session.flush()
            return StepRecord(run_id, row.seq, kind, station, status, note, at)

    async def list_steps(self, run_id: int) -> list[StepRecord]:
        stmt = select(TaskRunStep).where(TaskRunStep.run_id == run_id).order_by(TaskRunStep.seq)
        async with self._sessions() as session:
            return [
                StepRecord(r.run_id, r.seq, r.kind, r.station, r.status, r.note, r.at)
                for r in (await session.scalars(stmt)).all()
            ]

    async def _update_run(self, run_id: int, values: dict) -> bool:
        async with self._sessions.begin() as session:
            result = await session.execute(update(TaskRun).where(TaskRun.id == run_id).values(**values))
            return result.rowcount > 0

    async def update_totals(
        self, run_id: int, *, cost_usd: float, input_tokens: int, output_tokens: int
    ) -> bool:
        return await self._update_run(
            run_id,
            {"cost_usd": Decimal(str(cost_usd)), "input_tokens": input_tokens, "output_tokens": output_tokens},
        )

    async def finish_run(
        self,
        run_id: int,
        *,
        outcome: str,
        finished_at: datetime,
        reason: str | None = None,
        rework_count: int = 0,
    ) -> bool:
        return await self._update_run(
            run_id,
            {"outcome": outcome, "reason": reason, "finished_at": finished_at, "rework_count": rework_count},
        )

    async def supersede_open_runs(
        self, project_id: str, task_id: str, *, reason: str, finished_at: datetime
    ) -> int:
        stmt = (
            update(TaskRun)
            .where(
                TaskRun.project_id == project_id,
                TaskRun.task_id == task_id,
                TaskRun.outcome.is_(None),
            )
            .values(outcome="failed", reason=reason, finished_at=finished_at)
        )
        async with self._sessions.begin() as session:
            return (await session.execute(stmt)).rowcount


class PgProjectRepository:
    def __init__(self, sessions: async_sessionmaker, *, clock: Clock = datetime.now) -> None:
        self._sessions = sessions
        self._clock = clock

    async def exists(self, project_id: str) -> bool:
        async with self._sessions() as session:
            return await session.get(Project, project_id) is not None

    async def get_settings(self, project_id: str) -> dict | None:
        async with self._sessions() as session:
            row = await session.get(Project, project_id)
            return None if row is None else dict(row.settings)

    async def set_llm(self, project_id: str, llm) -> bool:
        async with self._sessions.begin() as session:
            # Row lock: a concurrent settings write (e.g. migration notes) can not be lost.
            row = await session.get(Project, project_id, with_for_update=True)
            if row is None:
                return False
            row.settings = {**(row.settings or {}), "llm": deepcopy(llm)}
            return True

    async def create_from_migration(
        self, project_id: str, *, settings: dict, tasks: Sequence[MigratedTask]
    ) -> bool:
        async with self._sessions.begin() as session:  # one transaction: any error rolls it all back
            created = (
                await session.execute(
                    pg_insert(Project)
                    .values(id=project_id, created_at=self._clock(), source=None, settings=settings)
                    .on_conflict_do_nothing(index_elements=["id"])
                    .returning(Project.id)
                )
            ).first()
            if created is None:
                return False
            for item in tasks:
                task = item.task
                inserted = (
                    await session.execute(
                        pg_insert(TaskRow)
                        .values(
                            project_id=project_id, id=task.id, title=task.title,
                            description=task.description, priority=Priority(task.priority).value,
                            status=Status(task.status).value, approval=Approval(task.approval).value,
                            rework_count=task.rework_count, max_reworks=task.max_reworks,
                            source=item.source, source_hash=item.source_hash,
                            created_at=task.created_at, updated_at=task.updated_at,
                        )
                        .on_conflict_do_nothing(index_elements=["project_id", "id"])
                        .returning(TaskRow.id)
                    )
                ).first()
                # A row already in the database wins (the insert did nothing): it keeps its status
                # and run history and gets no imported run (user ruling 2026-11-21). Only tasks
                # inserted here get one, numbered after any runs the task id already has.
                if item.run is None or inserted is None:
                    continue
                highest = (
                    await session.execute(
                        select(func.coalesce(func.max(TaskRun.run_no), 0)).where(
                            TaskRun.project_id == project_id, TaskRun.task_id == task.id
                        )
                    )
                ).scalar_one()
                session.add(
                    TaskRun(
                        project_id=project_id, task_id=task.id, run_no=highest + 1, seats=[],
                        outcome=item.run.outcome, reason=item.run.reason,
                        rework_count=item.run.rework_count, started_at=item.run.started_at,
                        finished_at=item.run.finished_at, cost_usd=Decimal("0"),
                        input_tokens=0, output_tokens=0, imported=True,
                    )
                )
            return True


def _agent_from_row(row: AgentRow) -> AgentRecord:
    return AgentRecord(
        id=row.id, builtin=row.builtin, kind=row.kind, display_name=row.display_name, duty=row.duty,
        look=row.look, system_prompt=row.system_prompt, llm=deepcopy(row.llm), max_retries=row.max_retries,
        created_at=row.created_at, updated_at=row.updated_at,
    )


def _pipeline_from_row(row: PipelineRow) -> PipelineRecord:
    pending = None if row.pending_seats is None else tuple(row.pending_seats)
    return PipelineRecord(id=row.id, name=row.name, is_default=row.is_default, seats=tuple(row.seats),
                          pending_seats=pending, updated_at=row.updated_at)


class PgLineupRepository:
    """``agents`` and ``pipelines`` rows (plan D4). Whole-lineup writes are one transaction each."""

    def __init__(self, sessions: async_sessionmaker, *, clock: Clock = datetime.now) -> None:
        self._sessions = sessions
        self._clock = clock

    async def has_agents(self, project_id: str) -> bool:
        stmt = select(AgentRow.id).where(AgentRow.project_id == project_id).limit(1)
        async with self._sessions() as session:
            return (await session.execute(stmt)).first() is not None

    async def list_agents(self, project_id: str) -> list[AgentRecord]:
        stmt = select(AgentRow).where(AgentRow.project_id == project_id).order_by(AgentRow.created_at, AgentRow.id)
        async with self._sessions() as session:
            records = [_agent_from_row(r) for r in (await session.scalars(stmt)).all()]
        ordered = sorted(enumerate(records), key=lambda item: agent_sort_key(item[1], item[0]))
        return [r for _, r in ordered]

    async def get_agent(self, project_id: str, agent_id: str) -> AgentRecord | None:
        async with self._sessions() as session:
            row = await session.get(AgentRow, (project_id, agent_id))
            return None if row is None else _agent_from_row(row)

    def _agent_values(self, project_id: str, record: AgentRecord, now: datetime) -> dict:
        return dict(
            project_id=project_id, id=record.id, builtin=record.builtin, kind=record.kind,
            display_name=record.display_name, duty=record.duty, look=record.look,
            system_prompt=record.system_prompt, max_retries=record.max_retries,
            # SQL NULL (not the JSON value null) means "follow the project default".
            llm=null() if record.llm is None else deepcopy(record.llm),
            created_at=record.created_at or now, updated_at=now,
        )

    async def insert_agent(self, project_id: str, record: AgentRecord) -> bool:
        stmt = (
            pg_insert(AgentRow)
            .values(**self._agent_values(project_id, record, self._clock()))
            .on_conflict_do_nothing(index_elements=["project_id", "id"])
            .returning(AgentRow.id)
        )
        async with self._sessions.begin() as session:
            return (await session.execute(stmt)).first() is not None

    async def update_agent(self, project_id: str, agent_id: str, **fields) -> bool:
        unknown = set(fields) - AGENT_UPDATABLE_FIELDS
        if unknown:
            raise ValueError(f"agent fields can not be updated: {sorted(unknown)}")
        values = {k: deepcopy(v) for k, v in fields.items()}
        if "llm" in values and values["llm"] is None:
            values["llm"] = null()  # SQL NULL, not the JSON value null
        stmt = (
            update(AgentRow)
            .where(AgentRow.project_id == project_id, AgentRow.id == agent_id)
            .values(**values, updated_at=self._clock())
            .returning(AgentRow.id)
        )
        async with self._sessions.begin() as session:
            return (await session.execute(stmt)).first() is not None

    async def delete_agent(self, project_id: str, agent_id: str) -> bool:
        stmt = delete(AgentRow).where(AgentRow.project_id == project_id, AgentRow.id == agent_id).returning(AgentRow.id)
        async with self._sessions.begin() as session:
            return (await session.execute(stmt)).first() is not None

    async def list_pipelines(self, project_id: str) -> list[PipelineRecord]:
        stmt = (
            select(PipelineRow)
            .where(PipelineRow.project_id == project_id)
            .order_by(PipelineRow.is_default.desc(), PipelineRow.id)
        )
        async with self._sessions() as session:
            return [_pipeline_from_row(r) for r in (await session.scalars(stmt)).all()]

    async def get_pipeline(self, project_id: str, pipeline_id: str) -> PipelineRecord | None:
        async with self._sessions() as session:
            row = await session.get(PipelineRow, (project_id, pipeline_id))
            return None if row is None else _pipeline_from_row(row)

    def _pipeline_values(self, project_id: str, record: PipelineRecord, now: datetime) -> dict:
        pending = None if record.pending_seats is None else list(record.pending_seats)
        return dict(project_id=project_id, id=record.id, name=record.name, is_default=record.is_default,
                    seats=list(record.seats), pending_seats=pending, route=None, updated_at=now)

    async def save_pipeline(self, project_id: str, record: PipelineRecord) -> None:
        values = self._pipeline_values(project_id, record, self._clock())
        stmt = pg_insert(PipelineRow).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["project_id", "id"],
            set_={k: stmt.excluded[k] for k in ("name", "is_default", "seats", "pending_seats", "updated_at")},
        )
        async with self._sessions.begin() as session:
            await session.execute(stmt)

    async def set_pending_seats(self, project_id: str, pipeline_id: str, seats) -> bool:
        stmt = (
            update(PipelineRow)
            .where(PipelineRow.project_id == project_id, PipelineRow.id == pipeline_id)
            .values(pending_seats=None if seats is None else list(seats), updated_at=self._clock())
            .returning(PipelineRow.id)
        )
        async with self._sessions.begin() as session:
            return (await session.execute(stmt)).first() is not None

    async def _write(self, session, project_id: str, agents, pipelines) -> None:
        now = self._clock()
        # Agents written together get creation times one microsecond apart, so "creation order"
        # stays the order they were given in.
        for i, record in enumerate(agents):
            values = self._agent_values(project_id, record, now)
            values["created_at"] = record.created_at or now + timedelta(microseconds=i)
            session.add(AgentRow(**values))
        for record in pipelines:
            session.add(PipelineRow(**self._pipeline_values(project_id, record, now)))
        await session.flush()

    async def _project_for_update(self, session, project_id: str) -> Project | None:
        return (
            await session.execute(select(Project).where(Project.id == project_id).with_for_update())
        ).scalar_one_or_none()

    async def import_initial(self, project_id: str, agents, pipelines, *, migration_notes=()) -> bool:
        async with self._sessions.begin() as session:
            await _lock(session, f"lineup:{project_id}")
            exists = (
                await session.execute(select(AgentRow.id).where(AgentRow.project_id == project_id).limit(1))
            ).first()
            if exists is not None:
                return False
            await self._write(session, project_id, agents, pipelines)
            if migration_notes:
                project = await self._project_for_update(session, project_id)
                if project is not None:
                    settings = dict(project.settings or {})
                    settings["migration_notes"] = [*settings.get("migration_notes", []), *deepcopy(list(migration_notes))]
                    project.settings = settings
            return True

    async def replace_all(self, project_id: str, agents, pipelines, *, project_llm=None) -> None:
        async with self._sessions.begin() as session:  # one transaction: any error rolls it all back
            await _lock(session, f"lineup:{project_id}")
            await session.execute(delete(AgentRow).where(AgentRow.project_id == project_id))
            await session.execute(delete(PipelineRow).where(PipelineRow.project_id == project_id))
            await self._write(session, project_id, agents, pipelines)
            if project_llm is not None:
                project = await self._project_for_update(session, project_id)
                if project is not None:
                    project.settings = {**(project.settings or {}), "llm": deepcopy(project_llm)}
