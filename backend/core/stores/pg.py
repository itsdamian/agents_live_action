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
from datetime import datetime
from decimal import Decimal

from sqlalchemy import case, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from core.stores import MigratedTask, RunRecord, StepRecord, TaskMeta, UsageTotals, priority_rank
from core.usage_tracker import UsageRecord
from db.models import Project, TaskRow, TaskRun, TaskRunStep, UsageRecordRow
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
                )
                if item.run is None:
                    continue
                # Rows already in the database win (the insert above did nothing for them): a task
                # still pending there gets no imported run, and numbering continues after its runs.
                stored_status = (
                    await session.execute(
                        select(TaskRow.status).where(TaskRow.project_id == project_id, TaskRow.id == task.id)
                    )
                ).scalar_one()
                if stored_status == Status.PENDING.value:
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
