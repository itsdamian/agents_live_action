"""GET /history and the task detail's runs against a real PostgreSQL (Task 15b, AC20 restart part).

The app is built the way the container builds it (TASK_STORE=db, DATABASE_URL, nothing injected).
A second app on the same database ("after a restart") lists the same notes.
"""

from __future__ import annotations

import time

import pytest
import yaml
from fastapi.testclient import TestClient

from core.task_history import DEFAULT_SEATS
from main import create_app

pytestmark = pytest.mark.integration

PROJECT = "demo"


@pytest.fixture
def env(monkeypatch, migrated_db_sqlalchemy_url):
    monkeypatch.setenv("FORCE_MOCK", "true")
    monkeypatch.setenv("TASK_STORE", "db")
    monkeypatch.setenv("PIPELINE_CHECKPOINTER", "memory")
    monkeypatch.setenv("DATABASE_URL", migrated_db_sqlalchemy_url)


def _wait(client, task_id, status, timeout=30):
    deadline = time.monotonic() + timeout
    seen = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/projects/{PROJECT}/tasks/{task_id}")
        seen = response.json()["task"]["status"] if response.status_code == 200 else None
        if seen == status:
            return
        time.sleep(0.1)
    raise AssertionError(f"{task_id} is {seen}, expected {status}")


def _approve_when_back_at_the_gate(client, task_id, timeout=30):
    # Right after a reject the stored status may still say awaiting_approval while the new gate
    # does not exist yet (approve is 409 then): retry until the reworked task waits again.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if client.post(f"/api/projects/{PROJECT}/tasks/{task_id}/approve").status_code == 200:
            return
        time.sleep(0.1)
    raise AssertionError(f"{task_id} never came back to its gate")


def test_history_and_runs_come_from_the_database_and_survive_a_new_app(tmp_path, env):
    project = tmp_path / PROJECT
    project.mkdir()
    (project / "tasks.yaml").write_text(yaml.safe_dump({"tasks": [
        {"id": "task-a", "title": "from the file", "description": "d", "priority": "high", "status": "pending"}]}),
        encoding="utf-8")

    with TestClient(create_app(projects_dir=str(tmp_path))) as client:
        _wait(client, "task-a", "done")
        created = client.post(f"/api/projects/{PROJECT}/tasks", json={"title": "gated", "approval": "after_swe"})
        gated = created.json()["task"]["id"]
        _wait(client, gated, "awaiting_approval")
        assert client.post(f"/api/projects/{PROJECT}/tasks/{gated}/reject", json={"note": "again"}).status_code == 200
        _approve_when_back_at_the_gate(client, gated)
        _wait(client, gated, "done")
        before = client.get(f"/api/projects/{PROJECT}/history").json()
        detail_before = client.get(f"/api/projects/{PROJECT}/tasks/{gated}").json()

    with TestClient(create_app(projects_dir=str(tmp_path))) as client:
        after = client.get(f"/api/projects/{PROJECT}/history").json()
        done_only = client.get(f"/api/projects/{PROJECT}/history?status=done&limit=1&offset=1").json()
        detail_after = client.get(f"/api/projects/{PROJECT}/tasks/{gated}").json()

    assert after == before
    assert after["total"] == 2 and after["counts"]["done"] == 2
    items = {i["task_id"]: i for i in after["items"]}
    assert items["task-a"]["path"] == ["pm", "swe", "qa"] and items["task-a"]["seats"] == DEFAULT_SEATS
    assert items[gated]["path"] == ["pm", "swe", "swe", "qa"]  # rejected at the gate, SWE again
    assert (items[gated]["rework_count"], items[gated]["runs"], items[gated]["outcome"]) == (1, 1, "done")
    # Finished notes, newest first: the gated task finished last.
    assert [i["task_id"] for i in after["items"]] == [gated, "task-a"]
    assert (done_only["total"], [i["task_id"] for i in done_only["items"]]) == (2, ["task-a"])

    assert detail_after["runs"] == detail_before["runs"]
    steps = [(s["seq"], s["kind"], s["station"], s["status"]) for s in detail_after["runs"][0]["steps"]]
    assert steps == [(1, "station", "pm", "done"), (2, "station", "swe", "done"), (3, "gate", "swe", "rejected"),
                     (4, "rework", "swe", "rework"), (5, "station", "swe", "done"), (6, "gate", "swe", "approved"),
                     (7, "station", "qa", "done")]
    assert [o["station"] for o in detail_after["agent_outputs"]] == ["pm", "swe", "qa"]
