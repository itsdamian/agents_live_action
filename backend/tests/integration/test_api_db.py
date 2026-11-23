"""The task REST flow against a real PostgreSQL (Task 11c, AC2 (b), (g)).

The app is built the way the container builds it: TASK_STORE=db and DATABASE_URL in the
environment, no injected repositories.
"""

from __future__ import annotations

import time
from pathlib import Path

import psycopg
import pytest
import yaml
from fastapi.testclient import TestClient

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


def _state(path: Path):
    return path.read_bytes(), path.stat().st_mtime_ns


def test_yaml_created_and_ui_created_tasks_both_run_and_the_file_is_never_written(tmp_path, env, migrated_db_url):
    project = tmp_path / PROJECT
    project.mkdir()
    tasks_yaml = project / "tasks.yaml"
    tasks_yaml.write_text(yaml.safe_dump({"tasks": [
        {"id": "task-file", "title": "from the file", "description": "d", "priority": "high", "status": "pending"}]}), encoding="utf-8")
    before = _state(tasks_yaml)

    with TestClient(create_app(projects_dir=str(tmp_path))) as client:
        _wait(client, "task-file", "done")  # imported from tasks.yaml at startup, then run
        created = client.post(f"/api/projects/{PROJECT}/tasks", json={"title": "from the UI", "approval": "after_qa"})
        assert created.status_code == 201
        ui_id = created.json()["task"]["id"]
        _wait(client, ui_id, "awaiting_approval")
        assert client.post(f"/api/projects/{PROJECT}/tasks/{ui_id}/approve").status_code == 200
        _wait(client, ui_id, "done")
        listed = {t["id"]: t["status"] for t in client.get(f"/api/projects/{PROJECT}/tasks").json()["tasks"]}
        exported = yaml.safe_load(client.get(f"/api/projects/{PROJECT}/tasks/export").text)["tasks"]

    assert listed == {"task-file": "done", ui_id: "done"}
    assert {t["id"] for t in exported} == {"task-file", ui_id}
    assert _state(tasks_yaml) == before  # byte for byte and mtime for mtime
    with psycopg.connect(migrated_db_url) as conn:
        rows = dict(conn.execute("SELECT id, source FROM tasks").fetchall())
    assert rows == {"task-file": "yaml", ui_id: "api"}


def test_editing_the_file_creates_a_task_and_rerun_runs_a_task_again(tmp_path, env):
    project = tmp_path / PROJECT
    project.mkdir()
    tasks_yaml = project / "tasks.yaml"
    tasks_yaml.write_text(yaml.safe_dump({"tasks": []}), encoding="utf-8")

    with TestClient(create_app(projects_dir=str(tmp_path))) as client:
        client.get(f"/api/projects/{PROJECT}/tasks")  # make sure the project is up
        tasks_yaml.write_text(yaml.safe_dump({"tasks": [
            {"id": "task-edit", "title": "added by editing", "description": "d", "priority": "low", "status": "pending"}]}), encoding="utf-8")
        _wait(client, "task-edit", "done")

        assert client.post(f"/api/projects/{PROJECT}/tasks/task-edit/rerun").status_code == 202
        orchestrator = client.app.state.runtime.orchestrators[PROJECT]
        deadline = time.monotonic() + 30
        while orchestrator.done_count < 2 and time.monotonic() < deadline:
            time.sleep(0.1)
        assert orchestrator.done_count == 2  # it ran twice
        _wait(client, "task-edit", "done")
        assert client.post(f"/api/projects/{PROJECT}/tasks/task-edit/rerun").status_code == 202  # done again: allowed
        assert client.post(f"/api/projects/{PROJECT}/tasks/nope/rerun").status_code == 404
