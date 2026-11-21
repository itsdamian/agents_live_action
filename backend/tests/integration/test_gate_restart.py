"""AC5 on a real PostgreSQL: a task waiting at its approval gate survives a backend restart
(Task 14, plan D7).

Each runtime lives in its own event loop (``asyncio.run``), opens its own AsyncPostgresSaver and
its own database connections, like two processes one after the other. The second one restores
the gate from the checkpoint tables, the decision is given there, and the task completes.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import psycopg
import pytest
import yaml

from core.runtime import FactoryRuntime
from models.task import Status
from tests.pipeline_harness import deliverable_names, signatures
from tests.test_gate_restart import llm_calls  # noqa: F401  (fixture)

pytestmark = pytest.mark.integration

TASK = "task-g"


@pytest.fixture
def pg_env(monkeypatch, migrated_db_sqlalchemy_url):
    monkeypatch.setenv("FORCE_MOCK", "true")
    monkeypatch.setenv("PIPELINE_ENGINE", "langgraph")
    monkeypatch.setenv("TASK_STORE", "db")
    monkeypatch.setenv("PIPELINE_CHECKPOINTER", "postgres")
    monkeypatch.setenv("DATABASE_URL", migrated_db_sqlalchemy_url)
    monkeypatch.delenv("DAILY_BUDGET_USD", raising=False)
    monkeypatch.delenv("PROJECT_BUDGET_USD", raising=False)


def _write_project(root: Path, name: str, approval: str) -> Path:
    project = root / name
    project.mkdir(parents=True)
    entry = {"id": TASK, "title": "Gate me", "description": "d", "priority": "high",
             "status": "pending", "approval": approval}
    (project / "tasks.yaml").write_text(yaml.safe_dump({"tasks": [entry]}, sort_keys=False), encoding="utf-8")
    return project


def _runtime(root: Path) -> tuple[FactoryRuntime, list[tuple[str, dict]]]:
    runtime = FactoryRuntime(projects_dir=root)  # everything from the environment, as in compose
    events: list[tuple[str, dict]] = []

    async def record(project_id, event, data):
        events.append((event, data))

    runtime.ws_manager.broadcast = record
    return runtime, events


async def _until(predicate, timeout=30.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not await predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition never became true")
        await asyncio.sleep(0.01)


def _entries(events) -> int:
    return sum(1 for name, _ in events if name == "task:awaiting_approval")


async def _status(runtime, project) -> Status:
    return (await runtime.task_repo.get(project, TASK)).status


async def _decide(runtime, events, project, actions, *, entries_offset):
    orch = runtime.orchestrators[project]
    for n, (action, note) in enumerate(actions):
        async def ready(n=n):
            return orch.is_awaiting_approval(TASK) and _entries(events) >= n + entries_offset
        await _until(ready)
        assert (orch.approve(TASK) if action == "approve" else orch.reject(TASK, note)) is True

    async def done():
        return await _status(runtime, project) is Status.DONE
    await _until(done)
    await asyncio.sleep(0.1)  # let the last events out


def _reference(root: Path, approval: str, actions) -> dict:
    project = _write_project(root, "ref", approval)

    async def scenario():
        runtime, events = _runtime(root)
        await runtime.startup()
        try:
            await _decide(runtime, events, "ref", actions, entries_offset=1)
        finally:
            await runtime.shutdown()
        first = next(i for i, (name, _) in enumerate(events) if name in ("task:approved", "task:rejected"))
        return signatures(events[first:])

    return {"after": asyncio.run(scenario()), "deliverables": deliverable_names(project, TASK)}


def _checkpoint_rows(plain_url: str, thread_id: str) -> int:
    with psycopg.connect(plain_url) as conn:
        return conn.execute("SELECT count(*) FROM checkpoints WHERE thread_id = %s", (thread_id,)).fetchone()[0]


def _restart(root: Path, plain_url: str, approval: str, actions, *, at_event: bool) -> dict:
    project = _write_project(root, "demo", approval)
    out: dict = {}

    async def before_restart():
        runtime, events = _runtime(root)
        await runtime.startup()
        orch = runtime.orchestrators["demo"]

        async def at_gate():
            if at_event:  # as early as possible: maybe before gate_wait reached interrupt()
                return _entries(events) >= 1
            return orch.is_awaiting_approval(TASK) and _entries(events) >= 1
        await _until(at_gate)
        await runtime.shutdown()  # immediately
        out["first_events"] = [name for name, _ in events]

    async def after_restart():
        runtime, events = _runtime(root)
        await runtime.startup()
        try:
            snap = runtime.snapshot("demo")["pipeline"]
            out["awaiting"] = [(a["task"]["id"], a["station"]) for a in snap["awaiting_approval"]]
            out["station"] = {k: v["status"] for k, v in snap["stations"].items()}
            await _decide(runtime, events, "demo", actions, entries_offset=0)
        finally:
            await runtime.shutdown()
        out["events"] = events

    asyncio.run(before_restart())
    out["status_between"] = asyncio.run(_stored_status(plain_url))
    out["checkpoints_between"] = _checkpoint_rows(plain_url, f"demo:{TASK}")
    out["deliverables_between"] = deliverable_names(project, TASK)
    asyncio.run(after_restart())
    out["checkpoints_after"] = _checkpoint_rows(plain_url, f"demo:{TASK}")
    out["deliverables"] = deliverable_names(project, TASK)
    return out


async def _stored_status(plain_url: str) -> str:
    with psycopg.connect(plain_url) as conn:
        return conn.execute("SELECT status FROM tasks WHERE project_id = 'demo' AND id = %s", (TASK,)).fetchone()[0]


@pytest.mark.parametrize("at_event", [False, True], ids=["at-interrupt", "at-gate-event"])
def test_ac5_after_swe_gate_survives_a_restart(tmp_path, pg_env, migrated_db_url, llm_calls, at_event):
    reference = _reference(tmp_path / "ref", "after_swe", [("approve", "")])
    assert llm_calls == {"PM_agent": 1, "SWE_agent": 1, "QA_agent": 1}
    llm_calls.clear()

    result = _restart(tmp_path / "run", migrated_db_url, "after_swe", [("approve", "")], at_event=at_event)

    assert result["status_between"] == "awaiting_approval"
    assert result["checkpoints_between"] > 0  # the graph is in Postgres while nobody runs
    assert result["awaiting"] == [(TASK, "swe")]  # snapshot rebuilt the gate (E5)
    assert result["station"]["swe"] == "awaiting_approval"
    names = [name for name, _ in result["events"]]
    assert "task:awaiting_approval" not in names and "task:created" not in names  # not re-sent
    assert signatures(result["events"]) == reference["after"]  # from task:approved on, unchanged
    assert llm_calls == {"PM_agent": 1, "SWE_agent": 1, "QA_agent": 1}  # PM and SWE not rerun
    assert result["deliverables_between"] == [n for n in reference["deliverables"] if "QA" not in n]
    assert result["deliverables"] == reference["deliverables"]  # no duplicates
    assert result["checkpoints_after"] == 0  # a finished task leaves no thread behind


def test_an_after_qa_gate_rejected_after_a_restart_reworks_and_completes(tmp_path, pg_env, migrated_db_url, llm_calls):
    actions = [("reject", "tighten tests"), ("approve", "")]
    reference = _reference(tmp_path / "ref", "after_qa", actions)
    reference_calls = dict(llm_calls)
    llm_calls.clear()

    result = _restart(tmp_path / "run", migrated_db_url, "after_qa", actions, at_event=False)

    assert result["awaiting"] == [(TASK, "qa")]
    names = [name for name, _ in result["events"]]
    assert names[0] == "task:rejected" and names.count("task:awaiting_approval") == 1
    assert signatures(result["events"]) == reference["after"]
    assert llm_calls == reference_calls == {"PM_agent": 1, "SWE_agent": 2, "QA_agent": 2}
    assert result["deliverables"] == reference["deliverables"]
