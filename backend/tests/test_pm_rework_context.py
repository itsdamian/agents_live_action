"""Work sent back to the built-in PM carries the feedback (plan 5.1, 2026-11-23 ruling).

When PM is the maker a reject or a check's FAIL goes back to, its context gets the non-empty
``approval_feedback`` / ``review_feedback`` (``pm_agent.py`` prints every key of its context).
The default lineup never sends work back to PM, so its context there is unchanged (the
characterization and golden tests cover that).
"""

from __future__ import annotations

import asyncio

import pytest

import config
from models.task import Approval
from tests.hud_harness import make_lineup
from tests.pipeline_harness import ScriptedClient, make_orchestrator, make_project, make_task, run_with_gate_actions

pytestmark = pytest.mark.skipif(
    config.get_pipeline_engine() != "langgraph", reason="custom lineups need PIPELINE_ENGINE=langgraph"
)

SECURITY_FAIL = "- secrets in the plan\nVERDICT: FAIL"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MOCK_LLM_DELAY", "0")
    monkeypatch.delenv("MOCK_CHECK_FAIL_FIRST", raising=False)


def _orchestrator(tmp_path, task, *specs, scripts=None):
    pm = ScriptedClient("Project Manager", "PM_agent")
    orch, events = make_orchestrator(make_project(tmp_path, [task]), engine="langgraph", pm_client=pm)
    lineup, agents = make_lineup(*specs, scripts=scripts)
    asyncio.run(orch.request_lineup(lineup, agents))
    return orch, pm


def test_a_gate_reject_sent_back_to_pm_reaches_its_prompt(tmp_path):
    task = make_task(approval=Approval.AFTER_QA)
    orch, pm = _orchestrator(tmp_path, task, "pm", "qa")

    result = asyncio.run(run_with_gate_actions(orch, task, [("reject", "split the plan by milestone"), ("approve", "")]))

    assert (result.status.value, result.rework_count) == ("done", 1)
    assert len(pm.prompts) == 2
    assert "approval_feedback" not in pm.prompts[0]
    assert "- approval_feedback: split the plan by milestone" in pm.prompts[1]


def test_a_check_fail_sent_back_to_pm_reaches_its_prompt(tmp_path):
    task = make_task()
    orch, pm = _orchestrator(tmp_path, task, "pm", ("security", "check"), scripts={"security": [SECURITY_FAIL]})

    result = asyncio.run(orch.process_task(task))

    assert (result.status.value, result.rework_count) == ("done", 1)
    assert len(pm.prompts) == 2
    assert "review_feedback" not in pm.prompts[0]
    assert "- review_feedback: - secrets in the plan\nVERDICT: FAIL" in pm.prompts[1]


def test_the_feedback_is_used_once(tmp_path):
    """After PM has redone the work, a later lap does not hand it the old note again."""

    task = make_task(approval=Approval.AFTER_QA, max_reworks=3)
    orch, pm = _orchestrator(tmp_path, task, "pm", "qa")

    asyncio.run(run_with_gate_actions(orch, task, [("reject", "first"), ("reject", "second"), ("approve", "")]))

    assert len(pm.prompts) == 3
    assert "approval_feedback: first" in pm.prompts[1] and "approval_feedback: second" in pm.prompts[2]
    assert "first" not in pm.prompts[2]
