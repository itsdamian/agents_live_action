"""The read side of the sticky-note history (Task 15b, plan D20, §7 GET /history, §7.1a).

``build_history`` is checked here on hand-seeded in-memory repositories (DB mode) and on a
project folder with log and deliverable files (file mode); the endpoints that call it are
covered in tests/test_history_api.py.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core.deliverables import read_agent_outputs, write_deliverable
from core.history_view import (
    DEFAULT_PIPELINE_ID,
    HISTORY_STATUSES,
    LiveState,
    build_history,
    count_statuses,
    effective_status,
    parse_status_filter,
    task_runs_view,
)
from core.stores.memory import InMemoryRunRepository
from core.task_history import DEFAULT_SEATS
from models.task import Priority, Status, Task

PROJECT = "p"
T0 = datetime(2026, 5, 1, 9, 0, 0)

ITEM_KEYS = {
    "task_id", "title", "priority", "status", "pipeline_id", "outcome", "reason", "rework_count", "path",
    "cost_usd", "started_at", "finished_at", "deliverable_count", "runs", "stale", "seats",
}


def at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


def task(task_id, status="pending", priority="medium", created=0, rework_count=0) -> Task:
    return Task(id=task_id, title=f"title {task_id}", description="d", priority=Priority(priority),
                status=Status(status), rework_count=rework_count, created_at=at(created), updated_at=at(created))


def run(coro):
    return asyncio.run(coro)


async def finished_run(runs, task_id, *, outcome="done", start=0, end=5, stations=("pm", "swe", "qa"),
                       reason=None, rework_count=0, cost=0.0, seats=DEFAULT_SEATS):
    record = await runs.open_run(PROJECT, task_id, started_at=at(start), seats=seats)
    for i, station in enumerate(stations):
        await runs.add_step(record.id, kind="station", at=at(start + i), station=station, status="done")
    await runs.update_totals(record.id, cost_usd=cost, input_tokens=10, output_tokens=2)
    await runs.finish_run(record.id, outcome=outcome, finished_at=at(end), reason=reason, rework_count=rework_count)
    return record


def history(tasks, runs, tmp_path, live=LiveState(), **kw):
    return run(build_history(tasks, project_id=PROJECT, run_repo=runs, project_path=tmp_path, live=live,
                             current_seats=[dict(s) for s in DEFAULT_SEATS], **kw))


# -- status: the database, overridden by what the orchestrator is doing ---------------------------------


@pytest.mark.parametrize("stored, live, expected", [
    ("pending", LiveState(), ("pending", False)),
    ("in_progress", LiveState(running=frozenset({"t"})), ("in_progress", False)),
    ("in_progress", LiveState(), ("in_progress", True)),  # nobody runs it: stale
    ("pending", LiveState(running=frozenset({"t"})), ("in_progress", False)),  # just picked up
    ("in_progress", LiveState(running=frozenset({"t"}), awaiting=frozenset({"t"})), ("awaiting_approval", False)),
    ("awaiting_approval", LiveState(awaiting=frozenset({"t"})), ("awaiting_approval", False)),
    ("done", LiveState(running=frozenset({"t"})), ("done", False)),  # stored first, event after
    ("failed", LiveState(), ("failed", False)),
])
def test_effective_status_and_stale(stored, live, expected):
    assert effective_status(task("t", stored), live) == expected


def test_counts_have_every_status_even_when_zero():
    tasks = [task("a", "done"), task("b", "done"), task("c", "in_progress"), task("d", "pending")]

    counts = count_statuses(tasks, LiveState(awaiting=frozenset({"c"})))

    assert counts == {"pending": 1, "in_progress": 0, "awaiting_approval": 1, "done": 2, "failed": 0}
    assert tuple(counts) == HISTORY_STATUSES


def test_parse_status_filter():
    assert parse_status_filter(None) is None and parse_status_filter("") is None
    assert parse_status_filter("done, failed") == ["done", "failed"]
    with pytest.raises(ValueError):
        parse_status_filter("done,finished")


# -- DB mode: items from tasks + their newest run --------------------------------------------------------


def test_an_item_carries_its_newest_run(tmp_path):
    runs = InMemoryRunRepository()

    async def seed():
        await finished_run(runs, "t1", outcome="failed", reason="first try", start=0, end=1, stations=("pm",))
        await finished_run(runs, "t1", outcome="done", start=2, end=9, cost=0.25, rework_count=1,
                           stations=("pm", "swe", "qa", "swe", "qa"))

    run(seed())
    write_deliverable(tmp_path, "t1", "PM_agent", "spec")
    write_deliverable(tmp_path, "t1", "SWE_agent", "```python\nprint(1)\n```")

    body = history([task("t1", "done", rework_count=1)], runs, tmp_path)

    assert (body["total"], len(body["items"])) == (1, 1)
    item = body["items"][0]
    assert set(item) == ITEM_KEYS
    assert item == {
        "task_id": "t1", "title": "title t1", "priority": "medium", "status": "done", "pipeline_id": None,
        "outcome": "done", "reason": None, "rework_count": 1, "path": ["pm", "swe", "qa", "swe", "qa"],
        "cost_usd": 0.25, "started_at": at(2).isoformat(), "finished_at": at(9).isoformat(),
        "deliverable_count": 3, "runs": 2, "stale": False, "seats": DEFAULT_SEATS,
    }


def test_a_task_without_a_run_gets_the_current_line_up_and_nulls(tmp_path):
    body = history([task("t1", "pending", rework_count=0)], InMemoryRunRepository(), tmp_path)

    item = body["items"][0]
    assert (item["outcome"], item["reason"], item["cost_usd"], item["started_at"], item["finished_at"]) == (
        None, None, None, None, None)
    assert (item["path"], item["runs"], item["deliverable_count"], item["seats"]) == ([], 0, 0, DEFAULT_SEATS)


def test_path_has_only_finished_or_failed_station_steps_of_seated_stations(tmp_path):
    runs = InMemoryRunRepository()

    async def seed():
        record = await runs.open_run(PROJECT, "t1", started_at=at(0), seats=DEFAULT_SEATS)
        for kind, station, status in [("station", "pm", "done"), ("station", "swe", "done"),
                                      ("gate", "swe", "approved"), ("station", "qa", "done"),
                                      ("rework", "swe", "rework"), ("station", "swe", "error"),
                                      ("station", "ghost", "done")]:
            await runs.add_step(record.id, kind=kind, at=at(1), station=station, status=status)

    run(seed())
    item = history([task("t1", "in_progress")], runs, tmp_path, live=LiveState(running=frozenset({"t1"})))["items"][0]

    assert item["path"] == ["pm", "swe", "qa", "swe"]  # no gate, no rework marker, nothing outside the seats
    assert (item["outcome"], item["finished_at"], item["status"], item["stale"]) == (None, None, "in_progress", False)


def test_an_open_run_shows_the_tasks_own_rework_count(tmp_path):
    runs = InMemoryRunRepository()
    run(runs.open_run(PROJECT, "t1", started_at=at(0), seats=DEFAULT_SEATS))

    item = history([task("t1", "in_progress", rework_count=2)], runs, tmp_path,
                   live=LiveState(running=frozenset({"t1"})))["items"][0]

    assert item["rework_count"] == 2


# -- filters, counts, order, pages ---------------------------------------------------------------------


def _mixed(runs):
    async def seed():
        await finished_run(runs, "d-old", start=0, end=10)
        await finished_run(runs, "d-new", start=0, end=30)
        await finished_run(runs, "f-mid", outcome="failed", reason="boom", start=0, end=20)

    run(seed())
    return [
        task("d-old", "done"), task("d-new", "done"), task("f-mid", "failed"),
        task("p-low-old", "pending", "low", created=1), task("p-high", "pending", "high", created=5),
        task("p-low-new", "pending", "low", created=3), task("busy", "in_progress", "medium", created=2),
        task("gate", "awaiting_approval", "critical", created=4),
    ]


def test_order_open_tasks_by_priority_then_age_and_finished_ones_newest_first(tmp_path):
    runs = InMemoryRunRepository()
    tasks = _mixed(runs)
    live = LiveState(running=frozenset({"busy", "gate"}), awaiting=frozenset({"gate"}))

    body = history(tasks, runs, tmp_path, live=live)

    assert [i["task_id"] for i in body["items"]] == [
        "gate", "p-high", "busy", "p-low-old", "p-low-new",  # open: priority, then created_at
        "d-new", "f-mid", "d-old",  # finished: finished_at, newest first
    ]
    assert body["total"] == 8
    assert body["counts"] == {"pending": 3, "in_progress": 1, "awaiting_approval": 1, "done": 2, "failed": 1}


def test_status_filter_narrows_items_and_total_but_not_counts(tmp_path):
    runs = InMemoryRunRepository()
    tasks = _mixed(runs)

    body = history(tasks, runs, tmp_path, statuses=["done", "failed"])

    assert [i["task_id"] for i in body["items"]] == ["d-new", "f-mid", "d-old"]
    assert body["total"] == 3
    assert body["counts"]["pending"] == 3  # counts are of the whole wall (for the tabs)
    assert [i["reason"] for i in body["items"]] == [None, "boom", None]


def test_pages(tmp_path):
    runs = InMemoryRunRepository()
    tasks = _mixed(runs)

    first = history(tasks, runs, tmp_path, limit=3, offset=0)
    rest = history(tasks, runs, tmp_path, limit=3, offset=3)
    past = history(tasks, runs, tmp_path, limit=3, offset=99)

    assert (first["total"], rest["total"], past["total"]) == (8, 8, 8)
    assert len(first["items"]) == 3 and len(rest["items"]) == 3 and past["items"] == []
    whole = [i["task_id"] for i in history(tasks, runs, tmp_path)["items"]]
    assert [i["task_id"] for i in first["items"] + rest["items"]] == whole[:6]


def test_pipeline_filter_the_default_line_holds_tasks_without_a_pipeline(tmp_path):
    runs = InMemoryRunRepository()
    tasks = _mixed(runs)

    main = history(tasks, runs, tmp_path, pipeline=DEFAULT_PIPELINE_ID)
    other = history(tasks, runs, tmp_path, pipeline="bugfix")

    assert main["total"] == 8
    assert (other["total"], other["items"]) == (0, [])
    assert other["counts"] == dict.fromkeys(HISTORY_STATUSES, 0)


def test_stale_in_progress_task(tmp_path):
    item = history([task("t1", "in_progress")], InMemoryRunRepository(), tmp_path)["items"][0]

    assert (item["status"], item["stale"]) == ("in_progress", True)


# -- file mode: tasks.yaml + log names + deliverables ------------------------------------------------------


def _log(project: Path, stamp: str, agent: str, task_id: str):
    logs = project / "logs"
    logs.mkdir(exist_ok=True)
    (logs / f"{stamp}_{agent}_{task_id}.log").write_text(f"agent: {agent}\nstatus: SUCCESS\n\nbody\n",
                                                          encoding="utf-8")


def test_file_mode_path_comes_from_the_log_names_and_run_fields_are_null(tmp_path):
    for stamp, agent in [("20260501_090000", "PM_agent"), ("20260501_090100", "SWE_agent"),
                         ("20260501_090200", "QA_agent"), ("20260501_090300", "SWE_agent"),
                         ("20260501_090400", "QA_agent"), ("20260501_090500", "DevOps_agent")]:
        _log(tmp_path, stamp, agent, "t1")
    _log(tmp_path, "20260501_090000", "PM_agent", "t10")  # another task
    write_deliverable(tmp_path, "t1", "PM_agent", "spec")

    body = history([task("t1", "done", rework_count=1), task("t10", "pending")], None, tmp_path)

    item = next(i for i in body["items"] if i["task_id"] == "t1")
    assert item["path"] == ["pm", "swe", "qa", "swe", "qa"]
    assert (item["outcome"], item["reason"], item["cost_usd"], item["started_at"], item["finished_at"],
            item["pipeline_id"]) == (None,) * 6
    assert (item["runs"], item["rework_count"], item["deliverable_count"], item["seats"]) == (0, 1, 1, DEFAULT_SEATS)
    assert body["counts"]["done"] == 1 and body["counts"]["pending"] == 1


# -- task detail: runs and agent outputs ----------------------------------------------------------------------


def test_task_runs_view_newest_first_with_steps(tmp_path):
    runs = InMemoryRunRepository()

    async def seed():
        await finished_run(runs, "t1", outcome="failed", reason="r", start=0, end=1, stations=("pm",))
        await finished_run(runs, "t1", start=2, end=3, cost=0.5)
        await finished_run(runs, "other", start=0, end=1)
        return await task_runs_view(runs, PROJECT, "t1")

    view = run(seed())

    assert [r["run_no"] for r in view] == [2, 1]
    assert set(view[0]) == {"run_no", "pipeline_id", "outcome", "reason", "rework_count", "started_at",
                            "finished_at", "cost_usd", "input_tokens", "output_tokens", "imported", "seats", "steps"}
    assert (view[1]["outcome"], view[1]["reason"], view[1]["started_at"], view[1]["finished_at"]) == (
        "failed", "r", at(0).isoformat(), at(1).isoformat())
    assert (view[0]["cost_usd"], view[0]["input_tokens"], view[0]["imported"], view[0]["seats"]) == (
        0.5, 10, False, DEFAULT_SEATS)
    assert view[0]["steps"][0] == {"seq": 1, "kind": "station", "station": "pm", "status": "done", "note": None,
                                   "at": at(2).isoformat()}
    assert [s["station"] for s in view[0]["steps"]] == ["pm", "swe", "qa"]
    assert run(task_runs_view(None, PROJECT, "t1")) == []  # file mode


def test_read_agent_outputs_latest_per_station_in_seat_order(tmp_path):
    write_deliverable(tmp_path, "t1", "QA_agent", "qa first")
    write_deliverable(tmp_path, "t1", "PM_agent", "the spec")
    write_deliverable(tmp_path, "t1", "SWE_agent", "swe v1")
    write_deliverable(tmp_path, "t1", "SWE_agent", "swe v2")
    write_deliverable(tmp_path, "t2", "PM_agent", "not mine")

    outputs = read_agent_outputs(tmp_path, "t1", DEFAULT_SEATS + [{"id": "designer", "display_name": "設計師"}])

    assert outputs == [
        {"station": "pm", "display_name": "PM", "content": "the spec", "truncated": False},
        {"station": "swe", "display_name": "SWE", "content": "swe v2", "truncated": False},
        {"station": "qa", "display_name": "QA", "content": "qa first", "truncated": False},
    ]  # designer produced nothing: not listed


def test_read_agent_outputs_caps_the_content(tmp_path):
    write_deliverable(tmp_path, "t1", "PM_agent", "x" * 20_001)
    write_deliverable(tmp_path, "t1", "QA_agent", "y" * 20_000)

    outputs = {o["station"]: o for o in read_agent_outputs(tmp_path, "t1", DEFAULT_SEATS)}

    assert (len(outputs["pm"]["content"]), outputs["pm"]["truncated"]) == (20_000, True)
    assert (len(outputs["qa"]["content"]), outputs["qa"]["truncated"]) == (20_000, False)
    assert read_agent_outputs(tmp_path, "nothing", DEFAULT_SEATS) == []
