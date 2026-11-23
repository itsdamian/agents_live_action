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
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from agents.custom_agent import parse_verdict
from agents.qa_agent import QAAgent
from core.pipeline import EVENT_AGENT_PHASE, STATION_PM, STATION_QA, STATION_SWE
from models.lineup import DEFAULT_LINEUP, Lineup, Seat
from models.task import Status, Task

if TYPE_CHECKING:
    from core.pipeline import PipelineOrchestrator

logger = logging.getLogger(__name__)

# Marker for PipelineGraphRunner._drive: the graph already waits at an interrupt, so the first
# step is to wait for the decision, not to stream.
_AT_GATE = object()


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
    gate_station: str | None  # seat id (was Literal["swe", "qa"]; widened for custom seats)
    decision: Literal["approve", "reject"] | None
    decision_note: str
    # LangGraph 學習: reducer — 每個 node 回傳的清單會被「加」到既有值，而不是覆蓋
    deliverables: Annotated[list[str], operator.add]
    error: str | None  # station failure reason; a value routes to ``fail``
    outcome: Literal["done", "failed"] | None
    # -- custom lineups (plan 5.2; only added, never set by initial_state) --
    # LangGraph 學習: 自訂 reducer：merge_dicts 讓每個自訂 seat 只回傳 {自己的 id: 輸出}，
    # LangGraph 把它合併進既有的 dict，而不是整個覆蓋。
    outputs: Annotated[dict[str, str], merge_dicts]  # latest output of each custom seat
    check_passed: bool  # verdict of the custom check that just ran
    rework_target: str | None  # make seat the rework goes back to
    review_feedback: str | None  # the failing check's report; cleared once the target succeeds
    review_from: str | None  # the seat that wrote review_feedback


PHASES_PER_SEAT = 3


def phase_text(
    phase: str,
    *,
    title: str = "",
    first: bool = False,
    upstream_count: int = 0,
    rework_note: str | None = None,
    llm_model: str = "",
    error: str | None = None,
    passed: bool | None = None,
    target_name: str = "",
) -> str:
    """The one-line text of an ``agent:phase`` event (plan section 6.3)."""

    if phase == "intake":
        if rework_note is not None:
            first_line = next((line.strip() for line in rework_note.splitlines() if line.strip()), "")
            return f"重做：{first_line[:30]}"
        if first:
            return f"讀任務：「{title}」"
        return f"收到 {upstream_count} 份上游產出"
    if phase == "thinking":
        return f"思考中…（{llm_model}）"
    if phase == "output":
        return f"出錯了：{error[:30]}" if error else "交出成果，往下傳"
    if phase == "verdict":
        return "檢查通過" if passed else f"退件 → {target_name}"
    raise ValueError(f"unknown phase {phase!r}")


def merge_dicts(a: dict | None, b: dict | None) -> dict:
    """Reducer of ``outputs``: later keys win."""

    return {**(a or {}), **(b or {})}


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


# -- routing for any lineup (plan section 5.3, D17) ------------------------------------------------
#
# The route_after_* functions above are the fixed three-station version, kept as the reference
# the factories below are tested against (tests/test_lineup_routing.py). The factories close over
# a lineup and a seat index and return ordinary routing functions; *_paths give the matching
# path maps (the possible targets, which is what the drawn graph shows).
#
# Approval gates on any lineup: ``after_swe`` = after the last make seat; ``after_qa`` = after the
# last check seat passed, or after the last seat when there is no check. A failed check sends the
# work back to the nearest make seat in front of it; a rejection at the gate sends it back to the
# gate seat if that is a make seat, else to the make seat in front of it.


def gate_seat_index(lineup: Lineup, approval: str) -> int | None:
    """Index of the seat after which ``approval`` pauses, or ``None`` for no gate."""

    kinds = [seat.kind for seat in lineup.seats]
    if approval == "after_swe":
        return max(i for i, kind in enumerate(kinds) if kind == "make")
    if approval == "after_qa":
        checks = [i for i, kind in enumerate(kinds) if kind == "check"]
        return checks[-1] if checks else len(kinds) - 1
    return None


def prev_maker(lineup: Lineup, index: int) -> str:
    """The rework target of seat ``index``: itself if it is a make seat, else the nearest make in front."""

    for i in range(index, -1, -1):
        if lineup.seats[i].kind == "make":
            return lineup.seats[i].id
    raise ValueError(f"no make seat at or before seat {index} of {lineup.ids}")


def _gate_indices(lineup: Lineup) -> list[int]:
    found = {gate_seat_index(lineup, "after_swe"), gate_seat_index(lineup, "after_qa")}
    return sorted(found)


def _after(lineup: Lineup, index: int) -> str:
    return lineup.ids[index + 1] if index + 1 < len(lineup.seats) else "done"


def _check_passed(seat: Seat, state: PipelineState) -> bool:
    # The built-in QA keeps its own flag (QAAgent.is_pass); custom checks use parse_verdict.
    if seat.builtin and seat.id == STATION_QA:
        return bool(state.get("qa_passed"))
    return bool(state.get("check_passed"))


def make_seat_router(lineup: Lineup, index: int):
    """Routing function for the conditional edges leaving seat ``index``."""

    seat = lineup.seats[index]
    after = _after(lineup, index)

    # LangGraph 學習: 路由函式可以由工廠在執行期產生：lineup 與 index 存在閉包裡，
    # 每個 seat 拿到自己的一個函式，所以同一套程式能接上任何編制。
    def route(state: PipelineState) -> str:
        if state.get("error"):
            return "fail"
        if seat.kind == "check" and not _check_passed(seat, state):
            return "rework"
        if gate_seat_index(lineup, state["approval"]) == index:
            return "gate_enter"
        return after

    route.__name__ = f"route_after_{seat.id}"
    return route


def seat_paths(lineup: Lineup, index: int) -> list[str]:
    """Path map of seat ``index`` (for the default lineup exactly the old lists)."""

    gate = ["gate_enter"] if index in _gate_indices(lineup) else []
    if lineup.seats[index].kind == "check":
        return ["rework", *gate, _after(lineup, index), "fail"]
    return [_after(lineup, index), *gate, "fail"]


def make_gate_router(lineup: Lineup):
    """Routing function after ``gate_exit``: rework on reject, else the seat after the gate seat."""

    def route_after_gate_exit(state: PipelineState) -> str:
        if state.get("decision") == "reject":
            return "rework"
        return _after(lineup, lineup.index(state["gate_station"]))

    return route_after_gate_exit


def gate_paths(lineup: Lineup) -> list[str]:
    paths = ["rework"]
    for index in _gate_indices(lineup):
        if _after(lineup, index) not in paths:
            paths.append(_after(lineup, index))
    return paths


def _rework_targets(lineup: Lineup) -> list[str]:
    targets = {prev_maker(lineup, i) for i, seat in enumerate(lineup.seats) if seat.kind == "check"}
    targets |= {prev_maker(lineup, i) for i in _gate_indices(lineup)}
    return [seat_id for seat_id in lineup.ids if seat_id in targets]


def make_rework_router(lineup: Lineup):
    """Routing function after ``rework``: END when the task failed, else the rework target."""

    fallback = _rework_targets(lineup)[0]

    def route_after_rework_node(state: PipelineState):
        if state.get("outcome") == "failed":
            return END
        # A checkpoint written before rework_target existed has none: the first target is the
        # only one the old three-station graph had (swe).
        return state.get("rework_target") or fallback

    return route_after_rework_node


def rework_paths(lineup: Lineup) -> list:
    return [*_rework_targets(lineup), END]


class PipelineGraphRunner:
    """Drives one task through the LangGraph pipeline graph.

    Nodes only decide flow and call the orchestrator's shared helpers
    (``_run_station``, ``_persist_deliverable``, ``_enter_rework`` ...), so the
    events, tasks.yaml writes and deliverables are produced by the very same
    code as the legacy engine.
    """

    def __init__(self, orch: PipelineOrchestrator, checkpointer=None) -> None:
        self._orch = orch
        # Live Task objects live outside the graph state (plan D3): agents
        # mutate them directly and checkpoints would only hold copies.
        self._live_tasks: dict[str, Task] = {}
        # LangGraph 學習: checkpointer 在每個 node 結束後把 state 存成 checkpoint；
        # interrupt() 和 Command(resume=...) 都靠它才能暫停與繼續。
        # 沒有注入時每個 runner 有自己的 InMemorySaver（程序結束就消失）；runtime 在
        # PIPELINE_CHECKPOINTER=postgres 時注入共用的 AsyncPostgresSaver（core/checkpointing.py，plan D7）。
        self._checkpointer = checkpointer if checkpointer is not None else InMemorySaver()
        # Compiled graphs by lineup signature (plan 5.1); _graph is the current lineup's.
        self._graphs: dict[tuple, object] = {}
        self._lineup = DEFAULT_LINEUP
        self._graph = self._compiled(DEFAULT_LINEUP)

    def _compiled(self, lineup: Lineup):
        key = lineup.signature()
        if key not in self._graphs:
            # LangGraph 學習: compile() 把「定義」(StateGraph) 變成可執行的 graph。編制不同，
            # graph 的 node 與邊就不同，所以每種編制各 compile 一次並快取（只看 id 與 kind）。
            self._graphs[key] = build_lineup_graph(self, lineup).compile(checkpointer=self._checkpointer)
        return self._graphs[key]

    @property
    def lineup(self) -> Lineup:
        return self._lineup

    def use_lineup(self, lineup: Lineup) -> None:
        """Run tasks on ``lineup`` from now on (only while no task is running)."""

        if self._live_tasks:
            raise RuntimeError("can not switch lineups while a task is running")
        graph = self._compiled(lineup)  # may raise: then nothing has changed
        self._lineup = lineup
        self._graph = graph

    def use_checkpointer(self, checkpointer) -> None:
        """Re-compile the graph on ``checkpointer`` (only while no task is running)."""

        if self._live_tasks:
            raise RuntimeError("can not switch checkpointers while a task is running")
        # LangGraph 學習: checkpointer 是 compile() 時綁上去的，換 checkpointer 就要重新 compile；
        # graph 的結構（node 與邊）不變。
        self._checkpointer = checkpointer
        self._graphs = {}
        self._graph = self._compiled(self._lineup)

    def _task(self, state: PipelineState) -> Task:
        return self._live_tasks[state["task_id"]]

    # -- nodes ------------------------------------------------------------------
    # LangGraph 學習: node 是一個接收 state、回傳「state 更新」的函式；
    # 回傳的 dict 只含要改的欄位，其餘欄位不變。

    async def intake(self, state: PipelineState) -> dict:
        await self._orch._start_task(self._task(state))
        return {}

    def _custom_upstream(self, state: PipelineState, lineup: Lineup, seat_id: str) -> dict:
        """``{"<id>_output": ...}`` of the custom seats in front of ``seat_id`` (plan 5.1).

        Empty for the default lineup, so a built-in seat's context stays exactly the legacy one.
        """

        outputs = state.get("outputs") or {}
        return {
            f"{seat.id}_output": outputs[seat.id]
            for seat in lineup.seats[: lineup.index(seat_id)]
            if not seat.builtin and outputs.get(seat.id) is not None
        }

    async def pm(self, state: PipelineState, *, lineup: Lineup = DEFAULT_LINEUP) -> dict:
        task = self._task(state)
        context = self._custom_upstream(state, lineup, STATION_PM) or None
        result = await self._orch._run_station(STATION_PM, task, context=context)
        if not result.success:
            return {"error": result.error or "PM agent failed"}
        task.pm_output = result.output
        paths = await self._orch._persist_deliverable(task, STATION_PM, result.output)
        # PM holds the gate when it is the last make seat (after_swe) or the last seat with no
        # check (after_qa), plan D17: (pm, qa), (pm,).
        return {"pm_output": result.output, "gate_station": STATION_PM, "deliverables": paths}

    async def swe(self, state: PipelineState, *, lineup: Lineup = DEFAULT_LINEUP) -> dict:
        task = self._task(state)
        context = self._orch._build_swe_context(task, state.get("approval_feedback"))
        context.update(self._custom_upstream(state, lineup, STATION_SWE))
        review = state.get("review_feedback") if state.get("rework_target") == STATION_SWE else None
        if review:
            # A custom check sent the work back: SWE gets its report too (plan 5.1).
            context["review_feedback"] = review
        result = await self._orch._run_station(STATION_SWE, task, context)
        if not result.success:
            return {"error": result.error or "SWE agent failed"}
        task.swe_output = result.output
        paths = await self._orch._persist_deliverable(task, STATION_SWE, result.output)
        update = {
            "swe_output": result.output,
            "approval_feedback": None,
            "gate_station": "swe",
            "deliverables": paths,
        }
        if review:
            update.update(review_feedback=None, review_from=None)
        return update

    async def qa(self, state: PipelineState, *, lineup: Lineup = DEFAULT_LINEUP) -> dict:
        task = self._task(state)
        context = {"swe_output": task.swe_output, "pm_output": task.pm_output}
        context.update(self._custom_upstream(state, lineup, STATION_QA))
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
            target = prev_maker(lineup, lineup.index(STATION_QA))
            update["rework_target"] = target
            if not lineup.seat(target).builtin:
                # Built-in SWE reads QA's report from the task (legacy); a custom maker gets it here.
                update.update(review_feedback=result.output, review_from=STATION_QA)
        return update

    def make_custom_seat(self, seat: Seat, lineup: Lineup):
        """The node of a custom seat (plan D17): runs its CustomAgent through _run_station."""

        index = lineup.index(seat.id)
        orch = self._orch

        def latest_output(other: Seat, state: PipelineState, task: Task) -> str | None:
            if other.builtin:
                return getattr(task, f"{other.id}_output", None)
            return (state.get("outputs") or {}).get(other.id)

        async def custom_seat(state: PipelineState) -> dict:
            task = self._task(state)
            context: dict = {
                "upstream": [(other.id, latest_output(other, state, task)) for other in lineup.seats[:index]]
            }
            sent_back_here = state.get("rework_target") == seat.id
            previous = (state.get("outputs") or {}).get(seat.id)
            if seat.kind == "check":
                if previous is not None and not parse_verdict(previous).passed:
                    # Looking again at work it rejected: the check sees its own earlier report.
                    context["feedback"] = (seat.id, previous)
                elif state.get("decision") == "reject" and state.get("decision_note"):
                    # Looking again at work a human rejected at the gate: the check sees the note
                    # (plan 4.4), so a Mock check passes it and a reject costs one rework.
                    context["reviewer_note"] = state["decision_note"]
            elif sent_back_here and state.get("review_feedback"):
                context["feedback"] = (state.get("review_from") or "", state["review_feedback"])
            elif sent_back_here and state.get("approval_feedback"):
                context["reviewer_note"] = state["approval_feedback"]
            result = await orch._run_station(seat.id, task, context)
            if not result.success:
                return {"error": result.error or f"{seat.id.upper()} agent failed"}
            paths = await orch._persist_deliverable(task, seat.id, result.output)
            update: dict = {"outputs": {seat.id: result.output}, "gate_station": seat.id, "deliverables": paths}
            if seat.kind == "make":
                if sent_back_here:  # the feedback that sent the work here has been used
                    update.update(approval_feedback=None, review_feedback=None, review_from=None)
                return update
            verdict = parse_verdict(result.output)
            update["check_passed"] = verdict.passed
            if not verdict.passed:
                update.update(
                    rework_target=prev_maker(lineup, index),
                    review_feedback=result.output,
                    review_from=seat.id,
                    rework_reason=f"{seat.id.upper()} still failing after {task.max_reworks} reworks",
                )
            return update

        custom_seat.__name__ = seat.id
        return custom_seat

    def _seat_output(self, seat: Seat, state: PipelineState, task: Task) -> str | None:
        if seat.builtin:
            return getattr(task, f"{seat.id}_output", None)
        return (state.get("outputs") or {}).get(seat.id)

    def _intake_text(self, seat: Seat, state: PipelineState, task: Task) -> str:
        if task.rework_count > 0 and state.get("rework_target") == seat.id:
            failed_qa = state.get("qa_output") if not state.get("qa_passed") else None
            note = state.get("review_feedback") or state.get("approval_feedback") or failed_qa or ""
            return phase_text("intake", rework_note=note)
        lineup = self._lineup
        index = lineup.index(seat.id)
        if index == 0:
            return phase_text("intake", title=task.title, first=True)
        count = sum(1 for other in lineup.seats[:index] if self._seat_output(other, state, task) is not None)
        return phase_text("intake", upstream_count=count)

    def _display_name(self, seat_id: str | None) -> str:
        try:
            return self._lineup.seat(seat_id).display_name
        except KeyError:
            return seat_id or ""

    def with_phases(self, node, seat: Seat):
        """Wrap a seat node so it reports three ``agent:phase`` events (plan D16).

        intake and thinking before the agent runs; output (make seat, or any error) or verdict
        (check seat) after. The events go to the live channel only: the driver forwards the
        custom stream to ``on_live_event``. Without a live listener the driver streams only
        "updates" and the writer does nothing, so ``on_event`` is untouched either way (AC13).
        """

        runner = self

        async def phased(state: PipelineState) -> dict:
            # LangGraph 學習: get_stream_writer() 取得這次執行的 custom stream writer（從 contextvar
            # 讀出，所以要在 node 執行中呼叫）。寫進去的東西會以 ("custom", payload) 出現在
            # astream(stream_mode=[..., "custom"]) 裡；stream_mode 沒有 "custom" 時 writer 是什麼都不做的函式。
            writer = get_stream_writer()
            task = runner._task(state)

            def emit(step: int, phase: str, text: str, passed=None, rework_target=None) -> None:
                writer({"event": EVENT_AGENT_PHASE, "data": {
                    "task_id": task.id, "station": seat.id, "step": step, "of": PHASES_PER_SEAT,
                    "phase": phase, "text": text, "passed": passed, "rework_target": rework_target,
                }})

            agent = runner._orch._agents.get(seat.id)
            model = getattr(getattr(agent, "llm_client", None), "model", None) or getattr(
                getattr(agent, "config", None), "llm_model", "")
            emit(1, "intake", runner._intake_text(seat, state, task))
            emit(2, "thinking", phase_text("thinking", llm_model=model))
            update = await node(state)
            if update.get("error"):
                emit(3, "output", phase_text("output", error=update["error"]))
            elif seat.kind == "check":
                flag = "qa_passed" if seat.builtin and seat.id == STATION_QA else "check_passed"
                passed = bool(update.get(flag))
                target = None if passed else update.get("rework_target")
                emit(3, "verdict", phase_text("verdict", passed=passed, target_name=runner._display_name(target)),
                     passed=passed, rework_target=target)
            else:
                emit(3, "output", phase_text("output"))
            return update

        phased.__name__ = seat.id
        return phased

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

    async def gate_exit(self, state: PipelineState, *, lineup: Lineup = DEFAULT_LINEUP) -> dict:
        station = state["gate_station"]
        decision = state["decision"]
        note = state["decision_note"]
        await self._orch._gate_exit(self._task(state), station, decision, note)
        if decision == "reject":
            return {
                "rework_reason": f"Rejected at approval gate (after {station.upper()})",
                "approval_feedback": note,
                "rework_target": prev_maker(lineup, lineup.index(station)),
            }
        return {}

    async def rework(self, state: PipelineState) -> dict:
        task = self._task(state)
        # The rework event names the seat the work goes back to (swe on the default lineup;
        # a checkpoint from before custom lineups has no rework_target and meant swe).
        station = state.get("rework_target") or STATION_SWE
        failed = await self._orch._enter_rework(task, state["rework_reason"], station=station)
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

    def _config(self, task: Task) -> dict:
        # LangGraph 學習: thread_id 把同一個任務的所有 checkpoint 串在一起。它只由專案與任務 id
        # 組成、不含程序或時間，所以後端重啟後用同一個 thread_id 就能在 Postgres 找回同一條執行。
        return {
            "configurable": {"thread_id": f"{self._orch.project_id}:{task.id}"},
            # Each rework lap passes through at most ~9 nodes plus the seats (plan D9, 5.1);
            # the default lineup keeps 20 + 12 * (r + 1).
            "recursion_limit": 20 + max(12, len(self._lineup.seats) + 9) * (task.max_reworks + 1),
        }

    async def _gate_checkpoint(self, task: Task):
        """The task's latest checkpoint if its graph waits at the gate, else ``None``.

        Waiting means the next node is ``gate_wait``: either inside it (interrupt() was reached,
        ``interrupts`` is set) or right before it (the process stopped after ``gate_enter`` was
        checkpointed but before gate_wait ran; it has no side effects, rule I1, so running it
        again is safe). One more case counts, only for a task stored as ``awaiting_approval``:
        the next node is ``gate_enter`` itself. The status is written inside gate_enter, so the
        process stopped while gate_enter was running, after that write and before its checkpoint;
        :meth:`_resume_at_gate` then marks gate_enter done instead of running it again. That
        includes a stop after gate_enter's pending writes were stored but before the step's
        checkpoint: ``next`` then leaves gate_enter out (it already has writes), so the pending
        task is looked up in ``tasks`` instead.
        """

        # LangGraph 學習: aget_state(config) 讀這個 thread 最新的 checkpoint（StateSnapshot）：
        # .values 是當時的 state、.next 是接下來要跑的 node、.interrupts 是停住時 interrupt(value)
        # 帶出來的值。重啟後就靠它判斷「這條 thread 停在閘門（要 resume）還是要重來」。
        snapshot = await self._graph.aget_state(self._config(task))
        if "gate_wait" in snapshot.next:
            return snapshot
        # LangGraph 學習: snapshot.next 不含「已經有 pending writes」的 task（node 跑完、writes 已存，
        # 但這一步的 checkpoint 還沒存）；snapshot.tasks 才是這一步所有的 task（含已有 writes 的）。
        # 所以 gate_enter 跑完就被關掉時，next 是空的，要看 tasks 才知道停在 gate_enter。
        pending = {t.name for t in snapshot.tasks}
        if "gate_enter" in pending and task.status is Status.AWAITING_APPROVAL:
            return snapshot
        return None

    async def restore_gate(self, task: Task) -> str | None:
        """Restart recovery: if ``task`` waits at its gate, put its outputs back on it.

        Returns the gate's station, or ``None`` when the checkpointer holds no waiting gate.
        """

        snapshot = await self._gate_checkpoint(task)
        if snapshot is None:
            return None
        values = snapshot.values
        task.pm_output = values.get("pm_output")
        task.swe_output = values.get("swe_output")
        task.qa_output = values.get("qa_output")
        task.rework_count = values.get("rework_count", task.rework_count)
        if snapshot.interrupts:
            return snapshot.interrupts[0].value["station"]
        return values.get("gate_station")

    async def run(self, task: Task) -> Task:
        config = self._config(task)
        thread_id = config["configurable"]["thread_id"]
        # LangGraph 學習: 開頭先看這個 thread 的 checkpoint：停在閘門 → 不刪、從閘門接著跑
        # （重啟恢復，plan D7）；其他情況（沒有、已跑完、半途中斷）→ 刪掉後全新執行，
        # 否則同一個 thread_id 會從舊 checkpoint 接續 (plan D6)。
        if await self._gate_checkpoint(task) is not None:
            return await self._resume_at_gate(task, config)
        await self._checkpointer.adelete_thread(thread_id)
        return await self._drive(task, config, initial_state(task))

    async def _resume_at_gate(self, task: Task, config: dict) -> Task:
        snapshot = await self._gate_checkpoint(task)
        # The wait event stays registered after a decision arrives (only _gate_wait clears it), so
        # a restored gate that was already approved before the worker got here is not replaced.
        if task.id not in self._orch._gate_events:
            # Not restored by the runtime: rebuild what restore_gate would have.
            station = await self.restore_gate(task)
            self._orch._gate_register(task, station)
        logger.info("Resuming task %s at its approval gate (thread %s)",
                    task.id, config["configurable"]["thread_id"])
        if snapshot.interrupts:
            return await self._drive(task, config, _AT_GATE)
        # gate_enter has not finished: mark it done without running it. If it finished and only its
        # step checkpoint is missing (not in next, its writes are stored), astream(None) below
        # applies those stored writes without re-running it, so no aupdate_state then.
        if "gate_enter" in snapshot.next:
            # LangGraph 學習: aupdate_state(config, values, as_node=X) 寫一個新 checkpoint，
            # 當作 node X 已經執行完並回傳了 values；next 於是變成 X 的下一站。這裡用來標記
            # gate_enter「已完成」：它的副作用（狀態、閘門事件）在舊程序已做過，不能再做一次。
            await self._graph.aupdate_state(config, None, as_node="gate_enter")
        # LangGraph 學習: astream(None, config) 不給新輸入，從 checkpoint 的 next（gate_wait）
        # 接著跑；gate_wait 一進去就 interrupt()，於是又停在閘門等決定。
        return await self._drive(task, config, None)

    async def _drive(self, task: Task, config: dict, graph_input) -> Task:
        """Stream the graph to END, answering every gate with the human decision."""

        thread_id = config["configurable"]["thread_id"]
        self._live_tasks[task.id] = task
        try:
            while True:
                if graph_input is not _AT_GATE:
                    # LangGraph 學習: astream(stream_mode="updates") 每個 node 結束
                    # 送出一個 {node 名稱: 該 node 回傳的更新}；這裡只拿來 debug log。
                    # LangGraph 學習: durability 決定 checkpoint 何時寫入。預設 "async" 在下一步執行時
                    # 於背景寫入；程序在那之間被關掉（cancel 時 LangGraph 的收尾工作是脫離出去的），
                    # 剛寫完 gate_enter 的 checkpoint 就可能遺失，重啟後閘門找不回來（Task 14 的
                    # 整合測試在負載下實際遇到）。"sync" 每一步都等 checkpoint 寫進 DB 才往下走，
                    # 代價是每個 node 多一次 DB 往返（幾毫秒，和 LLM 呼叫相比可忽略）。
                    # "exit" 只在整個 graph 結束時才寫，閘門就無法跨重啟。
                    # durability="sync": wait for each checkpoint to be stored before the next step,
                    # so a shutdown right after the gate is reached cannot lose the gate checkpoint.
                    # LangGraph 學習: stream_mode 給清單時，每個 chunk 變成 (mode, payload) 的 tuple；
                    # "custom" 是 node 用 get_stream_writer() 寫出來的東西（這裡是 agent:phase），
                    # 轉到 live 通道。沒有 live 監聽者時只要 "updates"（writer 便不做事）。
                    live = self._orch.has_live_listener
                    modes = ["updates", "custom"] if live else "updates"
                    async for chunk in self._graph.astream(
                        graph_input, config, stream_mode=modes, durability="sync"
                    ):
                        if live:
                            mode, chunk = chunk
                            if mode == "custom":
                                await self._orch._emit_live(chunk["event"], chunk["data"])
                                continue
                        logger.debug("graph %s update: %s", thread_id, chunk)
                    snapshot = await self._graph.aget_state(config)
                    if not snapshot.next:  # reached END
                        break
                # Stopped at an interrupt: wait for the human decision on the
                # event loop, then resume (plan D4).
                decision, note = await self._orch._gate_wait(task)
                # LangGraph 學習: Command(resume=...) 讓 gate_wait 從頭重跑，
                # 這次 interrupt() 直接回傳 resume 的值。跨重啟也一樣：新程序用同一個
                # thread_id 送 Command(resume=...)，gate_wait 從頭重跑（所以規則 I1 照樣成立）。
                graph_input = Command(resume={"decision": decision, "note": note})
            await self._checkpointer.adelete_thread(thread_id)
            return task
        finally:
            self._live_tasks.pop(task.id, None)
            # If the run is cancelled or fails anywhere between gate_enter and
            # _gate_wait (T5), no one else clears the gate registration, nor a
            # decision that arrived but was never consumed. A no-op otherwise.
            self._orch._discard_gate(task.id)


def _bind_lineup(method, lineup: Lineup):
    """``method(state, lineup=lineup)`` as a plain async node function."""

    async def node(state: PipelineState) -> dict:
        return await method(state, lineup=lineup)

    node.__name__ = method.__name__
    return node


def build_lineup_graph(runner: PipelineGraphRunner, lineup: Lineup) -> StateGraph:
    """Assemble the (uncompiled) pipeline graph of ``lineup`` around ``runner``'s nodes.

    Built-in seats use the runner's own node methods (only given the lineup so they can add the
    outputs of custom seats in front of them); custom seats get a CustomAgent node. For the
    default lineup the nodes and edges are exactly the original three-station graph (the golden
    mermaid in tests/fixtures/pipeline_graph_default.mmd).
    """

    # LangGraph 學習: StateGraph(PipelineState) 宣告 state 的形狀；node 與邊都掛在它上面。
    graph = StateGraph(PipelineState)
    graph.add_node("intake", runner.intake)
    # LangGraph 學習: node 可以在迴圈裡動態加入：graph 的形狀由資料（編制）決定，
    # 不必寫死在程式裡。node 名稱就是 seat id（不可含 LangGraph 保留的 ":"、"|"，見 models/lineup.py）。
    for seat in lineup.seats:
        if seat.builtin:
            node = _bind_lineup(getattr(runner, seat.id), lineup)
        else:
            node = runner.make_custom_seat(seat, lineup)
        graph.add_node(seat.id, runner.with_phases(node, seat))
    graph.add_node("gate_enter", runner.gate_enter)
    graph.add_node("gate_wait", runner.gate_wait)
    graph.add_node("gate_exit", _bind_lineup(runner.gate_exit, lineup))
    graph.add_node("rework", runner.rework)
    graph.add_node("done", runner.done)
    graph.add_node("fail", runner.fail)

    # LangGraph 學習: 固定邊 add_edge 一定走；條件邊 add_conditional_edges 由 routing 函式決定。
    graph.add_edge(START, "intake")
    graph.add_edge("intake", lineup.seats[0].id)
    for index, seat in enumerate(lineup.seats):
        graph.add_conditional_edges(seat.id, make_seat_router(lineup, index), seat_paths(lineup, index))
    graph.add_edge("gate_enter", "gate_wait")
    # LangGraph 學習: gate_wait 內的 interrupt() 讓 graph 在這裡暫停；
    # 用 Command(resume=...) 繼續後才會走到 gate_exit。
    graph.add_edge("gate_wait", "gate_exit")
    graph.add_conditional_edges("gate_exit", make_gate_router(lineup), gate_paths(lineup))
    graph.add_conditional_edges("rework", make_rework_router(lineup), rework_paths(lineup))
    graph.add_edge("done", END)
    graph.add_edge("fail", END)
    return graph


def build_graph(runner: PipelineGraphRunner) -> StateGraph:
    """The default three-station graph (``build_lineup_graph`` of ``DEFAULT_LINEUP``)."""

    return build_lineup_graph(runner, DEFAULT_LINEUP)
