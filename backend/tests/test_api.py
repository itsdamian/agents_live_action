"""Integration tests for the REST API and WebSocket endpoint.

Each test builds the real app via ``main.create_app`` against a temporary
``projects/`` directory and drives it with FastAPI's TestClient. Entering the
client context runs the full lifespan, so these tests exercise the actual
startup path: project bootstrap, default agent creation, task watcher, and
pipeline workers — all in mock mode (LLM env keys are cleared).
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from config import PROVIDER_ENV_VARS
from main import create_app


@pytest.fixture(autouse=True)
def force_mock_mode(monkeypatch):
    """Clear every provider key so tests run deterministically in mock mode."""

    for env_var in PROVIDER_ENV_VARS.values():
        monkeypatch.delenv(env_var, raising=False)


def make_project(tmp_path: Path, name: str = "demo-project", tasks: list | None = None) -> Path:
    project = tmp_path / name
    project.mkdir()
    if tasks is not None:
        (project / "tasks.yaml").write_text(
            yaml.safe_dump({"tasks": tasks}, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
    return project


def sample_task(task_id: str = "task-001", status: str = "done") -> dict:
    return {
        "id": task_id,
        "title": "Sample task",
        "description": "A sample task.",
        "priority": "medium",
        "status": status,
    }


@pytest.fixture()
def client(tmp_path):
    make_project(tmp_path, tasks=[sample_task()])
    app = create_app(projects_dir=str(tmp_path))
    with TestClient(app) as test_client:
        yield test_client


def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["mock_mode"] is True


def test_list_projects(client):
    body = client.get("/api/projects").json()
    assert body["mock_mode"] is True
    ids = [p["id"] for p in body["projects"]]
    assert ids == ["demo-project"]
    assert body["projects"][0]["task_count"] == 1


def test_list_projects_reflects_new_folder(client, tmp_path):
    """A folder dropped in after startup appears on the next query (Req 1.4)."""

    make_project(tmp_path, name="late-project")
    ids = [p["id"] for p in client.get("/api/projects").json()["projects"]]
    assert sorted(ids) == ["demo-project", "late-project"]


def test_get_project_detail_creates_default_agents(client, tmp_path):
    body = client.get("/api/projects/demo-project").json()
    agent_names = {a["name"] for a in body["agents"]}
    assert agent_names == {"PM_agent", "SWE_agent", "QA_agent", "DevOps_agent"}
    assert set(body["pipeline"]["stations"].keys()) == {"pm", "swe", "qa"}
    # The default YAML files were materialised on disk (Requirement 2.2).
    agents_dir = tmp_path / "demo-project" / "agents"
    assert sorted(p.name for p in agents_dir.glob("*.yaml")) == [
        "DevOps_agent.yaml",
        "PM_agent.yaml",
        "QA_agent.yaml",
        "SWE_agent.yaml",
    ]


def test_get_unknown_project_404(client):
    assert client.get("/api/projects/nope").status_code == 404


def test_list_tasks(client):
    body = client.get("/api/projects/demo-project/tasks").json()
    assert [t["id"] for t in body["tasks"]] == ["task-001"]


def test_create_task_appends_to_yaml(client, tmp_path):
    response = client.post(
        "/api/projects/demo-project/tasks",
        json={"title": "New feature", "description": "Build it.", "priority": "high"},
    )
    assert response.status_code == 201
    created = response.json()["task"]
    assert created["id"] == "task-002"
    assert created["status"] == "pending"

    raw = yaml.safe_load(
        (tmp_path / "demo-project" / "tasks.yaml").read_text(encoding="utf-8")
    )
    ids = [t["id"] for t in raw["tasks"]]
    assert ids == ["task-001", "task-002"]


def test_create_task_is_picked_up_by_pipeline(client, tmp_path):
    """The watcher sees the POSTed task and the mock pipeline completes it."""

    client.post(
        "/api/projects/demo-project/tasks",
        json={"title": "Run through pipeline", "priority": "critical"},
    )

    tasks_file = tmp_path / "demo-project" / "tasks.yaml"
    deadline = time.time() + 15
    status = "pending"
    while time.time() < deadline:
        raw = yaml.safe_load(tasks_file.read_text(encoding="utf-8"))
        entry = next(t for t in raw["tasks"] if t["id"] == "task-002")
        status = entry["status"]
        if status == "done":
            break
        time.sleep(0.2)
    assert status == "done"


def test_create_task_validation(client):
    assert (
        client.post("/api/projects/demo-project/tasks", json={"title": ""}).status_code
        == 422
    )
    assert (
        client.post("/api/projects/nope/tasks", json={"title": "x"}).status_code == 404
    )


def test_websocket_snapshot_and_close(client):
    with client.websocket_connect("/ws/demo-project") as ws:
        message = ws.receive_json()
        assert message["event"] == "snapshot"
        assert message["project_id"] == "demo-project"
        data = message["data"]
        assert data["mock_mode"] is True
        assert set(data["pipeline"]["stations"].keys()) == {"pm", "swe", "qa"}
        assert [t["id"] for t in data["tasks"]] == ["task-001"]


def test_websocket_unknown_project_closes(client):
    with client.websocket_connect("/ws/nope") as ws:
        # Server accepts then closes with an application code; the next
        # receive surfaces the close frame.
        message = ws.receive()
        assert message["type"] == "websocket.close"
        assert message["code"] == 4004


def test_websocket_receives_live_pipeline_events(client):
    """After the snapshot, live pipeline events reach the connected client.

    A task POSTed while a client is connected is picked up by the watcher and
    run through the mock pipeline; every state change the orchestrator emits is
    broadcast to the WebSocket (Requirements 7.1, 7.6).
    """

    with client.websocket_connect("/ws/demo-project") as ws:
        assert ws.receive_json()["event"] == "snapshot"

        client.post(
            "/api/projects/demo-project/tasks",
            json={"title": "Live event task", "priority": "critical"},
        )

        events: list[str] = []
        # task:completed always follows task:created + station:update events;
        # bounded so a missing terminal event can't loop forever.
        for _ in range(100):
            message = ws.receive_json()
            assert message["project_id"] == "demo-project"
            events.append(message["event"])
            if message["event"] == "task:completed":
                break

        assert "task:created" in events
        assert "station:update" in events
        assert "task:completed" in events
