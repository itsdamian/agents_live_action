"""The compiled graph has the ten nodes drawn in plan.md's flowchart."""

from __future__ import annotations

from tests.pipeline_harness import make_orchestrator, make_project, make_task

NODES = [
    "intake",
    "pm",
    "swe",
    "qa",
    "gate_enter",
    "gate_wait",
    "gate_exit",
    "rework",
    "done",
    "fail",
]


def test_graph_mermaid_contains_all_nodes(tmp_path):
    project = make_project(tmp_path, [make_task()])
    orch, _events = make_orchestrator(project, engine="langgraph")

    # LangGraph 學習: get_graph().draw_mermaid() 把 compiled graph 畫成 mermaid 流程圖。
    mermaid = orch._graph_runner._graph.get_graph().draw_mermaid()

    for node in NODES:
        assert node in mermaid, f"node {node!r} missing from graph"
