"""Models, Alembic configuration and the migration, checked without a database (Task 7).

The same migration is executed against a real PostgreSQL in tests/integration/test_migrations.py.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Index, Table

from db.migration_filters import CHECKPOINTER_TABLES, include_object
from db.models import Base
from db.session import create_engine_for, session_factory

BACKEND = Path(__file__).resolve().parent.parent
TABLES = {"projects", "tasks", "task_runs", "task_run_steps", "usage_records", "agents", "pipelines"}
FAKE_URL = "postgresql+psycopg://user:pw@db.invalid:5432/factory"


def _config(url: str = FAKE_URL) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "db" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.attributes["configure_logger"] = False
    return cfg


def test_the_models_define_exactly_the_phase_1a_tables():
    assert set(Base.metadata.tables) == TABLES


def test_primary_keys_follow_the_plan():
    keys = {name: [c.name for c in t.primary_key.columns] for name, t in Base.metadata.tables.items()}

    assert keys["projects"] == ["id"]
    assert keys["tasks"] == ["project_id", "id"]
    assert keys["agents"] == ["project_id", "id"]
    assert keys["pipelines"] == ["project_id", "id"]
    assert keys["task_runs"] == ["id"] and keys["task_run_steps"] == ["id"] and keys["usage_records"] == ["id"]


def test_no_agent_api_key_column_and_money_is_numeric():
    agents = Base.metadata.tables["agents"]
    assert not any("key" in c.name for c in agents.columns)  # api_key_env is never stored (D21)
    for table, column in (("usage_records", "cost"), ("task_runs", "cost_usd")):
        col = Base.metadata.tables[table].c[column]
        assert (col.type.precision, col.type.scale) == (12, 6)


def test_task_run_steps_reference_their_run_and_cascade():
    (fk,) = Base.metadata.tables["task_run_steps"].foreign_keys

    assert fk.target_fullname == "task_runs.id" and fk.ondelete == "CASCADE"
    # No other table has foreign keys: project rows are never created implicitly.
    others = [n for n, t in Base.metadata.tables.items() if n != "task_run_steps" and t.foreign_keys]
    assert others == []


def test_constraint_names_are_stable():
    tasks = Base.metadata.tables["tasks"]
    names = {c.name for c in tasks.constraints}

    assert {"pk_tasks", "ck_tasks_status_valid", "ck_tasks_priority_valid",
            "ck_tasks_approval_valid", "ck_tasks_source_valid"} <= names


def test_pipelines_allow_one_default_per_project():
    index = next(i for i in Base.metadata.tables["pipelines"].indexes if isinstance(i, Index))

    assert index.unique and [c.name for c in index.columns] == ["project_id"]
    assert str(index.dialect_options["postgresql"]["where"]) == "is_default"


# -- Alembic ----------------------------------------------------------------------------------


def test_alembic_ini_is_at_the_backend_root_where_start_sh_looks_for_it():
    assert (BACKEND / "alembic.ini").is_file()
    assert "script_location = db/migrations" in (BACKEND / "alembic.ini").read_text(encoding="utf-8")


def test_there_is_a_single_linear_head():
    script = ScriptDirectory.from_config(_config())

    assert script.get_heads() == ["0001"]
    assert script.get_revision("0001").down_revision is None


def test_offline_upgrade_emits_every_table_without_a_database():
    out = io.StringIO()
    cfg = _config()
    cfg.output_buffer = out

    command.upgrade(cfg, "head", sql=True)

    sql = out.getvalue()
    for table in TABLES:
        assert re.search(rf"CREATE TABLE {table} \(", sql), table
    assert "CREATE TABLE alembic_version" in sql
    assert "checkpoint" not in sql  # LangGraph's tables are not Alembic's business


def test_offline_downgrade_drops_every_table():
    out = io.StringIO()
    cfg = _config()
    cfg.output_buffer = out

    command.downgrade(cfg, "0001:base", sql=True)

    sql = out.getvalue()
    for table in TABLES:
        assert f"DROP TABLE {table};" in sql


def test_env_falls_back_to_database_url_from_the_environment(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", FAKE_URL)
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "db" / "migrations"))
    cfg.attributes["configure_logger"] = False
    out = io.StringIO()
    cfg.output_buffer = out

    command.upgrade(cfg, "head", sql=True)  # no sqlalchemy.url set: env.py reads DATABASE_URL

    assert "CREATE TABLE tasks" in out.getvalue()


def test_env_without_any_url_fails_with_the_database_url_message(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "db" / "migrations"))
    cfg.attributes["configure_logger"] = False
    cfg.output_buffer = io.StringIO()

    with pytest.raises(ValueError, match="DATABASE_URL is not set"):
        command.upgrade(cfg, "head", sql=True)


# -- the filter that keeps Alembic away from LangGraph's tables -----------------------------------------


@pytest.mark.parametrize("name", sorted(CHECKPOINTER_TABLES))
def test_checkpointer_tables_and_their_indexes_are_excluded(name):
    from sqlalchemy import Column, MetaData, Text

    table = Table(name, MetaData(), Column("x", Text))  # a stand-in, not one of our models
    fake_index = type("FakeIndex", (), {"table": table})()

    assert include_object(table, name, "table", True, None) is False
    assert include_object(fake_index, f"ix_{name}", "index", True, None) is False


def test_our_own_tables_are_included():
    for name, table in Base.metadata.tables.items():
        assert include_object(table, name, "table", False, None) is True


# -- session helpers ---------------------------------------------------------------------------------------


def test_session_helpers_build_without_connecting():
    pytest.importorskip("greenlet", reason="SQLAlchemy's asyncio extension needs greenlet")
    from sqlalchemy.ext.asyncio import AsyncEngine
    from sqlalchemy.pool import NullPool

    engine = create_engine_for(FAKE_URL, null_pool=True)

    assert isinstance(engine, AsyncEngine) and isinstance(engine.pool, NullPool)
    factory = session_factory(engine)
    assert factory.kw["expire_on_commit"] is False


def test_importing_the_session_module_does_not_need_greenlet():
    import subprocess, sys

    code = ("import sys; sys.modules['greenlet'] = None; import db.session; "
            "print('sqlalchemy.ext.asyncio' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, capture_output=True, text=True)

    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "False"
