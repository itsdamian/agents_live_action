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


def test_list_deliverables_after_pipeline_run(client, tmp_path):
    """A task run through the mock pipeline exposes downloadable deliverables."""

    client.post(
        "/api/projects/demo-project/tasks",
        json={"title": "Deliverable task", "priority": "critical"},
    )

    tasks_file = tmp_path / "demo-project" / "tasks.yaml"
    deadline = time.time() + 15
    while time.time() < deadline:
        raw = yaml.safe_load(tasks_file.read_text(encoding="utf-8"))
        entry = next(t for t in raw["tasks"] if t["id"] == "task-002")
        if entry["status"] == "done":
            break
        time.sleep(0.2)

    body = client.get(
        "/api/projects/demo-project/tasks/task-002/deliverables"
    ).json()
    names = [d["name"] for d in body["deliverables"]]
    assert "01_PM_agent.md" in names
    assert any(n.endswith("_SWE_agent_1.py") for n in names)

    # Every listed file is downloadable via its download_url.
    target = next(d for d in body["deliverables"] if d["name"] == "01_PM_agent.md")
    download = client.get(target["download_url"])
    assert download.status_code == 200
    assert download.content  # non-empty file body


def test_list_deliverables_empty_for_task_without_output(client):
    body = client.get(
        "/api/projects/demo-project/tasks/task-001/deliverables"
    ).json()
    assert body == {"task_id": "task-001", "deliverables": []}


def test_deliverables_unknown_project_404(client):
    assert (
        client.get("/api/projects/nope/tasks/task-001/deliverables").status_code
        == 404
    )


def test_download_unknown_deliverable_404(client):
    assert (
        client.get(
            "/api/projects/demo-project/tasks/task-001/deliverables/nope.md"
        ).status_code
        == 404
    )


def test_create_task_validation(client):
    assert (
        client.post("/api/projects/demo-project/tasks", json={"title": ""}).status_code
        == 422
    )
    assert (
        client.post("/api/projects/nope/tasks", json={"title": "x"}).status_code == 404
    )


def _run_task_to_done(client, tmp_path, title="Detail task", priority="critical"):
    """POST a task and wait for the mock pipeline to mark it done; return its id."""

    client.post(
        "/api/projects/demo-project/tasks",
        json={"title": title, "priority": priority},
    )
    tasks_file = tmp_path / "demo-project" / "tasks.yaml"
    deadline = time.time() + 15
    task_id = "task-002"
    while time.time() < deadline:
        raw = yaml.safe_load(tasks_file.read_text(encoding="utf-8"))
        entry = next(t for t in raw["tasks"] if t["id"] == task_id)
        if entry["status"] == "done":
            break
        time.sleep(0.2)
    return task_id


def test_get_task_detail_after_pipeline_run(client, tmp_path):
    """Task detail exposes stored fields, per-station outputs, logs, deliverables."""

    task_id = _run_task_to_done(client, tmp_path)

    body = client.get(f"/api/projects/demo-project/tasks/{task_id}").json()

    task = body["task"]
    assert task["id"] == task_id
    assert task["status"] == "done"
    assert "rework_count" in task
    assert "created_at" in task and "updated_at" in task
    # Per-station outputs rebuilt from the deliverable files.
    assert task["pm_output"]
    assert task["swe_output"]
    assert task["qa_output"]

    # Logs: one per station run, with links, and a matching status history.
    assert body["logs"], "expected at least one log file"
    agents_logged = {entry["agent"] for entry in body["logs"]}
    assert {"PM_agent", "SWE_agent", "QA_agent"} <= agents_logged
    assert all(entry["content_url"].endswith(entry["name"]) for entry in body["logs"])
    assert body["status_history"]

    # Deliverables echoed with download links.
    names = [d["name"] for d in body["deliverables"]]
    assert "01_PM_agent.md" in names


def test_get_task_detail_for_pending_task_has_no_outputs(client):
    """A task that never ran has its stored fields but empty outputs/logs."""

    body = client.get("/api/projects/demo-project/tasks/task-001").json()
    assert body["task"]["id"] == "task-001"
    assert body["task"]["pm_output"] is None
    assert body["logs"] == []
    assert body["deliverables"] == []


def test_get_task_detail_unknown_task_404(client):
    assert (
        client.get("/api/projects/demo-project/tasks/nope").status_code == 404
    )


def test_get_task_detail_unknown_project_404(client):
    assert (
        client.get("/api/projects/nope/tasks/task-001").status_code == 404
    )


def test_list_task_logs_after_pipeline_run(client, tmp_path):
    task_id = _run_task_to_done(client, tmp_path, title="Log task")

    body = client.get(f"/api/projects/demo-project/tasks/{task_id}/logs").json()
    assert body["task_id"] == task_id
    logs = body["logs"]
    assert logs, "expected log files for a task that ran"
    first = logs[0]
    assert first["content"], "log content should be included"
    assert first["status"] in {"SUCCESS", "FAILED"}
    assert first["content_url"].endswith(first["name"])


def test_list_task_logs_empty_for_task_without_run(client):
    body = client.get("/api/projects/demo-project/tasks/task-001/logs").json()
    assert body == {"task_id": "task-001", "logs": []}


def test_get_single_task_log_content(client, tmp_path):
    task_id = _run_task_to_done(client, tmp_path, title="Single log task")

    logs = client.get(
        f"/api/projects/demo-project/tasks/{task_id}/logs"
    ).json()["logs"]
    target = logs[0]

    resp = client.get(target["content_url"])
    assert resp.status_code == 200
    assert resp.text == target["content"]


def test_get_unknown_task_log_404(client):
    assert (
        client.get(
            "/api/projects/demo-project/tasks/task-001/logs/nope.log"
        ).status_code
        == 404
    )


def test_task_logs_unknown_project_404(client):
    assert (
        client.get("/api/projects/nope/tasks/task-001/logs").status_code == 404
    )


# ---------------------------------------------------------------------------
# Human-in-the-loop approval gate (task 17)
# ---------------------------------------------------------------------------


def _append_gated_task(tmp_path, task_id="task-gate", approval="after_swe", priority="critical"):
    """Append a task carrying an approval gate to the project's tasks.yaml.

    Writing the file directly (rather than via POST, which has no approval
    field) lets the running TaskWatcher pick the task up and pause it at a gate.
    """

    tasks_file = tmp_path / "demo-project" / "tasks.yaml"
    raw = yaml.safe_load(tasks_file.read_text(encoding="utf-8"))
    raw["tasks"].append(
        {
            "id": task_id,
            "title": "Gated task",
            "description": "Requires human sign-off.",
            "priority": priority,
            "status": "pending",
            "approval": approval,
        }
    )
    tasks_file.write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return tasks_file


def _wait_for_status(tasks_file, task_id, target, timeout=15):
    deadline = time.time() + timeout
    status = None
    while time.time() < deadline:
        raw = yaml.safe_load(tasks_file.read_text(encoding="utf-8"))
        entry = next((t for t in raw["tasks"] if t["id"] == task_id), None)
        status = entry["status"] if entry else None
        if status == target:
            return status
        time.sleep(0.1)
    return status


def test_approve_resumes_gated_task_to_done(client, tmp_path):
    """A task paused at an after_swe gate completes once approved via the API."""

    tasks_file = _append_gated_task(tmp_path, approval="after_swe")

    assert _wait_for_status(tasks_file, "task-gate", "awaiting_approval") == (
        "awaiting_approval"
    )

    resp = client.post("/api/projects/demo-project/tasks/task-gate/approve")
    assert resp.status_code == 200
    assert resp.json()["decision"] == "approved"

    assert _wait_for_status(tasks_file, "task-gate", "done") == "done"


def _poll_action(client, path, timeout=15):
    """Retry a gate action until it succeeds (200), tolerating the brief window
    before the task (re)registers at the gate. Returns the final response."""

    deadline = time.time() + timeout
    resp = client.post(path)
    while resp.status_code == 409 and time.time() < deadline:
        time.sleep(0.05)
        resp = client.post(path)
    return resp


def test_reject_routes_gated_task_through_rework(client, tmp_path):
    """Rejecting via the API pauses again after rework; a follow-up approve finishes it."""

    tasks_file = _append_gated_task(tmp_path, approval="after_swe")
    assert _wait_for_status(tasks_file, "task-gate", "awaiting_approval") == (
        "awaiting_approval"
    )

    reject = client.post(
        "/api/projects/demo-project/tasks/task-gate/reject",
        json={"note": "Add error handling"},
    )
    assert reject.status_code == 200
    assert reject.json()["decision"] == "rejected"

    # Rework re-runs SWE and pauses at the gate again; approving then completes
    # it. Poll the approve so we don't race the (fast, mock) rework pass.
    approve = _poll_action(
        client, "/api/projects/demo-project/tasks/task-gate/approve"
    )
    assert approve.status_code == 200

    assert _wait_for_status(tasks_file, "task-gate", "done") == "done"

    # The task was reworked exactly once before completing.
    raw = yaml.safe_load(tasks_file.read_text(encoding="utf-8"))
    entry = next(t for t in raw["tasks"] if t["id"] == "task-gate")
    assert entry["status"] == "done"


def test_approve_task_not_awaiting_returns_409(client):
    """Approving a task that is not at a gate is a 409 conflict."""

    resp = client.post("/api/projects/demo-project/tasks/task-001/approve")
    assert resp.status_code == 409


def test_reject_task_not_awaiting_returns_409(client):
    resp = client.post(
        "/api/projects/demo-project/tasks/task-001/reject", json={"note": "x"}
    )
    assert resp.status_code == 409


def test_approve_unknown_project_returns_404(client):
    resp = client.post("/api/projects/nope/tasks/task-001/approve")
    assert resp.status_code == 404


def test_websocket_receives_awaiting_approval_event(client, tmp_path):
    """A gated task broadcasts task:awaiting_approval to connected clients."""

    with client.websocket_connect("/ws/demo-project") as ws:
        assert ws.receive_json()["event"] == "snapshot"

        _append_gated_task(tmp_path, approval="after_swe")

        events: list[str] = []
        for _ in range(100):
            message = ws.receive_json()
            events.append(message["event"])
            if message["event"] == "task:awaiting_approval":
                break
        assert "task:awaiting_approval" in events

        # Approve and confirm the resume event reaches the client too.
        client.post("/api/projects/demo-project/tasks/task-gate/approve")
        for _ in range(100):
            message = ws.receive_json()
            events.append(message["event"])
            if message["event"] == "task:completed":
                break
        assert "task:approved" in events
        assert "task:completed" in events


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
