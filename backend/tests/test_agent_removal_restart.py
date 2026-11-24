"""Removing an agent a gated task still needs, then restarting (plan D17, §7.1 DELETE, Task 26a fix).

``DELETE /agents/{id}`` while a task is in flight takes the agent off the seats with the
running task's end; until then the agent stays in the roster and on the current seats (the
running task, or one resumed at its gate after a restart, still runs on it). The row goes when
the pending lineup is applied, together with ``agent:left``. The "restart" is a second app on
the same in-memory repositories and one InMemorySaver; the Postgres version is
``tests/integration/test_agent_removal_restart_pg.py``.
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
from core.runtime import FactoryRuntime
from core.stores.memory import (
    InMemoryLineupRepository,
    InMemoryProjectRepository,
    InMemoryRunRepository,
    InMemoryTaskRepository,
)
from main import create_app

PROJECT = "demo"
pytestmark = pytest.mark.skipif(
    config.get_pipeline_engine() != "langgraph", reason="the lineup endpoints write only with the graph engine"
)

MAKER = {"id": "maker", "display_name": "打樣師", "duty": "做出樣品", "prompt": "你是打樣師。",
         "kind": "make", "look": "beret", "after": "swe"}
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

    def client(self) -> TestClient:
        app = create_app(projects_dir=str(self.root))
        app.state.runtime = FactoryRuntime(
            projects_dir=self.root, task_store="db", task_repo=self.tasks, run_repo=self.runs,
            project_repo=self.projects, lineup_repo=self.lineups, checkpointer=self.saver)
        return TestClient(app)

    def agents(self):
        return [a.id for a in asyncio.run(self.lineups.list_agents(PROJECT))]

    def seats(self):
        record = asyncio.run(self.lineups.get_pipeline(PROJECT, "main"))
        return record.seats, record.pending_seats


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


def _wait(predicate, timeout=20.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "condition not reached in time"
        time.sleep(0.02)


def _gate_task(client) -> str:
    body = client.post(_url("/tasks"), json={"title": "hold the line", "approval": "after_swe"}).json()
    task_id = body["task"]["id"]
    _wait_status(client, task_id, {"awaiting_approval"})
    return task_id


def _roster(client):
    body = client.get(_url("/agents")).json()
    return [a["id"] for a in body["agents"]], body["pipelines"][0]


def _events_until(ws, last: str, limit: int = 300):
    seen = []
    for _ in range(limit):
        message = ws.receive_json()
        seen.append((message["event"], message["data"]))
        if message["event"] == last:
            return seen
    raise AssertionError(f"{last} never arrived; saw {[e for e, _ in seen]}")


def _removed_with_gate_task_pending(world) -> str:
    """Steps 1-3: a custom make seat after swe, a task gated after it, DELETE (202)."""

    with world.client() as client:
        assert client.post(_url("/agents"), json=MAKER).status_code == 201
        task_id = _gate_task(client)
        detail = client.get(_url(f"/tasks/{task_id}")).json()
        assert detail["task"]["status"] == "awaiting_approval"
        response = client.delete(_url("/agents/maker"))
        assert response.status_code == 202
        assert response.json() == {"agent_id": "maker", "effective": "next_task"}
        # Still in the roster and on the current seats until the running task ends.
        ids, pipeline = _roster(client)
        assert "maker" in ids
        assert pipeline["seats"] == ["pm", "swe", "maker", "qa"]
        assert pipeline["pending_seats"] == ["pm", "swe", "qa"]
    return task_id


def test_removing_an_agent_a_gated_task_needs_survives_a_restart(tmp_path):
    world = World(tmp_path)
    task_id = _removed_with_gate_task_pending(world)
    assert "maker" in world.agents()
    assert world.seats() == (("pm", "swe", "maker", "qa"), ("pm", "swe", "qa"))

    with world.client() as client:  # step 4: a restart
        ids, pipeline = _roster(client)
        assert "maker" in ids  # the UI still has it, leaving with this task
        assert pipeline["seats"] == ["pm", "swe", "maker", "qa"]
        assert pipeline["pending_seats"] == ["pm", "swe", "qa"]
        with client.websocket_connect(f"/ws/{PROJECT}") as ws:
            snapshot = ws.receive_json()
            assert snapshot["event"] == "snapshot"
            assert "maker" in [a["id"] for a in snapshot["data"]["agents"]]
            # step 5: approve -> the task finishes on the graph it started on
            assert client.post(_url(f"/tasks/{task_id}/approve")).status_code == 200
            events = _events_until(ws, "pipeline:updated")
        assert _wait_status(client, task_id, {"done", "failed"}) == "done"
        names = [e for e, _ in events]
        assert names.index("task:completed") < names.index("agent:left")
        assert ("agent:left", {"agent_id": "maker", "pipeline_id": "main"}) in events
        updated = dict(events)["pipeline:updated"]
        assert "maker" not in [a["id"] for a in updated["agents"]]
        ids, pipeline = _roster(client)
        assert ids == ["pm", "swe", "qa"]
        assert pipeline["seats"] == ["pm", "swe", "qa"] and pipeline["pending_seats"] is None
        assert client.get(_url("/agents/maker")).status_code == 404

    assert world.agents() == ["pm", "swe", "qa"]
    assert world.seats() == (("pm", "swe", "qa"), None)

    with world.client() as client:  # and it stays gone after another restart
        ids, pipeline = _roster(client)
        assert ids == ["pm", "swe", "qa"] and pipeline["seats"] == ["pm", "swe", "qa"]


def test_without_a_restart_the_row_goes_when_the_task_ends(tmp_path):
    world = World(tmp_path)
    with world.client() as client:
        assert client.post(_url("/agents"), json=MAKER).status_code == 201
        task_id = _gate_task(client)
        assert client.delete(_url("/agents/maker")).status_code == 202
        assert "maker" in world.agents()
        # A second DELETE of an agent already leaving: same answer, nothing changes.
        again = client.delete(_url("/agents/maker"))
        assert again.status_code == 202 and again.json() == {"agent_id": "maker", "effective": "next_task"}
        assert world.seats() == (("pm", "swe", "maker", "qa"), ("pm", "swe", "qa"))
        # A leaving agent can not be edited or seated again.
        assert client.put(_url("/agents/maker"), json={"duty": "x"}).status_code == 409
        seat_again = client.put(_url("/pipelines/main"), json={"seats": ["pm", "swe", "maker", "qa"]})
        assert seat_again.status_code == 422
        assert client.post(_url("/agents"), json=MAKER).status_code == 409  # the id is still taken
        assert client.post(_url(f"/tasks/{task_id}/approve")).status_code == 200
        assert _wait_status(client, task_id, {"done", "failed"}) == "done"
        _wait(lambda: world.agents() == ["pm", "swe", "qa"])
        assert world.seats() == (("pm", "swe", "qa"), None)
        assert client.delete(_url("/agents/maker")).status_code == 404
        # The id is free again.
        assert client.post(_url("/agents"), json=MAKER).status_code == 201


def test_deleting_when_idle_is_immediate(tmp_path):
    world = World(tmp_path)
    with world.client() as client:
        assert client.post(_url("/agents"), json=MAKER).status_code == 201
        response = client.delete(_url("/agents/maker"))
        assert response.status_code == 200 and response.json()["effective"] == "now"
        assert world.agents() == ["pm", "swe", "qa"]
        assert _roster(client)[0] == ["pm", "swe", "qa"]


def test_an_agent_only_on_the_pending_seats_goes_at_once(tmp_path):
    """Added while a task runs (pending) and deleted again before it ends: never seated, gone now."""

    world = World(tmp_path)
    with world.client() as client:
        task_id = _gate_task(client)
        assert client.post(_url("/agents"), json=DESIGNER).status_code == 201
        response = client.delete(_url("/agents/designer"))
        assert response.status_code == 202
        assert "designer" not in world.agents()
        assert world.seats() == (("pm", "swe", "qa"), ("pm", "swe", "qa"))
        assert client.post(_url(f"/tasks/{task_id}/approve")).status_code == 200
        assert _wait_status(client, task_id, {"done", "failed"}) == "done"


def test_import_keeps_a_seated_agent_until_the_task_ends_and_survives_a_restart(tmp_path):
    world = World(tmp_path)
    document = {"schema_version": 1, "agents": [], "pipelines": [
        {"id": "main", "name": "主線", "default": True, "seats": ["pm", "swe", "qa"]}]}
    with world.client() as client:
        assert client.post(_url("/agents"), json=MAKER).status_code == 201
        task_id = _gate_task(client)
        response = client.post(_url("/agents/import"), content=yaml.safe_dump(document),
                               headers={"content-type": "application/yaml"})
        assert response.status_code == 202
        assert "maker" in [a["id"] for a in response.json()["agents"]]
    assert "maker" in world.agents()
    assert world.seats() == (("pm", "swe", "maker", "qa"), ("pm", "swe", "qa"))

    with world.client() as client:  # a restart
        assert client.post(_url(f"/tasks/{task_id}/approve")).status_code == 200
        assert _wait_status(client, task_id, {"done", "failed"}) == "done"
        _wait(lambda: world.agents() == ["pm", "swe", "qa"])
        assert world.seats() == (("pm", "swe", "qa"), None)
