"""A gate reject with an empty note still costs one rework (plan section 4.4, 2026-11-23 ruling).

Re-checking work a human rejected at the gate, the check's prompt carries ``[退件意見]`` even when
the note is blank; the blank note reads as a fixed sentence.
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

EMPTY_NOTE = "[退件意見]\n<reviewer-note>\n（審核者沒有留下意見）\n</reviewer-note>"


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
@pytest.mark.parametrize("note", ["", "   "])
def test_one_empty_note_gate_reject_costs_one_rework(tmp_path, note):
    result, events, security = _run(tmp_path, [("reject", note), ("approve", "")], 3)

    assert (result.status.value, result.rework_count) == ("done", 2)  # 1 from the first FAIL, 1 from the reject
    assert _reworks(events) == ["swe", "swe"]
    assert [parse_verdict(MockLLMClient(role_key="check").call(p)).passed for p in security.prompts] == [
        False, True, True,
    ]
    assert EMPTY_NOTE in security.prompts[2]


@langgraph_only
def test_two_empty_note_gate_rejects_with_three_reworks_allowed_still_finish(tmp_path):
    result, events, _ = _run(tmp_path, [("reject", ""), ("reject", ""), ("approve", "")], 3)

    assert (result.status.value, result.rework_count) == ("done", 3)
    assert "task:failed" not in [e for e, _ in events]
