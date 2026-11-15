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
import operator
from typing import TYPE_CHECKING, Annotated, Literal, TypedDict

from langgraph.graph import END

from models.task import Task

if TYPE_CHECKING:
    from core.pipeline import PipelineOrchestrator

logger = logging.getLogger(__name__)


# LangGraph 學習: StateGraph 的 state 是一個 TypedDict；每個 node 回傳「要更新的欄位」，
# LangGraph 把它合併進 state。只放可序列化的流程資料；live Task 放在 graph 外 (plan D3)。
class PipelineState(TypedDict, total=False):
    task_id: str  # key into PipelineGraphRunner._live_tasks (plan D3)
    approval: str  # task.approval.value, for the conditional edges
    pm_output: str | None
    swe_output: str | None
    qa_output: str | None
    qa_passed: bool
    approval_feedback: str | None  # reviewer note on reject; cleared once SWE succeeds
    rework_count: int  # mirror of task.rework_count (the real counter is _enter_rework)
    rework_reason: str | None  # failure reason if this rework exceeds the ceiling
    gate_station: Literal["swe", "qa"] | None
    decision: Literal["approve", "reject"] | None
    decision_note: str
    # LangGraph 學習: reducer — 每個 node 回傳的清單會被「加」到既有值，而不是覆蓋
    deliverables: Annotated[list[str], operator.add]
    error: str | None  # station failure reason; a value routes to ``fail``
    outcome: Literal["done", "failed"] | None


def initial_state(task: Task) -> dict:
    """Graph input for ``task`` (before the ``intake`` node)."""

    return {
        "task_id": task.id,
        "approval": task.approval.value,
        "rework_count": task.rework_count,
        "deliverables": [],
        "approval_feedback": None,
        "qa_output": task.qa_output,
        "pm_output": None,
        "swe_output": None,
        "error": None,
        "outcome": None,
    }


# LangGraph 學習: 條件邊就是一個讀 state、回傳下一個 node 名稱的普通函式
def route_after_pm(state: PipelineState) -> str:
    if state.get("error"):
        return "fail"
    return "swe"


# LangGraph 學習: 條件邊就是一個讀 state、回傳下一個 node 名稱的普通函式
def route_after_swe(state: PipelineState) -> str:
    if state.get("error"):
        return "fail"
    if state["approval"] == "after_swe":
        return "gate_enter"
    return "qa"


# LangGraph 學習: 條件邊就是一個讀 state、回傳下一個 node 名稱的普通函式
def route_after_qa(state: PipelineState) -> str:
    if state.get("error"):
        return "fail"
    if not state.get("qa_passed"):
        return "rework"
    if state["approval"] == "after_qa":
        return "gate_enter"
    return "done"


# LangGraph 學習: 條件邊就是一個讀 state、回傳下一個 node 名稱的普通函式
def route_after_gate(state: PipelineState) -> str:
    """Where to go after ``gate_exit``: rework on reject, else onward."""

    if state.get("decision") == "reject":
        return "rework"
    if state.get("gate_station") == "swe":
        return "qa"
    return "done"


# LangGraph 學習: 條件邊就是一個讀 state、回傳下一個 node 名稱的普通函式
def route_after_rework(state: PipelineState):
    """Back to SWE, or END when ``_enter_rework`` already failed the task."""

    if state.get("outcome") == "failed":
        return END
    return "swe"


class PipelineGraphRunner:
    """Drives one task through the LangGraph pipeline graph."""

    def __init__(self, orch: PipelineOrchestrator) -> None:
        self._orch = orch

    async def run(self, task: Task) -> Task:
        raise NotImplementedError(
            "langgraph engine: see specs/langgraph-pipeline/tasks.md Task 10"
        )
