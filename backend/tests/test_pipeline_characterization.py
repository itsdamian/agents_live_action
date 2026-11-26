"""Characterization tests for the pipeline (plan §6, scenarios S1-S12).

Expected values are hard-coded from what the *legacy* engine actually does, so
they keep guarding behaviour while the engine is refactored or replaced. Each
test is parametrized by engine; ``ENGINES`` grows once the LangGraph engine
exists. Event signatures are defined in ``tests/pipeline_harness.py`` (plan §5).
"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest
import yaml

from core.usage_tracker import UsageTracker
from models.task import Approval, Status
from tests.pipeline_harness import (
    QA_FAIL_REPORT,
    ScriptedClient,
    deliverable_names,
    install_status_recorder,
    make_orchestrator,
    make_project,
    make_task,
    run_with_gate_actions,
    signatures,
)

ENGINES = ["legacy", "langgraph"]

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


# -- approval gates (S6-S10, S12) -------------------------------------------------


def _awaiting(station, rework_count=0):
    return (
        "task:awaiting_approval",
        ("approval", "station", "task"),
        station,
        None,
        "task-001",
        "awaiting_approval",
        rework_count,
    )


def _approved(station, rework_count=0):
    return (
        "task:approved",
        ("station", "task"),
        station,
        None,
        "task-001",
        "in_progress",
        rework_count,
    )


def _rejected(station, note, rework_count=0):
    return (
        "task:rejected",
        ("note", "station", "task"),
        station,
        None,
        "task-001",
        "in_progress",
        rework_count,
        note,
    )


def _pause(approval, station, rework_count, swe_output, qa_output):
    """What the orchestrator looks like while paused at a gate (legacy-measured).

    Note the snapshot's task summary says ``in_progress``: the summary is
    captured before the ``awaiting_approval`` status is written.
    """

    stations = {"pm": "idle", "swe": "idle", "qa": "idle"}
    stations[station] = "awaiting_approval"
    return {
        "awaiting": [
            {
                "task": {
                    "id": "task-001",
                    "title": "Add login page",
                    "priority": "high",
                    "status": "in_progress",
                    "rework_count": rework_count,
                },
                "station": station,
                "approval": approval,
            }
        ],
        "stations": stations,
        "yaml_status": "awaiting_approval",
        "rework_count": rework_count,
        "swe_output": swe_output,
        "qa_output": qa_output,
    }


def _yaml_status(project, task_id):
    raw = yaml.safe_load((project / "tasks.yaml").read_text(encoding="utf-8"))
    return next(e["status"] for e in raw["tasks"] if e["id"] == task_id)


def _run_gated(tmp_path, monkeypatch, engine, task, actions):
    """Run one gated task, answering each gate with ``actions``."""

    project = make_project(tmp_path, [task])
    writes = install_status_recorder(monkeypatch)
    clients = SimpleNamespace(
        pm=ScriptedClient("Project Manager", "PM_agent"),
        swe=ScriptedClient("Software Engineer", "SWE_agent"),
        qa=ScriptedClient("Quality Assurance", "QA_agent"),
    )
    orch, events = make_orchestrator(
        project,
        engine=engine,
        pm_client=clients.pm,
        swe_client=clients.swe,
        qa_client=clients.qa,
    )
    pauses = []

    def on_pause(orch, task):
        pauses.append(
            {
                "awaiting": orch.snapshot()["awaiting_approval"],
                "stations": {k: v["status"] for k, v in orch.station_states.items()},
                "yaml_status": _yaml_status(project, task.id),
                "rework_count": task.rework_count,
                "swe_output": task.swe_output is not None,
                "qa_output": task.qa_output is not None,
            }
        )

    result = asyncio.run(run_with_gate_actions(orch, task, actions, on_pause))
    # Once the last decision is processed nothing is left at a gate.
    assert orch.snapshot()["awaiting_approval"] == []
    assert not orch.is_awaiting_approval(task.id)
    return SimpleNamespace(
        orch=orch,
        events=events,
        result=result,
        writes=writes,
        files=deliverable_names(project, task.id),
        clients=clients,
        pauses=pauses,
    )


def _gate_entries(run):
    return sum(1 for name, _ in run.events if name == "task:awaiting_approval")


APPROVE = ("approve", "")

S6_FILES = S1_FILES  # approving does not change the deliverables
S8_FILES = [
    "01_PM_agent.md",
    "02_SWE_agent.md",
    "02_SWE_agent_1.py",
    "03_SWE_agent.md",
    "03_SWE_agent_1.py",
    "04_QA_agent.md",
]
S9_FILES = S8_FILES[:5]
S10_FILES = [
    "01_PM_agent.md",
    "02_SWE_agent.md",
    "02_SWE_agent_1.py",
    "03_QA_agent.md",
    "04_SWE_agent.md",
    "04_SWE_agent_1.py",
    "04_SWE_agent_2.py",  # the mock SWE echoes the QA report, adding a code block
    "05_QA_agent.md",
]
# in_progress -> awaiting_approval -> in_progress per gate entry
WRITES_ONE_GATE = ["in_progress", "awaiting_approval", "in_progress", "done"]
WRITES_TWO_GATES_DONE = [
    "in_progress",
    "awaiting_approval",
    "in_progress",
    "awaiting_approval",
    "in_progress",
    "done",
]


@pytest.mark.parametrize("engine", ENGINES)
def test_s6_after_swe_approve(tmp_path, monkeypatch, engine):
    task = make_task(approval=Approval.AFTER_SWE)
    run = _run_gated(tmp_path, monkeypatch, engine, task, [APPROVE])

    assert signatures(run.events) == [
        _created(),
        _working("pm"),
        _finished("pm"),
        _working("swe"),
        _finished("swe"),
        _awaiting("swe"),
        _approved("swe"),
        # QA only starts after the approval.
        _working("qa"),
        _finished("qa"),
        _completed(0, tuple(S6_FILES)),
    ]
    assert run.pauses == [_pause("after_swe", "swe", 0, swe_output=True, qa_output=False)]
    assert _gate_entries(run) == 1
    assert run.writes == WRITES_ONE_GATE
    assert run.files == S6_FILES
    assert run.result.status is Status.DONE
    assert run.result.rework_count == 0
    assert _outputs_present(run.result) == (True, True, True)
    assert (run.orch.done_count, run.orch.failed_count) == (1, 0)
    assert _prompt_counts(run) == (1, 1, 1)


@pytest.mark.parametrize("engine", ENGINES)
def test_s7_after_qa_approve(tmp_path, monkeypatch, engine):
    task = make_task(approval=Approval.AFTER_QA)
    run = _run_gated(tmp_path, monkeypatch, engine, task, [APPROVE])

    assert signatures(run.events) == [
        _created(),
        _working("pm"),
        _finished("pm"),
        _working("swe"),
        _finished("swe"),
        _working("qa"),
        _finished("qa"),
        _awaiting("qa"),
        _approved("qa"),
        _completed(0, tuple(S1_FILES)),
    ]
    # QA has already produced its report when the gate is reached.
    assert run.pauses == [_pause("after_qa", "qa", 0, swe_output=True, qa_output=True)]
    assert _gate_entries(run) == 1
    assert run.writes == WRITES_ONE_GATE
    assert run.files == S1_FILES
    assert run.result.status is Status.DONE
    assert run.result.rework_count == 0
    assert _outputs_present(run.result) == (True, True, True)
    assert (run.orch.done_count, run.orch.failed_count) == (1, 0)
    assert _prompt_counts(run) == (1, 1, 1)


@pytest.mark.parametrize("engine", ENGINES)
def test_s8_after_swe_reject_then_approve(tmp_path, monkeypatch, engine):
    note = "Please add input validation"
    task = make_task(approval=Approval.AFTER_SWE)
    run = _run_gated(tmp_path, monkeypatch, engine, task, [("reject", note), APPROVE])

    assert signatures(run.events) == [
        _created(),
        _working("pm"),
        _finished("pm"),
        _working("swe"),
        _finished("swe"),
        _awaiting("swe"),
        _rejected("swe", note),
        _rework(1),
        _working("swe", 1),
        _finished("swe", rework_count=1),
        _awaiting("swe", 1),
        _approved("swe", 1),
        _working("qa", 1),
        _finished("qa", rework_count=1),
        _completed(1, tuple(S8_FILES)),
    ]
    assert run.pauses == [
        _pause("after_swe", "swe", 0, swe_output=True, qa_output=False),
        _pause("after_swe", "swe", 1, swe_output=True, qa_output=False),
    ]
    assert _gate_entries(run) == 2
    assert run.writes == WRITES_TWO_GATES_DONE
    assert run.files == S8_FILES
    assert run.result.status is Status.DONE
    assert run.result.rework_count == 1
    assert _outputs_present(run.result) == (True, True, True)
    assert (run.orch.done_count, run.orch.failed_count) == (1, 0)
    assert _prompt_counts(run) == (1, 2, 1)
    # The reviewer's note reaches SWE's second run, and only that one.
    assert "approval_feedback: " + note in run.clients.swe.prompts[1]
    assert "approval_feedback" not in run.clients.swe.prompts[0]


@pytest.mark.parametrize("engine", ENGINES)
def test_s9_after_swe_reject_twice_exceeds_max_reworks(tmp_path, monkeypatch, engine):
    task = make_task(approval=Approval.AFTER_SWE, max_reworks=1)
    run = _run_gated(
        tmp_path, monkeypatch, engine, task, [("reject", "nope"), ("reject", "still nope")]
    )

    assert signatures(run.events) == [
        _created(),
        _working("pm"),
        _finished("pm"),
        _working("swe"),
        _finished("swe"),
        _awaiting("swe"),
        _rejected("swe", "nope"),
        _rework(1),
        _working("swe", 1),
        _finished("swe", rework_count=1),
        _awaiting("swe", 1),
        _rejected("swe", "still nope", 1),
        _failed(2, "Rejected at approval gate (after SWE)"),
    ]
    assert run.pauses == [
        _pause("after_swe", "swe", 0, swe_output=True, qa_output=False),
        _pause("after_swe", "swe", 1, swe_output=True, qa_output=False),
    ]
    assert _gate_entries(run) == 2
    assert run.writes == [
        "in_progress",
        "awaiting_approval",
        "in_progress",
        "awaiting_approval",
        "in_progress",
        "failed",
    ]
    assert run.files == S9_FILES
    assert run.result.status is Status.FAILED
    assert run.result.rework_count == 2
    assert _outputs_present(run.result) == (True, True, False)
    assert (run.orch.done_count, run.orch.failed_count) == (0, 1)
    # QA is never reached.
    assert _prompt_counts(run) == (1, 2, 0)


@pytest.mark.parametrize("engine", ENGINES)
def test_s10_after_qa_reject_then_approve(tmp_path, monkeypatch, engine):
    note = "tighten tests"
    task = make_task(approval=Approval.AFTER_QA)
    run = _run_gated(tmp_path, monkeypatch, engine, task, [("reject", note), APPROVE])

    assert signatures(run.events) == [
        _created(),
        _working("pm"),
        _finished("pm"),
        _working("swe"),
        _finished("swe"),
        _working("qa"),
        _finished("qa"),
        _awaiting("qa"),
        _rejected("qa", note),
        _rework(1),
        _working("swe", 1),
        _finished("swe", rework_count=1),
        _working("qa", 1),
        _finished("qa", rework_count=1),
        _awaiting("qa", 1),
        _approved("qa", 1),
        _completed(1, tuple(S10_FILES)),
    ]
    assert run.pauses == [
        _pause("after_qa", "qa", 0, swe_output=True, qa_output=True),
        _pause("after_qa", "qa", 1, swe_output=True, qa_output=True),
    ]
    assert _gate_entries(run) == 2
    assert run.writes == WRITES_TWO_GATES_DONE
    assert run.files == S10_FILES
    assert run.result.status is Status.DONE
    assert run.result.rework_count == 1
    assert _outputs_present(run.result) == (True, True, True)
    assert (run.orch.done_count, run.orch.failed_count) == (1, 0)
    assert _prompt_counts(run) == (1, 2, 2)
    # SWE's rework context carries both QA's earlier report and the note.
    second_swe_prompt = run.clients.swe.prompts[1]
    assert "approval_feedback: " + note in second_swe_prompt
    assert "Result: PASS" in second_swe_prompt


@pytest.mark.parametrize("engine", ENGINES)
def test_s12_after_swe_reject_with_empty_note_then_approve(tmp_path, monkeypatch, engine):
    task = make_task(approval=Approval.AFTER_SWE)
    run = _run_gated(tmp_path, monkeypatch, engine, task, [("reject", ""), APPROVE])

    # Same shape as S8, with an empty note on the rejection event.
    assert signatures(run.events) == [
        _created(),
        _working("pm"),
        _finished("pm"),
        _working("swe"),
        _finished("swe"),
        _awaiting("swe"),
        _rejected("swe", ""),
        _rework(1),
        _working("swe", 1),
        _finished("swe", rework_count=1),
        _awaiting("swe", 1),
        _approved("swe", 1),
        _working("qa", 1),
        _finished("qa", rework_count=1),
        _completed(1, tuple(S8_FILES)),
    ]
    assert run.pauses == [
        _pause("after_swe", "swe", 0, swe_output=True, qa_output=False),
        _pause("after_swe", "swe", 1, swe_output=True, qa_output=False),
    ]
    assert _gate_entries(run) == 2
    assert run.writes == WRITES_TWO_GATES_DONE
    assert run.files == S8_FILES
    assert run.result.status is Status.DONE
    assert run.result.rework_count == 1
    assert _outputs_present(run.result) == (True, True, True)
    assert (run.orch.done_count, run.orch.failed_count) == (1, 0)
    assert _prompt_counts(run) == (1, 2, 1)
    # An empty note is falsy, so it is not passed to SWE as feedback.
    assert "approval_feedback" not in run.clients.swe.prompts[1]


# -- gates driven through the worker loop ----------------------------------------


async def _wait_for_gate(orch, events, task_id):
    # Registered *and* announced: the gate is registered slightly before the
    # status write / event, and a stop() in that window would skip the cleanup.
    for _ in range(500):
        announced = any(name == "task:awaiting_approval" for name, _ in events)
        if orch.is_awaiting_approval(task_id) and announced:
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"task {task_id} never reached the approval gate")


@pytest.mark.parametrize("engine", ENGINES)
def test_approve_from_other_thread(tmp_path, engine):
    """REST endpoints run in a threadpool: approve() must work off-loop."""

    task = make_task(approval=Approval.AFTER_SWE)
    project = make_project(tmp_path, [task])
    orch, events = make_orchestrator(project, engine=engine)

    async def run():
        orch.start()
        orch.enqueue(task)
        await _wait_for_gate(orch, events, task.id)
        thread = threading.Thread(target=orch.approve, args=(task.id,))
        thread.start()
        thread.join()
        await asyncio.wait_for(orch._queue.join(), 10)
        await orch.stop()

    asyncio.run(run())

    assert orch.done_count == 1
    assert orch.failed_count == 0
    assert task.status is Status.DONE


@pytest.mark.parametrize("engine", ENGINES)
def test_stop_while_waiting_clears_gate(tmp_path, engine):
    task = make_task(approval=Approval.AFTER_SWE)
    project = make_project(tmp_path, [task])
    orch, events = make_orchestrator(project, engine=engine)

    async def run():
        orch.start()
        orch.enqueue(task)
        await _wait_for_gate(orch, events, task.id)
        await orch.stop()

    asyncio.run(run())

    assert orch.is_awaiting_approval(task.id) is False
    assert orch.snapshot()["awaiting_approval"] == []
    assert orch._approval_gates == {}


# -- S11: usage tracking and budget guard -----------------------------------------


def _usage(task_calls, project_calls):
    return (
        "usage:update",
        ("daily_cost", "project_usage", "record", "task", "task_id", "task_usage"),
        None,
        None,
        "task-001",
        "in_progress",
        0,
        task_calls,
        project_calls,
    )


def _budget_alert():
    return (
        "devops:alert",
        ("level", "message", "metric", "value"),
        None,
        None,
        None,
        None,
        None,
        "critical",
        "budget",
    )


@pytest.mark.parametrize("engine", ENGINES)
def test_s11_budget_crossing_alerts_once_and_task_still_completes(tmp_path, monkeypatch, engine):
    task = make_task()
    project = make_project(tmp_path, [task])
    writes = install_status_recorder(monkeypatch)
    tracker = UsageTracker(project_budget_usd=0.015)
    # Mock calls are free, so seed real-priced spend that already exceeds the
    # budget (0.02 > 0.015); the first recorded call then trips the guard.
    tracker.record(project.name, "seed", "openai", "gpt-4o", 1000, 1000)
    orch, events = make_orchestrator(project, engine=engine, usage_tracker=tracker)

    result = asyncio.run(orch.process_task(task))

    # project call counts include the seed record (2, 3, 4).
    assert signatures(events) == [
        _created(),
        _working("pm"),
        _finished("pm"),
        _usage(1, 2),
        _budget_alert(),  # only once, right after the first usage:update
        _working("swe"),
        _finished("swe"),
        _usage(2, 3),
        _working("qa"),
        _finished("qa"),
        _usage(3, 4),
        _completed(0, tuple(S1_FILES)),
    ]
    assert sum(1 for name, _ in events if name == "usage:update") == 3
    assert sum(1 for name, _ in events if name == "devops:alert") == 1
    assert writes == ["in_progress", "done"]
    # The in-flight task runs to completion despite the budget being exceeded.
    assert result.status is Status.DONE
    assert (orch.done_count, orch.failed_count) == (1, 0)
    assert orch.is_budget_paused() is True
    assert orch.snapshot()["budget_paused"] is True
