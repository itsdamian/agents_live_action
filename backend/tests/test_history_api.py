"""GET /history, snapshot ``history_counts`` and the task detail's ``runs`` / ``agent_outputs``
(Task 15b, plan §7, §7.1a, D20), in both task stores, with in-memory repositories and the Mock LLM.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from core.runtime import FactoryRuntime
from core.stores.memory import InMemoryRunRepository, InMemoryTaskRepository
from core.task_history import DEFAULT_SEATS
from main import create_app

PROJECT = "demo"


@pytest.fixture(autouse=True)
def _mock_only(monkeypatch):
    monkeypatch.setenv("FORCE_MOCK", "true")
    for name in ("TASK_STORE", "PIPELINE_CHECKPOINTER", "DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)


def _project(root: Path, entries=()) -> Path:
    project = root / PROJECT
    project.mkdir(parents=True, exist_ok=True)
    (project / "tasks.yaml").write_text(yaml.safe_dump({"tasks": list(entries)}, sort_keys=False), encoding="utf-8")
    return project


def _entry(task_id, status="pending", **extra):
    return {"id": task_id, "title": f"title {task_id}", "description": "d", "priority": "medium",
            "status": status, **extra}


def _db_app(root: Path, tasks=None, runs=None):
    app = create_app(projects_dir=str(root))
    app.state.runtime = FactoryRuntime(projects_dir=root, task_store="db", task_repo=tasks or InMemoryTaskRepository(),
                                       run_repo=runs or InMemoryRunRepository())
    return app


def _file_app(root: Path):
    return create_app(projects_dir=str(root))  # TASK_STORE unset: file mode


def _wait_status(client, task_id, target, timeout=20):
    deadline = time.monotonic() + timeout
    seen = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/projects/{PROJECT}/tasks/{task_id}")
        seen = response.json()["task"]["status"] if response.status_code == 200 else None
        if seen == target:
            return
        time.sleep(0.05)
    raise AssertionError(f"{task_id} is {seen}, expected {target}")


def _history(client, query=""):
    response = client.get(f"/api/projects/{PROJECT}/history{query}")
    assert response.status_code == 200, response.text
    return response.json()


def _snapshot(client):
    with client.websocket_connect(f"/ws/{PROJECT}") as ws:
        message = ws.receive_json()
    assert message["event"] == "snapshot"
    return message["data"]


# -- DB mode ---------------------------------------------------------------------------------------------------


def test_db_mode_history_lists_a_finished_task_with_its_run(tmp_path):
    _project(tmp_path)
    with TestClient(_db_app(tmp_path)) as client:
        client.post(f"/api/projects/{PROJECT}/tasks", json={"title": "one"})
        _wait_status(client, "task-001", "done")
        body = _history(client)

    assert (body["total"], body["counts"]) == (
        1, {"pending": 0, "in_progress": 0, "awaiting_approval": 0, "done": 1, "failed": 0})
    item = body["items"][0]
    assert (item["task_id"], item["status"], item["outcome"], item["runs"], item["stale"]) == (
        "task-001", "done", "done", 1, False)
    assert item["path"] == ["pm", "swe", "qa"] and item["seats"] == DEFAULT_SEATS
    assert item["deliverable_count"] >= 3 and item["finished_at"] is not None
    assert isinstance(item["cost_usd"], float)


def test_db_mode_gate_counts_in_history_and_snapshot(tmp_path):
    _project(tmp_path)
    with TestClient(_db_app(tmp_path)) as client:
        client.post(f"/api/projects/{PROJECT}/tasks", json={"title": "gated", "approval": "after_swe"})
        _wait_status(client, "task-001", "awaiting_approval")
        waiting = _history(client, "?status=awaiting_approval")
        snap = _snapshot(client)
        client.post(f"/api/projects/{PROJECT}/tasks/task-001/approve")
        _wait_status(client, "task-001", "done")
        after = _snapshot(client)

    assert [i["task_id"] for i in waiting["items"]] == ["task-001"]
    assert waiting["items"][0]["path"] == ["pm", "swe"]
    assert snap["history_counts"]["awaiting_approval"] == 1 and snap["history_counts"]["done"] == 0
    assert after["history_counts"] == {"pending": 0, "in_progress": 0, "awaiting_approval": 0, "done": 1, "failed": 0}


def test_db_mode_task_detail_has_runs_and_agent_outputs(tmp_path):
    _project(tmp_path)
    with TestClient(_db_app(tmp_path)) as client:
        client.post(f"/api/projects/{PROJECT}/tasks", json={"title": "one"})
        _wait_status(client, "task-001", "done")
        detail = client.get(f"/api/projects/{PROJECT}/tasks/task-001").json()

    assert {"task", "status_history", "logs", "deliverables", "usage"} <= set(detail)  # existing keys stay
    assert [r["run_no"] for r in detail["runs"]] == [1]
    assert [s["station"] for s in detail["runs"][0]["steps"]] == ["pm", "swe", "qa"]
    assert detail["runs"][0]["outcome"] == "done" and detail["runs"][0]["seats"] == DEFAULT_SEATS
    assert [(o["station"], o["display_name"], o["truncated"]) for o in detail["agent_outputs"]] == [
        ("pm", "PM", False), ("swe", "SWE", False), ("qa", "QA", False)]
    assert all(o["content"] for o in detail["agent_outputs"])


def test_db_mode_history_survives_a_new_runtime(tmp_path):
    _project(tmp_path)
    tasks, runs = InMemoryTaskRepository(), InMemoryRunRepository()
    with TestClient(_db_app(tmp_path, tasks, runs)) as client:
        client.post(f"/api/projects/{PROJECT}/tasks", json={"title": "one"})
        _wait_status(client, "task-001", "done")
        before = _history(client)

    with TestClient(_db_app(tmp_path, tasks, runs)) as client:  # "restart": same storage, new process
        after = _history(client)
        snap = _snapshot(client)

    assert after == before
    assert snap["history_counts"]["done"] == 1


def test_query_validation_and_unknown_project(tmp_path):
    _project(tmp_path)
    with TestClient(_db_app(tmp_path)) as client:
        for query in ("?status=finished", "?limit=0", "?limit=201", "?offset=-1"):
            assert client.get(f"/api/projects/{PROJECT}/history{query}").status_code == 422, query
        assert client.get(f"/api/projects/{PROJECT}/history?limit=200").status_code == 200
        assert client.get("/api/projects/nope/history").status_code == 404


# -- file mode --------------------------------------------------------------------------------------------------


def test_file_mode_history_from_yaml_logs_and_deliverables(tmp_path):
    _project(tmp_path, [_entry("task-001"), _entry("task-old", status="in_progress")])
    with TestClient(_file_app(tmp_path)) as client:
        _wait_status(client, "task-001", "done")
        body = _history(client)
        filtered = _history(client, "?status=done&pipeline=main")
        snap = _snapshot(client)
        detail = client.get(f"/api/projects/{PROJECT}/tasks/task-001").json()

    items = {i["task_id"]: i for i in body["items"]}
    done = items["task-001"]
    assert (done["status"], done["path"], done["runs"], done["outcome"], done["cost_usd"]) == (
        "done", ["pm", "swe", "qa"], 0, None, None)
    assert done["deliverable_count"] >= 3 and done["seats"] == DEFAULT_SEATS
    # Left in_progress by an earlier process and nobody runs it: stale.
    assert (items["task-old"]["status"], items["task-old"]["stale"]) == ("in_progress", True)
    assert [i["task_id"] for i in filtered["items"]] == ["task-001"]
    assert body["counts"] == snap["history_counts"] == {
        "pending": 0, "in_progress": 1, "awaiting_approval": 0, "done": 1, "failed": 0}
    assert detail["runs"] == []
    assert [o["station"] for o in detail["agent_outputs"]] == ["pm", "swe", "qa"]
