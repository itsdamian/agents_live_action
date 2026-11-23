"""Agents and pipelines REST on a real PostgreSQL (Task 25a, AC16-AC18).

The app builds its repositories from ``DATABASE_URL`` the way the container does. A custom
agent survives a restart, a refused request leaves no row, export -> import into another
project gives the same roster and seats, and a seat change while a task waits at its gate only
records ``pending_seats``.
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
OTHER = "other"
DESIGNER = {"id": "designer", "display_name": "設計師", "duty": "畫介面草圖", "prompt": "你是介面設計師。",
            "kind": "make", "look": "beret", "after": "pm"}


@pytest.fixture
def db_env(monkeypatch, migrated_db_sqlalchemy_url):
    monkeypatch.setenv("FORCE_MOCK", "true")
    monkeypatch.setenv("MOCK_LLM_DELAY", "0")
    monkeypatch.setenv("PIPELINE_ENGINE", "langgraph")
    monkeypatch.setenv("TASK_STORE", "db")
    monkeypatch.setenv("PIPELINE_CHECKPOINTER", "postgres")
    monkeypatch.setenv("DATABASE_URL", migrated_db_sqlalchemy_url)
    return migrated_db_sqlalchemy_url


def _project(root: Path, name: str) -> None:
    (root / name).mkdir(parents=True)
    (root / name / "tasks.yaml").write_text(yaml.safe_dump({"tasks": []}), encoding="utf-8")


def _rows(url: str, sql: str):
    async def query():
        engine = create_engine_for(url, null_pool=True)
        try:
            async with engine.connect() as conn:
                return [tuple(r) for r in (await conn.execute(text(sql))).all()]
        finally:
            await engine.dispose()

    return asyncio.run(query())


def _agent_ids(url, project=PROJECT):
    return [r[0] for r in _rows(url, f"SELECT id FROM agents WHERE project_id = '{project}' ORDER BY id")]


def _seats(url, project=PROJECT):
    return _rows(url, f"SELECT seats, pending_seats FROM pipelines WHERE project_id = '{project}'")[0]


def test_a_custom_agent_survives_a_restart_and_refusals_leave_no_row(tmp_path, db_env):
    _project(tmp_path, PROJECT)
    with TestClient(create_app(projects_dir=str(tmp_path))) as client:
        assert client.post(f"/api/projects/{PROJECT}/agents", json=DESIGNER).status_code == 201
        bad = client.post(f"/api/projects/{PROJECT}/agents", json={**DESIGNER, "id": "x2", "api_key_env": "HOME"})
        dup = client.post(f"/api/projects/{PROJECT}/agents", json={**DESIGNER, "display_name": "又一個"})
        reserved = client.post(f"/api/projects/{PROJECT}/agents", json={**DESIGNER, "id": "history"})
    assert (bad.status_code, dup.status_code, reserved.status_code) == (422, 409, 422)
    assert _agent_ids(db_env) == ["designer", "pm", "qa", "swe"]

    with TestClient(create_app(projects_dir=str(tmp_path))) as client:  # a restart
        body = client.get(f"/api/projects/{PROJECT}/agents").json()
        detail = client.get(f"/api/projects/{PROJECT}/agents/designer").json()

    assert [a["id"] for a in body["agents"]] == ["pm", "swe", "qa", "designer"]
    assert body["pipelines"][0]["seats"] == ["pm", "designer", "swe", "qa"]
    assert detail["prompt"] == "你是介面設計師。"
    assert list(_seats(db_env)[0]) == ["pm", "designer", "swe", "qa"] and _seats(db_env)[1] is None


def test_export_then_import_into_another_project(tmp_path, db_env):
    _project(tmp_path, PROJECT)
    _project(tmp_path, OTHER)
    with TestClient(create_app(projects_dir=str(tmp_path))) as client:
        client.post(f"/api/projects/{PROJECT}/agents", json=DESIGNER)
        exported = client.get(f"/api/projects/{PROJECT}/agents/export").text
        imported = client.post(f"/api/projects/{OTHER}/agents/import", content=exported,
                               headers={"content-type": "application/yaml"})
        source = client.get(f"/api/projects/{PROJECT}/agents").json()
        target = client.get(f"/api/projects/{OTHER}/agents").json()

    assert imported.status_code == 200
    assert target == source
    assert _agent_ids(db_env, OTHER) == _agent_ids(db_env, PROJECT)


def test_a_seat_change_while_a_task_waits_only_records_pending_seats(tmp_path, db_env):
    _project(tmp_path, PROJECT)
    with TestClient(create_app(projects_dir=str(tmp_path))) as client:
        task = client.post(f"/api/projects/{PROJECT}/tasks", json={"title": "gate", "approval": "after_swe"}).json()
        deadline = time.monotonic() + 30
        while client.get(f"/api/projects/{PROJECT}/tasks/{task['task']['id']}").json()["task"]["status"] != "awaiting_approval":
            assert time.monotonic() < deadline
            time.sleep(0.05)
        created = client.post(f"/api/projects/{PROJECT}/agents", json=DESIGNER)

    assert created.status_code == 201 and created.json()["effective"] == "next_task"
    seats, pending = _seats(db_env)
    assert list(seats) == ["pm", "swe", "qa"] and list(pending) == ["pm", "designer", "swe", "qa"]
