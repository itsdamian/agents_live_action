"""A pending lineup and a task resumed at its gate, on a real PostgreSQL (plan D17, 2026-11-23 and 2026-11-24 rulings).

The first "process" runs a task to its gate and records a lineup change in
``pipelines.pending_seats``. The second one (its own event loop, saver and connections) restores
the gate from the checkpoint tables and asks for the stored pending seats: the line is busy with
the resumed task, so the change stays pending until that task has finished on the old graph,
and is applied (``pending_seats`` cleared) as soon as it has; the next task runs on the new lineup.
"""

from __future__ import annotations

import asyncio

import pytest

from core.checkpointing import open_postgres_checkpointer
from core.stores import PipelineRecord
from core.stores.pg import PgLineupRepository
from db.session import create_engine_for, session_factory
from models.lineup import DEFAULT_LINEUP
from models.task import Approval, Status
from tests.hud_harness import make_lineup, make_orchestrator_with_repo
from tests.pipeline_harness import make_project, make_task
from tests.test_lineup_restart import _to_gate, _until, _working_stations

pytestmark = pytest.mark.integration

DEFAULT_SEATS = ("pm", "swe", "qa")
NEW_SEATS = ("pm", "designer", "swe", "qa")


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MOCK_LLM_DELAY", "0")


@pytest.fixture
def sessions(migrated_db_sqlalchemy_url):
    engine = create_engine_for(migrated_db_sqlalchemy_url, null_pool=True)
    yield session_factory(engine)
    asyncio.run(engine.dispose())


def test_pending_seats_in_the_database_wait_for_the_task_resumed_at_its_gate(
    tmp_path, migrated_db_sqlalchemy_url, sessions
):
    gated, later = make_task("task-001", approval=Approval.AFTER_SWE), make_task("task-002")
    project = make_project(tmp_path, [gated, later])
    repo = PgLineupRepository(sessions)

    async def before_restart():
        await repo.import_initial(project.name, [], [PipelineRecord("main", "主線", True, DEFAULT_SEATS)])
        async with open_postgres_checkpointer(migrated_db_sqlalchemy_url) as saver:
            first, _ = make_orchestrator_with_repo(project, engine="langgraph")
            first.use_checkpointer(saver)
            await _to_gate(first, gated)
        await repo.set_pending_seats(project.name, "main", NEW_SEATS)

    asyncio.run(before_restart())

    second, events = make_orchestrator_with_repo(project, engine="langgraph")
    second._lineup_repo = repo
    stored = make_task("task-001", approval=Approval.AFTER_SWE, status=Status.AWAITING_APPROVAL)

    async def after_restart():
        async with open_postgres_checkpointer(migrated_db_sqlalchemy_url) as saver:
            second.use_checkpointer(saver)
            assert await second.restore_gate(stored) is True
            record = await repo.get_pipeline(project.name, "main")
            assert record.pending_seats == NEW_SEATS
            lineup, agents = make_lineup(*[s if s != "designer" else ("designer", "make")
                                           for s in record.pending_seats])
            assert await second.request_lineup(lineup, agents) == "next_task"
            assert second.lineup == DEFAULT_LINEUP
            assert (await repo.get_pipeline(project.name, "main")).pending_seats == NEW_SEATS

            second.start()
            assert second.approve(stored.id)
            await _until(lambda: stored.status is Status.DONE, timeout=30)
            # Applied as soon as the resumed task ended, before any next task (AC18, 2026-11-24).
            for _ in range(600):
                record = await repo.get_pipeline(project.name, "main")
                if record.pending_seats is None:
                    break
                await asyncio.sleep(0.05)
            assert (record.seats, record.pending_seats) == (NEW_SEATS, None)
            assert second.lineup == lineup and second.pending_lineup is None

            second.enqueue(later)
            await _until(lambda: later.status is Status.DONE, timeout=30)
            await second.stop()
        return lineup, await repo.get_pipeline(project.name, "main")

    lineup, record = asyncio.run(after_restart())
    assert (record.seats, record.pending_seats) == (NEW_SEATS, None)
    assert second.lineup == lineup
    assert _working_stations(events, "task-001") == ["qa"]
    assert _working_stations(events, "task-002") == list(NEW_SEATS)
