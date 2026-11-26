"""Removing an agent a gated task still needs, then restarting, on a real PostgreSQL (Task 26a fix).

The Postgres version of ``tests/test_agent_removal_restart.py``: the app builds its repositories
and the checkpointer from ``DATABASE_URL`` the way the container does. After ``DELETE`` (202) and
a restart, the agent row is still there (``projects.settings.leaving_agents`` names it), the task
resumed at its gate finishes on its old graph, and the row goes when the task ends.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import text

from db.session import create_engine_for
from main import create_app

pytestmark = pytest.mark.integration

PROJECT = "demo"
MAKER = {"id": "maker", "display_name": "打樣師", "duty": "做出樣品", "prompt": "你是打樣師。",
         "kind": "make", "look": "beret", "after": "swe"}


@pytest.fixture
def db_env(monkeypatch, migrated_db_sqlalchemy_url):
    monkeypatch.setenv("FORCE_MOCK", "true")
    monkeypatch.setenv("MOCK_LLM_DELAY", "0")
    monkeypatch.setenv("PIPELINE_ENGINE", "langgraph")
    monkeypatch.setenv("TASK_STORE", "db")
    monkeypatch.setenv("PIPELINE_CHECKPOINTER", "postgres")
    monkeypatch.setenv("DATABASE_URL", migrated_db_sqlalchemy_url)
    return migrated_db_sqlalchemy_url


def _project(root: Path) -> None:
    (root / PROJECT).mkdir(parents=True)
    (root / PROJECT / "tasks.yaml").write_text(yaml.safe_dump({"tasks": []}), encoding="utf-8")


def _rows(url: str, sql: str):
    async def query():
        engine = create_engine_for(url, null_pool=True)
        try:
            async with engine.connect() as conn:
                return [tuple(r) for r in (await conn.execute(text(sql))).all()]
        finally:
            await engine.dispose()

    return asyncio.run(query())


def _agent_ids(url):
    return [r[0] for r in _rows(url, f"SELECT id FROM agents WHERE project_id = '{PROJECT}' ORDER BY id")]


def _seats(url):
    seats, pending = _rows(url, f"SELECT seats, pending_seats FROM pipelines WHERE project_id = '{PROJECT}'")[0]
    return list(seats), None if pending is None else list(pending)


def _leaving(url):
    return _rows(url, f"SELECT settings -> 'leaving_agents' FROM projects WHERE id = '{PROJECT}'")[0][0]


def _url(path: str) -> str:
    return f"/api/projects/{PROJECT}{path}"


def _wait_status(client, task_id, statuses, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.get(_url(f"/tasks/{task_id}")).json()["task"]["status"]
        if status in statuses:
            return status
        time.sleep(0.05)
    raise AssertionError(f"task {task_id} never reached {statuses}")


def _wait(predicate, timeout=30.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "condition not reached in time"
        time.sleep(0.05)


def test_removing_an_agent_a_gated_task_needs_survives_a_restart(tmp_path, db_env):
    _project(tmp_path)
    with TestClient(create_app(projects_dir=str(tmp_path))) as client:
        assert client.post(_url("/agents"), json=MAKER).status_code == 201
        task_id = client.post(_url("/tasks"), json={"title": "gate", "approval": "after_swe"}).json()["task"]["id"]
        _wait_status(client, task_id, {"awaiting_approval"})
        response = client.delete(_url("/agents/maker"))
        assert response.status_code == 202 and response.json()["effective"] == "next_task"

    assert "maker" in _agent_ids(db_env)
    assert _seats(db_env) == (["pm", "swe", "maker", "qa"], ["pm", "swe", "qa"])
    assert _leaving(db_env) == ["maker"]

    with TestClient(create_app(projects_dir=str(tmp_path))) as client:  # a restart
        body = client.get(_url("/agents")).json()
        assert "maker" in [a["id"] for a in body["agents"]]
        assert body["pipelines"][0]["seats"] == ["pm", "swe", "maker", "qa"]
        assert body["pipelines"][0]["pending_seats"] == ["pm", "swe", "qa"]

        assert client.post(_url(f"/tasks/{task_id}/approve")).status_code == 200
        assert _wait_status(client, task_id, {"done", "failed"}) == "done"
        _wait(lambda: "maker" not in _agent_ids(db_env))
        body = client.get(_url("/agents")).json()

    assert [a["id"] for a in body["agents"]] == ["pm", "swe", "qa"]
    assert _agent_ids(db_env) == ["pm", "qa", "swe"]
    assert _seats(db_env) == (["pm", "swe", "qa"], None)
    assert _leaving(db_env) is None
