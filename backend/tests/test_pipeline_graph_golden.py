"""Golden mermaid of the default pipeline graph (Task 5, AC12).

``tests/fixtures/pipeline_graph_default.mmd`` is ``draw_mermaid()`` of the default
three-station graph exactly as it was before the dynamic lineup work. The dynamic
graph builder must produce the identical text for the default lineup, node by node
and edge by edge. If this fails, the default pipeline's shape changed.

To regenerate on purpose (only after the change was reviewed and approved)::

    cd backend && .venv/bin/python - <<'PY'
    import tempfile, pathlib
    from tests.pipeline_harness import make_orchestrator, make_project, make_task
    orch, _ = make_orchestrator(make_project(pathlib.Path(tempfile.mkdtemp()), [make_task()]), engine="langgraph")
    pathlib.Path("tests/fixtures/pipeline_graph_default.mmd").write_text(
        orch._graph_runner._graph.get_graph().draw_mermaid(), encoding="utf-8", newline="")
    PY
"""

from __future__ import annotations

from pathlib import Path

from tests.pipeline_harness import make_orchestrator, make_project, make_task

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "pipeline_graph_default.mmd"

NODES = {
    "intake", "pm", "swe", "qa", "gate_enter", "gate_wait", "gate_exit",
    "rework", "done", "fail", "__start__", "__end__",
}


def _mermaid(tmp_path) -> str:
    project = make_project(tmp_path, [make_task()])
    orch, _events = make_orchestrator(project, engine="langgraph")
    return orch._graph_runner._graph.get_graph().draw_mermaid()


def _edges(text: str) -> list[tuple[str, str, bool]]:
    """(source, target, conditional) for every edge line in the mermaid text."""

    edges = []
    for line in text.splitlines():
        line = line.strip()
        for arrow, conditional in (("-.->", True), ("-->", False)):
            if f" {arrow} " in line:
                source, target = line.rstrip(";").split(f" {arrow} ")
                edges.append((source, target, conditional))
                break
    return edges


def test_default_graph_matches_the_golden_mermaid_exactly(tmp_path):
    golden = GOLDEN.read_bytes().decode("utf-8")  # bytes: keep tabs and the final newline

    assert _mermaid(tmp_path) == golden


def test_golden_has_the_expected_shape(tmp_path):
    """A readable cross-check of the fixture itself (20 edges: 6 fixed, 14 conditional)."""

    edges = _edges(GOLDEN.read_bytes().decode("utf-8"))

    assert len(edges) == 20
    assert sum(1 for *_, conditional in edges if conditional) == 14
    assert {node for source, target, _ in edges for node in (source, target)} == NODES
    assert sorted((s, t) for s, t, conditional in edges if not conditional) == [
        ("__start__", "intake"),
        ("done", "__end__"),
        ("fail", "__end__"),
        ("gate_enter", "gate_wait"),
        ("gate_wait", "gate_exit"),
        ("intake", "pm"),
    ]
    # The conditional fan-out of each routing node, as in plan section 5.3.
    fan_out = {}
    for source, target, conditional in edges:
        if conditional:
            fan_out.setdefault(source, set()).add(target)
    assert fan_out == {
        "pm": {"swe", "fail"},
        "swe": {"qa", "gate_enter", "fail"},
        "qa": {"rework", "gate_enter", "done", "fail"},
        "gate_exit": {"rework", "qa", "done"},
        "rework": {"swe", "__end__"},
    }
