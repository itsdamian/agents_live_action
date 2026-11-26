"""Custom lineups C1-C6 (plan section 9) and request_lineup timing (Task 22, D17, AC14, AC18).

The expected signatures were printed from a run first, checked line by line against the anchors
of plan section 9, then written down here:

* C1 every seat goes working -> done in order; the SWE prompt contains ``designer_output``.
* C2 after security is done: ``station:update {station: "swe", status: "rework"}`` with
  rework_count 1; swe -> qa -> security run again; SWE's second prompt has ``review_feedback``.
* C3 ``task:failed`` reason ``SECURITY still failing after 1 reworks``.
* C4 the gate's station is ``security``.
* C5 the gate's station is ``writer``; a reject sends the work back to ``writer``.
* C6 the gate's station is ``swe``; a reject sends the work back to ``swe``.

The legacy engine only runs the default lineup, so the scenarios are skipped under
``PIPELINE_ENGINE=legacy``; what legacy does with a custom lineup is tested separately.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

import config
from agents.custom_agent import CHECK_SYSTEM_SUFFIX
from core.stores import PipelineRecord
from core.stores.memory import InMemoryLineupRepository
from models.lineup import DEFAULT_LINEUP
from models.task import Approval
from tests.hud_harness import make_lineup
from tests.pipeline_harness import (
    ScriptedClient,
    make_orchestrator,
    make_project,
    make_task,
    run_with_gate_actions,
    signatures,
)

ENGINE = config.get_pipeline_engine()
langgraph_only = pytest.mark.skipif(
    ENGINE != "langgraph", reason="custom lineups need PIPELINE_ENGINE=langgraph (legacy runs the default lineup)"
)

SECURITY_FAIL = "- SQL injection in login\nVERDICT: FAIL"
T = "task-001"

# -- signature builders (pipeline_harness.event_signature) ----------------------------------------

DONE_KEYS = ("error", "execution_time", "station", "status", "success", "task")


def created():
    return ("task:created", ("queue_size", "task"), None, None, T, "pending", 0, 0)


def working(station, rc=0):
    return ("station:update", ("station", "status", "task"), station, "working", T, "in_progress", rc)


def done(station, rc=0):
    return ("station:update", DONE_KEYS, station, "done", T, "in_progress", rc)


def run_seat(station, rc=0):
    return [working(station, rc), done(station, rc)]


def rework(station, rc):
    return ("station:update", ("station", "status", "task"), station, "rework", T, "in_progress", rc)


def awaiting(station, rc=0):
    return ("task:awaiting_approval", ("approval", "station", "task"), station, None, T, "awaiting_approval", rc)


def approved(station, rc=0):
    return ("task:approved", ("station", "task"), station, None, T, "in_progress", rc)


def rejected(station, note, rc=0):
    return ("task:rejected", ("note", "station", "task"), station, None, T, "in_progress", rc, note)


def completed(rc, deliverables):
    return ("task:completed", ("deliverables", "done_count", "task"), None, None, T, "done", rc, 1, deliverables)


def failed(rc, reason):
    return ("task:failed", ("failed_count", "reason", "task"), None, None, T, "failed", rc, 1, reason)


def seats(*ids, rc=0):
    return [sig for station in ids for sig in run_seat(station, rc)]


SCENARIOS = {
    "C1": dict(
        specs=("pm", ("designer", "make"), "swe", "qa"),
        expected=[created(), *seats("pm", "designer", "swe", "qa"),
                  completed(0, ("01_PM_agent.md", "02_designer.md", "03_SWE_agent.md", "03_SWE_agent_1.py", "04_QA_agent.md"))],
    ),
    "C2": dict(
        specs=("pm", "swe", "qa", ("security", "check")),
        scripts={"security": [SECURITY_FAIL]},
        expected=[created(), *seats("pm", "swe", "qa", "security"), rework("swe", 1), *seats("swe", "qa", "security", rc=1),
                  completed(1, ("01_PM_agent.md", "02_SWE_agent.md", "02_SWE_agent_1.py", "03_QA_agent.md", "04_security.md",
                                "05_SWE_agent.md", "05_SWE_agent_1.py", "05_SWE_agent_2.py", "06_QA_agent.md", "07_security.md"))],
    ),
    "C3": dict(
        task={"max_reworks": 1},
        specs=("pm", "swe", "qa", ("security", "check")),
        scripts={"security": [SECURITY_FAIL] * 5},
        expected=[created(), *seats("pm", "swe", "qa", "security"), rework("swe", 1), *seats("swe", "qa", "security", rc=1),
                  failed(2, "SECURITY still failing after 1 reworks")],
    ),
    "C4": dict(
        task={"approval": Approval.AFTER_QA},
        actions=[("approve", "")],
        specs=("pm", "swe", "qa", ("security", "check")),
        expected=[created(), *seats("pm", "swe", "qa", "security"), awaiting("security"), approved("security"),
                  completed(0, ("01_PM_agent.md", "02_SWE_agent.md", "02_SWE_agent_1.py", "03_QA_agent.md", "04_security.md"))],
    ),
    "C5": dict(
        task={"approval": Approval.AFTER_QA},
        actions=[("reject", "more examples"), ("approve", "")],
        specs=("pm", ("writer", "make")),
        expected=[created(), *seats("pm", "writer"), awaiting("writer"), rejected("writer", "more examples"),
                  rework("writer", 1), *seats("writer", rc=1), awaiting("writer", 1), approved("writer", 1),
                  completed(1, ("01_PM_agent.md", "02_writer.md", "03_writer.md"))],
    ),
    "C6": dict(
        task={"approval": Approval.AFTER_SWE},
        actions=[("reject", "add validation"), ("approve", "")],
        specs=("pm", ("designer", "make"), "swe", "qa"),
        expected=[created(), *seats("pm", "designer", "swe"), awaiting("swe"), rejected("swe", "add validation"),
                  rework("swe", 1), *seats("swe", rc=1), awaiting("swe", 1), approved("swe", 1), *seats("qa", rc=1),
                  completed(1, ("01_PM_agent.md", "02_designer.md", "03_SWE_agent.md", "03_SWE_agent_1.py",
                                "04_SWE_agent.md", "04_SWE_agent_1.py", "05_QA_agent.md"))],
    ),
}


def run_scenario(tmp_path, name):
    spec = SCENARIOS[name]
    task = make_task(**spec.get("task", {}))
    project = make_project(tmp_path, [task])
    swe = ScriptedClient("Software Engineer", "SWE_agent")
    orch, events = make_orchestrator(project, engine=ENGINE, swe_client=swe)
    lineup, agents = make_lineup(*spec["specs"], scripts=spec.get("scripts"))
    assert asyncio.run(orch.request_lineup(lineup, agents)) == "now"
    if spec.get("actions"):
        result = asyncio.run(run_with_gate_actions(orch, task, spec["actions"]))
    else:
        result = asyncio.run(orch.process_task(task))
    return result, events, swe, agents, orch


@langgraph_only
@pytest.mark.parametrize("name", list(SCENARIOS))
def test_scenario_signatures(tmp_path, name):
    _, events, _, _, _ = run_scenario(tmp_path, name)
    assert signatures(events) == SCENARIOS[name]["expected"]


@langgraph_only
def test_c1_swe_prompt_contains_the_designer_output(tmp_path):
    _, _, swe, agents, orch = run_scenario(tmp_path, "C1")
    assert len(agents["designer"].llm_client.prompts) == 1
    assert "- designer_output: [MOCK MODE] Work output" in swe.prompts[0]
    assert list(orch.station_states) == ["pm", "designer", "swe", "qa"]


@langgraph_only
def test_c2_feedback_reaches_swe_and_the_check_sees_its_report_again(tmp_path):
    result, _, swe, agents, _ = run_scenario(tmp_path, "C2")
    assert result.rework_count == 1
    assert "review_feedback" not in swe.prompts[0]
    assert "- review_feedback: - SQL injection in login" in swe.prompts[1]
    security = agents["security"].llm_client
    assert security.system_prompts[0] == "You are security.\n\n" + CHECK_SYSTEM_SUFFIX
    assert "[退件意見]" not in security.prompts[0]
    assert '[退件意見]\n<upstream agent="security">\n- SQL injection in login' in security.prompts[1]


@langgraph_only
def test_c5_reject_note_reaches_the_writer(tmp_path):
    _, _, _, agents, _ = run_scenario(tmp_path, "C5")
    writer = agents["writer"].llm_client
    assert "[退件意見]" not in writer.prompts[0]
    assert "[退件意見]\n<reviewer-note>\nmore examples\n</reviewer-note>" in writer.prompts[1]


@langgraph_only
def test_c6_reject_note_reaches_swe(tmp_path):
    _, _, swe, _, _ = run_scenario(tmp_path, "C6")
    assert "approval_feedback: add validation" in swe.prompts[1]


@langgraph_only
def test_mock_check_fail_first_gives_one_deterministic_rework(tmp_path, monkeypatch):
    monkeypatch.setenv("MOCK_CHECK_FAIL_FIRST", "true")
    task = make_task()
    orch, events = make_orchestrator(make_project(tmp_path, [task]), engine=ENGINE)
    lineup, agents = make_lineup("pm", "swe", "qa", ("security", "check"))
    asyncio.run(orch.request_lineup(lineup, agents))

    result = asyncio.run(orch.process_task(task))

    assert (result.status.value, result.rework_count) == ("done", 1)
    assert [d["station"] for e, d in events if e == "station:update" and d["status"] == "rework"] == ["swe"]


# -- request_lineup timing (AC18) ------------------------------------------------------------------


@langgraph_only
def test_request_lineup_waits_for_the_running_task(tmp_path):
    first, second = make_task("task-001", approval=Approval.AFTER_SWE), make_task("task-002")
    project = make_project(tmp_path, [first, second])
    orch, events = make_orchestrator(project, engine=ENGINE)
    repo = InMemoryLineupRepository()
    asyncio.run(repo.import_initial(project.name, [], [PipelineRecord("main", "主線", True, ("pm", "swe", "qa"))]))
    orch._lineup_repo = repo
    lineup, agents = make_lineup("pm", ("designer", "make"), "swe", "qa")

    async def scenario():
        running = asyncio.create_task(orch.process_task(first))
        for _ in range(500):
            if orch.is_awaiting_approval(first.id):
                break
            await asyncio.sleep(0.005)
        assert orch.in_flight_task_id == first.id
        assert await orch.request_lineup(lineup, agents) == "next_task"
        assert orch.lineup == DEFAULT_LINEUP and orch.pending_lineup == lineup
        stored = await repo.get_pipeline(project.name, "main")
        assert (stored.seats, stored.pending_seats) == (("pm", "swe", "qa"), ("pm", "designer", "swe", "qa"))
        assert list(orch.station_states) == ["pm", "swe", "qa"]
        orch.approve(first.id)
        await running
        assert orch.in_flight_task_id is None
        # The running task finished on the lineup it started with.
        assert "designer" not in {d.get("station") for _, d in events}
        # Applied as soon as that task ended, before any next task (AC18, 2026-11-24 ruling).
        assert orch.lineup == lineup and orch.pending_lineup is None
        stored = await repo.get_pipeline(project.name, "main")
        assert (stored.seats, stored.pending_seats) == (("pm", "designer", "swe", "qa"), None)
        await orch.process_task(second)
        stored = await repo.get_pipeline(project.name, "main")
        assert (stored.seats, stored.pending_seats) == (("pm", "designer", "swe", "qa"), None)

    asyncio.run(scenario())
    assert orch.lineup == lineup and orch.pending_lineup is None
    second_stations = [d["station"] for e, d in events if e == "station:update" and d["task"]["id"] == "task-002"
                       and d["status"] == "working"]
    assert second_stations == ["pm", "designer", "swe", "qa"]
    assert list(orch.station_states) == ["pm", "designer", "swe", "qa"]


@langgraph_only
def test_request_lineup_when_idle_applies_now_and_stores_the_seats(tmp_path):
    project = make_project(tmp_path, [make_task()])
    orch, _ = make_orchestrator(project, engine=ENGINE)
    repo = InMemoryLineupRepository()
    orch._lineup_repo = repo
    lineup, agents = make_lineup("pm", "swe", "qa", ("security", "check"))

    assert asyncio.run(orch.request_lineup(lineup, agents)) == "now"
    assert orch.lineup == lineup and orch.extra_agents == agents
    stored = asyncio.run(repo.get_pipeline(project.name, "main"))
    assert (stored.name, stored.is_default, stored.seats, stored.pending_seats) == (
        "主線", True, ("pm", "swe", "qa", "security"), None)
    # Back to the default lineup: the custom agent is gone and the stations are the old three.
    assert asyncio.run(orch.request_lineup(DEFAULT_LINEUP, {})) == "now"
    assert orch.extra_agents == {} and "security" not in orch._agents
    assert list(orch.station_states) == ["pm", "swe", "qa"]


def test_request_lineup_needs_an_agent_for_every_custom_seat(tmp_path):
    orch, _ = make_orchestrator(make_project(tmp_path, [make_task()]), engine="langgraph")
    lineup, _ = make_lineup("pm", ("designer", "make"))
    with pytest.raises(ValueError, match="designer"):
        asyncio.run(orch.request_lineup(lineup, {}))
    assert orch.lineup == DEFAULT_LINEUP


def test_the_constructor_takes_a_lineup_and_its_agents(tmp_path):
    from tests.hud_harness import make_orchestrator_with_repo

    lineup, agents = make_lineup("pm", ("designer", "make"), "swe", "qa")
    orch, _ = make_orchestrator_with_repo(make_project(tmp_path, [make_task()]), engine="langgraph")
    rebuilt = type(orch)(
        orch.project_id, orch.project_path, orch._agents["pm"], orch._agents["swe"], orch._agents["qa"],
        engine="langgraph", lineup=lineup, extra_agents=agents, write_status=False,
    )
    assert rebuilt.lineup == lineup and rebuilt._graph_runner.lineup == lineup
    assert list(rebuilt.station_states) == ["pm", "designer", "swe", "qa"]


# -- legacy: default lineup only -------------------------------------------------------------------


def test_legacy_ignores_a_custom_lineup_with_a_warning(tmp_path, caplog):
    task = make_task()
    orch, events = make_orchestrator(make_project(tmp_path, [task]), engine="legacy")
    lineup, agents = make_lineup("pm", ("designer", "make"), "swe", "qa")

    with caplog.at_level(logging.WARNING, logger="core.pipeline"):
        assert asyncio.run(orch.request_lineup(lineup, agents)) == "now"
    assert orch.lineup == DEFAULT_LINEUP
    assert any("legacy engine only runs the default lineup" in r.getMessage() for r in caplog.records)
    asyncio.run(orch.process_task(task))
    assert [d["station"] for e, d in events if e == "station:update" and d["status"] == "working"] == ["pm", "swe", "qa"]
