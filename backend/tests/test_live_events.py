"""The live channel: agent:phase and lineup events (Task 23, plan D16, 6.3, AC13).

* ``on_event`` receives the same events with and without a live listener (S1, S2, S6, C2).
* Every seat visit sends three ``agent:phase`` events; every key is always present.
* A writer call while streaming only "updates" does nothing (plan section 15).
* ``phase_text`` table; the data shapes of the lineup events.
"""

from __future__ import annotations

import asyncio

import pytest
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

import config
from core.pipeline_graph import phase_text
from models.lineup import DEFAULT_LINEUP
from models.task import Approval
from tests.hud_harness import make_lineup, make_orchestrator_with_repo
from tests.pipeline_harness import (
    QA_FAIL_REPORT,
    ScriptedClient,
    make_project,
    make_task,
    run_with_gate_actions,
    signatures,
)

ENGINE = config.get_pipeline_engine()
langgraph_only = pytest.mark.skipif(ENGINE != "langgraph", reason="agent:phase comes from the LangGraph engine")

PHASE_KEYS = ["task_id", "station", "step", "of", "phase", "text", "passed", "rework_target"]
APPROVE = ("approve", "")

SCENARIOS = {
    "S1": dict(),
    "S2": dict(qa=[QA_FAIL_REPORT]),
    "S6": dict(task={"approval": Approval.AFTER_SWE}, actions=[APPROVE]),
    "S5": dict(pm=[RuntimeError("provider unreachable at 10.0.0.1:443, giving up")]),
    "C2": dict(specs=("pm", "swe", "qa", ("security", "check")), scripts={"security": ["- XSS\nVERDICT: FAIL"]}),
}


def run(tmp_path, name, *, live: bool):
    spec = SCENARIOS[name]
    task = make_task(**spec.get("task", {}))
    workdir = tmp_path / ("live" if live else "plain")
    workdir.mkdir()
    project = make_project(workdir, [task])
    live_events: list[tuple[str, dict]] = []
    orch, events = make_orchestrator_with_repo(
        project, engine=ENGINE,
        pm_client=ScriptedClient("Project Manager", "PM_agent", spec.get("pm")),
        swe_client=ScriptedClient("Software Engineer", "SWE_agent", spec.get("swe")),
        qa_client=ScriptedClient("Quality Assurance", "QA_agent", spec.get("qa")),
        on_live_event=(lambda e, d: live_events.append((e, d))) if live else None,
    )
    if "specs" in spec:
        lineup, agents = make_lineup(*spec["specs"], scripts=spec.get("scripts"))
        asyncio.run(orch.request_lineup(lineup, agents))
        live_events.clear()
    if spec.get("actions"):
        asyncio.run(run_with_gate_actions(orch, task, spec["actions"]))
    else:
        asyncio.run(orch.process_task(task))
    return events, live_events


def phases(live_events):
    return [d for e, d in live_events if e == "agent:phase"]


# -- AC13: the live channel does not change on_event -----------------------------------------------


@langgraph_only
@pytest.mark.parametrize("name", ["S1", "S2", "S6", "C2"])
def test_on_event_is_the_same_with_and_without_a_live_listener(tmp_path, name):
    plain, nothing = run(tmp_path, name, live=False)
    with_live, live_events = run(tmp_path, name, live=True)

    assert nothing == []
    assert signatures(with_live) == signatures(plain)
    assert [e for e, _ in with_live] == [e for e, _ in plain]
    assert not any(e.startswith(("agent:", "pipeline:")) for e, _ in with_live)
    assert {e for e, _ in live_events} == {"agent:phase"}


@langgraph_only
@pytest.mark.parametrize("name", ["S1", "S2", "S6", "C2"])
def test_three_phases_for_every_seat_visit(tmp_path, name):
    events, live_events = run(tmp_path, name, live=True)
    visits = [d["station"] for e, d in events if e == "station:update" and d["status"] == "working"]
    got = phases(live_events)

    assert len(got) == 3 * len(visits)
    for i, station in enumerate(visits):
        triple = got[3 * i: 3 * i + 3]
        assert [p["station"] for p in triple] == [station] * 3
        assert [(p["step"], p["of"]) for p in triple] == [(1, 3), (2, 3), (3, 3)]
        assert [p["phase"] for p in triple][:2] == ["intake", "thinking"]
        assert triple[2]["phase"] == ("verdict" if station in ("qa", "security") else "output")
    for p in got:
        assert list(p) == PHASE_KEYS
        assert p["task_id"] == "task-001"
        if p["phase"] != "verdict":
            assert p["passed"] is None and p["rework_target"] is None


@langgraph_only
def test_phase_texts_of_s1(tmp_path):
    _, live_events = run(tmp_path, "S1", live=True)
    assert [(p["station"], p["phase"], p["text"]) for p in phases(live_events)] == [
        ("pm", "intake", "讀任務：「Add login page」"),
        ("pm", "thinking", "思考中…（mock-model）"),
        ("pm", "output", "交出成果，往下傳"),
        ("swe", "intake", "收到 1 份上游產出"),
        ("swe", "thinking", "思考中…（mock-model）"),
        ("swe", "output", "交出成果，往下傳"),
        ("qa", "intake", "收到 2 份上游產出"),
        ("qa", "thinking", "思考中…（mock-model）"),
        ("qa", "verdict", "檢查通過"),
    ]


@langgraph_only
def test_verdict_fields_and_rework_intake_of_s2(tmp_path):
    _, live_events = run(tmp_path, "S2", live=True)
    got = phases(live_events)
    first_verdict, second_verdict = [p for p in got if p["phase"] == "verdict"]
    assert (first_verdict["passed"], first_verdict["rework_target"], first_verdict["text"]) == (False, "swe", "退件 → SWE")
    assert (second_verdict["passed"], second_verdict["rework_target"], second_verdict["text"]) == (True, None, "檢查通過")
    swe_intakes = [p["text"] for p in got if p["station"] == "swe" and p["phase"] == "intake"]
    assert swe_intakes == ["收到 1 份上游產出", "重做：[MOCK MODE] QA FAILURE REPORT"]


@langgraph_only
def test_custom_check_verdict_names_the_rework_target(tmp_path):
    _, live_events = run(tmp_path, "C2", live=True)
    verdicts = [(p["station"], p["passed"], p["rework_target"], p["text"]) for p in phases(live_events) if p["phase"] == "verdict"]
    assert verdicts == [
        ("qa", True, None, "檢查通過"),
        ("security", False, "swe", "退件 → SWE"),
        ("qa", True, None, "檢查通過"),
        ("security", True, None, "檢查通過"),
    ]
    swe_intake = [p["text"] for p in phases(live_events) if p["station"] == "swe" and p["phase"] == "intake"]
    assert swe_intake == ["收到 1 份上游產出", "重做：- XSS"]


@langgraph_only
def test_an_agent_error_ends_with_an_output_phase(tmp_path):
    _, live_events = run(tmp_path, "S5", live=True)
    assert [(p["phase"], p["text"]) for p in phases(live_events)] == [
        ("intake", "讀任務：「Add login page」"),
        ("thinking", "思考中…（mock-model）"),
        ("output", "出錯了：provider unreachable at 10.0.0"),  # first 30 characters
    ]


# -- plan section 15: the writer is a no-op without the custom stream -------------------------------


class _State(TypedDict, total=False):
    n: int


def test_writer_does_nothing_when_only_updates_are_streamed():
    async def node(state):
        get_stream_writer()({"event": "agent:phase", "data": {}})
        return {"n": state.get("n", 0) + 1}

    graph = StateGraph(_State)
    graph.add_node("work", node)
    graph.add_edge(START, "work")
    graph.add_edge("work", END)
    compiled = graph.compile()

    async def scenario():
        updates = [chunk async for chunk in compiled.astream({"n": 0}, stream_mode="updates")]
        both = [chunk async for chunk in compiled.astream({"n": 0}, stream_mode=["updates", "custom"])]
        return updates, both

    updates, both = asyncio.run(scenario())
    assert updates == [{"work": {"n": 1}}]
    assert both == [("custom", {"event": "agent:phase", "data": {}}), ("updates", {"work": {"n": 1}})]


# -- phase_text --------------------------------------------------------------------------------------


@pytest.mark.parametrize(("kwargs", "text"), [
    (dict(phase="intake", first=True, title="加登入頁"), "讀任務：「加登入頁」"),
    (dict(phase="intake", upstream_count=3), "收到 3 份上游產出"),
    (dict(phase="intake", rework_note="\n  請補上錯誤處理  \n第二行"), "重做：請補上錯誤處理"),
    (dict(phase="intake", rework_note="字" * 40), "重做：" + "字" * 30),
    (dict(phase="intake", rework_note=""), "重做："),
    (dict(phase="intake", rework_note="x", first=True, title="t"), "重做：x"),  # a rework wins
    (dict(phase="thinking", llm_model="claude-opus-5-5"), "思考中…（claude-opus-5-5）"),
    (dict(phase="output"), "交出成果，往下傳"),
    (dict(phase="output", error="e" * 40), "出錯了：" + "e" * 30),
    (dict(phase="verdict", passed=True), "檢查通過"),
    (dict(phase="verdict", passed=False, target_name="設計師"), "退件 → 設計師"),
])
def test_phase_text(kwargs, text):
    assert phase_text(**kwargs) == text


def test_phase_text_refuses_an_unknown_phase():
    with pytest.raises(ValueError):
        phase_text("dreaming")


# -- lineup events -----------------------------------------------------------------------------------

AGENT_VIEW_KEYS = ["id", "display_name", "duty", "kind", "look", "builtin", "llm", "effective_llm"]


def lineup_orchestrator(tmp_path, tasks):
    live: list[tuple[str, dict]] = []
    orch, events = make_orchestrator_with_repo(make_project(tmp_path, tasks), engine="langgraph",
                                               on_live_event=lambda e, d: live.append((e, d)))
    return orch, events, live


def test_lineup_events_when_applied_now(tmp_path):
    orch, events, live = lineup_orchestrator(tmp_path, [make_task()])
    lineup, agents = make_lineup("pm", ("designer", "make"), "swe", "qa")
    roster = [{"id": s, "display_name": s.upper(), "duty": "d", "kind": k, "look": "cap", "builtin": b, "llm": None,
               "effective_llm": {"provider": "mock", "model": "mock-model", "source": "project", "mock_reason": "configured_mock"}}
              for s, k, b in [("pm", "make", True), ("swe", "make", True), ("qa", "check", True),
                              ("designer", "make", False), ("writer", "make", False)]]

    asyncio.run(orch.request_lineup(lineup, agents, roster=roster))

    assert [e for e, _ in live] == ["agent:joined", "pipeline:updated"]
    joined, updated = live[0][1], live[1][1]
    assert list(joined) == ["agent", "pipeline_id", "position"]
    assert (joined["agent"]["id"], joined["pipeline_id"], joined["position"]) == ("designer", "main", 1)
    assert updated == {"pipeline": {"id": "main", "name": "主線", "seats": ["pm", "designer", "swe", "qa"]},
                       "agents": roster}  # the whole roster, unseated writer included
    assert events == []  # nothing on the regular channel

    live.clear()
    asyncio.run(orch.request_lineup(DEFAULT_LINEUP, {}))
    assert [e for e, _ in live] == ["agent:left", "pipeline:updated"]
    assert live[0][1] == {"agent_id": "designer", "pipeline_id": "main"}
    fallback = live[1][1]["agents"]
    assert [v["id"] for v in fallback] == ["pm", "swe", "qa"]
    assert all(list(v) == AGENT_VIEW_KEYS for v in fallback)


@langgraph_only
def test_lineup_events_when_deferred_then_applied(tmp_path):
    first, second = make_task("task-001", approval=Approval.AFTER_SWE), make_task("task-002")
    orch, events, live = lineup_orchestrator(tmp_path, [first, second])
    lineup, agents = make_lineup("pm", "swe", "qa", ("security", "check"))

    async def scenario():
        running = asyncio.create_task(orch.process_task(first))
        for _ in range(500):
            if orch.is_awaiting_approval(first.id):
                break
            await asyncio.sleep(0.005)
        assert await orch.request_lineup(lineup, agents) == "next_task"
        pending = [d for e, d in live if e == "pipeline:pending"]
        assert pending == [{"pipeline_id": "main", "seats": ["pm", "swe", "qa", "security"],
                            "reason": "task_in_flight", "task_id": "task-001"}]
        assert not [e for e, _ in live if e in ("agent:joined", "pipeline:updated")]
        orch.approve(first.id)
        await running
        await orch.process_task(second)

    asyncio.run(scenario())
    names = [e for e, _ in live if e != "agent:phase"]
    assert names == ["pipeline:pending", "agent:joined", "pipeline:updated"]
    joined = next(d for e, d in live if e == "agent:joined")
    assert (joined["agent"]["id"], joined["position"]) == ("security", 3)
    assert list(joined["agent"]) == AGENT_VIEW_KEYS
    # The joined event comes before the next task's first phase.
    order = [e for e, _ in live]
    first_phase_of_second = next(i for i, (e, d) in enumerate(live) if e == "agent:phase" and d["task_id"] == "task-002")
    assert order.index("agent:joined") < first_phase_of_second
    assert not any(e.startswith(("agent:", "pipeline:")) for e, _ in events)


def test_no_lineup_events_without_a_live_listener(tmp_path):
    orch, events = make_orchestrator_with_repo(make_project(tmp_path, [make_task()]), engine="langgraph")
    lineup, agents = make_lineup("pm", ("designer", "make"), "swe", "qa")
    assert asyncio.run(orch.request_lineup(lineup, agents)) == "now"
    assert events == []
