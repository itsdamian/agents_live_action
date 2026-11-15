"""LangGraph engine for the pipeline (``PIPELINE_ENGINE=langgraph``).

Runs one task through the same PM -> SWE -> QA flow as the legacy loop in
``core/pipeline.py``, but expresses the flow control as a compiled LangGraph
``StateGraph``. Side effects (events, tasks.yaml writes, deliverables, usage)
stay in the orchestrator's shared helpers; the graph only decides what runs
next. See ``specs/langgraph-pipeline/plan.md``.

This is the only production module that imports ``langgraph``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from models.task import Task

if TYPE_CHECKING:
    from core.pipeline import PipelineOrchestrator

logger = logging.getLogger(__name__)


class PipelineGraphRunner:
    """Drives one task through the LangGraph pipeline graph."""

    def __init__(self, orch: PipelineOrchestrator) -> None:
        self._orch = orch

    async def run(self, task: Task) -> Task:
        raise NotImplementedError(
            "langgraph engine: see specs/langgraph-pipeline/tasks.md Task 10"
        )
