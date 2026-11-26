"""The project's default LLM on a real PostgreSQL (Task 25b, plan D21): ``PUT /settings`` keeps
the other settings, survives a restart, and ``FORCE_MOCK`` still decides what runs."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from main import create_app

pytestmark = pytest.mark.integration

PROJECT = "demo"
SONNET = {"provider": "anthropic", "model": "claude-sonnet-5-5"}


@pytest.fixture
def db_env(monkeypatch, migrated_db_sqlalchemy_url):
    monkeypatch.setenv("FORCE_MOCK", "true")
    monkeypatch.setenv("MOCK_LLM_DELAY", "0")
    monkeypatch.setenv("PIPELINE_ENGINE", "langgraph")
    monkeypatch.setenv("TASK_STORE", "db")
    monkeypatch.setenv("PIPELINE_CHECKPOINTER", "postgres")
    monkeypatch.setenv("DATABASE_URL", migrated_db_sqlalchemy_url)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return migrated_db_sqlalchemy_url


def _project(root: Path) -> None:
    (root / PROJECT).mkdir()
    (root / PROJECT / "tasks.yaml").write_text(yaml.safe_dump({"tasks": []}), encoding="utf-8")


def test_settings_survive_a_restart_and_force_mock_still_wins(tmp_path, db_env):
    _project(tmp_path)
    with TestClient(create_app(projects_dir=str(tmp_path))) as client:
        response = client.put(f"/api/projects/{PROJECT}/settings", json={"llm": SONNET})
        bad = client.put(f"/api/projects/{PROJECT}/settings", json={"llm": {**SONNET, "api_key_env": "HOME"}})

    with TestClient(create_app(projects_dir=str(tmp_path))) as client:
        settings = client.get(f"/api/projects/{PROJECT}/settings").json()
        with client.websocket_connect(f"/ws/{PROJECT}") as ws:
            snap = ws.receive_json()["data"]

    assert response.status_code == 200 and bad.status_code == 422
    assert settings["llm"] == SONNET and settings["force_mock"] is True
    assert snap["llm"]["project"] == SONNET
    assert {a["effective_llm"]["mock_reason"] for a in snap["agents"]} == {"force_mock"}
