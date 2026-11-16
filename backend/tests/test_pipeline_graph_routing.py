"""Table-driven tests for the graph's conditional-edge functions (no graph run)."""

from __future__ import annotations

import pytest
from langgraph.graph import END

from core.pipeline_graph import (
    initial_state,
    route_after_gate,
    route_after_pm,
    route_after_qa,
    route_after_rework,
    route_after_swe,
)
from models.task import Approval
from tests.pipeline_harness import make_task


@pytest.mark.parametrize(
    "state, expected",
    [
        ({"error": "PM agent failed"}, "fail"),
        ({"error": None}, "swe"),
        ({}, "swe"),
    ],
)
def test_route_after_pm(state, expected):
    assert route_after_pm(state) == expected


@pytest.mark.parametrize(
    "state, expected",
    [
        ({"approval": "none", "error": "boom"}, "fail"),
        ({"approval": "after_swe", "error": "boom"}, "fail"),  # error wins over the gate
        ({"approval": "after_swe", "error": None}, "gate_enter"),
        ({"approval": "none", "error": None}, "qa"),
        ({"approval": "after_qa", "error": None}, "qa"),  # that gate comes after QA
    ],
)
def test_route_after_swe(state, expected):
    assert route_after_swe(state) == expected


@pytest.mark.parametrize(
    "state, expected",
    [
        ({"approval": "none", "error": "boom", "qa_passed": True}, "fail"),
        ({"approval": "none", "error": None, "qa_passed": False}, "rework"),
        ({"approval": "after_qa", "error": None, "qa_passed": False}, "rework"),
        ({"approval": "after_qa", "error": None, "qa_passed": True}, "gate_enter"),
        ({"approval": "none", "error": None, "qa_passed": True}, "done"),
        ({"approval": "after_swe", "error": None, "qa_passed": True}, "done"),
    ],
)
def test_route_after_qa(state, expected):
    assert route_after_qa(state) == expected


@pytest.mark.parametrize(
    "state, expected",
    [
        ({"decision": "reject", "gate_station": "swe"}, "rework"),
        ({"decision": "reject", "gate_station": "qa"}, "rework"),
        ({"decision": "approve", "gate_station": "swe"}, "qa"),
        ({"decision": "approve", "gate_station": "qa"}, "done"),
    ],
)
def test_route_after_gate(state, expected):
    assert route_after_gate(state) == expected


@pytest.mark.parametrize(
    "state, expected",
    [
        ({"outcome": "failed"}, END),  # _enter_rework already failed the task
        ({"outcome": None}, "swe"),
    ],
)
def test_route_after_rework(state, expected):
    assert route_after_rework(state) == expected


def test_initial_state_mirrors_task():
    task = make_task(approval=Approval.AFTER_QA, rework_count=2)
    task.qa_output = "old report"

    assert initial_state(task) == {
        "task_id": "task-001",
        "approval": "after_qa",
        "rework_count": 2,
        "deliverables": [],
        "approval_feedback": None,
        "qa_output": "old report",
        "pm_output": None,
        "swe_output": None,
        "error": None,
        "outcome": None,
    }
