"""Plan §7 conventions for the endpoints this feature added (QA gate A, item 3).

``GET /history``, ``GET /tasks/export`` and ``POST /tasks/{tid}/rerun`` are ``async def`` and
their error ``detail`` is Traditional Chinese. Endpoints that existed before keep theirs.
"""

from __future__ import annotations

import asyncio
import inspect
import re

from fastapi.testclient import TestClient

import api.history
import api.tasks
from models.task import Status
from tests.test_api_db_mode import (  # noqa: F401  (fixtures)
    PROJECT,
    _app_whose_migration_fails,
    _mock_only,
    _project,
    _wait_status,
    client,
    file_client,
    repo,
)

_CJK = re.compile(r"[一-鿿]")


def _chinese_detail(response, status_code: int) -> str:
    assert response.status_code == status_code
    detail = response.json()["detail"]
    assert isinstance(detail, str) and _CJK.search(detail), detail
    return detail


def test_the_new_endpoints_are_async():
    for endpoint in (api.history.get_history, api.tasks.export_tasks, api.tasks.rerun_task):
        assert inspect.iscoroutinefunction(endpoint), endpoint.__name__


def test_history_errors_are_in_chinese(client, tmp_path):
    _project(tmp_path)
    _chinese_detail(client.get("/api/projects/no-such-project/history"), 404)
    detail = _chinese_detail(client.get(f"/api/projects/{PROJECT}/history?status=bogus"), 422)
    assert "bogus" in detail


def test_history_still_answers(client, tmp_path):
    _project(tmp_path)
    body = client.get(f"/api/projects/{PROJECT}/history").json()
    assert body["total"] == 0 and body["items"] == []


def test_rerun_and_export_errors_are_in_chinese(client, tmp_path):
    _project(tmp_path)
    client.post(f"/api/projects/{PROJECT}/tasks", json={"title": "x", "approval": "after_swe"})
    _wait_status(client, "task-001", "awaiting_approval")

    _chinese_detail(client.post(f"/api/projects/{PROJECT}/tasks/task-001/rerun"), 409)
    _chinese_detail(client.post(f"/api/projects/{PROJECT}/tasks/nope/rerun"), 404)
    _chinese_detail(client.post("/api/projects/no-such-project/tasks/task-001/rerun"), 404)
    _chinese_detail(client.get("/api/projects/no-such-project/tasks/export"), 404)
    assert client.get(f"/api/projects/{PROJECT}/tasks/export").status_code == 200


def test_file_mode_409s_are_in_chinese(file_client, tmp_path):
    _project(tmp_path, [{"id": "task-001", "title": "t", "description": "d", "priority": "medium",
                        "status": "done"}])
    _chinese_detail(file_client.post(f"/api/projects/{PROJECT}/tasks/task-001/rerun"), 409)
    _chinese_detail(file_client.get(f"/api/projects/{PROJECT}/tasks/export"), 409)


def test_rerun_in_a_project_that_failed_to_initialise_is_503_in_chinese(tmp_path, repo):
    _project(tmp_path)
    task = asyncio.run(repo.create(PROJECT, "old work", ""))
    asyncio.run(repo.set_status(PROJECT, task.id, Status.DONE))
    with TestClient(_app_whose_migration_fails(tmp_path, repo)) as client:
        _chinese_detail(client.post(f"/api/projects/{PROJECT}/tasks/{task.id}/rerun"), 503)
