"""Task data model.

Represents a single unit of work that flows through the
PM_agent -> SWE_agent -> QA_agent pipeline.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class Priority(StrEnum):
    """Task priority. Lower ordinal = higher scheduling priority.

    Used by the pipeline's asyncio.PriorityQueue
    (critical=0, high=1, medium=2, low=3).
    """

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Status(StrEnum):
    """Task lifecycle status."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    # Pipeline paused at a human approval gate (see :class:`Approval`); the task
    # waits here until an approve/reject decision arrives (task 17).
    AWAITING_APPROVAL = "awaiting_approval"
    DONE = "done"
    FAILED = "failed"


class Approval(StrEnum):
    """Human-in-the-loop approval gate for a task (task 17).

    Controls whether the pipeline pauses for a human decision before it may
    proceed past a station:

    * ``none`` — no gate; the task flows straight through (default, preserving
      the pre-approval behaviour for tasks authored without the field);
    * ``after_swe`` — pause after SWE_agent implements, before QA verifies;
    * ``after_qa`` — pause after QA_agent passes, before the task is marked done.
    """

    NONE = "none"
    AFTER_SWE = "after_swe"
    AFTER_QA = "after_qa"


class Task(BaseModel):
    """A task definition parsed from tasks.yaml / TODO.md."""

    id: str
    title: str
    description: str
    priority: Priority
    status: Status
    # Human-in-the-loop approval gate. Defaults to ``none`` so tasks authored
    # without the field behave exactly as before (task 17).
    approval: Approval = Approval.NONE
    rework_count: int = 0
    max_reworks: int = 3
    created_at: datetime
    updated_at: datetime
    # Per-station outputs, populated as the task flows through the pipeline.
    pm_output: str | None = Field(default=None)
    swe_output: str | None = Field(default=None)
    qa_output: str | None = Field(default=None)
