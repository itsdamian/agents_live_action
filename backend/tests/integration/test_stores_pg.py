"""PostgreSQL repositories against the shared contracts, plus Postgres-only behaviour (Task 9, AC3)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import psycopg
import pytest

from core.stores.pg import PgProjectRepository, PgRunRepository, PgTaskRepository, PgUsageRepository
from db.session import create_engine_for, session_factory
from models.task import Priority, Status
from tests.stores.contract_projects import ProjectRepositoryContract
from tests.stores.contract_runs import RunRepositoryContract
from tests.stores.contract_tasks import TaskRepositoryContract, make
from tests.stores.contract_usage import UsageRepositoryContract

pytestmark = pytest.mark.integration


@pytest.fixture
def sessions(migrated_db_sqlalchemy_url):
    # NullPool: each contract test runs its coroutine in its own event loop, and a pooled
    # connection belongs to the loop that opened it.
    engine = create_engine_for(migrated_db_sqlalchemy_url, null_pool=True)
    yield session_factory(engine)
    asyncio.run(engine.dispose())


class TestPgTasks(TaskRepositoryContract):
    @pytest.fixture
    def repo(self, sessions):
        return PgTaskRepository(sessions)

    async def seed(self, repo, project_id, task):
        await repo.insert_if_absent(project_id, task, source="yaml")


class TestPgUsage(UsageRepositoryContract):
    @pytest.fixture
    def repo(self, sessions):
        return PgUsageRepository(sessions)


class TestPgRuns(RunRepositoryContract):
    @pytest.fixture
    def repo(self, sessions):
        return PgRunRepository(sessions)


class TestPgProjects(ProjectRepositoryContract):
    @pytest.fixture
    def repos(self, sessions):
        return PgProjectRepository(sessions), PgTaskRepository(sessions), PgRunRepository(sessions)


# -- behaviour only a real database can show --------------------------------------------------------


def test_concurrent_creates_get_distinct_sequential_ids(sessions):
    repo = PgTaskRepository(sessions)

    async def scenario():
        tasks = await asyncio.gather(*[repo.create("p", f"t{i}") for i in range(20)])
        assert sorted(t.id for t in tasks) == [f"task-{i:03d}" for i in range(1, 21)]
        assert len(await repo.list_tasks("p")) == 20

    asyncio.run(scenario())


def test_concurrent_runs_and_steps_are_numbered_without_gaps_or_collisions(sessions):
    runs = PgRunRepository(sessions)
    t0 = datetime(2026, 5, 1, 9, 0, 0)

    async def scenario():
        opened = await asyncio.gather(*[runs.open_run("p", "t1", started_at=t0) for _ in range(12)])
        assert sorted(r.run_no for r in opened) == list(range(1, 13))
        run = opened[0]
        steps = await asyncio.gather(
            *[runs.add_step(run.id, kind="station", station="pm", at=t0 + timedelta(seconds=i)) for i in range(15)]
        )
        assert sorted(s.seq for s in steps) == list(range(1, 16))
        assert [s.seq for s in await runs.list_steps(run.id)] == list(range(1, 16))

    asyncio.run(scenario())


def test_adding_a_step_to_an_unknown_run_fails_instead_of_inventing_one(sessions):
    runs = PgRunRepository(sessions)

    async def scenario():
        with pytest.raises(Exception) as excinfo:
            await runs.add_step(10**9, kind="station", at=datetime(2026, 5, 1))
        assert "foreign key" in str(excinfo.value).lower()

    asyncio.run(scenario())


def test_values_are_stored_as_plain_text_and_inserting_a_task_creates_no_project_row(
    sessions, migrated_db_url
):
    repo = PgTaskRepository(sessions)

    async def scenario():
        await repo.insert_if_absent(
            "p", make("task-001", priority=Priority.HIGH, status=Status.AWAITING_APPROVAL), source="todo", source_hash="abc"
        )

    asyncio.run(scenario())
    with psycopg.connect(migrated_db_url) as conn:
        row = conn.execute("SELECT priority, status, approval, source, source_hash FROM tasks").fetchone()
        assert row == ("high", "awaiting_approval", "none", "todo", "abc")
        # A project row means "migrated/imported" (plan D3 (3)); writing a task must not fake one.
        assert conn.execute("SELECT count(*) FROM projects").fetchone() == (0,)


def test_usage_money_keeps_six_decimals_and_sums_exactly(sessions):
    from core.usage_tracker import UsageRecord

    repo = PgUsageRepository(sessions)
    t0 = datetime(2026, 5, 1)

    async def scenario():
        for i in range(10):  # ten * 0.1 is 1.0 exactly in numeric, not 0.9999999999999999
            await repo.add(UsageRecord("p", "t", "anthropic", "claude-opus-5-5", 1, 1, 0.1, t0 + timedelta(seconds=i)))
        assert (await repo.totals("p", "t")).cost == 1.0

    asyncio.run(scenario())


def test_state_survives_a_new_engine(migrated_db_sqlalchemy_url):
    """What was written is in the database, not in the repository object."""

    async def write():
        engine = create_engine_for(migrated_db_sqlalchemy_url, null_pool=True)
        repo = PgTaskRepository(session_factory(engine))
        await repo.create("p", "survives")
        await repo.set_status("p", "task-001", Status.DONE)
        await engine.dispose()

    async def read():
        engine = create_engine_for(migrated_db_sqlalchemy_url, null_pool=True)
        task = await PgTaskRepository(session_factory(engine)).get("p", "task-001")
        await engine.dispose()
        return task

    asyncio.run(write())
    task = asyncio.run(read())

    assert (task.title, task.status) == ("survives", Status.DONE)
