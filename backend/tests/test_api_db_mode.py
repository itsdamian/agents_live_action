"""The task REST endpoints in DB mode, with in-memory repositories (Task 11c, AC2 (g), AC19-style 409s).

TASK_STORE=file behaviour is covered by the unchanged tests/test_api.py; here the runtime is
injected in DB mode and tasks.yaml must never be written.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from core.runtime import FactoryRuntime
from core.stores.memory import InMemoryRunRepository, InMemoryTaskRepository
from main import create_app
from models.task import Status

PROJECT = "demo"


@pytest.fixture(autouse=True)
def _mock_only(monkeypatch):
    monkeypatch.setenv("FORCE_MOCK", "true")
    for name in ("TASK_STORE", "PIPELINE_CHECKPOINTER", "DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)


def _project(root: Path, entries=None, write_yaml=True) -> Path:
    project = root / PROJECT
    project.mkdir(parents=True, exist_ok=True)
    if write_yaml:
        (project / "tasks.yaml").write_text(
            yaml.safe_dump({"tasks": entries or []}, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return project


def _entry(task_id, status="pending", priority="medium"):
    return {"id": task_id, "title": f"title {task_id}", "description": "d", "priority": priority, "status": status}


@pytest.fixture
def repo():
    return InMemoryTaskRepository()


@pytest.fixture
def client(tmp_path, repo):
    app = create_app(projects_dir=str(tmp_path))
    app.state.runtime = FactoryRuntime(
        projects_dir=tmp_path, task_store="db", task_repo=repo, run_repo=InMemoryRunRepository())
    with TestClient(app) as test_client:
        yield test_client


def _status(client, task_id, project=PROJECT):
    body = client.get(f"/api/projects/{project}/tasks/{task_id}")
    return body.json()["task"]["status"] if body.status_code == 200 else None


def _wait_status(client, task_id, target, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _status(client, task_id) == target:
            return
        time.sleep(0.05)
    raise AssertionError(f"{task_id} never became {target}; is {_status(client, task_id)}")


def _file_state(project: Path):
    path = project / "tasks.yaml"
    return (path.read_bytes(), path.stat().st_mtime_ns) if path.exists() else None


# -- list and detail come from the database -----------------------------------------------------------


def test_list_and_detail_read_the_database_not_the_file(tmp_path, repo):
    project = _project(tmp_path, [_entry("task-001", status="pending")])
    app = create_app(projects_dir=str(tmp_path))
    app.state.runtime = FactoryRuntime(projects_dir=tmp_path, task_store="db", task_repo=repo, run_repo=InMemoryRunRepository())
    with TestClient(app) as client:
        _wait_status(client, "task-001", "done")  # runs, and the file still says pending

        listed = client.get(f"/api/projects/{PROJECT}/tasks").json()["tasks"]
        detail = client.get(f"/api/projects/{PROJECT}/tasks/task-001").json()

    assert [(t["id"], t["status"]) for t in listed] == [("task-001", "done")]
    assert detail["task"]["status"] == "done" and "usage" in detail and "deliverables" in detail
    assert yaml.safe_load((project / "tasks.yaml").read_text())["tasks"][0]["status"] == "pending"


def test_unknown_task_and_project_are_404(client, tmp_path):
    _project(tmp_path)
    assert client.get(f"/api/projects/{PROJECT}/tasks/nope").status_code == 404
    assert client.get("/api/projects/no-such-project/tasks").status_code == 404
    assert client.post("/api/projects/no-such-project/tasks", json={"title": "x"}).status_code == 404


# -- AC2 (g): UI-created tasks go to the database, never to tasks.yaml -------------------------------------


def test_a_task_created_in_the_ui_is_stored_in_the_database_run_and_never_written_to_yaml(tmp_path, repo):
    project = _project(tmp_path, [_entry("task-existing", status="done")])
    before = _file_state(project)
    app = create_app(projects_dir=str(tmp_path))
    app.state.runtime = FactoryRuntime(projects_dir=tmp_path, task_store="db", task_repo=repo, run_repo=InMemoryRunRepository())
    with TestClient(app) as client:
        response = client.post(f"/api/projects/{PROJECT}/tasks", json={"title": "  Built in the UI  ", "priority": "high"})
        assert response.status_code == 201
        task = response.json()["task"]
        assert (task["id"], task["title"], task["status"], task["priority"]) == ("task-001", "Built in the UI", "pending", "high")
        _wait_status(client, "task-001", "done")

    stored = asyncio.run(repo.get(PROJECT, "task-001"))
    assert (stored.status, asyncio.run(repo.get_meta(PROJECT, "task-001")).source) == (Status.DONE, "api")
    assert _file_state(project) == before  # not one byte, not one mtime tick


def test_creating_a_task_does_not_create_tasks_yaml_for_a_project_without_one(tmp_path, repo):
    project = _project(tmp_path, write_yaml=False)
    app = create_app(projects_dir=str(tmp_path))
    app.state.runtime = FactoryRuntime(projects_dir=tmp_path, task_store="db", task_repo=repo, run_repo=InMemoryRunRepository())
    with TestClient(app) as client:
        assert client.post(f"/api/projects/{PROJECT}/tasks", json={"title": "x"}).status_code == 201
        _wait_status(client, "task-001", "done")

    assert not (project / "tasks.yaml").exists()


def test_ids_continue_across_ui_created_tasks(client, tmp_path):
    _project(tmp_path)
    ids = [client.post(f"/api/projects/{PROJECT}/tasks", json={"title": f"t{i}"}).json()["task"]["id"] for i in range(3)]

    assert ids == ["task-001", "task-002", "task-003"]


@pytest.mark.parametrize("payload", [{"title": ""}, {"title": "   "}, {"title": "x", "priority": "urgent"},
                                     {"title": "x", "approval": "sometimes"}, {}])
def test_invalid_task_payloads_are_422_and_store_nothing(client, tmp_path, repo, payload):
    _project(tmp_path)

    assert client.post(f"/api/projects/{PROJECT}/tasks", json=payload).status_code == 422
    assert asyncio.run(repo.list_tasks(PROJECT)) == []


def test_the_approval_field_makes_the_task_stop_at_a_gate_and_the_gate_works(client, tmp_path):
    _project(tmp_path)
    created = client.post(f"/api/projects/{PROJECT}/tasks", json={"title": "gated", "approval": "after_swe"})
    assert created.status_code == 201 and created.json()["task"]["approval"] == "after_swe"

    _wait_status(client, "task-001", "awaiting_approval")
    assert client.post(f"/api/projects/{PROJECT}/tasks/task-001/approve").status_code == 200
    _wait_status(client, "task-001", "done")


# -- rerun ---------------------------------------------------------------------------------------------------------


def test_rerun_runs_a_finished_task_again(client, tmp_path, repo):
    _project(tmp_path)
    client.post(f"/api/projects/{PROJECT}/tasks", json={"title": "again"})
    _wait_status(client, "task-001", "done")
    asyncio.run(repo.set_rework_count(PROJECT, "task-001", 2))

    response = client.post(f"/api/projects/{PROJECT}/tasks/task-001/rerun")

    assert response.status_code == 202
    assert response.json()["task"]["id"] == "task-001" and response.json()["task"]["rework_count"] == 0
    _wait_status(client, "task-001", "done")
    # It really ran a second time: the worker has completed two tasks now.
    orchestrator = client.app.state.runtime.orchestrators[PROJECT]
    deadline = time.monotonic() + 20
    while orchestrator.done_count < 2 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert orchestrator.done_count == 2


def test_rerun_of_a_failed_task_works_too(client, tmp_path, repo):
    _project(tmp_path)
    client.post(f"/api/projects/{PROJECT}/tasks", json={"title": "x"})
    _wait_status(client, "task-001", "done")
    asyncio.run(repo.set_status(PROJECT, "task-001", Status.FAILED))

    assert client.post(f"/api/projects/{PROJECT}/tasks/task-001/rerun").status_code == 202
    _wait_status(client, "task-001", "done")


def test_rerun_is_refused_unless_the_task_is_done_or_failed(client, tmp_path, repo):
    _project(tmp_path)
    client.post(f"/api/projects/{PROJECT}/tasks", json={"title": "x", "approval": "after_swe"})
    _wait_status(client, "task-001", "awaiting_approval")

    assert client.post(f"/api/projects/{PROJECT}/tasks/task-001/rerun").status_code == 409
    assert client.post(f"/api/projects/{PROJECT}/tasks/nope/rerun").status_code == 404
    assert client.post("/api/projects/no-such-project/tasks/task-001/rerun").status_code == 404


# -- a project whose initialisation failed refuses new work (503) -----------------------------------------


def _app_whose_migration_fails(tmp_path, repo):
    """DB mode where the project's migration always fails, so its worker never starts."""

    from core.stores.memory import InMemoryProjectRepository

    runs = InMemoryRunRepository()
    projects = InMemoryProjectRepository(repo, runs)

    async def always_fails(project_id, *, settings, tasks):
        raise RuntimeError("database hiccup")

    projects.create_from_migration = always_fails
    app = create_app(projects_dir=str(tmp_path))
    app.state.runtime = FactoryRuntime(
        projects_dir=tmp_path, task_store="db", task_repo=repo, run_repo=runs, project_repo=projects)
    return app


def test_creating_a_task_in_a_project_that_failed_to_initialise_is_503_and_stores_nothing(tmp_path, repo, caplog):
    _project(tmp_path)
    caplog.set_level("ERROR")
    with TestClient(_app_whose_migration_fails(tmp_path, repo)) as client:
        response = client.post(f"/api/projects/{PROJECT}/tasks", json={"title": "never runs"})

    assert response.status_code == 503
    assert "initiali" in response.json()["detail"] and "restart" in response.json()["detail"]
    assert asyncio.run(repo.list_tasks(PROJECT)) == []  # nothing accepted, nothing left pending forever
    assert "database hiccup" in caplog.text  # the original error is still logged


def test_rerun_in_a_project_that_failed_to_initialise_is_503_and_changes_nothing(tmp_path, repo):
    _project(tmp_path)
    task = asyncio.run(repo.create(PROJECT, "old work", ""))
    asyncio.run(repo.set_status(PROJECT, task.id, Status.DONE))
    with TestClient(_app_whose_migration_fails(tmp_path, repo)) as client:
        response = client.post(f"/api/projects/{PROJECT}/tasks/{task.id}/rerun")

    assert response.status_code == 503
    assert asyncio.run(repo.get(PROJECT, task.id)).status is Status.DONE


# -- export -----------------------------------------------------------------------------------------------------------


def test_export_returns_the_database_as_a_tasks_yaml_document(client, tmp_path):
    _project(tmp_path)
    client.post(f"/api/projects/{PROJECT}/tasks", json={"title": "中文標題", "priority": "low", "approval": "after_qa"})
    client.post(f"/api/projects/{PROJECT}/tasks", json={"title": "second"})

    response = client.get(f"/api/projects/{PROJECT}/tasks/export")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/yaml")
    assert 'filename="tasks.yaml"' in response.headers["content-disposition"]
    tasks = yaml.safe_load(response.text)["tasks"]
    assert [t["id"] for t in tasks] == ["task-001", "task-002"]
    assert (tasks[0]["title"], tasks[0]["priority"], tasks[0]["approval"]) == ("中文標題", "low", "after_qa")
    assert "approval" not in tasks[1]  # defaults are left out, like a hand-written file
    assert all(t["status"] in {"pending", "in_progress", "done", "awaiting_approval"} for t in tasks)


def test_export_is_not_swallowed_by_the_task_detail_route(client, tmp_path):
    _project(tmp_path)

    assert client.get(f"/api/projects/{PROJECT}/tasks/export").status_code == 200
    assert client.get("/api/projects/no-such-project/tasks/export").status_code == 404


# -- file mode keeps its behaviour --------------------------------------------------------------------------------------


@pytest.fixture
def file_client(tmp_path):
    with TestClient(create_app(projects_dir=str(tmp_path))) as test_client:
        yield test_client


def test_in_file_mode_rerun_and_export_are_409(file_client, tmp_path):
    _project(tmp_path, [_entry("task-001", status="done")])

    assert file_client.post(f"/api/projects/{PROJECT}/tasks/task-001/rerun").status_code == 409
    assert file_client.get(f"/api/projects/{PROJECT}/tasks/export").status_code == 409


def test_in_file_mode_the_approval_field_is_written_to_tasks_yaml_only_when_set(file_client, tmp_path):
    project = _project(tmp_path, [_entry("task-001", status="done")])

    gated = file_client.post(f"/api/projects/{PROJECT}/tasks", json={"title": "gated", "approval": "after_qa"})
    plain = file_client.post(f"/api/projects/{PROJECT}/tasks", json={"title": "plain"})

    assert gated.status_code == plain.status_code == 201
    entries = {e["id"]: e for e in yaml.safe_load((project / "tasks.yaml").read_text())["tasks"]}
    assert entries[gated.json()["task"]["id"]]["approval"] == "after_qa"
    assert "approval" not in entries[plain.json()["task"]["id"]]
