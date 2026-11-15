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

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from agents.qa_agent import QAAgent
from core.pipeline import STATION_PM, STATION_QA, STATION_SWE
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
    """Drives one task through the LangGraph pipeline graph.

    Nodes only decide flow and call the orchestrator's shared helpers
    (``_run_station``, ``_persist_deliverable``, ``_enter_rework`` ...), so the
    events, tasks.yaml writes and deliverables are produced by the very same
    code as the legacy engine.
    """

    def __init__(self, orch: PipelineOrchestrator) -> None:
        self._orch = orch
        # Live Task objects live outside the graph state (plan D3): agents
        # mutate them directly and checkpoints would only hold copies.
        self._live_tasks: dict[str, Task] = {}
        # LangGraph 學習: checkpointer 在每個 node 結束後把 state 存成 checkpoint；
        # interrupt() 和 Command(resume=...) 都靠它才能暫停與繼續。
        self._checkpointer = InMemorySaver()
        # LangGraph 學習: compile() 把「定義」(StateGraph) 變成可執行的 graph。
        self._graph = build_graph(self).compile(checkpointer=self._checkpointer)

    def _task(self, state: PipelineState) -> Task:
        return self._live_tasks[state["task_id"]]

    # -- nodes ------------------------------------------------------------------
    # LangGraph 學習: node 是一個接收 state、回傳「state 更新」的函式；
    # 回傳的 dict 只含要改的欄位，其餘欄位不變。

    async def intake(self, state: PipelineState) -> dict:
        await self._orch._start_task(self._task(state))
        return {}

    async def pm(self, state: PipelineState) -> dict:
        task = self._task(state)
        result = await self._orch._run_station(STATION_PM, task, context=None)
        if not result.success:
            return {"error": result.error or "PM agent failed"}
        task.pm_output = result.output
        paths = await self._orch._persist_deliverable(task, STATION_PM, result.output)
        return {"pm_output": result.output, "deliverables": paths}

    async def swe(self, state: PipelineState) -> dict:
        task = self._task(state)
        context = self._orch._build_swe_context(task, state.get("approval_feedback"))
        result = await self._orch._run_station(STATION_SWE, task, context)
        if not result.success:
            return {"error": result.error or "SWE agent failed"}
        task.swe_output = result.output
        paths = await self._orch._persist_deliverable(task, STATION_SWE, result.output)
        return {
            "swe_output": result.output,
            "approval_feedback": None,
            "gate_station": "swe",
            "deliverables": paths,
        }

    async def qa(self, state: PipelineState) -> dict:
        task = self._task(state)
        context = {"swe_output": task.swe_output, "pm_output": task.pm_output}
        result = await self._orch._run_station(STATION_QA, task, context)
        if not result.success:
            return {"error": result.error or "QA agent failed"}
        task.qa_output = result.output
        paths = await self._orch._persist_deliverable(task, STATION_QA, result.output)
        passed = QAAgent.is_pass(task.qa_output)
        update = {
            "qa_output": result.output,
            "qa_passed": passed,
            "gate_station": "qa",
            "deliverables": paths,
        }
        if not passed:
            update["rework_reason"] = f"QA still failing after {task.max_reworks} reworks"
        return update

    async def gate_enter(self, state: PipelineState) -> dict:
        await self._orch._gate_enter(self._task(state), state["gate_station"])
        return {}

    async def gate_wait(self, state: PipelineState) -> dict:
        # Rule I1 (plan §8): this node re-runs from its first line when the graph
        # resumes, so NOTHING before interrupt() may have a side effect: no events,
        # no tasks.yaml writes, no deliverables, no agent calls, no counters, no
        # live Task changes, no gate registration. Read state, interrupt, return.
        #
        # LangGraph 學習: interrupt(value) 第一次執行時暫停 graph（value 會出現在
        # snapshot.interrupts）；resume 後這個 node 從頭重跑，interrupt() 直接回傳
        # Command(resume=...) 給的值。
        answer = interrupt(
            {"task_id": state["task_id"], "station": state["gate_station"]}
        )
        return {"decision": answer["decision"], "decision_note": answer["note"]}

    async def gate_exit(self, state: PipelineState) -> dict:
        station = state["gate_station"]
        decision = state["decision"]
        note = state["decision_note"]
        await self._orch._gate_exit(self._task(state), station, decision, note)
        if decision == "reject":
            return {
                "rework_reason": f"Rejected at approval gate (after {station.upper()})",
                "approval_feedback": note,
            }
        return {}

    async def rework(self, state: PipelineState) -> dict:
        task = self._task(state)
        failed = await self._orch._enter_rework(task, state["rework_reason"])
        update: dict = {"rework_count": task.rework_count}
        if failed is not None:
            # _enter_rework already failed the task (ceiling exceeded).
            update["outcome"] = "failed"
        return update

    async def done(self, state: PipelineState) -> dict:
        await self._orch._complete_task(self._task(state), state["deliverables"])
        return {"outcome": "done"}

    async def fail(self, state: PipelineState) -> dict:
        await self._orch._fail_task(self._task(state), state["error"])
        return {"outcome": "failed"}

    # -- driver -----------------------------------------------------------------

    async def run(self, task: Task) -> Task:
        # LangGraph 學習: thread_id 把同一次執行的所有 checkpoint 串在一起；
        # 同一個 thread_id 再跑會從舊 checkpoint 接續，所以每次開始前先刪掉 (plan D6)。
        thread_id = f"{self._orch.project_id}:{task.id}"
        config = {
            "configurable": {"thread_id": thread_id},
            # Each rework lap passes through at most ~9 nodes (plan D9).
            "recursion_limit": 20 + 12 * (task.max_reworks + 1),
        }
        await self._checkpointer.adelete_thread(thread_id)
        self._live_tasks[task.id] = task
        graph_input: dict | Command = initial_state(task)
        try:
            while True:
                # LangGraph 學習: astream(stream_mode="updates") 每個 node 結束
                # 送出一個 {node 名稱: 該 node 回傳的更新}；這裡只拿來 debug log。
                async for chunk in self._graph.astream(
                    graph_input, config, stream_mode="updates"
                ):
                    logger.debug("graph %s update: %s", thread_id, chunk)
                snapshot = await self._graph.aget_state(config)
                if not snapshot.next:  # reached END
                    break
                # Stopped at an interrupt: wait for the human decision on the
                # event loop, then resume (plan D4).
                decision, note = await self._orch._gate_wait(task)
                # LangGraph 學習: Command(resume=...) 讓 gate_wait 從頭重跑，
                # 這次 interrupt() 直接回傳 resume 的值。
                graph_input = Command(resume={"decision": decision, "note": note})
            await self._checkpointer.adelete_thread(thread_id)
            return task
        finally:
            self._live_tasks.pop(task.id, None)
            # If the run is cancelled or fails anywhere between gate_enter and
            # _gate_wait (T5), no one else clears the gate registration, nor a
            # decision that arrived but was never consumed. A no-op otherwise.
            self._orch._discard_gate(task.id)


def build_graph(runner: PipelineGraphRunner) -> StateGraph:
    """Assemble the (uncompiled) pipeline graph around ``runner``'s nodes."""

    # LangGraph 學習: StateGraph(PipelineState) 宣告 state 的形狀；node 與邊都掛在它上面。
    graph = StateGraph(PipelineState)
    graph.add_node("intake", runner.intake)
    graph.add_node("pm", runner.pm)
    graph.add_node("swe", runner.swe)
    graph.add_node("qa", runner.qa)
    graph.add_node("gate_enter", runner.gate_enter)
    graph.add_node("gate_wait", runner.gate_wait)
    graph.add_node("gate_exit", runner.gate_exit)
    graph.add_node("rework", runner.rework)
    graph.add_node("done", runner.done)
    graph.add_node("fail", runner.fail)

    # LangGraph 學習: 固定邊 add_edge 一定走；條件邊 add_conditional_edges 由 routing 函式決定。
    graph.add_edge(START, "intake")
    graph.add_edge("intake", "pm")
    graph.add_conditional_edges("pm", route_after_pm, ["swe", "fail"])
    graph.add_conditional_edges("swe", route_after_swe, ["qa", "gate_enter", "fail"])
    graph.add_conditional_edges(
        "qa", route_after_qa, ["rework", "gate_enter", "done", "fail"]
    )
    graph.add_edge("gate_enter", "gate_wait")
    # LangGraph 學習: gate_wait 內的 interrupt() 讓 graph 在這裡暫停；
    # 用 Command(resume=...) 繼續後才會走到 gate_exit。
    graph.add_edge("gate_wait", "gate_exit")
    graph.add_conditional_edges("gate_exit", route_after_gate, ["rework", "qa", "done"])
    graph.add_conditional_edges("rework", route_after_rework, ["swe", END])
    graph.add_edge("done", END)
    graph.add_edge("fail", END)
    return graph
