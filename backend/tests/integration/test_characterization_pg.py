"""DB mode against a real PostgreSQL behaves like file mode (Task 10, AC1).

S1 (plain run), S6 (approval gate) and S11 (usage and budget) from the characterization
scenarios, with a PgTaskRepository injected into the orchestrator.
"""

from __future__ import annotations

import asyncio

import pytest

from core.stores.pg import PgTaskRepository
from db.session import create_engine_for, session_factory
from tests.hud_harness import SCENARIOS, run_scenario

pytestmark = pytest.mark.integration

ENGINES = ["legacy", "langgraph"]


@pytest.fixture
def pg_repo(migrated_db_sqlalchemy_url):
    engine = create_engine_for(migrated_db_sqlalchemy_url, null_pool=True)
    yield PgTaskRepository(session_factory(engine))
    asyncio.run(engine.dispose())


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("scenario_id", ["S1", "S6", "S11"])
def test_postgres_mode_is_observably_identical_to_file_mode(
    tmp_path, monkeypatch, pg_repo, engine, scenario_id
):
    spec = SCENARIOS[scenario_id]

    file_obs, _ = run_scenario(
        spec, mode="file", engine=engine, workdir=tmp_path / "file", monkeypatch=monkeypatch
    )
    db_obs, db_extras = run_scenario(
        spec, mode="db", engine=engine, workdir=tmp_path / "db", monkeypatch=monkeypatch, repo=pg_repo
    )

    assert db_obs == file_obs
    assert db_extras["file_writes"] == [] and db_extras["yaml_unchanged"] is True


@pytest.mark.parametrize("engine", ENGINES)
def test_the_status_history_is_really_in_the_database(tmp_path, monkeypatch, pg_repo, engine):
    obs, _ = run_scenario(
        SCENARIOS["S6"], mode="db", engine=engine, workdir=tmp_path, monkeypatch=monkeypatch, repo=pg_repo
    )

    stored = asyncio.run(pg_repo.get("demo-project", "task-001"))
    assert obs["status_writes"] == ["in_progress", "awaiting_approval", "in_progress", "done"]
    assert stored.status.value == "done"
    assert asyncio.run(pg_repo.count_by_status("demo-project")) == {
        "pending": 0, "in_progress": 0, "awaiting_approval": 0, "done": 1, "failed": 0}
