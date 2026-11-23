"""The runtime's line-up wiring on a real PostgreSQL (Task 24): repositories from ``DATABASE_URL``.

* the first start creates the ``projects`` row, then imports ``agents/*.yaml`` (the notes of an
  LLM setting that can not be kept land in ``projects.settings.migration_notes``);
* seats left pending by an earlier process are applied at the next start (no task to resume);
* usage rows go through the synchronous engine with keepalives and ``tcp_user_timeout``, which a
  real server accepts, off the event loop.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml

from core.runtime import FactoryRuntime
from core.stores.pg import PgLineupRepository, PgProjectRepository
from core.stores.usage_store import PgUsageStore
from core.usage_tracker import UsageTracker
from db.session import create_engine_for, create_sync_engine_for, session_factory

pytestmark = pytest.mark.integration

PROJECT = "demo"
DESIGNER = {"id": "designer", "display_name": "設計師", "duty": "畫介面草圖", "kind": "make",
            "look": "beret", "system_prompt": "你是介面設計師。"}
PM_FILE = {"name": "PM_agent", "role": "Project Manager", "description": "d", "llm_provider": "openai",
           "llm_model": "gpt-4o", "api_key_env": "OPENAI_API_KEY", "system_prompt": "你是 PM。"}


@pytest.fixture
def db_env(monkeypatch, migrated_db_sqlalchemy_url):
    monkeypatch.setenv("FORCE_MOCK", "true")
    monkeypatch.setenv("MOCK_LLM_DELAY", "0")
    monkeypatch.setenv("TASK_STORE", "db")
    monkeypatch.setenv("PIPELINE_CHECKPOINTER", "memory")
    monkeypatch.setenv("DATABASE_URL", migrated_db_sqlalchemy_url)
    return migrated_db_sqlalchemy_url


def _write(root: Path) -> Path:
    project = root / PROJECT
    (project / "agents").mkdir(parents=True)
    (project / "tasks.yaml").write_text(yaml.safe_dump({"tasks": []}), encoding="utf-8")
    for name, body in {"PM_agent.yaml": PM_FILE, "designer.yaml": DESIGNER}.items():
        (project / "agents" / name).write_text(yaml.safe_dump(body, allow_unicode=True), encoding="utf-8")
    return project


async def _repos(url: str):
    engine = create_engine_for(url, null_pool=True)
    sessions = session_factory(engine)
    return engine, PgLineupRepository(sessions), PgProjectRepository(sessions)


def test_first_start_imports_the_lineup_after_the_project_row_and_applies_pending_seats(tmp_path, db_env):
    _write(tmp_path)

    async def scenario():
        runtime = FactoryRuntime(projects_dir=tmp_path)
        await runtime.startup()
        try:
            assert isinstance(runtime.lineup_repo, PgLineupRepository)
            first = runtime.snapshot(PROJECT)
        finally:
            await runtime.shutdown()
        engine, lineups, projects = await _repos(db_env)
        try:
            settings = await projects.get_settings(PROJECT)
            await lineups.set_pending_seats(PROJECT, "main", ("pm", "designer", "swe", "qa"))
            runtime = FactoryRuntime(projects_dir=tmp_path)
            await runtime.startup()
            try:
                second = runtime.snapshot(PROJECT)
            finally:
                await runtime.shutdown()
            record = await lineups.get_pipeline(PROJECT, "main")
            return first, settings, second, (record.seats, record.pending_seats)
        finally:
            await engine.dispose()

    first, settings, second, stored = asyncio.run(scenario())

    assert [v["id"] for v in first["agents"]] == ["pm", "swe", "qa", "designer"]
    assert first["pipelines"] == [{"id": "main", "name": "主線", "seats": ["pm", "swe", "qa"], "pending_seats": None}]
    assert [n["agent"] for n in settings["migration_notes"]] == ["pm"]
    assert second["pipelines"][0]["seats"] == ["pm", "designer", "swe", "qa"]
    assert stored == (("pm", "designer", "swe", "qa"), None)


def test_usage_goes_through_the_keepalive_engine_off_the_loop(db_env):
    engine = create_sync_engine_for(db_env)
    store = PgUsageStore(engine)
    tracker = UsageTracker(store=store)

    async def scenario():
        return await tracker.arecord(PROJECT, "t1", "anthropic", "claude-opus-5-5", 100, 50)

    try:
        record = asyncio.run(scenario())
        stored = store.list_records(PROJECT)
    finally:
        store.dispose()

    assert len(stored) == 1 and stored[0].input_tokens == record.input_tokens == 100
    assert tracker.unsaved_count == 0
