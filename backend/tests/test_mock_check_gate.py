"""MOCK_CHECK_FAIL_FIRST and the approval gate (plan section 4.4, 2026-11-23 ruling).

The Mock check fails until its prompt carries rejection feedback: a ``[退件意見]`` section with its
own earlier report or with the note of a human who rejected the work at the gate
(``approval_feedback``; a check looking again at rejected work gets it as ``<reviewer-note>``).
So a gate reject costs exactly one rework. The Mock renderers themselves are unchanged.
"""

from __future__ import annotations

import asyncio

import pytest

import config
from agents.custom_agent import parse_verdict
from core.llm_client import MockLLMClient
from models.task import Approval
from tests.hud_harness import make_lineup
from tests.pipeline_harness import make_orchestrator, make_project, make_task, run_with_gate_actions

langgraph_only = pytest.mark.skipif(
    config.get_pipeline_engine() != "langgraph", reason="custom lineups need PIPELINE_ENGINE=langgraph"
)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MOCK_LLM_DELAY", "0")
    monkeypatch.setenv("MOCK_CHECK_FAIL_FIRST", "true")


def _reworks(events):
    return [d["station"] for e, d in events if e == "station:update" and d["status"] == "rework"]


def _run(tmp_path, actions, max_reworks):
    task = make_task(approval=Approval.AFTER_QA, max_reworks=max_reworks)
    orch, events = make_orchestrator(make_project(tmp_path, [task]), engine="langgraph")
    lineup, agents = make_lineup("pm", "swe", "qa", ("security", "check"))
    asyncio.run(orch.request_lineup(lineup, agents))
    result = asyncio.run(run_with_gate_actions(orch, task, actions))
    return result, events, agents["security"].llm_client


@langgraph_only
def test_one_gate_reject_costs_one_rework(tmp_path):
    result, events, security = _run(tmp_path, [("reject", "tighten the login checks"), ("approve", "")], 3)

    assert (result.status.value, result.rework_count) == ("done", 2)  # 1 from the first FAIL, 1 from the reject
    assert _reworks(events) == ["swe", "swe"]
    assert [parse_verdict(MockLLMClient(role_key="check").call(p)).passed for p in security.prompts] == [
        False, True, True,
    ]
    # Re-checking after the reject, the check's prompt carries the reviewer's note (plan 4.4).
    assert "[退件意見]\n<reviewer-note>\ntighten the login checks\n</reviewer-note>" in security.prompts[2]


@langgraph_only
def test_two_gate_rejects_with_three_reworks_allowed_still_finish(tmp_path):
    result, events, _ = _run(tmp_path, [("reject", "first note"), ("reject", "second note"), ("approve", "")], 3)

    assert (result.status.value, result.rework_count) == ("done", 3)
    assert "task:failed" not in [e for e, _ in events]


def test_the_mock_check_passes_on_a_reviewer_note(monkeypatch):
    prompt = "[任務]\n...\n[退件意見]\n<reviewer-note>\nadd validation\n</reviewer-note>\n"
    assert parse_verdict(MockLLMClient(role_key="check").call(prompt)).passed
    assert not parse_verdict(MockLLMClient(role_key="check").call("[任務]\n...")).passed
