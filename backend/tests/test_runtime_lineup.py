"""Runtime wiring of the line-up (Task 24, AC21, plan D17, D20, §6.4), with in-memory stores.

* the snapshot keeps every existing key and value and gains ``engine``, ``task_store``,
  ``force_mock``, ``agents``, ``pipelines`` and ``live``;
* DB mode reads the roster and the seats from the ``LineupRepository`` (imported from
  ``agents/*.yaml`` the first time, after the project row exists, so ``migration_notes`` stay);
* custom seats get :class:`CustomAgent` instances; the live channel reaches the event bus; the
  history recorder snapshots the real seats;
* at startup ``pipelines.pending_seats`` is read after the gates are restored and handed to
  ``request_lineup`` (with the real roster), which waits for the resumed task (D17 ruling);
* a seat change through the runtime never writes ``pipelines`` itself (the orchestrator does);
* usage is written off the event loop, retried, and kept for a later write when the store fails,
  so the row is stored before ``usage:update`` (D20) whenever the store comes back in time.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

import pytest
import yaml
from langgraph.checkpoint.memory import InMemorySaver

import config
from agents.custom_agent import CustomAgent
from core.pipeline import PipelineOrchestrator
from core.runtime import FactoryRuntime
from core.stores import PipelineRecord
from core.stores.memory import (
    InMemoryLineupRepository,
    InMemoryProjectRepository,
    InMemoryRunRepository,
    InMemoryTaskRepository,
)
from core.stores.usage_store import InMemoryUsageStore
from core.usage_tracker import UsageTracker
from models.task import Status
from tests.hud_harness import make_orchestrator_with_repo
from tests.pipeline_harness import make_project, make_task

PROJECT = "demo"
GRAPH_ONLY = pytest.mark.skipif(
    config.get_pipeline_engine() != "langgraph", reason="custom lineups need the graph engine"
)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("FORCE_MOCK", "true")
    monkeypatch.setenv("MOCK_LLM_DELAY", "0")
    for name in ("TASK_STORE", "PIPELINE_CHECKPOINTER", "DATABASE_URL", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def _project(root: Path, entries=(), agent_files: dict | None = None) -> Path:
    project = root / PROJECT
    project.mkdir(parents=True, exist_ok=True)
    (project / "tasks.yaml").write_text(
        yaml.safe_dump({"tasks": list(entries)}, allow_unicode=True, sort_keys=False), encoding="utf-8")
    if agent_files:
        agents_dir = project / "agents"
        agents_dir.mkdir(exist_ok=True)
        for name, body in agent_files.items():
            (agents_dir / name).write_text(yaml.safe_dump(body, allow_unicode=True, sort_keys=False),
                                           encoding="utf-8")
    return project


def _entry(task_id, status="pending", **extra):
    return {"id": task_id, "title": f"title {task_id}", "description": "d", "priority": "medium",
            "status": status, **extra}


DESIGNER = {"id": "designer", "display_name": "設計師", "duty": "畫介面草圖", "kind": "make",
            "look": "beret", "system_prompt": "你是介面設計師。"}
SECURITY = {"id": "security", "display_name": "資安", "duty": "檢查安全", "kind": "check",
            "look": "antenna", "system_prompt": "你是資安把關。"}


class World:
    """Everything that survives a restart (in memory)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.tasks = InMemoryTaskRepository()
        self.runs = InMemoryRunRepository()
        self.projects = InMemoryProjectRepository(self.tasks, self.runs)
        self.lineups = InMemoryLineupRepository(self.projects)
        self.usage = InMemoryUsageStore()
        self.saver = InMemorySaver()

    def runtime(self, *, lineups=True) -> tuple[FactoryRuntime, list[tuple[str, dict]]]:
        runtime = FactoryRuntime(
            projects_dir=self.root, task_store="db", task_repo=self.tasks, run_repo=self.runs,
            project_repo=self.projects, lineup_repo=self.lineups if lineups else None,
            usage_store=self.usage, checkpointer=self.saver,
        )
        events: list[tuple[str, dict]] = []

        async def record(project_id, event, data):
            events.append((event, data))

        runtime.ws_manager.broadcast = record
        return runtime, events


async def _until(predicate, timeout=10.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.005)


async def _wait_status(world: World, task_id: str, status: Status) -> None:
    async def current():
        task = await world.tasks.get(PROJECT, task_id)
        return None if task is None else task.status

    for _ in range(4000):
        if await current() is status:
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"{task_id} never became {status.value}; is {await current()}")


def _strip_times(snapshot: dict) -> dict:
    snap = json.loads(json.dumps(snapshot, default=str))
    for task in snap["tasks"]:
        task.pop("created_at", None)
        task.pop("updated_at", None)
    return snap


# -- AC21: existing snapshot keys and values are those recorded before the change ---------------------

IDLE = {"status": "idle", "task_id": None, "task_title": None}
ZERO_USAGE = {"call_count": 0, "cost": 0.0, "input_tokens": 0, "output_tokens": 0}
# Recorded with the code before Task 24 (dcc487b): one done task, nothing ran.
RECORDED_BEFORE = {
    "devops_alerts": [],
    "history_counts": {"awaiting_approval": 0, "done": 1, "failed": 0, "in_progress": 0, "pending": 0},
    "mock_mode": True,
    "pipeline": {
        "awaiting_approval": [], "budget_paused": False, "done_count": 0, "failed_count": 0,
        "project_id": PROJECT, "queue_size": 0,
        "stations": {"pm": IDLE, "swe": IDLE, "qa": IDLE},
        "usage": ZERO_USAGE,
    },
    "providers": [],
    "tasks": [{"approval": "none", "description": "d", "id": "task-001", "max_reworks": 3, "pm_output": None,
               "priority": "medium", "qa_output": None, "rework_count": 0, "status": "done",
               "swe_output": None, "title": "title task-001"}],
    "usage": {"budget_paused": False, "daily_budget_usd": None, "daily_cost": 0.0, "project": ZERO_USAGE,
              "project_budget_usd": None},
}
NEW_KEYS = {"engine", "task_store", "force_mock", "agents", "pipelines", "live", "llm"}  # llm: Task 25b

BUILTIN_VIEWS = [
    {"id": "pm", "display_name": "PM", "duty": "把需求拆成規格", "kind": "make", "look": "glasses", "builtin": True},
    {"id": "swe", "display_name": "SWE", "duty": "照規格寫程式", "kind": "make", "look": "headset", "builtin": True},
    {"id": "qa", "display_name": "QA", "duty": "測試並決定過不過", "kind": "check", "look": "cap", "builtin": True},
]
DEFAULT_PIPELINE_VIEW = {"id": "main", "name": "主線", "seats": ["pm", "swe", "qa"], "pending_seats": None}


def _view_core(view: dict) -> dict:
    return {k: view[k] for k in ("id", "display_name", "duty", "kind", "look", "builtin")}


def _snapshot_of(runtime_factory) -> dict:
    async def life():
        runtime = runtime_factory()
        await runtime.startup()
        try:
            return runtime.snapshot(PROJECT)
        finally:
            await runtime.shutdown()

    return asyncio.run(life())


@pytest.mark.parametrize("mode", ["file", "db", "db-with-lineup"])
def test_existing_snapshot_keys_and_values_are_unchanged(tmp_path, mode):
    _project(tmp_path, [_entry("task-001", status="done")])
    world = World(tmp_path)
    if mode == "file":
        factory = lambda: FactoryRuntime(projects_dir=tmp_path)  # noqa: E731
    else:
        factory = lambda: world.runtime(lineups=mode == "db-with-lineup")[0]  # noqa: E731

    snap = _strip_times(_snapshot_of(factory))

    assert {k: snap[k] for k in RECORDED_BEFORE} == RECORDED_BEFORE
    assert set(snap) == set(RECORDED_BEFORE) | NEW_KEYS


@pytest.mark.parametrize("mode", ["file", "db", "db-with-lineup"])
def test_new_snapshot_keys_for_the_default_lineup(tmp_path, mode):
    _project(tmp_path)
    world = World(tmp_path)
    factory = (lambda: FactoryRuntime(projects_dir=tmp_path)) if mode == "file" else (  # noqa: E731
        lambda: world.runtime(lineups=mode == "db-with-lineup")[0])

    snap = _snapshot_of(factory)

    assert snap["engine"] == config.get_pipeline_engine()
    assert snap["task_store"] == ("file" if mode == "file" else "db")
    assert snap["force_mock"] is True
    assert [_view_core(v) for v in snap["agents"]] == BUILTIN_VIEWS
    for view in snap["agents"]:
        assert view["llm"] is None
        assert set(view["effective_llm"]) == {"provider", "model", "source", "mock_reason"}
        assert view["effective_llm"]["provider"] == "mock"
    assert snap["pipelines"] == [DEFAULT_PIPELINE_VIEW]
    assert snap["live"] == {}
    json.dumps(snap)  # everything is JSON as it goes over the WebSocket


# -- DB mode: the roster and the seats come from the LineupRepository ------------------------------------


def test_db_mode_reads_the_roster_and_seats_from_the_lineup_repository(tmp_path):
    _project(tmp_path, agent_files={"designer.yaml": DESIGNER, "security.yaml": SECURITY})
    world = World(tmp_path)

    snap = _snapshot_of(lambda: world.runtime()[0])
    stored = asyncio.run(world.lineups.list_agents(PROJECT))

    assert [r.id for r in stored] == ["pm", "swe", "qa", "designer", "security"]  # imported from the files
    assert [v["id"] for v in snap["agents"]] == ["pm", "swe", "qa", "designer", "security"]  # seated or not
    designer = snap["agents"][3]
    assert _view_core(designer) == {"id": "designer", "display_name": "設計師", "duty": "畫介面草圖",
                                    "kind": "make", "look": "beret", "builtin": False}
    assert snap["pipelines"] == [DEFAULT_PIPELINE_VIEW]


@GRAPH_ONLY
def test_stored_custom_seats_run_with_custom_agents_and_live_events_reach_the_bus(tmp_path):
    _project(tmp_path, agent_files={"designer.yaml": DESIGNER})
    world = World(tmp_path)

    async def scenario():
        runtime, _ = world.runtime()  # the first start imports the files
        await runtime.startup()
        await runtime.shutdown()
        # Seats an earlier process applied.
        await world.lineups.save_pipeline(
            PROJECT, PipelineRecord("main", "主線", True, ("pm", "designer", "swe", "qa")))
        runtime, events = world.runtime()
        await runtime.startup()
        try:
            task = await runtime.create_task(PROJECT, "with a designer")
            await _wait_status(world, task.id, Status.DONE)
            orchestrator = runtime.orchestrators[PROJECT]
            return (events, runtime.snapshot(PROJECT), orchestrator._agents.get("designer"),
                    await world.runs.list_runs(PROJECT, task.id))
        finally:
            await runtime.shutdown()

    events, snap, designer, runs = asyncio.run(scenario())

    assert isinstance(designer, CustomAgent) and designer.seat.display_name == "設計師"
    assert snap["pipelines"][0]["seats"] == ["pm", "designer", "swe", "qa"]
    assert list(snap["pipeline"]["stations"]) == ["pm", "designer", "swe", "qa"]
    working = [d["station"] for e, d in events if e == "station:update" and d["status"] == "working"]
    assert working == ["pm", "designer", "swe", "qa"]
    phases = [d for e, d in events if e == "agent:phase"]
    assert {d["station"] for d in phases} == {"pm", "designer", "swe", "qa"}  # live channel -> bus -> WS
    # The history recorder snapshotted the seats the run had (plan D20).
    assert [s["id"] for s in runs[0].seats] == ["pm", "designer", "swe", "qa"]
    assert runs[0].seats[1] == {"id": "designer", "display_name": "設計師", "look": "beret", "kind": "make"}


@GRAPH_ONLY
def test_live_phases_are_kept_for_the_snapshot_until_the_task_ends(tmp_path):
    _project(tmp_path, [_entry("task-001", approval="after_swe")])
    world = World(tmp_path)

    async def scenario():
        runtime, _ = world.runtime()
        await runtime.startup()
        try:
            orchestrator = runtime.orchestrators[PROJECT]
            await _until(lambda: orchestrator.is_awaiting_approval("task-001"))
            at_gate = runtime.snapshot(PROJECT)["live"]
            orchestrator.approve("task-001")
            await _wait_status(world, "task-001", Status.DONE)
            await asyncio.sleep(0.05)
            return at_gate, runtime.snapshot(PROJECT)["live"]
        finally:
            await runtime.shutdown()

    at_gate, after = asyncio.run(scenario())

    assert set(at_gate) == {"pm", "swe"}
    assert at_gate["swe"]["task_id"] == "task-001" and at_gate["swe"]["phase"] == "output"
    assert set(at_gate["swe"]) == {"task_id", "phase", "text"}
    assert after == {}


# -- addendum 2: the projects row exists before the line-up is imported ---------------------------------------


def test_the_project_row_is_created_before_the_lineup_import_so_migration_notes_are_kept(tmp_path):
    pm_file = {"name": "PM_agent", "role": "Project Manager", "description": "d",
               "llm_provider": "openai", "llm_model": "gpt-4o", "api_key_env": "OPENAI_API_KEY",
               "system_prompt": "你是 PM。"}
    project = _project(tmp_path, agent_files={"PM_agent.yaml": pm_file})
    before = (project / "agents" / "PM_agent.yaml").read_bytes()
    world = World(tmp_path)

    async def life():
        runtime, _ = world.runtime()
        await runtime.startup()
        await runtime.shutdown()

    asyncio.run(life())
    settings = asyncio.run(world.projects.get_settings(PROJECT))
    pm = asyncio.run(world.lineups.get_agent(PROJECT, "pm"))

    assert settings is not None and settings["llm"] == "mock"
    notes = settings.get("migration_notes", [])
    assert [n["agent"] for n in notes] == ["pm"] and "openai/gpt-4o" in notes[0]["message"]
    assert pm.system_prompt == "你是 PM。" and pm.llm is None
    assert (project / "agents" / "PM_agent.yaml").read_bytes() == before


def test_built_in_agents_use_the_prompt_stored_in_the_database(tmp_path):
    _project(tmp_path)
    world = World(tmp_path)

    async def life():
        runtime, _ = world.runtime()
        await runtime.startup()
        await runtime.shutdown()
        await world.lineups.update_agent(PROJECT, "swe", system_prompt="改過的 SWE prompt")
        runtime, _ = world.runtime()
        await runtime.startup()
        try:
            return runtime.orchestrators[PROJECT]._agents["swe"].config.system_prompt
        finally:
            await runtime.shutdown()

    assert asyncio.run(life()) == "改過的 SWE prompt"


# -- addenda 1 and 3: pending_seats after restore_gate, always with the real roster -------------------------

NEW_SEATS = ("pm", "designer", "swe", "qa")


@pytest.fixture
def lineup_calls(monkeypatch):
    """Every ``request_lineup`` call: (seat ids, roster ids or None)."""

    calls: list[tuple[tuple, list | None]] = []
    original = PipelineOrchestrator.request_lineup

    async def spy(self, lineup, agents=None, *, roster=None):
        calls.append((lineup.ids, None if roster is None else [v["id"] for v in roster]))
        return await original(self, lineup, agents, roster=roster)

    monkeypatch.setattr(PipelineOrchestrator, "request_lineup", spy)
    return calls


@GRAPH_ONLY
def test_pending_seats_wait_for_the_task_resumed_at_its_gate(tmp_path, lineup_calls):
    _project(tmp_path, [_entry("task-001", approval="after_swe")], agent_files={"designer.yaml": DESIGNER})
    world = World(tmp_path)

    async def first_life():
        runtime, _ = world.runtime()
        await runtime.startup()
        orchestrator = runtime.orchestrators[PROJECT]
        await _until(lambda: orchestrator.is_awaiting_approval("task-001"))
        await asyncio.sleep(0.05)  # gate_wait reaches interrupt()
        # A seat change asked for while the task waited, as the previous process recorded it.
        await world.lineups.set_pending_seats(PROJECT, "main", NEW_SEATS)
        await runtime.shutdown()

    async def second_life():
        runtime, events = world.runtime()
        await runtime.startup()
        try:
            orchestrator = runtime.orchestrators[PROJECT]
            at_start = runtime.snapshot(PROJECT)["pipelines"]
            record = await world.lineups.get_pipeline(PROJECT, "main")
            stored_at_start = (record.seats, record.pending_seats)
            assert orchestrator.approve("task-001")
            await _wait_status(world, "task-001", Status.DONE)
            await runtime.create_task(PROJECT, "next one")
            await _until(lambda: any(e == "task:completed" and d["task"]["title"] == "next one"
                                     for e, d in events))
            record = await world.lineups.get_pipeline(PROJECT, "main")
            return at_start, stored_at_start, (record.seats, record.pending_seats), events
        finally:
            await runtime.shutdown()

    asyncio.run(first_life())
    lineup_calls.clear()
    at_start, stored_at_start, stored_after, events = asyncio.run(second_life())

    assert at_start == [{**DEFAULT_PIPELINE_VIEW, "pending_seats": list(NEW_SEATS)}]
    assert stored_at_start == (("pm", "swe", "qa"), NEW_SEATS)
    assert lineup_calls == [(NEW_SEATS, ["pm", "swe", "qa", "designer"])]  # the real roster
    pending = [d for e, d in events if e == "pipeline:pending"]
    assert pending and pending[0]["task_id"] == "task-001"
    resumed = [d["station"] for e, d in events if e == "station:update" and d["status"] == "working"
               and d["task"]["id"] == "task-001"]
    assert "designer" not in resumed  # the resumed task finished on the graph it started on
    nxt = [d["station"] for e, d in events if e == "station:update" and d["status"] == "working"
           and d["task"]["title"] == "next one"]
    assert nxt == list(NEW_SEATS)
    assert stored_after == (NEW_SEATS, None)
    updated = [d for e, d in events if e == "pipeline:updated"]
    assert [v["id"] for v in updated[-1]["agents"]] == ["pm", "swe", "qa", "designer"]


@GRAPH_ONLY
def test_pending_seats_apply_at_startup_when_no_task_is_resumed(tmp_path, lineup_calls):
    _project(tmp_path, agent_files={"designer.yaml": DESIGNER})
    world = World(tmp_path)

    async def scenario():
        runtime, _ = world.runtime()
        await runtime.startup()
        await runtime.shutdown()
        await world.lineups.set_pending_seats(PROJECT, "main", NEW_SEATS)
        lineup_calls.clear()
        runtime, events = world.runtime()
        await runtime.startup()
        try:
            record = await world.lineups.get_pipeline(PROJECT, "main")
            return runtime.snapshot(PROJECT)["pipelines"], (record.seats, record.pending_seats), events
        finally:
            await runtime.shutdown()

    views, stored, events = asyncio.run(scenario())

    assert lineup_calls == [(NEW_SEATS, ["pm", "swe", "qa", "designer"])]
    assert views == [{**DEFAULT_PIPELINE_VIEW, "seats": list(NEW_SEATS)}]
    assert stored == (NEW_SEATS, None)
    joined = [d for e, d in events if e == "agent:joined"]
    assert [(d["agent"]["id"], d["position"]) for d in joined] == [("designer", 1)]


def test_unusable_pending_seats_are_dropped_with_a_warning(tmp_path, caplog):
    _project(tmp_path)
    world = World(tmp_path)

    async def scenario():
        runtime, _ = world.runtime()
        await runtime.startup()
        await runtime.shutdown()
        await world.lineups.set_pending_seats(PROJECT, "main", ("pm", "ghost", "qa"))
        runtime, _ = world.runtime()
        await runtime.startup()
        try:
            record = await world.lineups.get_pipeline(PROJECT, "main")
            return runtime.snapshot(PROJECT)["pipelines"], (record.seats, record.pending_seats)
        finally:
            await runtime.shutdown()

    caplog.set_level(logging.WARNING)
    views, stored = asyncio.run(scenario())

    assert views == [DEFAULT_PIPELINE_VIEW]
    assert stored == (("pm", "swe", "qa"), None)
    assert "ghost" in caplog.text


# -- addendum 4: a seat change through the runtime leaves the pipelines table to the orchestrator ------------


@GRAPH_ONLY
def test_a_seat_change_while_a_task_runs_writes_only_pending_seats(tmp_path, monkeypatch, lineup_calls):
    _project(tmp_path, [_entry("task-001", approval="after_swe")], agent_files={"designer.yaml": DESIGNER})
    world = World(tmp_path)
    saves: list = []
    original_save = world.lineups.save_pipeline

    async def counting_save(project_id, record):
        saves.append(record.seats)
        return await original_save(project_id, record)

    world.lineups.save_pipeline = counting_save

    async def scenario():
        runtime, _ = world.runtime()
        await runtime.startup()
        try:
            orchestrator = runtime.orchestrators[PROJECT]
            await _until(lambda: orchestrator.is_awaiting_approval("task-001"))
            saves.clear()
            view, effective = await runtime.request_seats(PROJECT, "main", list(NEW_SEATS))
            record = await world.lineups.get_pipeline(PROJECT, "main")
            return view, effective, (record.seats, record.pending_seats)
        finally:
            await runtime.shutdown()

    view, effective, stored = asyncio.run(scenario())

    assert effective == "next_task"
    assert view == {**DEFAULT_PIPELINE_VIEW, "pending_seats": list(NEW_SEATS)}
    assert stored == (("pm", "swe", "qa"), NEW_SEATS)
    assert saves == []  # nothing rewrote pipelines.seats
    assert lineup_calls[-1] == (NEW_SEATS, ["pm", "swe", "qa", "designer"])


@GRAPH_ONLY  # the legacy engine refuses every lineup write (409, Task 25a)
def test_request_seats_refuses_an_invalid_lineup_without_touching_anything(tmp_path):
    from models.lineup import InvalidLineup

    _project(tmp_path)
    world = World(tmp_path)

    async def scenario():
        runtime, _ = world.runtime()
        await runtime.startup()
        try:
            with pytest.raises(InvalidLineup) as info:
                await runtime.request_seats(PROJECT, "main", ["qa", "pm"])
            record = await world.lineups.get_pipeline(PROJECT, "main")
            return info.value.errors, (record.seats, record.pending_seats)
        finally:
            await runtime.shutdown()

    errors, stored = asyncio.run(scenario())

    assert any("第一席" in e["message"] for e in errors)
    assert stored == (("pm", "swe", "qa"), None)


# -- addenda 5 and 6: usage written off the loop, retried, kept and written later ---------------------------


def test_the_sync_engine_sets_tcp_keepalives_and_a_user_timeout(monkeypatch):
    import sqlalchemy

    from db.session import create_sync_engine_for

    captured = {}

    def fake_create_engine(url, **kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(sqlalchemy, "create_engine", fake_create_engine)
    create_sync_engine_for("postgresql+psycopg://u:p@localhost:1/db")

    args = captured["connect_args"]
    assert args["connect_timeout"] == 5
    assert args["keepalives"] == 1
    assert 0 < args["keepalives_idle"] <= 60 and args["keepalives_interval"] > 0 and args["keepalives_count"] > 0
    assert 0 < args["tcp_user_timeout"] <= 60_000  # milliseconds


class SlowStore(InMemoryUsageStore):
    def add(self, record):
        time.sleep(0.3)  # a blocking INSERT
        super().add(record)


def test_arecord_writes_off_the_event_loop():
    tracker = UsageTracker(store=SlowStore())

    async def scenario():
        ticks = 0
        done = False

        async def ticker():
            nonlocal ticks
            while not done:
                ticks += 1
                await asyncio.sleep(0.01)

        ticking = asyncio.create_task(ticker())
        record = await tracker.arecord("p", "t", "anthropic", "claude-opus-5-5", 10, 5)
        done = True
        await ticking
        return record, ticks

    record, ticks = asyncio.run(scenario())

    assert ticks >= 10  # the loop kept running while the row was written
    assert tracker._store.list_records() == [record]


class FlakyStore(InMemoryUsageStore):
    """Fails the first ``failures`` writes."""

    def __init__(self, failures: int) -> None:
        super().__init__()
        self.failures = failures
        self.attempts = 0

    def add(self, record):
        self.attempts += 1
        if self.failures > 0:
            self.failures -= 1
            raise RuntimeError("database hiccup")
        super().add(record)


def _fast_retries(tracker: UsageTracker) -> UsageTracker:
    tracker.store_retry_delays = (0, 0)
    return tracker


def test_a_transient_failure_is_retried_so_the_row_is_stored_before_usage_update(tmp_path):
    store = FlakyStore(failures=2)
    tracker = _fast_retries(UsageTracker(store=store))
    task = make_task()
    project = make_project(tmp_path, [task])
    orch, _ = make_orchestrator_with_repo(project, engine=config.get_pipeline_engine(), usage_tracker=tracker)
    checks: list[tuple[int, int]] = []
    inner = orch._on_event

    def watching(event, data):
        if event == "usage:update":
            checks.append((len(store.list_records()), int(data["project_usage"]["call_count"])))
        return inner(event, data)

    orch._on_event = watching
    asyncio.run(orch.process_task(task))

    assert checks == [(1, 1), (2, 2), (3, 3)]
    assert store.attempts == 5  # two failed attempts, then every row once


def test_a_failed_write_is_kept_and_written_before_the_next_one(caplog):
    store = FlakyStore(failures=3)  # more than one call's retries
    tracker = _fast_retries(UsageTracker(store=store))
    caplog.set_level(logging.WARNING)

    async def scenario():
        first = await tracker.arecord("p", "t", "anthropic", "claude-opus-5-5", 10, 5)
        after_first = list(store.list_records())
        second = await tracker.arecord("p", "t", "anthropic", "claude-opus-5-5", 20, 5)
        return first, after_first, second

    first, after_first, second = asyncio.run(scenario())

    assert after_first == [] and tracker.project_usage("p").call_count == 2
    assert store.list_records() == [first, second]  # kept, then written first, in order
    assert tracker.unsaved_count == 0
    assert "written later" in caplog.text


def test_the_sync_path_also_keeps_a_failed_record_for_later():
    store = FlakyStore(failures=1)
    tracker = UsageTracker(store=store)

    first = tracker.record("p", "t", "anthropic", "claude-opus-5-5", 10, 5)  # fails, kept
    second = tracker.record("p", "t", "anthropic", "claude-opus-5-5", 20, 5)

    assert store.list_records() == [first, second]


def test_the_runtime_writes_kept_usage_before_history_and_at_shutdown(tmp_path):
    _project(tmp_path)
    world = World(tmp_path)
    world.usage = FlakyStore(failures=0)

    async def scenario():
        runtime, _ = world.runtime()
        await runtime.startup()
        tracker = _fast_retries(runtime.usage_tracker)
        world.usage.failures = 3
        await tracker.arecord(PROJECT, "t", "anthropic", "claude-opus-5-5", 10, 5)
        kept = tracker.unsaved_count
        await runtime.shutdown()
        return kept

    kept = asyncio.run(scenario())

    assert kept == 1
    assert len(world.usage.list_records()) == 1  # written when the runtime shut down
