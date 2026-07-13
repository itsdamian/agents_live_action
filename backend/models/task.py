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
    DONE = "done"
    FAILED = "failed"


class Task(BaseModel):
    """A task definition parsed from tasks.yaml / TODO.md."""

    id: str
    title: str
    description: str
    priority: Priority
    status: Status
    rework_count: int = 0
    max_reworks: int = 3
    created_at: datetime
    updated_at: datetime
    # Per-station outputs, populated as the task flows through the pipeline.
    pm_output: str | None = Field(default=None)
    swe_output: str | None = Field(default=None)
    qa_output: str | None = Field(default=None)
