"""PostgreSQL LineupRepository against the shared contract (Task 18)."""

from __future__ import annotations

import asyncio

import pytest

from core.stores.pg import PgLineupRepository, PgProjectRepository
from db.session import create_engine_for, session_factory
from tests.stores.contract_lineup import BUILTINS, MAIN, LineupRepositoryContract

pytestmark = pytest.mark.integration


@pytest.fixture
def sessions(migrated_db_sqlalchemy_url):
    engine = create_engine_for(migrated_db_sqlalchemy_url, null_pool=True)
    yield session_factory(engine)
    asyncio.run(engine.dispose())


class TestPgLineup(LineupRepositoryContract):
    @pytest.fixture
    def repos(self, sessions):
        return PgLineupRepository(sessions), PgProjectRepository(sessions)


def test_concurrent_initial_imports_write_the_lineup_exactly_once(sessions):
    repo = PgLineupRepository(sessions)

    async def scenario():
        results = await asyncio.gather(*[repo.import_initial("p", BUILTINS, [MAIN]) for _ in range(8)])
        assert sorted(results) == [False] * 7 + [True]
        assert [a.id for a in await repo.list_agents("p")] == ["pm", "swe", "qa"]

    asyncio.run(scenario())


def test_follow_the_project_default_is_stored_as_sql_null(sessions, migrated_db_url):
    import psycopg

    from tests.stores.contract_lineup import custom

    repo = PgLineupRepository(sessions)

    async def scenario():
        await repo.import_initial("p", [*BUILTINS, custom("designer", llm="mock")], [MAIN])
        await repo.insert_agent("p", custom("writer"))
        await repo.update_agent("p", "designer", llm=None)

    asyncio.run(scenario())
    with psycopg.connect(migrated_db_url) as conn:
        rows = conn.execute("SELECT id, llm IS NULL FROM agents WHERE project_id = 'p' ORDER BY id").fetchall()
    assert rows == [("designer", True), ("pm", True), ("qa", True), ("swe", True), ("writer", True)]
