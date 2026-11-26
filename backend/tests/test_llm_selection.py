"""LLM selection (Task 25b, plan D21, D22, AC25): ``resolve_llm``, ``GET``/``PUT /settings``,
``llm:updated``, snapshot ``llm`` and ``project_llm_client``.

No real request is ever sent: wherever a Claude client could be built, ``anthropic.Anthropic`` is
replaced by a fake SDK object (or by one that raises, to prove it is never built), and the API
key is a fake string.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import anthropic
import pytest
import yaml
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

import config
from core.llm_client import ANTHROPIC_MODELS, AnthropicClient, MockLLMClient
from core.llm_selection import EffectiveLLM, MODEL_LABELS, resolve_llm
from core.runtime import FactoryRuntime
from core.stores.memory import (
    InMemoryLineupRepository,
    InMemoryProjectRepository,
    InMemoryRunRepository,
    InMemoryTaskRepository,
)
from core.usage_tracker import MODEL_PRICES
from main import create_app
from tests.test_anthropic_client import FakeSDK, message, text

PROJECT = "demo"
FAKE_KEY = "sk-ant-FAKE-not-a-real-key"
SONNET = {"provider": "anthropic", "model": "claude-sonnet-5-5"}
OPUS = {"provider": "anthropic", "model": "claude-opus-5-5"}
GRAPH_ONLY = pytest.mark.skipif(
    config.get_pipeline_engine() != "langgraph", reason="LLM settings are written only with the graph engine"
)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("FORCE_MOCK", "true")
    monkeypatch.setenv("MOCK_LLM_DELAY", "0")
    for name in ("TASK_STORE", "PIPELINE_CHECKPOINTER", "DATABASE_URL", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def no_sdk(monkeypatch):
    """Building a real Anthropic SDK client fails the test."""

    def refuse(*args, **kwargs):
        raise AssertionError("a real Anthropic SDK client was built")

    monkeypatch.setattr(anthropic, "Anthropic", refuse)


@pytest.fixture
def fake_sdks(monkeypatch):
    """Every ``anthropic.Anthropic(...)`` returns a recording fake; nothing touches the network."""

    built: list[FakeSDK] = []

    def factory(*args, **kwargs):
        sdk = FakeSDK(message([text("Result: PASS\nVERDICT: PASS")]))
        built.append(sdk)
        return sdk

    monkeypatch.setattr(anthropic, "Anthropic", factory)
    monkeypatch.delenv("FORCE_MOCK", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    return built


# -- resolve_llm --------------------------------------------------------------------------------------------

KEY = {"ANTHROPIC_API_KEY": FAKE_KEY}
MOCK = ("mock", "mock-model")


@pytest.mark.parametrize("agent_llm, project_llm, force_mock, env, expected", [
    (None, "mock", False, KEY, (*MOCK, "project", "configured_mock")),
    (None, None, False, KEY, (*MOCK, "project", "configured_mock")),
    (None, {"provider": "mock"}, False, KEY, (*MOCK, "project", "configured_mock")),
    (None, SONNET, False, KEY, ("anthropic", "claude-sonnet-5-5", "project", None)),
    (None, SONNET, False, {}, (*MOCK, "project", "missing_key")),
    (None, SONNET, False, {"ANTHROPIC_API_KEY": "  "}, (*MOCK, "project", "missing_key")),
    (OPUS, "mock", False, KEY, ("anthropic", "claude-opus-5-5", "agent", None)),
    (OPUS, SONNET, False, KEY, ("anthropic", "claude-opus-5-5", "agent", None)),
    (OPUS, SONNET, False, {}, (*MOCK, "agent", "missing_key")),
    ("mock", SONNET, False, KEY, (*MOCK, "agent", "configured_mock")),
    ({"provider": "mock"}, SONNET, False, KEY, (*MOCK, "agent", "configured_mock")),
    # FORCE_MOCK wins over everything, whatever was configured (plan D22, line 208).
    (None, SONNET, True, KEY, (*MOCK, "project", "force_mock")),
    (OPUS, "mock", True, KEY, (*MOCK, "agent", "force_mock")),
    (None, "mock", True, KEY, (*MOCK, "project", "force_mock")),
    ("mock", "mock", True, {}, (*MOCK, "agent", "force_mock")),
    (None, SONNET, True, {}, (*MOCK, "project", "force_mock")),
])
def test_resolve_llm(agent_llm, project_llm, force_mock, env, expected):
    effective = resolve_llm(agent_llm, project_llm, force_mock=force_mock, env=env)

    assert isinstance(effective, EffectiveLLM)
    assert (effective.provider, effective.model, effective.source, effective.mock_reason) == expected
    assert effective.to_dict() == dict(zip(("provider", "model", "source", "mock_reason"), expected))


def test_every_whitelisted_model_has_a_price_and_a_label():
    for model in ANTHROPIC_MODELS:
        assert ("anthropic", model) in MODEL_PRICES
        assert MODEL_LABELS[model].startswith("Claude ")


# -- the app -------------------------------------------------------------------------------------------------


class World:
    def __init__(self) -> None:
        self.tasks = InMemoryTaskRepository()
        self.runs = InMemoryRunRepository()
        self.projects = InMemoryProjectRepository(self.tasks, self.runs)
        self.lineups = InMemoryLineupRepository(self.projects)
        self.saver = InMemorySaver()

    def runtime(self, root: Path) -> FactoryRuntime:
        return FactoryRuntime(projects_dir=root, task_store="db", task_repo=self.tasks, run_repo=self.runs,
                              project_repo=self.projects, lineup_repo=self.lineups, checkpointer=self.saver)

    def app(self, root: Path):
        app = create_app(projects_dir=str(root))
        app.state.runtime = self.runtime(root)
        return app

    def project_llm(self):
        return (asyncio.run(self.projects.get_settings(PROJECT)) or {}).get("llm")


def _project(root: Path) -> Path:
    project = root / PROJECT
    project.mkdir(parents=True, exist_ok=True)
    (project / "tasks.yaml").write_text(yaml.safe_dump({"tasks": []}), encoding="utf-8")
    return project


@pytest.fixture
def world():
    return World()


@pytest.fixture
def client(tmp_path, world):
    _project(tmp_path)
    with TestClient(world.app(tmp_path)) as test_client:
        yield test_client


def _url(path: str) -> str:
    return f"/api/projects/{PROJECT}{path}"


def _events_until(ws, last: str, limit: int = 200) -> list[tuple[str, dict]]:
    seen = []
    for _ in range(limit):
        message_ = ws.receive_json()
        seen.append((message_["event"], message_["data"]))
        if message_["event"] == last:
            return seen
    raise AssertionError(f"{last} never arrived; saw {[e for e, _ in seen]}")


def _gate_task(client):
    task_id = client.post(_url("/tasks"), json={"title": "hold", "approval": "after_swe"}).json()["task"]["id"]
    deadline = time.monotonic() + 20
    while client.get(_url(f"/tasks/{task_id}")).json()["task"]["status"] != "awaiting_approval":
        assert time.monotonic() < deadline, "the task never reached its gate"
        time.sleep(0.02)
    return task_id


def _wait_done(client, task_id, timeout=30):
    deadline = time.monotonic() + timeout
    while client.get(_url(f"/tasks/{task_id}")).json()["task"]["status"] not in ("done", "failed"):
        assert time.monotonic() < deadline, "the task never finished"
        time.sleep(0.02)
    return client.get(_url(f"/tasks/{task_id}")).json()["task"]["status"]


MODELS = [{"id": "claude-opus-5-5", "label": "Claude Opus 5.5"},
          {"id": "claude-sonnet-5-5", "label": "Claude Sonnet 5.5"},
          {"id": "claude-haiku-5-5", "label": "Claude Haiku 5.5"},
          {"id": "claude-fable-5-2", "label": "Claude Fable 5.2"}]


# -- GET /settings and snapshot llm ------------------------------------------------------------------------------


def test_get_settings_and_snapshot_llm(client):
    body = client.get(_url("/settings")).json()
    with client.websocket_connect(f"/ws/{PROJECT}") as ws:
        snap = ws.receive_json()["data"]

    assert body == {"llm": "mock", "force_mock": True, "anthropic_key_present": False, "models": MODELS}
    assert snap["llm"] == {"project": "mock", "force_mock": True, "anthropic_key_present": False, "models": MODELS}
    for agent in snap["agents"]:
        assert agent["effective_llm"] == {"provider": "mock", "model": "mock-model", "source": "project",
                                          "mock_reason": "force_mock"}


def test_file_mode_settings_are_readable(tmp_path):
    _project(tmp_path)
    with TestClient(create_app(projects_dir=str(tmp_path))) as client:
        body = client.get(_url("/settings")).json()
        refused = client.put(_url("/settings"), json={"llm": SONNET})
        with client.websocket_connect(f"/ws/{PROJECT}") as ws:
            snap = ws.receive_json()["data"]

    assert body["llm"] == "mock" and body["models"] == MODELS
    assert refused.status_code == 409 and "langgraph" in refused.json()["detail"]
    assert snap["llm"]["project"] == "mock"
    assert {a["effective_llm"]["mock_reason"] for a in snap["agents"]} == {"force_mock"}


def test_the_snapshot_never_carries_the_key(tmp_path, world, fake_sdks):
    _project(tmp_path)
    with TestClient(world.app(tmp_path)) as client:
        with client.websocket_connect(f"/ws/{PROJECT}") as ws:
            snap = ws.receive_json()["data"]
        settings = client.get(_url("/settings")).text

    assert snap["llm"]["anthropic_key_present"] is True
    assert FAKE_KEY not in json.dumps(snap) and FAKE_KEY not in settings


# -- PUT /settings -------------------------------------------------------------------------------------------------


@GRAPH_ONLY
def test_put_settings_when_idle_is_200_and_broadcasts_llm_updated(client, world, no_sdk):
    with client.websocket_connect(f"/ws/{PROJECT}") as ws:
        assert ws.receive_json()["event"] == "snapshot"
        response = client.put(_url("/settings"), json={"llm": SONNET})
        events = _events_until(ws, "llm:updated")

    assert response.status_code == 200
    assert response.json() == {"llm": SONNET, "force_mock": True, "anthropic_key_present": False,
                               "models": MODELS, "effective": "now"}
    assert world.project_llm() == SONNET
    updated = [d for e, d in events if e == "llm:updated"][-1]
    assert updated["project_llm"] == SONNET and updated["effective"] == "now"
    assert [a["id"] for a in updated["agents"]] == ["pm", "swe", "qa"]
    assert {a["effective_llm"]["mock_reason"] for a in updated["agents"]} == {"force_mock"}  # FORCE_MOCK still wins
    assert client.get(_url("/settings")).json()["llm"] == SONNET


@GRAPH_ONLY
@pytest.mark.parametrize("body", [
    {"llm": {"provider": "anthropic", "model": "claude-2"}},
    {"llm": {"provider": "openai", "model": "gpt-4o"}},
    {"llm": {"provider": "anthropic", "model": "claude-opus-5-5", "api_key_env": "HOME"}},
    {"llm": SONNET, "api_key_env": "HOME"},
    {"llm": None},
    {},
    {"llm": "claude"},
    ["mock"],
])
def test_invalid_settings_are_422_and_change_nothing(client, world, body):
    response = client.put(_url("/settings"), json=body)

    assert response.status_code == 422 and response.json()["detail"]
    assert world.project_llm() == "mock"


@GRAPH_ONLY
def test_put_settings_while_a_task_is_in_flight_is_202(client, world, no_sdk):
    _gate_task(client)
    with client.websocket_connect(f"/ws/{PROJECT}") as ws:
        assert ws.receive_json()["event"] == "snapshot"
        response = client.put(_url("/settings"), json={"llm": SONNET})
        events = _events_until(ws, "llm:updated")

    assert response.status_code == 202 and response.json()["effective"] == "next_task"
    assert [d for e, d in events if e == "llm:updated"][-1]["effective"] == "next_task"
    assert world.project_llm() == SONNET


@GRAPH_ONLY
def test_an_agent_llm_change_rebuilds_it_and_broadcasts_llm_updated(client, world, no_sdk):
    with client.websocket_connect(f"/ws/{PROJECT}") as ws:
        assert ws.receive_json()["event"] == "snapshot"
        response = client.put(_url("/agents/qa"), json={"llm": OPUS})
        events = _events_until(ws, "llm:updated")

    assert response.status_code == 200
    assert response.json()["agent"]["llm"] == OPUS
    assert response.json()["agent"]["effective_llm"] == {"provider": "mock", "model": "mock-model",
                                                         "source": "agent", "mock_reason": "force_mock"}
    updated = [d for e, d in events if e == "llm:updated"][-1]
    assert updated["project_llm"] == "mock"
    assert next(a for a in updated["agents"] if a["id"] == "qa")["llm"] == OPUS


# -- AC25: the next task builds its clients with the project's model ----------------------------------------------


@GRAPH_ONLY
def test_the_project_model_reaches_the_sdk_and_individual_choices_are_kept(tmp_path, world, fake_sdks):
    _project(tmp_path)
    with TestClient(world.app(tmp_path)) as client:
        assert client.put(_url("/agents/qa"), json={"llm": "mock"}).status_code == 200
        assert client.post(_url("/agents"), json={"id": "designer", "display_name": "設計師", "duty": "畫圖",
                                                  "prompt": "你是設計師。", "kind": "make", "look": "beret",
                                                  "after": "pm", "llm": OPUS}).status_code == 201
        assert fake_sdks  # the designer's own Claude client exists already
        fake_sdks.clear()

        response = client.put(_url("/settings"), json={"llm": SONNET})
        views = {a["id"]: a for a in client.get(_url("/agents")).json()["agents"]}
        task_id = client.post(_url("/tasks"), json={"title": "用 Claude 跑"}).json()["task"]["id"]
        _wait_done(client, task_id)
        qa_detail = client.get(_url("/agents/qa")).json()

    assert response.status_code == 200
    assert views["pm"]["effective_llm"] == {"provider": "anthropic", "model": "claude-sonnet-5-5",
                                            "source": "project", "mock_reason": None}
    assert views["designer"]["effective_llm"]["model"] == "claude-opus-5-5"
    assert views["qa"]["effective_llm"] == {"provider": "mock", "model": "mock-model", "source": "agent",
                                            "mock_reason": "configured_mock"}
    sent = [call[1]["model"] for sdk in fake_sdks for call in sdk.calls]
    assert sorted(set(sent)) == ["claude-opus-5-5", "claude-sonnet-5-5"]
    assert sent.count("claude-opus-5-5") >= 1 and sent.count("claude-sonnet-5-5") >= 2  # designer; pm and swe
    assert (qa_detail["llm_provider"], qa_detail["llm_model"]) == ("mock", "mock-model")


@GRAPH_ONLY
def test_a_change_during_a_task_reaches_the_sdk_only_from_the_next_task(tmp_path, world, fake_sdks):
    _project(tmp_path)
    with TestClient(world.app(tmp_path)) as client:
        running = _gate_task(client)  # runs on the Mock (the project default)
        response = client.put(_url("/settings"), json={"llm": SONNET})
        assert client.post(_url(f"/tasks/{running}/approve")).status_code == 200
        _wait_done(client, running)
        calls_for_running = [call for sdk in fake_sdks for call in sdk.calls]
        following = client.post(_url("/tasks"), json={"title": "下一張"}).json()["task"]["id"]
        _wait_done(client, following)

    assert response.status_code == 202 and response.json()["effective"] == "next_task"
    assert calls_for_running == []  # the running task finished on the clients it started with
    sent = {call[1]["model"] for sdk in fake_sdks for call in sdk.calls}
    assert sent == {"claude-sonnet-5-5"}


@GRAPH_ONLY
def test_without_a_key_the_effective_llm_is_mock_with_missing_key(tmp_path, world, monkeypatch, no_sdk):
    monkeypatch.delenv("FORCE_MOCK", raising=False)
    _project(tmp_path)
    with TestClient(world.app(tmp_path)) as client:
        response = client.put(_url("/settings"), json={"llm": SONNET})
        views = client.get(_url("/agents")).json()["agents"]
        settings = client.get(_url("/settings")).json()

    assert response.status_code == 200
    assert settings["anthropic_key_present"] is False and settings["force_mock"] is False
    for view in views:
        assert view["effective_llm"] == {"provider": "mock", "model": "mock-model", "source": "project",
                                         "mock_reason": "missing_key"}


@GRAPH_ONLY
def test_settings_survive_a_restart(tmp_path, world, no_sdk):
    _project(tmp_path)
    with TestClient(world.app(tmp_path)) as client:
        client.put(_url("/settings"), json={"llm": SONNET})
    with TestClient(world.app(tmp_path)) as client:
        assert client.get(_url("/settings")).json()["llm"] == SONNET


# -- project_llm_client (for the staffing graph, phase 3) -------------------------------------------------------


def test_project_llm_client_follows_the_project_setting(tmp_path, world, fake_sdks):
    _project(tmp_path)

    async def scenario():
        runtime = world.runtime(tmp_path)
        await runtime.startup()
        try:
            first = runtime.project_llm_client(PROJECT, mock_role_key="make")
            await world.projects.set_llm(PROJECT, SONNET)
            await runtime.reload_project_llm(PROJECT)
            second = runtime.project_llm_client(PROJECT)
            return first, second
        finally:
            await runtime.shutdown()

    first, second = asyncio.run(scenario())

    assert isinstance(first, MockLLMClient) and first.role_key == "make"
    assert isinstance(second, AnthropicClient) and second.model == "claude-sonnet-5-5"


def test_project_llm_client_is_mock_under_force_mock(tmp_path, world, no_sdk):
    _project(tmp_path)

    async def scenario():
        runtime = world.runtime(tmp_path)
        await runtime.startup()
        try:
            await world.projects.set_llm(PROJECT, SONNET)
            await runtime.reload_project_llm(PROJECT)
            return runtime.project_llm_client(PROJECT)
        finally:
            await runtime.shutdown()

    assert isinstance(asyncio.run(scenario()), MockLLMClient)
