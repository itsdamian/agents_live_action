"""SQLAlchemy 2 declarative models for the HUD's database (plan section 4.1, FROZEN).

Tables of phase 1a: projects, tasks, task_runs, task_run_steps, usage_records, agents,
pipelines. Later phases add imports, staffing_proposals and gate_decisions through new
Alembic revisions. LangGraph's checkpointer creates and migrates its own tables
(``AsyncPostgresSaver.setup()``); they are not modelled here and Alembic ignores them
(see ``db.migration_filters``).

Conventions
-----------
* Timestamps are timezone-naive local times, like every ``datetime`` in the app
  (``datetime.now()``), so values round-trip unchanged.
* Money is ``numeric(12, 6)`` in the database; the repositories convert to ``float``.
* Enumerated values are ``text`` plus a named CHECK constraint (easier to migrate than a
  PostgreSQL enum type).
* There are deliberately no foreign keys to ``projects``: a project row marks "this
  project has been migrated/imported" (plan D3 (3)), so inserting a task must never
  create it implicitly. ``task_run_steps.run_id`` does have one.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Stable constraint names: Alembic autogenerate compares them, and downgrades can drop them by name.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

STATUSES = ("pending", "in_progress", "awaiting_approval", "done", "failed")
PRIORITIES = ("critical", "high", "medium", "low")
APPROVALS = ("none", "after_swe", "after_qa")
TASK_SOURCES = ("yaml", "todo", "api")
RUN_OUTCOMES = ("done", "failed")
STEP_KINDS = ("station", "rework", "gate")
AGENT_KINDS = ("make", "check")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    # Where an imported project came from (phase 3); null for projects created by dropping a folder.
    source: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # {"llm": "mock" | {...}, "migration_notes": [...]}
    settings: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))


class TaskRow(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint(_in("status", STATUSES), name="status_valid"),
        CheckConstraint(_in("priority", PRIORITIES), name="priority_valid"),
        CheckConstraint(_in("approval", APPROVALS), name="approval_valid"),
        CheckConstraint(_in("source", TASK_SOURCES), name="source_valid"),
        Index("ix_tasks_project_status_priority_created", "project_id", "status", "priority", "created_at"),
    )

    project_id: Mapped[str] = mapped_column(Text, primary_key=True)
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    approval: Mapped[str] = mapped_column(Text, server_default="none")
    pipeline_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    rework_count: Mapped[int] = mapped_column(Integer, server_default="0")
    max_reworks: Mapped[int] = mapped_column(Integer, server_default="3")
    source: Mapped[str] = mapped_column(Text)
    source_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class TaskRun(Base):
    """One execution of a task (a "sticky note" in the history, plan D20)."""

    __tablename__ = "task_runs"
    __table_args__ = (
        UniqueConstraint("project_id", "task_id", "run_no"),
        CheckConstraint("outcome IS NULL OR " + _in("outcome", RUN_OUTCOMES), name="outcome_valid"),
        Index("ix_task_runs_project_task", "project_id", "task_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(Text)
    task_id: Mapped[str] = mapped_column(Text)
    run_no: Mapped[int] = mapped_column(Integer)
    pipeline_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Snapshot of the lineup when the run started: [{id, display_name, look, kind}]
    seats: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    outcome: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    rework_count: Mapped[int] = mapped_column(Integer, server_default="0")
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), server_default="0")
    input_tokens: Mapped[int] = mapped_column(BigInteger, server_default="0")
    output_tokens: Mapped[int] = mapped_column(BigInteger, server_default="0")
    # True for runs created when an existing tasks.yaml project was migrated (plan D3 (3)).
    imported: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))


class TaskRunStep(Base):
    __tablename__ = "task_run_steps"
    __table_args__ = (
        UniqueConstraint("run_id", "seq"),
        CheckConstraint(_in("kind", STEP_KINDS), name="kind_valid"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("task_runs.id", ondelete="CASCADE")
    )
    seq: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(Text)
    station: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime)


class UsageRecordRow(Base):
    """One LLM call; the source of truth for spend (plan D20)."""

    __tablename__ = "usage_records"
    __table_args__ = (Index("ix_usage_records_project_task_created", "project_id", "task_id", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(Text)
    task_id: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(BigInteger)
    output_tokens: Mapped[int] = mapped_column(BigInteger)
    cost: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    created_at: Mapped[datetime] = mapped_column(DateTime)


class AgentRow(Base):
    __tablename__ = "agents"
    __table_args__ = (CheckConstraint(_in("kind", AGENT_KINDS), name="kind_valid"),)

    project_id: Mapped[str] = mapped_column(Text, primary_key=True)
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    builtin: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    kind: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text)
    duty: Mapped[str] = mapped_column(Text)
    look: Mapped[str] = mapped_column(Text)
    system_prompt: Mapped[str] = mapped_column(Text)
    # null = follow the project default; "mock" or {"provider", "model"} (plan D21). No api_key_env.
    llm: Mapped[dict | str | None] = mapped_column(JSONB, nullable=True)
    max_retries: Mapped[int] = mapped_column(Integer, server_default="3")
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class PipelineRow(Base):
    __tablename__ = "pipelines"
    __table_args__ = (
        # At most one default pipeline per project.
        Index(
            "uq_pipelines_project_default",
            "project_id",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )

    project_id: Mapped[str] = mapped_column(Text, primary_key=True)
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    is_default: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    seats: Mapped[list[str]] = mapped_column(ARRAY(Text))
    pending_seats: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    route: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime)
