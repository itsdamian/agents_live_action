"""Routing factories for any lineup (Task 20, plan section 5.3, D17).

For the default lineup they must agree with the fixed three-station ``route_after_*`` functions
(which stay as the reference) and produce the same path maps; for custom lineups the C1-C6
branches of plan section 9 are pinned here.
"""

from __future__ import annotations

import itertools

import pytest
from langgraph.graph import END, START, StateGraph

from core.pipeline_graph import (
    PipelineState,
    gate_paths,
    gate_seat_index,
    make_gate_router,
    make_rework_router,
    make_seat_router,
    prev_maker,
    rework_paths,
    route_after_gate,
    route_after_pm,
    route_after_qa,
    route_after_rework,
    route_after_swe,
    seat_paths,
)
from models.lineup import BUILTIN_SEATS, DEFAULT_LINEUP, Lineup, Seat

PM, SWE, QA = BUILTIN_SEATS
DESIGNER = Seat("designer", "make", False, "設計師", "畫介面草圖", "beret")
SECURITY = Seat("security", "check", False, "資安", "找漏洞", "antenna")
WRITER = Seat("writer", "make", False, "寫手", "寫文件", "bow")

C1 = Lineup("main", (PM, DESIGNER, SWE, QA))
C2 = Lineup("main", (PM, SWE, QA, SECURITY))
C5 = Lineup("main", (PM, WRITER))

APPROVALS = ("none", "after_swe", "after_qa")


# -- equivalence with route_after_* on the default lineup ------------------------------------------


def states(**axes):
    keys = list(axes)
    for values in itertools.product(*axes.values()):
        yield dict(zip(keys, values))


@pytest.mark.parametrize("state", list(states(approval=APPROVALS, error=(None, "boom"))))
def test_pm_router_equals_route_after_pm(state):
    assert make_seat_router(DEFAULT_LINEUP, 0)(state) == route_after_pm(state)


@pytest.mark.parametrize("state", list(states(approval=APPROVALS, error=(None, "boom"))))
def test_swe_router_equals_route_after_swe(state):
    assert make_seat_router(DEFAULT_LINEUP, 1)(state) == route_after_swe(state)


@pytest.mark.parametrize("state", list(states(approval=APPROVALS, error=(None, "boom"), qa_passed=(True, False))))
def test_qa_router_equals_route_after_qa(state):
    assert make_seat_router(DEFAULT_LINEUP, 2)(state) == route_after_qa(state)


@pytest.mark.parametrize("state", list(states(decision=("approve", "reject"), gate_station=("swe", "qa"))))
def test_gate_router_equals_route_after_gate(state):
    assert make_gate_router(DEFAULT_LINEUP)(state) == route_after_gate(state)


@pytest.mark.parametrize("state", list(states(outcome=(None, "failed"), rework_target=("swe", None))))
def test_rework_router_equals_route_after_rework(state):
    assert make_rework_router(DEFAULT_LINEUP)(state) == route_after_rework(state)


def test_default_path_maps_are_the_current_ones():
    assert seat_paths(DEFAULT_LINEUP, 0) == ["swe", "fail"]
    assert seat_paths(DEFAULT_LINEUP, 1) == ["qa", "gate_enter", "fail"]
    assert seat_paths(DEFAULT_LINEUP, 2) == ["rework", "gate_enter", "done", "fail"]
    assert gate_paths(DEFAULT_LINEUP) == ["rework", "qa", "done"]
    assert rework_paths(DEFAULT_LINEUP) == ["swe", END]


# -- helpers ---------------------------------------------------------------------------------------


def test_gate_seat_index():
    assert gate_seat_index(DEFAULT_LINEUP, "none") is None
    assert gate_seat_index(DEFAULT_LINEUP, "after_swe") == 1  # last make
    assert gate_seat_index(DEFAULT_LINEUP, "after_qa") == 2  # last check
    assert gate_seat_index(C1, "after_swe") == 2  # swe is the last make, after designer
    assert gate_seat_index(C2, "after_qa") == 3  # security, the last check
    assert gate_seat_index(C5, "after_swe") == 1  # writer
    assert gate_seat_index(C5, "after_qa") == 1  # no check: the last seat
    assert gate_seat_index(Lineup("main", (PM, SECURITY, WRITER)), "after_qa") == 1


def test_prev_maker():
    assert prev_maker(DEFAULT_LINEUP, 2) == "swe"
    assert prev_maker(C2, 3) == "swe"  # security sends work back to the nearest make in front
    assert prev_maker(Lineup("main", (PM, SECURITY, QA)), 2) == "pm"
    assert prev_maker(C5, 1) == "writer"  # a make seat is its own rework target
    assert prev_maker(DEFAULT_LINEUP, 0) == "pm"


# -- C1-C6 branch tables (plan section 9) ----------------------------------------------------------


def route(lineup, i, **state):
    return make_seat_router(lineup, i)({"approval": "none", "error": None, **state})


def test_c1_designer_between_pm_and_swe():
    assert [route(C1, i, qa_passed=True) for i in range(4)] == ["designer", "swe", "qa", "done"]
    assert seat_paths(C1, 1) == ["swe", "fail"]
    assert route(C1, 1, error="boom") == "fail"


def test_c2_c3_security_check_at_the_end():
    assert [route(C2, i, qa_passed=True, check_passed=True) for i in range(4)] == ["swe", "qa", "security", "done"]
    assert route(C2, 3, qa_passed=True, check_passed=False) == "rework"
    assert route(C2, 2, qa_passed=False, check_passed=True) == "rework"
    assert seat_paths(C2, 2) == ["rework", "security", "fail"]
    assert seat_paths(C2, 3) == ["rework", "gate_enter", "done", "fail"]
    rework = make_rework_router(C2)
    assert rework({"rework_target": "swe"}) == "swe"
    assert rework({"rework_target": "swe", "outcome": "failed"}) == END
    assert rework_paths(C2) == ["swe", END]


def test_c4_after_qa_gate_at_security():
    assert route(C2, 2, approval="after_qa", qa_passed=True) == "security"  # qa is not the last check
    assert route(C2, 3, approval="after_qa", check_passed=True) == "gate_enter"
    gate = make_gate_router(C2)
    assert gate({"decision": "approve", "gate_station": "security"}) == "done"
    assert gate({"decision": "reject", "gate_station": "security"}) == "rework"
    assert gate_paths(C2) == ["rework", "qa", "done"]  # after_swe: swe -> qa; after_qa: security -> done


def test_c5_writer_only_make_after_qa():
    assert route(C5, 0) == "writer"
    assert route(C5, 1, approval="after_qa") == "gate_enter"
    assert route(C5, 1, approval="after_swe") == "gate_enter"
    assert route(C5, 1) == "done"
    assert seat_paths(C5, 1) == ["done", "gate_enter", "fail"]
    assert make_gate_router(C5)({"decision": "approve", "gate_station": "writer"}) == "done"
    assert gate_paths(C5) == ["rework", "done"]
    assert rework_paths(C5) == ["writer", END]


def test_c6_after_swe_with_designer():
    assert route(C1, 1, approval="after_swe") == "swe"  # designer is not the last make
    assert route(C1, 2, approval="after_swe") == "gate_enter"
    gate = make_gate_router(C1)
    assert gate({"decision": "approve", "gate_station": "swe"}) == "qa"
    assert gate({"decision": "reject", "gate_station": "swe"}) == "rework"
    assert rework_paths(C1) == ["swe", END]


def test_rework_targets_of_a_lineup_with_two_makers_and_a_gate_on_the_first_check():
    lineup = Lineup("main", (PM, DESIGNER, SECURITY, SWE, QA))
    # security -> designer, qa -> swe; gates: after_swe at swe, after_qa at qa (both reworked to swe)
    assert rework_paths(lineup) == ["designer", "swe", END]
    assert gate_paths(lineup) == ["rework", "qa", "done"]


def test_every_routed_target_is_in_its_path_map():
    lineups = [DEFAULT_LINEUP, C1, C2, C5, Lineup("main", (PM, DESIGNER, SECURITY, SWE, QA)),
               Lineup("main", (WRITER, SECURITY))]
    for lineup in lineups:
        for i in range(len(lineup.seats)):
            router, paths = make_seat_router(lineup, i), seat_paths(lineup, i)
            for state in states(approval=APPROVALS, error=(None, "x"), qa_passed=(True, False),
                                check_passed=(True, False)):
                assert router(state) in paths, (lineup.ids, i, state)
        gate = make_gate_router(lineup)
        for approval in APPROVALS[1:]:
            g = gate_seat_index(lineup, approval)
            for decision in ("approve", "reject"):
                assert gate({"decision": decision, "gate_station": lineup.ids[g]}) in gate_paths(lineup)


# -- node names with "-" (plan section 15) ---------------------------------------------------------


def test_node_names_with_a_dash_compile_route_and_draw():
    ux = Seat("ux-writer", "make", False, "文案", "寫文案", "bow")
    lineup = Lineup("main", (PM, ux))

    async def noop(state):
        return {}

    graph = StateGraph(PipelineState)
    for name in ("pm", "ux-writer", "gate_enter", "done", "fail"):
        graph.add_node(name, noop)
    graph.add_edge(START, "pm")
    for i in range(2):
        graph.add_conditional_edges(lineup.ids[i], make_seat_router(lineup, i), seat_paths(lineup, i))
    graph.add_edge("gate_enter", "done")
    graph.add_edge("done", END)
    graph.add_edge("fail", END)
    compiled = graph.compile()

    mermaid = compiled.get_graph().draw_mermaid()
    assert "pm -.-> ux-writer;" in mermaid
    assert "ux-writer -.-> done;" in mermaid

    import asyncio

    final = asyncio.run(compiled.ainvoke({"approval": "none", "error": None}))
    assert final["approval"] == "none"
