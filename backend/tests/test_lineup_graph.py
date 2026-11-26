"""build_lineup_graph: custom seats, cache, recursion limit (Task 21, plan 5.1, 5.2).

The C1-C6 scenarios with recorded event anchors are Task 22 (tests/test_dynamic_lineup.py);
this file pins the graph itself and runs two short smoke checks through private hooks.
"""

from __future__ import annotations

import asyncio

import pytest

from agents.custom_agent import CustomAgent, custom_agent_config
from core.pipeline_graph import build_graph, build_lineup_graph
from models.lineup import BUILTIN_SEATS, DEFAULT_LINEUP, Lineup, Seat
from tests.pipeline_harness import ScriptedClient, make_orchestrator, make_project, make_task

PM, SWE, QA = BUILTIN_SEATS
DESIGNER = Seat("designer", "make", False, "設計師", "畫介面草圖", "beret")
SECURITY = Seat("security", "check", False, "資安", "找漏洞", "antenna")
C1 = Lineup("main", (PM, DESIGNER, SWE, QA))
C2 = Lineup("main", (PM, SWE, QA, SECURITY))


def orchestrator(tmp_path, *, swe_client=None):
    project = make_project(tmp_path, [make_task()])
    return make_orchestrator(project, engine="langgraph", swe_client=swe_client)


def mermaid_of(graph) -> str:
    return graph.compile().get_graph().draw_mermaid()


def edges(text: str) -> set[str]:
    return {line.strip() for line in text.splitlines() if "-->" in line or "-.->" in line}


def test_build_graph_is_the_default_lineup(tmp_path):
    orch, _ = orchestrator(tmp_path)
    runner = orch._graph_runner
    assert mermaid_of(build_graph(runner)) == mermaid_of(build_lineup_graph(runner, DEFAULT_LINEUP))


def test_c1_graph_has_the_designer_seat_between_pm_and_swe(tmp_path):
    orch, _ = orchestrator(tmp_path)
    text = mermaid_of(build_lineup_graph(orch._graph_runner, C1))
    assert "\tdesigner(designer)" in text
    assert {"intake --> pm;", "pm -.-> designer;", "pm -.-> fail;", "designer -.-> swe;",
            "designer -.-> fail;", "swe -.-> qa;", "swe -.-> gate_enter;"} <= edges(text)
    assert "pm -.-> swe;" not in edges(text)


def test_c2_graph_routes_security_failures_to_rework(tmp_path):
    orch, _ = orchestrator(tmp_path)
    found = edges(mermaid_of(build_lineup_graph(orch._graph_runner, C2)))
    assert {"qa -.-> security;", "qa -.-> rework;", "security -.-> rework;", "security -.-> done;",
            "security -.-> gate_enter;", "gate_exit -.-> qa;", "gate_exit -.-> done;", "rework -.-> swe;"} <= found
    assert "qa -.-> done;" not in found and "qa -.-> gate_enter;" not in found


def test_compiled_graphs_are_cached_by_signature(tmp_path):
    orch, _ = orchestrator(tmp_path)
    runner = orch._graph_runner
    default_graph = runner._graph
    runner.use_lineup(C1)
    c1_graph = runner._graph
    assert c1_graph is not default_graph and runner.lineup == C1
    renamed = Lineup("main", (PM, Seat("designer", "make", False, "設計", "別的職責", "cap"), SWE, QA))
    runner.use_lineup(renamed)
    assert runner._graph is c1_graph  # same ids and kinds: same graph
    runner.use_lineup(DEFAULT_LINEUP)
    assert runner._graph is default_graph


def test_switching_the_checkpointer_recompiles_the_current_lineup(tmp_path):
    from langgraph.checkpoint.memory import InMemorySaver

    orch, _ = orchestrator(tmp_path)
    runner = orch._graph_runner
    runner.use_lineup(C1)
    before = runner._graph
    saver = InMemorySaver()
    orch.use_checkpointer(saver)
    assert runner._graph is not before and runner.lineup == C1
    assert runner._graph.checkpointer is saver


def test_lineup_can_not_change_while_a_task_runs(tmp_path):
    orch, _ = orchestrator(tmp_path)
    orch._graph_runner._live_tasks["task-001"] = make_task()
    with pytest.raises(RuntimeError):
        orch._graph_runner.use_lineup(C1)


@pytest.mark.parametrize(("seats", "reworks", "limit"), [
    (3, 3, 20 + 12 * 4),  # the default lineup keeps its old limit
    (3, 0, 20 + 12),
    (4, 3, 20 + 13 * 4),
    (8, 2, 20 + 17 * 3),
])
def test_recursion_limit(tmp_path, seats, reworks, limit):
    orch, _ = orchestrator(tmp_path)
    makers = tuple(Seat(f"m{i}x", "make", False, f"m{i}", "d", "cap") for i in range(seats - 3))
    lineup = Lineup("main", (PM, *makers, SWE, QA)) if seats > 3 else DEFAULT_LINEUP
    orch._graph_runner.use_lineup(lineup)
    assert orch._graph_runner._config(make_task(max_reworks=reworks))["recursion_limit"] == limit


# -- smoke runs through the private hooks (the public API is Task 22) ------------------------------


def custom(seat, reply_script=None):
    client = ScriptedClient("custom", seat.id, reply_script)
    client.role_key = seat.kind
    return CustomAgent(seat, custom_agent_config(seat, f"you are {seat.id}"), llm_client=client,
                       retry_wait_multiplier=0)


def test_c1_smoke_designer_output_reaches_swe(tmp_path):
    swe_client = ScriptedClient("Software Engineer", "SWE_agent")
    orch, events = orchestrator(tmp_path, swe_client=swe_client)
    orch._agents["designer"] = custom(DESIGNER, ["WIREFRAME-42"])
    orch._graph_runner.use_lineup(C1)

    task = asyncio.run(orch.process_task(make_task()))

    assert task.status.value == "done"
    working = [d["station"] for e, d in events if e == "station:update" and d["status"] == "working"]
    assert working == ["pm", "designer", "swe", "qa"]
    assert "designer_output: WIREFRAME-42" in swe_client.prompts[0]


def test_c2_smoke_security_failure_sends_work_back_to_swe(tmp_path):
    swe_client = ScriptedClient("Software Engineer", "SWE_agent")
    orch, events = orchestrator(tmp_path, swe_client=swe_client)
    orch._agents["security"] = custom(SECURITY, ["- SQL injection\nVERDICT: FAIL"])
    orch._graph_runner.use_lineup(C2)

    task = asyncio.run(orch.process_task(make_task()))

    assert task.status.value == "done" and task.rework_count == 1
    reworks = [d for e, d in events if e == "station:update" and d["status"] == "rework"]
    assert [d["station"] for d in reworks] == ["swe"]
    assert "review_feedback: - SQL injection" in swe_client.prompts[1]
    security_prompts = orch._agents["security"].llm_client.prompts
    assert "[退件意見]" not in security_prompts[0]
    assert '[退件意見]\n<upstream agent="security">\n- SQL injection' in security_prompts[1]


def test_rework_from_an_old_checkpoint_without_a_target_goes_to_swe(tmp_path):
    orch, events = orchestrator(tmp_path)
    runner = orch._graph_runner
    task = make_task()
    runner._live_tasks[task.id] = task
    asyncio.run(runner.rework({"task_id": task.id, "rework_reason": "QA still failing after 3 reworks"}))
    assert [(e, d["station"], d["status"]) for e, d in events] == [("station:update", "swe", "rework")]
