"""Task detail: each ``status_history`` entry names its agent (QA gate D, Task 26a).

``agent`` stays what it was (the name in the log file: ``PM_agent``, or a custom agent's id);
the added ``display_name`` is the agent's name as the roster has it now, else as the task's run
snapshot had it (a deleted agent), else the id itself.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

import config
from tests.test_api_agents import DESIGNER, World, _project, _url

pytestmark = pytest.mark.skipif(
    config.get_pipeline_engine() != "langgraph", reason="custom agents run only with the graph engine"
)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("FORCE_MOCK", "true")
    monkeypatch.setenv("MOCK_LLM_DELAY", "0")
    for name in ("TASK_STORE", "PIPELINE_CHECKPOINTER", "DATABASE_URL", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def _run_to_done(client) -> str:
    task_id = client.post(_url("/tasks"), json={"title": "畫登入頁"}).json()["task"]["id"]
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        detail = client.get(_url(f"/tasks/{task_id}")).json()
        if detail["task"]["status"] == "done" and detail["runs"] and detail["runs"][0]["outcome"]:
            return task_id
        time.sleep(0.02)
    raise AssertionError("the task never finished")


def _history(client, task_id):
    return [(h["agent"], h["display_name"]) for h in client.get(_url(f"/tasks/{task_id}")).json()["status_history"]]


def test_status_history_carries_display_names_also_for_a_deleted_agent(tmp_path):
    _project(tmp_path)
    with TestClient(World().app(tmp_path)) as client:
        assert client.post(_url("/agents"), json=DESIGNER).status_code == 201
        task_id = _run_to_done(client)

        history = client.get(_url(f"/tasks/{task_id}")).json()["status_history"]
        assert {"agent", "status", "timestamp", "display_name"} == set(history[0])  # old keys stay
        assert _history(client, task_id) == [
            ("PM_agent", "PM"), ("designer", "設計師"), ("SWE_agent", "SWE"), ("QA_agent", "QA")]

        # Renamed: the roster's current name wins.
        assert client.put(_url("/agents/designer"), json={"display_name": "介面設計"}).status_code == 200
        assert ("designer", "介面設計") in _history(client, task_id)

        # Deleted: the run's seat snapshot still knows the name it had then.
        assert client.delete(_url("/agents/designer")).status_code in (200, 204)
        assert ("designer", "設計師") in _history(client, task_id)


def test_an_agent_known_nowhere_falls_back_to_its_id(tmp_path):
    from api.tasks import status_history_names

    names = status_history_names(roster=[{"id": "pm", "display_name": "PM"}],
                                 runs=[{"seats": [{"id": "old", "display_name": "舊角色"}]}])
    assert [names(a) for a in ("PM_agent", "old", "agent-09")] == ["PM", "舊角色", "agent-09"]
