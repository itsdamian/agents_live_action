"""Lineup writes while a task runs (plan D17, §7.1, Task 26a fix, QA re-check @ 0ed3f66).

* ``kind`` of an agent on the running seats can not change while a task is in flight, waits at
  its gate or waits to be resumed after a restart (409): the restored graph is rebuilt from the
  stored record, so a changed kind would break the task's checkpoint. Idle, or an agent only on
  the pending seats: allowed. An import that would do it is refused as a whole (409, no rows).
* The cleanup of leaving agents at a task's end runs under the project lock, so an import in the
  middle of it can not leave memory and the database disagreeing.
* ``GET /agents/export`` leaves out the agents that are leaving (and exports the seats they leave
  for), so importing the file again does not bring them back.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

import config
from core.runtime import FactoryRuntime, LineupRequestError
from core.stores.memory import (
    InMemoryLineupRepository,
    InMemoryProjectRepository,
    InMemoryRunRepository,
    InMemoryTaskRepository,
)
from main import create_app
from models.task import Approval, Status

PROJECT = "demo"
pytestmark = pytest.mark.skipif(
    config.get_pipeline_engine() != "langgraph", reason="the lineup endpoints write only with the graph engine"
)

DESIGNER = {"id": "designer", "display_name": "設計師", "duty": "畫介面草圖", "prompt": "你是介面設計師。",
            "kind": "make", "look": "beret", "after": "pm"}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("FORCE_MOCK", "true")
    monkeypatch.setenv("MOCK_LLM_DELAY", "0")
    for name in ("TASK_STORE", "PIPELINE_CHECKPOINTER", "DATABASE_URL", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)


class World:
    """What survives a restart: the stores and the checkpointer."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.tasks = InMemoryTaskRepository()
        self.runs = InMemoryRunRepository()
        self.projects = InMemoryProjectRepository(self.tasks, self.runs)
        self.lineups = InMemoryLineupRepository(self.projects)
        self.saver = InMemorySaver()
        project = root / PROJECT
        project.mkdir(parents=True, exist_ok=True)
        (project / "tasks.yaml").write_text(yaml.safe_dump({"tasks": []}), encoding="utf-8")

    def runtime(self) -> FactoryRuntime:
        return FactoryRuntime(
            projects_dir=self.root, task_store="db", task_repo=self.tasks, run_repo=self.runs,
            project_repo=self.projects, lineup_repo=self.lineups, checkpointer=self.saver)

    def client(self) -> TestClient:
        app = create_app(projects_dir=str(self.root))
        app.state.runtime = self.runtime()
        return TestClient(app)

    def agents(self):
        return {a.id: a for a in asyncio.run(self.lineups.list_agents(PROJECT))}


def _url(path: str = "") -> str:
    return f"/api/projects/{PROJECT}{path}"


def _wait_status(client, task_id, statuses, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.get(_url(f"/tasks/{task_id}")).json()["task"]["status"]
        if status in statuses:
            return status
        time.sleep(0.02)
    raise AssertionError(f"task {task_id} never reached {statuses}")


def _gate_task(client) -> str:
    body = client.post(_url("/tasks"), json={"title": "hold the line", "approval": "after_swe"}).json()
    task_id = body["task"]["id"]
    _wait_status(client, task_id, {"awaiting_approval"})
    return task_id


def _import(client, document):
    return client.post(_url("/agents/import"), content=yaml.safe_dump(document, allow_unicode=True),
                       headers={"content-type": "application/yaml"})


def _designer_document(kind: str) -> dict:
    return {"schema_version": 1, "agents": [{
        "id": "designer", "display_name": "設計師", "duty": "畫介面草圖", "kind": kind, "look": "beret",
        "system_prompt": "你是介面設計師。"}],
        "pipelines": [{"id": "main", "name": "主線", "default": True, "seats": ["pm", "designer", "swe", "qa"]}]}


# -- 1. kind of a seated agent while a task is busy -------------------------------------------------


def test_kind_change_of_a_seated_agent_at_a_gate_is_409_and_the_task_survives_a_restart(tmp_path):
    """QA repro: designer seated -> gate -> PUT kind (was 202) -> restart -> approve -> KeyError."""

    world = World(tmp_path)
    with world.client() as client:
        assert client.post(_url("/agents"), json=DESIGNER).status_code == 201
        task_id = _gate_task(client)
        response = client.put(_url("/agents/designer"), json={"kind": "check"})
        assert response.status_code == 409
        assert "類型" in response.json()["detail"]
        assert world.agents()["designer"].kind == "make"
        # The other fields still change while the task waits.
        assert client.put(_url("/agents/designer"), json={"display_name": "介面設計師"}).status_code == 202
        # Same kind as now is not a change.
        assert client.put(_url("/agents/designer"), json={"kind": "make"}).status_code == 202

    with world.client() as client:  # a restart: the task is restored at its gate
        response = client.put(_url("/agents/designer"), json={"kind": "check"})
        assert response.status_code == 409
        assert client.post(_url(f"/tasks/{task_id}/approve")).status_code == 200
        assert _wait_status(client, task_id, {"done", "failed"}) == "done"
        # Idle again: the kind may change.
        response = client.put(_url("/agents/designer"), json={"kind": "check"})
        assert response.status_code == 200
        assert response.json()["agent"]["kind"] == "check"
    assert world.agents()["designer"].kind == "check"


def test_kind_change_when_idle_is_allowed(tmp_path):
    world = World(tmp_path)
    with world.client() as client:
        assert client.post(_url("/agents"), json=DESIGNER).status_code == 201
        response = client.put(_url("/agents/designer"), json={"kind": "check"})
        assert response.status_code == 200 and response.json()["effective"] == "now"


def test_kind_change_of_an_agent_only_on_the_pending_seats_is_allowed(tmp_path):
    """Added while a task waits: not on the running seats, so no checkpoint depends on it."""

    world = World(tmp_path)
    with world.client() as client:
        task_id = _gate_task(client)
        assert client.post(_url("/agents"), json=DESIGNER).status_code == 201
        assert client.put(_url("/agents/designer"), json={"kind": "check"}).status_code == 202
        assert client.post(_url(f"/tasks/{task_id}/approve")).status_code == 200
        assert _wait_status(client, task_id, {"done", "failed"}) == "done"


def test_import_changing_the_kind_of_a_seated_agent_while_busy_is_409_and_writes_nothing(tmp_path):
    world = World(tmp_path)
    with world.client() as client:
        assert client.post(_url("/agents"), json=DESIGNER).status_code == 201
        task_id = _gate_task(client)
        before = client.get(_url("/agents")).json()
        response = _import(client, _designer_document("check"))
        assert response.status_code == 409
        assert "類型" in response.json()["detail"]
        assert client.get(_url("/agents")).json() == before
        assert world.agents()["designer"].kind == "make"
        # The same kind imports fine while busy.
        assert _import(client, _designer_document("make")).status_code == 202
        assert client.post(_url(f"/tasks/{task_id}/approve")).status_code == 200
        assert _wait_status(client, task_id, {"done", "failed"}) == "done"
        # Idle: the kind may change by import too.
        assert _import(client, _designer_document("check")).status_code == 200
    assert world.agents()["designer"].kind == "check"


# -- 2. the leaving cleanup at a task's end runs under the project lock -----------------------------


def test_an_import_during_the_task_end_cleanup_waits_for_it(tmp_path):
    """The cleanup pauses inside its first row delete; an import arrives right then.

    Without the lock the import ran in that pause and the cleanup then deleted the rows the
    import had just written: memory had aaa / bbb, the database did not.
    """

    world = World(tmp_path)
    agents = [{"id": agent_id, "display_name": agent_id, "duty": "做事", "prompt": "你做事。",
               "kind": "make", "look": "beret", "after": "swe"} for agent_id in ("aaa", "bbb")]
    document = {"schema_version": 1, "agents": [
        {k: v for k, v in a.items() if k not in ("after", "prompt")} | {"system_prompt": "你做事。"}
        for a in agents],
        "pipelines": [{"id": "main", "name": "主線", "default": True, "seats": ["pm", "swe", "aaa", "bbb", "qa"]}]}
    paused, release = asyncio.Event(), asyncio.Event()
    original_delete = world.lineups.delete_agent

    async def slow_delete(project_id, agent_id):
        if not paused.is_set():
            paused.set()
            await release.wait()
        return await original_delete(project_id, agent_id)

    async def scenario():
        runtime = world.runtime()
        await runtime.startup()
        try:
            for payload in agents:
                await runtime.create_agent(PROJECT, payload)
            task = await runtime.create_task(PROJECT, "hold the line", approval=Approval.AFTER_SWE)
            orchestrator = runtime.orchestrators[PROJECT]
            while task.id not in orchestrator._awaiting:
                await asyncio.sleep(0.01)
            for agent_id in ("aaa", "bbb"):
                assert await runtime.delete_agent(PROJECT, agent_id) == "next_task"
            world.lineups.delete_agent = slow_delete
            assert orchestrator.approve(task.id)
            await asyncio.wait_for(paused.wait(), 10)
            importing = asyncio.create_task(runtime.import_lineup(PROJECT, yaml.safe_dump(document)))
            done, _ = await asyncio.wait({importing}, timeout=0.5)
            assert not done, "the import ran in the middle of the cleanup"
            release.set()
            _, effective = await asyncio.wait_for(importing, 10)
            assert effective == "now"
            stored = {a.id for a in await world.lineups.list_agents(PROJECT)}
            in_memory = {view["id"] for view in runtime.roster_views(PROJECT)}
            assert stored == in_memory == {"pm", "swe", "qa", "aaa", "bbb"}
            assert list(await world.lineups.list_leaving(PROJECT)) == []
            assert (await runtime.task_repo.get(PROJECT, task.id)).status is Status.DONE
        finally:
            release.set()
            await runtime.shutdown()

    asyncio.run(scenario())


# -- 3. export leaves out the leaving agents ---------------------------------------------------------


def test_export_leaves_out_leaving_agents_and_imports_back_cleanly(tmp_path):
    world = World(tmp_path)
    maker = {**DESIGNER, "id": "maker", "display_name": "打樣師", "after": "swe"}
    with world.client() as client:
        assert client.post(_url("/agents"), json=maker).status_code == 201
        task_id = _gate_task(client)
        assert client.delete(_url("/agents/maker")).status_code == 202
        exported = yaml.safe_load(client.get(_url("/agents/export")).text)
        assert "maker" not in [a["id"] for a in exported["agents"]]
        assert exported["pipelines"][0]["seats"] == ["pm", "swe", "qa"]
        assert _import(client, exported).status_code == 202
        assert client.post(_url(f"/tasks/{task_id}/approve")).status_code == 200
        assert _wait_status(client, task_id, {"done", "failed"}) == "done"
        for _ in range(500):
            if "maker" not in world.agents():
                break
            time.sleep(0.02)
        assert "maker" not in world.agents()
