"""Smoke test for the integration fixtures: a real Postgres answers and each test gets its own database."""

from __future__ import annotations

import psycopg
import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.integration


_seen_databases: set[str] = set()


def test_select_one(empty_db_url):
    with psycopg.connect(empty_db_url) as conn:
        assert conn.execute("SELECT 1").fetchone() == (1,)


def test_every_test_gets_a_fresh_empty_database(empty_db_url):
    with psycopg.connect(empty_db_url) as conn:
        name = conn.execute("SELECT current_database()").fetchone()[0]
        tables = conn.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'"
        ).fetchone()[0]
        conn.execute("CREATE TABLE leftover (id int)")  # must not be visible to the next test
        conn.commit()

    assert name.startswith("t_")
    assert tables == 0
    assert name not in _seen_databases
    _seen_databases.add(name)


def test_second_fresh_database_is_empty_too(empty_db_url):
    with psycopg.connect(empty_db_url) as conn:
        assert conn.execute("SELECT to_regclass('public.leftover')").fetchone()[0] is None
        name = conn.execute("SELECT current_database()").fetchone()[0]

    assert name not in _seen_databases


def test_sqlalchemy_url_uses_the_psycopg_3_driver(empty_db_sqlalchemy_url):
    assert empty_db_sqlalchemy_url.startswith("postgresql+psycopg://")
    engine = create_engine(empty_db_sqlalchemy_url)
    try:
        with engine.connect() as conn:
            assert conn.execute(text("SELECT 1")).scalar() == 1
    finally:
        engine.dispose()
