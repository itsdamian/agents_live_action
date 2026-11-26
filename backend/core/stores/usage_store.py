"""Synchronous usage stores for :class:`~core.usage_tracker.UsageTracker` (Task 12, plan D20).

``UsageTracker.record`` is synchronous and runs on the event loop, and the row must be stored
before it returns, so these stores are synchronous too (the async ``UsageRepository`` stays for
queries from async code). The PostgreSQL store does one small INSERT per LLM call on a
synchronous psycopg connection; an LLM call takes seconds, so the few milliseconds are fine.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import insert, select
from sqlalchemy.engine import Engine

from core.usage_tracker import UsageRecord
from db.models import UsageRecordRow


class InMemoryUsageStore:
    """A list in memory: for unit tests, and it survives "restarts" within a test."""

    def __init__(self) -> None:
        self._records: list[UsageRecord] = []

    def add(self, record: UsageRecord) -> None:
        self._records.append(record)

    def list_records(self, project_id: str | None = None) -> list[UsageRecord]:
        return [r for r in self._records if project_id is None or r.project_id == project_id]


class PgUsageStore:
    """``usage_records`` rows over a synchronous engine (``postgresql+psycopg``)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def add(self, record: UsageRecord) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                insert(UsageRecordRow).values(
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

    def list_records(self, project_id: str | None = None) -> list[UsageRecord]:
        stmt = select(UsageRecordRow).order_by(UsageRecordRow.id)
        if project_id is not None:
            stmt = stmt.where(UsageRecordRow.project_id == project_id)
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).all()
        return [
            UsageRecord(
                project_id=r.project_id, task_id=r.task_id, provider=r.provider, model=r.model,
                input_tokens=r.input_tokens, output_tokens=r.output_tokens, cost=float(r.cost),
                timestamp=r.created_at,
            )
            for r in rows
        ]

    def dispose(self) -> None:
        self._engine.dispose()
