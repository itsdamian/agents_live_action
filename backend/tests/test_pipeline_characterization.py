"""Characterization tests for the pipeline (plan §6, scenarios S1-S12).

Expected values are hard-coded from what the *legacy* engine actually does, so
they keep guarding behaviour while the engine is refactored or replaced. Each
test is parametrized by engine; ``ENGINES`` grows once the LangGraph engine
exists. Event signatures are defined in ``tests/pipeline_harness.py`` (plan §5).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from models.task import Status
from tests.pipeline_harness import (
    QA_FAIL_REPORT,
    ScriptedClient,
    deliverable_names,
    install_status_recorder,
    make_orchestrator,
    make_project,
    make_task,
    signatures,
)

ENGINES = ["legacy"]

# -- expected-signature builders ------------------------------------------------
# The key tuples are literals on purpose: they pin the event ``data`` shapes.


def _created():
    return ("task:created", ("queue_size", "task"), None, None, "task-001", "pending", 0, 0)


def _working(station, rework_count=0):
    return (
        "station:update",
        ("station", "status", "task"),
        station,
        "working",
        "task-001",
        "in_progress",
        rework_count,
    )


def _finished(station, status="done", task_status="in_progress", rework_count=0):
    return (
        "station:update",
        ("error", "execution_time", "station", "status", "success", "task"),
        station,
        status,
        "task-001",
        task_status,
        rework_count,
    )


def _rework(rework_count):
    return (
        "station:update",
        ("station", "status", "task"),
        "swe",
        "rework",
        "task-001",
        "in_progress",
        rework_count,
    )


def _completed(rework_count, names):
    return (
        "task:completed",
        ("deliverables", "done_count", "task"),
        None,
        None,
        "task-001",
        "done",
        rework_count,
        1,
        names,
    )


def _failed(rework_count, reason):
    return (
        "task:failed",
        ("failed_count", "reason", "task"),
        None,
        None,
        "task-001",
        "failed",
        rework_count,
        1,
        reason,
    )


def _run(tmp_path, monkeypatch, engine, task, pm_script=None, swe_script=None, qa_script=None):
    """Run one task without gates and collect everything the tests assert on."""

    project = make_project(tmp_path, [task])
    writes = install_status_recorder(monkeypatch)
    clients = SimpleNamespace(
        pm=ScriptedClient("Project Manager", "PM_agent", pm_script),
        swe=ScriptedClient("Software Engineer", "SWE_agent", swe_script),
        qa=ScriptedClient("Quality Assurance", "QA_agent", qa_script),
    )
    orch, events = make_orchestrator(
        project,
        engine=engine,
        pm_client=clients.pm,
        swe_client=clients.swe,
        qa_client=clients.qa,
    )
    result = asyncio.run(orch.process_task(task))
    return SimpleNamespace(
        orch=orch,
        events=events,
        result=result,
        writes=writes,
        files=deliverable_names(project, task.id),
        clients=clients,
    )


def _prompt_counts(run):
    return (len(run.clients.pm.prompts), len(run.clients.swe.prompts), len(run.clients.qa.prompts))


def _outputs_present(task):
    return (task.pm_output is not None, task.swe_output is not None, task.qa_output is not None)


# -- S1: normal flow ------------------------------------------------------------

S1_FILES = ["01_PM_agent.md", "02_SWE_agent.md", "02_SWE_agent_1.py", "03_QA_agent.md"]


@pytest.mark.parametrize("engine", ENGINES)
def test_s1_normal_flow(tmp_path, monkeypatch, engine):
    run = _run(tmp_path, monkeypatch, engine, make_task())

    assert signatures(run.events) == [
        _created(),
        _working("pm"),
        _finished("pm"),
        _working("swe"),
        _finished("swe"),
        _working("qa"),
        _finished("qa"),
        _completed(0, tuple(S1_FILES)),
    ]
    assert run.writes == ["in_progress", "done"]
    assert run.files == S1_FILES
    assert run.result.status is Status.DONE
    assert run.result.rework_count == 0
    assert _outputs_present(run.result) == (True, True, True)
    assert (run.orch.done_count, run.orch.failed_count) == (1, 0)
    assert _prompt_counts(run) == (1, 1, 1)


# -- S2: QA fails once, then passes ---------------------------------------------

S2_FILES = [
    "01_PM_agent.md",
    "02_SWE_agent.md",
    "02_SWE_agent_1.py",
    "03_QA_agent.md",
    "04_SWE_agent.md",
    "04_SWE_agent_1.py",
    "05_QA_agent.md",
]


@pytest.mark.parametrize("engine", ENGINES)
def test_s2_qa_fails_once_then_passes(tmp_path, monkeypatch, engine):
    run = _run(tmp_path, monkeypatch, engine, make_task(), qa_script=[QA_FAIL_REPORT])

    assert signatures(run.events) == [
        _created(),
        _working("pm"),
        _finished("pm"),
        _working("swe"),
        _finished("swe"),
        _working("qa"),
        _finished("qa"),
        _rework(1),
        _working("swe", 1),
        _finished("swe", rework_count=1),
        _working("qa", 1),
        _finished("qa", rework_count=1),
        _completed(1, tuple(S2_FILES)),
    ]
    assert run.writes == ["in_progress", "done"]
    assert run.files == S2_FILES
    assert run.result.status is Status.DONE
    assert run.result.rework_count == 1
    assert _outputs_present(run.result) == (True, True, True)
    assert (run.orch.done_count, run.orch.failed_count) == (1, 0)
    assert _prompt_counts(run) == (1, 2, 2)
    # The second SWE prompt is a rework prompt carrying QA's failure report.
    assert "REWORK" in run.clients.swe.prompts[1]
    assert "QA failure report" in run.clients.swe.prompts[1]


# -- S3: QA never passes, max_reworks=2 -----------------------------------------

S3_FILES = [
    "01_PM_agent.md",
    "02_SWE_agent.md",
    "02_SWE_agent_1.py",
    "03_QA_agent.md",
    "04_SWE_agent.md",
    "04_SWE_agent_1.py",
    "05_QA_agent.md",
    "06_SWE_agent.md",
    "06_SWE_agent_1.py",
    "07_QA_agent.md",
]


@pytest.mark.parametrize("engine", ENGINES)
def test_s3_qa_never_passes_exceeds_max_reworks(tmp_path, monkeypatch, engine):
    run = _run(
        tmp_path,
        monkeypatch,
        engine,
        make_task(max_reworks=2),
        qa_script=[QA_FAIL_REPORT] * 3,
    )

    assert signatures(run.events) == [
        _created(),
        _working("pm"),
        _finished("pm"),
        _working("swe"),
        _finished("swe"),
        _working("qa"),
        _finished("qa"),
        _rework(1),
        _working("swe", 1),
        _finished("swe", rework_count=1),
        _working("qa", 1),
        _finished("qa", rework_count=1),
        _rework(2),
        _working("swe", 2),
        _finished("swe", rework_count=2),
        _working("qa", 2),
        _finished("qa", rework_count=2),
        _failed(3, "QA still failing after 2 reworks"),
    ]
    assert run.writes == ["in_progress", "failed"]
    assert run.files == S3_FILES
    assert run.result.status is Status.FAILED
    assert run.result.rework_count == 3
    assert _outputs_present(run.result) == (True, True, True)
    assert (run.orch.done_count, run.orch.failed_count) == (0, 1)
    assert _prompt_counts(run) == (1, 3, 3)


# -- S4: SWE client raises ------------------------------------------------------


@pytest.mark.parametrize("engine", ENGINES)
def test_s4_swe_exception_fails_task(tmp_path, monkeypatch, engine):
    run = _run(
        tmp_path,
        monkeypatch,
        engine,
        make_task(),
        swe_script=[RuntimeError("provider unreachable")],
    )

    assert signatures(run.events) == [
        _created(),
        _working("pm"),
        _finished("pm"),
        _working("swe"),
        # The agent itself flips task.status to failed, so the error event
        # already carries it (the pipeline only writes it to tasks.yaml later).
        _finished("swe", status="error", task_status="failed"),
        _failed(0, "provider unreachable"),
    ]
    assert run.writes == ["in_progress", "failed"]
    assert run.files == ["01_PM_agent.md"]
    assert run.result.status is Status.FAILED
    assert run.result.rework_count == 0
    assert _outputs_present(run.result) == (True, False, False)
    assert (run.orch.done_count, run.orch.failed_count) == (0, 1)
    assert _prompt_counts(run) == (1, 1, 0)


# -- S5: PM client raises -------------------------------------------------------


@pytest.mark.parametrize("engine", ENGINES)
def test_s5_pm_exception_fails_task_and_skips_later_stations(tmp_path, monkeypatch, engine):
    run = _run(
        tmp_path,
        monkeypatch,
        engine,
        make_task(),
        pm_script=[RuntimeError("provider unreachable")],
    )

    assert signatures(run.events) == [
        _created(),
        _working("pm"),
        _finished("pm", status="error", task_status="failed"),
        _failed(0, "provider unreachable"),
    ]
    assert run.writes == ["in_progress", "failed"]
    assert run.files == []
    assert run.result.status is Status.FAILED
    assert run.result.rework_count == 0
    assert _outputs_present(run.result) == (False, False, False)
    assert (run.orch.done_count, run.orch.failed_count) == (0, 1)
    # SWE and QA are never called.
    assert _prompt_counts(run) == (1, 0, 0)
