"""Plan §7: error ``detail`` is Traditional Chinese, including the 503 for a project whose
DB initialisation failed (``ProjectInitFailedError``) on every endpoint that can return it."""

from __future__ import annotations

import asyncio
import re

import pytest
from fastapi.testclient import TestClient

import config
from core.runtime import FactoryRuntime, ProjectInitFailedError
from core.stores.memory import InMemoryLineupRepository, InMemoryProjectRepository, InMemoryRunRepository
from main import create_app
from tests.test_api_db_mode import (  # noqa: F401  (fixtures)
    PROJECT,
    _app_whose_migration_fails,
    _mock_only,
    _project,
    repo,
)

_CJK = re.compile(r"[一-鿿]")
_ASCII_WORDS = re.compile(r"[A-Za-z]{4,}")


def _chinese_503(response) -> str:
    assert response.status_code == 503, response.text
    detail = response.json()["detail"]
    assert isinstance(detail, str) and _CJK.search(detail), detail
    return detail


def test_the_exception_message_is_chinese_and_names_the_project():
    message = str(ProjectInitFailedError("demo"))

    assert _CJK.search(message), message
    assert "demo" in message
    # Only the project id may be Latin text; the sentence itself is not English.
    assert _ASCII_WORDS.findall(message.replace("demo", "")) == [], message


def test_creating_a_task_answers_503_in_chinese(tmp_path, repo):
    _project(tmp_path)
    with TestClient(_app_whose_migration_fails(tmp_path, repo)) as client:
        detail = _chinese_503(client.post(f"/api/projects/{PROJECT}/tasks", json={"title": "never runs"}))

    assert PROJECT in detail
    assert asyncio.run(repo.list_tasks(PROJECT)) == []


def _lineup_app_whose_migration_fails(tmp_path, repo):
    """Like ``_app_whose_migration_fails`` but with a lineup repository, so lineup writes are allowed."""

    runs = InMemoryRunRepository()
    projects = InMemoryProjectRepository(repo, runs)

    async def always_fails(project_id, *, settings, tasks):
        raise RuntimeError("database hiccup")

    projects.create_from_migration = always_fails
    app = create_app(projects_dir=str(tmp_path))
    app.state.runtime = FactoryRuntime(
        projects_dir=tmp_path, task_store="db", task_repo=repo, run_repo=runs, project_repo=projects,
        lineup_repo=InMemoryLineupRepository(projects))
    return app


@pytest.mark.skipif(config.get_pipeline_engine() != "langgraph", reason="lineup writes need the graph engine")
def test_agent_writes_answer_503_in_chinese(tmp_path, repo):
    _project(tmp_path)
    agent = {"display_name": "設計師", "duty": "畫面", "prompt": "設計畫面", "kind": "make", "look": "beret"}
    with TestClient(_lineup_app_whose_migration_fails(tmp_path, repo)) as client:
        responses = [
            client.post(f"/api/projects/{PROJECT}/agents", json=agent),
            client.put(f"/api/projects/{PROJECT}/settings", json={"llm": "mock"}),
            client.put(f"/api/projects/{PROJECT}/pipelines/main", json={"seats": ["pm", "swe", "qa"]}),
        ]

    for response in responses:
        _chinese_503(response)
