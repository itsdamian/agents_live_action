"""Usage persistence against a real PostgreSQL (Task 12, AC6)."""

from __future__ import annotations

import asyncio
from datetime import datetime

import psycopg
import pytest

from core.runtime import FactoryRuntime
from core.stores.pg import PgUsageRepository
from core.stores.usage_store import PgUsageStore
from core.usage_tracker import UsageRecord, UsageTracker
from db.session import create_engine_for, create_sync_engine_for, session_factory

pytestmark = pytest.mark.integration


@pytest.fixture
def store(migrated_db_sqlalchemy_url):
    engine = create_sync_engine_for(migrated_db_sqlalchemy_url)
    yield PgUsageStore(engine)
    engine.dispose()


def _filled(tracker):
    remaining = [datetime(2026, 6, 1, 23, 50), datetime(2026, 6, 1, 23, 51), datetime(2026, 6, 2, 0, 10)]
    tracker._clock = lambda: remaining.pop(0) if len(remaining) > 1 else remaining[0]
    tracker.record("p1", "t1", "anthropic", "claude-opus-5-5", 1000, 500)
    tracker.record("p1", "t2", "openai", "gpt-4o", 200, 100)
    tracker.record("p2", "t1", "anthropic", "claude-sonnet-5-5", 300, 300)


def test_records_round_trip_through_postgres_in_order(store):
    first = UsageRecord("p", "t", "anthropic", "claude-opus-5-5", 10, 5, 0.000123, datetime(2026, 1, 1, 12, 0, 0))
    second = UsageRecord("q", "t", "openai", "gpt-4o", 1, 1, 0.5, datetime(2026, 1, 1, 12, 0, 1))

    store.add(first)
    store.add(second)

    assert store.list_records() == [first, second]
    assert store.list_records("q") == [second]


def test_a_rebuilt_tracker_has_the_same_totals_and_budget_status(store):
    before = UsageTracker(project_budget_usd=0.01, store=store)
    _filled(before)

    after = UsageTracker(project_budget_usd=0.01)
    after.load_from(store)

    for project in ("p1", "p2"):
        assert after.project_usage(project) == before.project_usage(project)
    assert after.task_usage("p1", "t1") == before.task_usage("p1", "t1")
    assert after.daily_usage(datetime(2026, 6, 1).date()) == before.daily_usage(datetime(2026, 6, 1).date())
    assert after.daily_usage(datetime(2026, 6, 2).date()).call_count == 1
    assert after.budget_status("p1").exceeded == before.budget_status("p1").exceeded is True


def test_the_async_repository_sees_what_the_sync_store_wrote(store, migrated_db_sqlalchemy_url):
    store.add(UsageRecord("p", "t", "anthropic", "claude-opus-5-5", 7, 3, 0.25, datetime(2026, 1, 1)))
    store.add(UsageRecord("p", "t", "anthropic", "claude-opus-5-5", 5, 2, 0.75, datetime(2026, 1, 2)))

    async def read():
        engine = create_engine_for(migrated_db_sqlalchemy_url, null_pool=True)
        try:
            totals = await PgUsageRepository(session_factory(engine)).totals("p", "t")
        finally:
            await engine.dispose()
        return totals

    totals = asyncio.run(read())

    assert (totals.input_tokens, totals.output_tokens, totals.call_count, totals.cost) == (12, 5, 2, 1.0)


def test_the_row_is_in_the_database_when_usage_update_is_emitted(tmp_path, monkeypatch, migrated_db_sqlalchemy_url, migrated_db_url):
    """A real run on a real database: at every usage:update the table already has that call."""

    monkeypatch.setenv("FORCE_MOCK", "true")
    monkeypatch.setenv("TASK_STORE", "db")
    monkeypatch.setenv("PIPELINE_CHECKPOINTER", "memory")
    monkeypatch.setenv("DATABASE_URL", migrated_db_sqlalchemy_url)
    project = tmp_path / "demo"
    project.mkdir()
    (project / "tasks.yaml").write_text(
        "tasks:\n  - {id: task-001, title: t, description: d, priority: low, status: pending}\n", encoding="utf-8")
    seen: list[tuple[int, int]] = []

    def stored_calls() -> int:
        with psycopg.connect(migrated_db_url) as conn:
            return conn.execute("SELECT count(*) FROM usage_records").fetchone()[0]

    async def life():
        runtime = FactoryRuntime(projects_dir=tmp_path)
        inner = runtime.ws_manager.broadcast

        async def watching(project_id, event, data):
            if event == "usage:update":
                seen.append((stored_calls(), data["project_usage"]["call_count"]))
            return await inner(project_id, event, data)

        runtime.ws_manager.broadcast = watching
        await runtime.startup()
        try:
            deadline = asyncio.get_running_loop().time() + 30
            while asyncio.get_running_loop().time() < deadline and stored_calls() < 3:
                await asyncio.sleep(0.1)
            await asyncio.sleep(0.3)
            return runtime.usage_summary("demo")
        finally:
            await runtime.shutdown()

    first = asyncio.run(life())
    assert seen == [(1, 1), (2, 2), (3, 3)]
    assert first["project"]["call_count"] == 3 and first["project"]["input_tokens"] > 0

    async def second_life():
        runtime = FactoryRuntime(projects_dir=tmp_path)
        await runtime.startup()
        try:
            return runtime.usage_summary("demo")
        finally:
            await runtime.shutdown()

    assert asyncio.run(second_life()) == first  # the spend survived the restart
