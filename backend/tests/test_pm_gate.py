"""The built-in PM seat can hold the approval gate (QA gate C, item 2; plan D17).

``after_swe`` pauses after the last make seat and ``after_qa`` after the last check, or after
the last seat when there is none. With PM as the only make seat (``(pm, qa)``, ``(pm,)``) the
gate is PM's, so PM's node has to record ``gate_station`` like every other seat.
"""

from __future__ import annotations

import asyncio

import pytest

from models.lineup import Lineup
from models.task import Approval
from tests.hud_harness import make_lineup, make_orchestrator_with_repo
from tests.pipeline_harness import make_project, make_task, run_with_gate_actions

CASES = {
    "pm_qa_after_swe": (("pm", "qa"), Approval.AFTER_SWE, ["pm", "qa"]),
    "pm_after_swe": (("pm",), Approval.AFTER_SWE, ["pm"]),
    "pm_after_qa": (("pm",), Approval.AFTER_QA, ["pm"]),
}


def _run(tmp_path, seats, approval, actions):
    task = make_task(approval=approval)
    orch, events = make_orchestrator_with_repo(make_project(tmp_path, [task]), engine="langgraph")
    lineup, agents = make_lineup(*seats)
    assert asyncio.run(orch.request_lineup(lineup, agents)) == "now"
    result = asyncio.run(run_with_gate_actions(orch, task, actions))
    return result, events


def _gates(events):
    return [(e, d["station"]) for e, d in events if e in ("task:awaiting_approval", "task:approved", "task:rejected")]


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_gate_after_pm_is_entered_and_approved(tmp_path, name):
    seats, approval, working = CASES[name]
    result, events = _run(tmp_path, seats, approval, [("approve", "")])

    assert (result.status.value, result.rework_count) == ("done", 0)
    assert _gates(events) == [("task:awaiting_approval", "pm"), ("task:approved", "pm")]
    assert [d["station"] for e, d in events if e == "station:update" and d["status"] == "working"] == working


def test_a_reject_at_pms_gate_sends_the_work_back_to_pm(tmp_path):
    result, events = _run(tmp_path, ("pm", "qa"), Approval.AFTER_SWE, [("reject", "split it up"), ("approve", "")])

    assert (result.status.value, result.rework_count) == ("done", 1)
    assert _gates(events) == [("task:awaiting_approval", "pm"), ("task:rejected", "pm"),
                              ("task:awaiting_approval", "pm"), ("task:approved", "pm")]
    assert [d["station"] for e, d in events if e == "station:update" and d["status"] == "rework"] == ["pm"]
    assert [d["station"] for e, d in events if e == "station:update" and d["status"] == "working"] == [
        "pm", "pm", "qa"]


def test_pm_failing_after_its_gate_rejects_has_the_pm_reason(tmp_path):
    task = make_task(approval=Approval.AFTER_QA, max_reworks=0)
    orch, events = make_orchestrator_with_repo(make_project(tmp_path, [task]), engine="langgraph")
    lineup, agents = make_lineup("pm")
    asyncio.run(orch.request_lineup(lineup, agents))
    result = asyncio.run(run_with_gate_actions(orch, task, [("reject", "no")]))

    assert result.status.value == "failed"
    assert [d["reason"] for e, d in events if e == "task:failed"] == ["Rejected at approval gate (after PM)"]


def test_these_lineups_pass_validation(tmp_path):
    from models.lineup import lineup_errors

    for seats, _, _ in CASES.values():
        lineup, _ = make_lineup(*seats)
        assert isinstance(lineup, Lineup) and lineup_errors(lineup) == []
