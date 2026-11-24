"""REST for agents and pipelines (Task 25a, plan §7.1, §7.1a, AC16-AC19), with in-memory stores.

Every refused write is checked to leave nothing behind: no agent row, the same seats, no new file
in the project folder (AC17). Seat changes are 200 when the line is idle and 202 while a task is
in flight (AC18); a gated task (``after_swe``) holds the line for as long as the test needs.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

import config
from core.runtime import FactoryRuntime
from core.stores.memory import (
    InMemoryLineupRepository,
    InMemoryProjectRepository,
    InMemoryRunRepository,
    InMemoryTaskRepository,
)
from main import create_app

PROJECT = "demo"
OTHER = "other"
pytestmark = pytest.mark.skipif(
    config.get_pipeline_engine() != "langgraph", reason="the lineup endpoints write only with the graph engine"
)

DESIGNER = {"display_name": "設計師", "duty": "畫介面草圖", "prompt": "你是介面設計師。", "kind": "make",
            "look": "beret", "id": "designer", "after": "pm"}
SECURITY = {"display_name": "資安", "duty": "檢查安全", "prompt": "你是資安把關。", "kind": "check",
            "look": "antenna", "id": "security", "after": "swe"}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("FORCE_MOCK", "true")
    monkeypatch.setenv("MOCK_LLM_DELAY", "0")
    for name in ("TASK_STORE", "PIPELINE_CHECKPOINTER", "DATABASE_URL", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def _project(root: Path, name: str = PROJECT, entries=()) -> Path:
    project = root / name
    project.mkdir(parents=True, exist_ok=True)
    (project / "tasks.yaml").write_text(yaml.safe_dump({"tasks": list(entries)}, sort_keys=False), encoding="utf-8")
    return project


class World:
    def __init__(self) -> None:
        self.tasks = InMemoryTaskRepository()
        self.runs = InMemoryRunRepository()
        self.projects = InMemoryProjectRepository(self.tasks, self.runs)
        self.lineups = InMemoryLineupRepository(self.projects)
        self.saver = InMemorySaver()

    def app(self, root: Path):
        app = create_app(projects_dir=str(root))
        app.state.runtime = FactoryRuntime(
            projects_dir=root, task_store="db", task_repo=self.tasks, run_repo=self.runs,
            project_repo=self.projects, lineup_repo=self.lineups, checkpointer=self.saver)
        return app

    def agents(self, project=PROJECT):
        return [a.id for a in asyncio.run(self.lineups.list_agents(project))]

    def seats(self, project=PROJECT):
        record = asyncio.run(self.lineups.get_pipeline(project, "main"))
        return record.seats, record.pending_seats


@pytest.fixture
def world():
    return World()


@pytest.fixture
def client(tmp_path, world):
    _project(tmp_path)
    with TestClient(world.app(tmp_path)) as test_client:
        yield test_client


def _files(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*")}


def _url(path: str = "", project: str = PROJECT) -> str:
    return f"/api/projects/{project}{path}"


def _gate_task(client, title="hold the line"):
    """A task that stops at its gate after SWE: the line is busy until it is approved."""

    body = client.post(_url("/tasks"), json={"title": title, "approval": "after_swe"}).json()
    task_id = body["task"]["id"]
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if client.get(_url(f"/tasks/{task_id}")).json()["task"]["status"] == "awaiting_approval":
            return task_id
        time.sleep(0.02)
    raise AssertionError("the task never reached its gate")


def _events_until(ws, last: str, limit: int = 200) -> list[tuple[str, dict]]:
    seen = []
    for _ in range(limit):
        message = ws.receive_json()
        seen.append((message["event"], message["data"]))
        if message["event"] == last:
            return seen
    raise AssertionError(f"{last} never arrived; saw {[e for e, _ in seen]}")


# -- reads ----------------------------------------------------------------------------------------------


def test_get_agents_lists_the_roster_and_the_pipelines(client):
    body = client.get(_url("/agents")).json()

    assert [a["id"] for a in body["agents"]] == ["pm", "swe", "qa"]
    assert set(body["agents"][0]) == {"id", "display_name", "duty", "kind", "look", "builtin", "llm", "effective_llm"}
    assert body["pipelines"] == [{"id": "main", "name": "主線", "seats": ["pm", "swe", "qa"], "pending_seats": None}]
    assert client.get(_url("/pipelines")).json() == {"pipelines": body["pipelines"]}


def test_agent_detail_adds_prompt_and_llm_fields(client):
    client.post(_url("/agents"), json=DESIGNER)

    designer = client.get(_url("/agents/designer")).json()
    pm = client.get(_url("/agents/pm")).json()

    assert designer["prompt"] == "你是介面設計師。" and designer["builtin"] is False
    assert (designer["llm_provider"], designer["llm_model"]) == ("mock", "mock-model")
    assert pm["builtin"] is True and pm["prompt"] and {"llm_provider", "llm_model"} <= set(pm)
    assert client.get(_url("/agents/ghost")).status_code == 404


def test_unknown_project_is_404(client):
    assert client.get(_url("/agents", project="nope")).status_code == 404
    assert client.post(_url("/agents", project="nope"), json=DESIGNER).status_code == 404
    assert client.put(_url("/pipelines/main", project="nope"), json={"seats": ["pm"]}).status_code == 404


# -- POST /agents ------------------------------------------------------------------------------------------


def test_create_an_agent_when_idle_seats_it_now_and_broadcasts(client, world):
    with client.websocket_connect(f"/ws/{PROJECT}") as ws:
        assert ws.receive_json()["event"] == "snapshot"
        response = client.post(_url("/agents"), json=DESIGNER)
        events = _events_until(ws, "pipeline:updated")

    assert response.status_code == 201
    body = response.json()
    assert body["effective"] == "now"
    assert body["agent"]["id"] == "designer" and body["agent"]["look"] == "beret" and body["agent"]["llm"] is None
    assert world.agents() == ["pm", "swe", "qa", "designer"]
    assert world.seats() == (("pm", "designer", "swe", "qa"), None)
    joined = [d for e, d in events if e == "agent:joined"]
    assert [(d["agent"]["id"], d["position"], d["pipeline_id"]) for d in joined] == [("designer", 1, "main")]
    updated = [d for e, d in events if e == "pipeline:updated"][-1]
    assert updated["pipeline"]["seats"] == ["pm", "designer", "swe", "qa"]
    assert [a["id"] for a in updated["agents"]] == ["pm", "swe", "qa", "designer"]


def test_create_without_an_id_derives_one_and_after_null_seats_it_first(client, world):
    body = client.post(_url("/agents"), json={**DESIGNER, "id": None, "display_name": "Ux Writer",
                                              "after": None}).json()

    assert body["agent"]["id"] == "ux-writer"
    assert world.seats()[0] == ("ux-writer", "pm", "swe", "qa")


def test_create_without_after_appends_to_the_line(client, world):
    payload = {k: v for k, v in DESIGNER.items() if k != "after"}
    client.post(_url("/agents"), json=payload)

    assert world.seats()[0] == ("pm", "swe", "qa", "designer")


def test_create_with_an_llm_choice_keeps_it(client, world):
    body = client.post(_url("/agents"), json={**DESIGNER, "llm": {"provider": "anthropic",
                                                                   "model": "claude-opus-5-5"}}).json()

    assert body["agent"]["llm"] == {"provider": "anthropic", "model": "claude-opus-5-5"}
    stored = asyncio.run(world.lineups.get_agent(PROJECT, "designer"))
    assert stored.llm == {"provider": "anthropic", "model": "claude-opus-5-5"}


BAD_CREATES = {
    "id-format": {**DESIGNER, "id": "Designer!"},
    "id-reserved": {**DESIGNER, "id": "history"},
    "id-builtin": {**DESIGNER, "id": "pm"},
    "name-control": {**DESIGNER, "display_name": "設計\u0007師"},
    "name-format-char": {**DESIGNER, "display_name": "設計​師"},
    "name-empty": {**DESIGNER, "display_name": "  "},
    "name-too-long": {**DESIGNER, "display_name": "名" * 15},
    "duty-too-long": {**DESIGNER, "duty": "職" * 25},
    "prompt-too-long": {**DESIGNER, "prompt": "p" * 2001},
    "kind": {**DESIGNER, "kind": "review"},
    "look": {**DESIGNER, "look": "crown"},
    "llm-model": {**DESIGNER, "llm": {"provider": "anthropic", "model": "claude-2"}},
    "llm-provider": {**DESIGNER, "llm": {"provider": "openai", "model": "gpt-4o"}},
    "api-key-env": {**DESIGNER, "api_key_env": "HOME"},
    "api-key-env-in-llm": {**DESIGNER, "llm": {"provider": "anthropic", "model": "claude-opus-5-5",
                                               "api_key_env": "HOME"}},
    "unknown-field": {**DESIGNER, "color": "red"},
    "after-unknown": {**DESIGNER, "after": "ghost"},
    "check-first": {**SECURITY, "after": None},
    "pipeline-unknown": {**DESIGNER, "pipeline_id": "side"},
    "not-an-object": ["designer"],
}


@pytest.mark.parametrize("case", sorted(BAD_CREATES))
def test_invalid_creates_are_422_and_leave_nothing_behind(client, world, tmp_path, case):
    files_before = _files(tmp_path)

    response = client.post(_url("/agents"), json=BAD_CREATES[case])

    assert response.status_code == 422, response.text
    body = response.json()
    assert isinstance(body["detail"], str) and body["detail"]
    if case != "not-an-object":
        assert body["errors"] and all({"field", "message"} <= set(e) for e in body["errors"])
    assert world.agents() == ["pm", "swe", "qa"]
    assert world.seats() == (("pm", "swe", "qa"), None)
    assert _files(tmp_path) == files_before


def test_an_existing_id_is_409(client, world):
    client.post(_url("/agents"), json=DESIGNER)

    response = client.post(_url("/agents"), json={**DESIGNER, "display_name": "另一個"})

    assert response.status_code == 409
    assert world.agents() == ["pm", "swe", "qa", "designer"]


def test_a_ninth_seat_is_422(client, world):
    for n in range(5):
        assert client.post(_url("/agents"), json={**DESIGNER, "id": f"helper-{n}", "display_name": f"h{n}"}).status_code == 201

    response = client.post(_url("/agents"), json={**DESIGNER, "id": "one-more", "display_name": "more"})

    assert response.status_code == 422
    assert "one-more" not in world.agents() and len(world.seats()[0]) == 8


def test_the_seventeenth_custom_agent_is_409(client, world):
    document = {"schema_version": 1, "agents": [
        {"id": f"helper-{n:02d}", "display_name": f"helper {n}", "duty": "幫忙", "kind": "make", "look": "cap",
         "system_prompt": "p"} for n in range(16)
    ], "pipelines": [{"id": "main", "name": "主線", "default": True, "seats": ["pm", "swe", "qa"]}]}
    imported = client.post(_url("/agents/import"), content=yaml.safe_dump(document, allow_unicode=True),
                           headers={"content-type": "application/yaml"})
    assert imported.status_code == 200 and len(world.agents()) == 3 + 16

    response = client.post(_url("/agents"), json={**DESIGNER, "id": "one-more"})

    assert response.status_code == 409
    assert "one-more" not in world.agents()


def test_creating_while_a_task_is_in_flight_defers_the_seat(client, world):
    task_id = _gate_task(client)
    with client.websocket_connect(f"/ws/{PROJECT}") as ws:
        assert ws.receive_json()["event"] == "snapshot"
        response = client.post(_url("/agents"), json=DESIGNER)
        events = _events_until(ws, "pipeline:pending")

    assert response.status_code == 201 and response.json()["effective"] == "next_task"
    assert world.agents() == ["pm", "swe", "qa", "designer"]
    assert world.seats() == (("pm", "swe", "qa"), ("pm", "designer", "swe", "qa"))
    pending = [d for e, d in events if e == "pipeline:pending"][-1]
    assert pending == {"pipeline_id": "main", "seats": ["pm", "designer", "swe", "qa"],
                       "reason": "task_in_flight", "task_id": task_id}
    assert client.get(_url("/pipelines")).json()["pipelines"][0]["pending_seats"] == ["pm", "designer", "swe", "qa"]


# -- PUT /agents/{id} --------------------------------------------------------------------------------------


def test_update_a_custom_agent(client, world):
    client.post(_url("/agents"), json=DESIGNER)

    response = client.put(_url("/agents/designer"), json={"display_name": "UI 設計", "look": "bow",
                                                          "prompt": "新的 prompt"})

    assert response.status_code == 200 and response.json()["effective"] == "now"
    assert response.json()["agent"]["display_name"] == "UI 設計"
    stored = asyncio.run(world.lineups.get_agent(PROJECT, "designer"))
    assert (stored.display_name, stored.look, stored.system_prompt) == ("UI 設計", "bow", "新的 prompt")
    assert client.get(_url("/agents/designer")).json()["prompt"] == "新的 prompt"


def test_update_a_builtin_prompt_and_llm_but_nothing_else(client, world):
    ok = client.put(_url("/agents/swe"), json={"prompt": "改過的 SWE", "llm": "mock"})
    refused = client.put(_url("/agents/swe"), json={"display_name": "工程師"})
    kind = client.put(_url("/agents/qa"), json={"kind": "make"})

    assert ok.status_code == 200
    stored = asyncio.run(world.lineups.get_agent(PROJECT, "swe"))
    assert (stored.system_prompt, stored.llm, stored.display_name) == ("改過的 SWE", "mock", "SWE")
    assert refused.status_code == 422 and kind.status_code == 422
    assert asyncio.run(world.lineups.get_agent(PROJECT, "qa")).kind == "check"


def test_a_kind_change_that_breaks_a_lineup_is_409_and_changes_nothing(client, world):
    client.post(_url("/agents"), json={**DESIGNER, "after": None})  # designer sits first

    response = client.put(_url("/agents/designer"), json={"kind": "check"})

    assert response.status_code == 409
    assert response.json()["errors"]
    assert asyncio.run(world.lineups.get_agent(PROJECT, "designer")).kind == "make"


@pytest.mark.parametrize("payload", [
    {"api_key_env": "HOME"},
    {"llm": {"provider": "anthropic", "model": "claude-opus-5-5", "api_key_env": "HOME"}},
    {"llm": {"provider": "gemini", "model": "x"}},
    {"display_name": ""},
    {"id": "renamed"},
    {"kind": "review"},
])
def test_invalid_updates_are_422_and_change_nothing(client, world, payload):
    client.post(_url("/agents"), json=DESIGNER)
    before = asyncio.run(world.lineups.get_agent(PROJECT, "designer"))

    response = client.put(_url("/agents/designer"), json=payload)

    assert response.status_code == 422
    assert asyncio.run(world.lineups.get_agent(PROJECT, "designer")) == before


def test_update_unknown_agent_is_404(client):
    assert client.put(_url("/agents/ghost"), json={"prompt": "x"}).status_code == 404


def test_update_while_a_task_is_in_flight_is_202(client, world):
    client.post(_url("/agents"), json=DESIGNER)
    _gate_task(client)

    response = client.put(_url("/agents/designer"), json={"prompt": "下一張起生效"})

    assert response.status_code == 202 and response.json()["effective"] == "next_task"
    assert asyncio.run(world.lineups.get_agent(PROJECT, "designer")).system_prompt == "下一張起生效"


# -- DELETE /agents/{id} -----------------------------------------------------------------------------------


def test_delete_a_seated_custom_agent(client, world):
    client.post(_url("/agents"), json=DESIGNER)
    with client.websocket_connect(f"/ws/{PROJECT}") as ws:
        assert ws.receive_json()["event"] == "snapshot"
        response = client.delete(_url("/agents/designer"))
        events = _events_until(ws, "pipeline:updated")

    assert response.status_code == 200
    assert response.json() == {"agent_id": "designer", "effective": "now"}
    assert world.agents() == ["pm", "swe", "qa"]
    assert world.seats() == (("pm", "swe", "qa"), None)
    assert ("agent:left", {"agent_id": "designer", "pipeline_id": "main"}) in events


def test_delete_while_a_task_is_in_flight_removes_the_seat_with_the_next_task(client, world):
    client.post(_url("/agents"), json=DESIGNER)
    _gate_task(client)

    response = client.delete(_url("/agents/designer"))

    assert response.status_code == 202 and response.json()["effective"] == "next_task"
    # The row stays while the running task still has the seat (gone when it ends, Task 26a fix;
    # tests/test_agent_removal_restart.py).
    assert world.agents() == ["pm", "swe", "qa", "designer"]
    assert world.seats() == (("pm", "designer", "swe", "qa"), ("pm", "swe", "qa"))


def test_delete_refusals(client, world):
    client.post(_url("/agents"), json={**DESIGNER, "after": None})
    client.put(_url("/pipelines/main"), json={"seats": ["designer", "qa"]})

    builtin = client.delete(_url("/agents/pm"))
    unknown = client.delete(_url("/agents/ghost"))
    last_maker = client.delete(_url("/agents/designer"))  # would leave [qa]: no make, check first

    assert builtin.status_code == 409 and unknown.status_code == 404
    assert last_maker.status_code == 409 and last_maker.json()["errors"]
    assert world.agents() == ["pm", "swe", "qa", "designer"]
    assert world.seats() == (("designer", "qa"), None)


# -- pipelines ---------------------------------------------------------------------------------------------


def test_put_pipeline_seats(client, world):
    client.post(_url("/agents"), json=SECURITY)
    assert world.seats() == (("pm", "swe", "security", "qa"), None)

    response = client.put(_url("/pipelines/main"), json={"seats": ["pm", "swe", "qa", "security"]})

    assert response.status_code == 200
    assert response.json() == {"pipeline": {"id": "main", "name": "主線", "seats": ["pm", "swe", "qa", "security"],
                                            "pending_seats": None}, "effective": "now"}
    assert world.seats() == (("pm", "swe", "qa", "security"), None)


@pytest.mark.parametrize("seats", [[], ["qa", "pm"], ["pm", "pm"], ["pm", "ghost"], ["qa"], "pm",
                                   ["pm", "swe", "qa", "a", "b", "c", "d", "e", "f"]])
def test_invalid_pipeline_seats_are_422(client, world, seats):
    response = client.put(_url("/pipelines/main"), json={"seats": seats})

    assert response.status_code == 422
    assert world.seats() == (("pm", "swe", "qa"), None)


def test_unknown_pipeline_is_404(client):
    assert client.put(_url("/pipelines/side"), json={"seats": ["pm"]}).status_code == 404


def test_put_pipeline_while_a_task_is_in_flight_is_202_and_only_records_pending_seats(client, world):
    client.post(_url("/agents"), json=SECURITY)
    _gate_task(client)

    response = client.put(_url("/pipelines/main"), json={"seats": ["pm", "security", "swe", "qa"]})

    assert response.status_code == 202 and response.json()["effective"] == "next_task"
    assert response.json()["pipeline"]["pending_seats"] == ["pm", "security", "swe", "qa"]
    assert world.seats() == (("pm", "swe", "security", "qa"), ("pm", "security", "swe", "qa"))


# -- export / import (AC16) --------------------------------------------------------------------------------


def test_export_then_import_into_an_empty_project_gives_the_same_roster_and_seats(tmp_path, world):
    _project(tmp_path)
    _project(tmp_path, OTHER)
    with TestClient(world.app(tmp_path)) as client:
        client.post(_url("/agents"), json={**DESIGNER, "llm": {"provider": "anthropic", "model": "claude-opus-5-5"}})
        client.post(_url("/agents"), json=SECURITY)
        exported = client.get(_url("/agents/export"))
        imported = client.post(_url("/agents/import", OTHER), content=exported.text,
                               headers={"content-type": "application/yaml"})
        source, target = client.get(_url("/agents")).json(), client.get(_url("/agents", OTHER)).json()

    assert exported.status_code == 200 and exported.headers["content-type"].startswith("application/yaml")
    document = yaml.safe_load(exported.text)
    assert document["schema_version"] == 1 and "api_key_env" not in exported.text
    assert imported.status_code == 200 and imported.json()["effective"] == "now"
    assert imported.json()["agents"] == target["agents"] and imported.json()["pipelines"] == target["pipelines"]
    assert target["agents"] == source["agents"]
    assert target["pipelines"] == source["pipelines"]
    assert source["pipelines"][0]["seats"] == ["pm", "designer", "swe", "security", "qa"]


@pytest.mark.parametrize("text", [
    "schema_version: 2\nagents: []\n",
    "not: [valid",
    "schema_version: 1\nagents:\n  - {id: x1, display_name: X, duty: d, kind: make, look: cap, system_prompt: p,"
    " api_key_env: HOME}\npipelines:\n  - {id: main, name: 主線, default: true, seats: [pm, swe, qa]}\n",
    "schema_version: 1\nagents: []\npipelines:\n  - {id: main, name: 主線, default: true, seats: [qa, pm]}\n",
    "schema_version: 1\nagents: []\npipelines:\n  - {id: side, name: 副線, default: true, seats: [pm, swe, qa]}\n",
])
def test_an_invalid_import_is_422_and_writes_nothing(client, world, text):
    client.post(_url("/agents"), json=DESIGNER)

    response = client.post(_url("/agents/import"), content=text, headers={"content-type": "application/yaml"})

    assert response.status_code == 422 and response.json()["errors"]
    assert world.agents() == ["pm", "swe", "qa", "designer"]
    assert world.seats() == (("pm", "designer", "swe", "qa"), None)


def test_import_while_a_task_is_in_flight_defers_the_seats(client, world):
    client.post(_url("/agents"), json=DESIGNER)
    _gate_task(client)
    document = {"schema_version": 1, "agents": [], "pipelines": [
        {"id": "main", "name": "主線", "default": True, "seats": ["pm", "swe", "qa"]}]}

    response = client.post(_url("/agents/import"), content=yaml.safe_dump(document),
                           headers={"content-type": "application/yaml"})

    assert response.status_code == 202 and response.json()["effective"] == "next_task"
    # designer is dropped by the import but the running seats still have it: it stays (leaving)
    # until the task ends (Task 26a fix).
    assert world.agents() == ["pm", "swe", "qa", "designer"]
    # The running task's seats stay recorded until it is done; the new ones wait.
    assert world.seats() == (("pm", "designer", "swe", "qa"), ("pm", "swe", "qa"))


# -- AC19: legacy engine or file mode --------------------------------------------------------------------------

WRITES = [
    ("post", "/agents", {"json": DESIGNER}),
    ("put", "/agents/pm", {"json": {"prompt": "x"}}),
    ("delete", "/agents/pm", {}),
    ("post", "/agents/import", {"content": "schema_version: 1\n", "headers": {"content-type": "application/yaml"}}),
    ("put", "/pipelines/main", {"json": {"seats": ["pm", "swe", "qa"]}}),
]


def _assert_read_only(client, tmp_path, files_before):
    for method, path, kwargs in WRITES:
        response = getattr(client, method)(_url(path), **kwargs)
        assert response.status_code == 409, (method, path, response.text)
        assert "langgraph" in response.json()["detail"] and "db" in response.json()["detail"]
    body = client.get(_url("/agents")).json()
    assert [a["id"] for a in body["agents"]] == ["pm", "swe", "qa"]
    assert body["pipelines"] == [{"id": "main", "name": "主線", "seats": ["pm", "swe", "qa"], "pending_seats": None}]
    assert client.get(_url("/pipelines")).status_code == 200
    assert client.get(_url("/agents/pm")).json()["prompt"]
    assert yaml.safe_load(client.get(_url("/agents/export")).text)["pipelines"][0]["seats"] == ["pm", "swe", "qa"]
    assert _files(tmp_path) == files_before


def test_file_mode_reads_work_and_writes_are_409(tmp_path):
    _project(tmp_path)
    app = create_app(projects_dir=str(tmp_path))
    with TestClient(app) as client:
        files_before = _files(tmp_path)
        _assert_read_only(client, tmp_path, files_before)


def test_legacy_engine_reads_work_and_writes_are_409(tmp_path, world, monkeypatch):
    monkeypatch.setenv("PIPELINE_ENGINE", "legacy")
    _project(tmp_path)
    app = create_app(projects_dir=str(tmp_path))
    app.state.runtime = FactoryRuntime(projects_dir=tmp_path, task_store="db", task_repo=world.tasks,
                                       run_repo=world.runs, project_repo=world.projects, lineup_repo=world.lineups)
    with TestClient(app) as client:
        files_before = _files(tmp_path)
        _assert_read_only(client, tmp_path, files_before)
    assert world.agents() == ["pm", "swe", "qa"]
