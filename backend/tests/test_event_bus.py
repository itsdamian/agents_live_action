"""EventBus (Task 16, plan D8): the runtime's event exit goes through a bus.

``InProcessEventBus`` is the single-process implementation (phase 6 adds a Redis one behind the
same interface). The runtime records history before publishing, keeps sending ``devops:alert``
to every client directly, and delivers to WebSocket clients as a bus subscriber.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from agents.devops_agent import EVENT_DEVOPS_ALERT
from core.event_bus import EventBus, InProcessEventBus
from core.runtime import FactoryRuntime
from core.stores.memory import InMemoryRunRepository, InMemoryTaskRepository
from core.task_history import HistoryRecorder, TrackerUsage

PROJECT = "demo"


@pytest.fixture(autouse=True)
def _mock_only(monkeypatch):
    monkeypatch.setenv("FORCE_MOCK", "true")
    for name in ("TASK_STORE", "PIPELINE_CHECKPOINTER", "DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)


def _collector(seen: list, name: str):
    async def handler(project_id, event, data):
        seen.append((name, project_id, event, data))

    return handler


# -- InProcessEventBus ---------------------------------------------------------------------------------------


def test_in_process_bus_satisfies_the_interface():
    assert isinstance(InProcessEventBus(), EventBus)


def test_publish_reaches_every_subscriber_in_subscription_order():
    bus, seen = InProcessEventBus(), []
    bus.subscribe(_collector(seen, "a"))
    bus.subscribe(_collector(seen, "b"))

    asyncio.run(bus.publish("p", "task:created", {"x": 1}))

    assert seen == [("a", "p", "task:created", {"x": 1}), ("b", "p", "task:created", {"x": 1})]


def test_unsubscribe_stops_delivery_and_publishing_to_nobody_is_fine():
    bus, seen = InProcessEventBus(), []
    unsubscribe = bus.subscribe(_collector(seen, "a"))
    unsubscribe()
    unsubscribe()  # twice is harmless

    asyncio.run(bus.publish("p", "e", {}))

    assert seen == []


def test_a_failing_subscriber_is_logged_and_does_not_stop_the_others(caplog):
    bus, seen = InProcessEventBus(), []

    async def broken(project_id, event, data):
        raise RuntimeError("subscriber down")

    bus.subscribe(broken)
    bus.subscribe(_collector(seen, "ok"))
    with caplog.at_level(logging.ERROR, logger="core.event_bus"):
        asyncio.run(bus.publish("p", "e", {"k": "v"}))

    assert seen == [("ok", "p", "e", {"k": "v"})]
    assert "subscriber down" in caplog.text


def test_a_subscriber_may_unsubscribe_while_an_event_is_delivered():
    bus, seen = InProcessEventBus(), []
    unsubscribe = None

    async def once(project_id, event, data):
        seen.append(event)
        unsubscribe()

    unsubscribe = bus.subscribe(once)
    bus.subscribe(_collector(seen, "b"))

    async def scenario():
        await bus.publish("p", "first", {})
        await bus.publish("p", "second", {})

    asyncio.run(scenario())

    assert seen == ["first", ("b", "p", "first", {}), ("b", "p", "second", {})]


# -- the runtime's event exit -------------------------------------------------------------------------------------


def _db_runtime(tmp_path, bus=None):
    tasks, runs = InMemoryTaskRepository(), InMemoryRunRepository()
    runtime = FactoryRuntime(projects_dir=tmp_path, task_store="db", task_repo=tasks, run_repo=runs,
                             event_bus=bus)
    runtime.history = HistoryRecorder(runs, usage=TrackerUsage(runtime.usage_tracker))  # startup does this
    return runtime, runs


def _created(task_id="task-001"):
    return {"task": {"id": task_id, "title": "t", "priority": "medium", "status": "pending", "rework_count": 0},
            "queue_size": 0}


def test_the_runtime_uses_an_injected_bus_or_its_own(tmp_path):
    bus = InProcessEventBus()
    assert FactoryRuntime(projects_dir=tmp_path, event_bus=bus).event_bus is bus
    assert isinstance(FactoryRuntime(projects_dir=tmp_path).event_bus, InProcessEventBus)


def test_pipeline_events_go_through_the_bus_to_the_websocket_clients(tmp_path, monkeypatch):
    runtime, _ = _db_runtime(tmp_path)
    order = []

    async def broadcast(project_id, event, data):
        order.append(("ws", project_id, event))

    monkeypatch.setattr(runtime.ws_manager, "broadcast", broadcast)
    runtime.event_bus.subscribe(_collector(order, "spy"))

    asyncio.run(runtime._make_event_forwarder(PROJECT)("task:created", _created()))

    # The WebSocket delivery is the first subscriber; the event went through the bus once.
    assert order == [("ws", PROJECT, "task:created"), ("spy", PROJECT, "task:created", _created())]


def test_history_is_recorded_before_the_bus_publishes(tmp_path):
    bus = InProcessEventBus()
    runtime, runs = _db_runtime(tmp_path, bus)
    seen_runs = []

    async def check(project_id, event, data):
        seen_runs.append(await runs.find_open_run(project_id, "task-001"))

    bus.subscribe(check)
    asyncio.run(runtime._make_event_forwarder(PROJECT)("task:created", _created()))

    assert seen_runs[0] is not None and seen_runs[0].run_no == 1


def test_devops_alerts_bypass_the_bus_and_reach_every_client(tmp_path, monkeypatch):
    runtime, _ = _db_runtime(tmp_path)
    on_bus, to_all = [], []
    runtime.event_bus.subscribe(_collector(on_bus, "spy"))

    async def broadcast_all(event, data):
        to_all.append((event, data))

    monkeypatch.setattr(runtime.ws_manager, "broadcast_all", broadcast_all)
    alert = {"severity": "critical", "message": "budget"}

    asyncio.run(runtime._make_event_forwarder(PROJECT)(EVENT_DEVOPS_ALERT, alert))

    assert to_all == [(EVENT_DEVOPS_ALERT, alert)]
    assert on_bus == []
    assert list(runtime.recent_alerts) == [alert]
