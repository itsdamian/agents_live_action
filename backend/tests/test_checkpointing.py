"""Checkpointer wiring (Task 13, plan D7, AC23) without any database.

The Postgres side (``setup()``, the real table names, idempotency) is in
``tests/integration/test_checkpointing_pg.py``.
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from core.checkpointing import conninfo_from_database_url
from core.runtime import FactoryRuntime
from core.stores.memory import InMemoryRunRepository, InMemoryTaskRepository
from models.task import Status
from tests.hud_harness import make_orchestrator_with_repo
from tests.pipeline_harness import make_project, make_task

BACKEND_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _mock_only(monkeypatch):
    monkeypatch.setenv("FORCE_MOCK", "true")
    monkeypatch.delenv("TASK_STORE", raising=False)
    monkeypatch.delenv("PIPELINE_CHECKPOINTER", raising=False)


# -- DATABASE_URL -> psycopg conninfo ---------------------------------------------------------


def test_the_sqlalchemy_driver_suffix_is_dropped_for_psycopg():
    assert (
        conninfo_from_database_url("postgresql+psycopg://factory:factory@postgres:5432/factory")
        == "postgresql://factory:factory@postgres:5432/factory"
    )


def test_a_plain_postgresql_url_is_kept_and_an_escaped_password_survives():
    assert conninfo_from_database_url("postgresql://u:p@h/db") == "postgresql://u:p@h/db"
    assert (
        conninfo_from_database_url("postgresql+psycopg://u:p%40ss%2Fw@h:1/db")
        == "postgresql://u:p%40ss%2Fw@h:1/db"
    )


# -- injection into the graph runner ---------------------------------------------------------


class CountingSaver(InMemorySaver):
    def __init__(self) -> None:
        super().__init__()
        self.puts = 0

    async def aput(self, config, checkpoint, metadata, new_versions):
        self.puts += 1
        return await super().aput(config, checkpoint, metadata, new_versions)


def test_an_injected_checkpointer_is_the_one_the_graph_writes_to(tmp_path):
    task = make_task()
    project = make_project(tmp_path, [task])
    saver = CountingSaver()
    orch, _ = make_orchestrator_with_repo(project, engine="langgraph")

    orch.use_checkpointer(saver)
    result = asyncio.run(orch.process_task(task))

    assert result.status is Status.DONE
    assert orch._graph_runner._checkpointer is saver
    assert saver.puts > 0


def test_without_injection_each_runner_keeps_its_own_in_memory_saver(tmp_path):
    project = make_project(tmp_path, [make_task()])
    one, _ = make_orchestrator_with_repo(project, engine="langgraph")
    two, _ = make_orchestrator_with_repo(project, engine="langgraph")

    assert isinstance(one._graph_runner._checkpointer, InMemorySaver)
    assert one._graph_runner._checkpointer is not two._graph_runner._checkpointer


def test_the_legacy_engine_refuses_a_checkpointer(tmp_path):
    project = make_project(tmp_path, [make_task()])
    orch, _ = make_orchestrator_with_repo(project, engine="legacy")

    with pytest.raises(ValueError, match="checkpointer"):
        orch.use_checkpointer(InMemorySaver())


def test_switching_checkpointers_mid_task_is_refused(tmp_path):
    task = make_task()
    project = make_project(tmp_path, [task])
    orch, _ = make_orchestrator_with_repo(project, engine="langgraph")
    orch._graph_runner._live_tasks[task.id] = task  # as during run()

    with pytest.raises(RuntimeError, match="running"):
        orch.use_checkpointer(InMemorySaver())


@pytest.fixture
def graph_engine(monkeypatch):
    """The runtime tests below are about the graph engine, whatever PIPELINE_ENGINE the suite uses."""

    monkeypatch.setenv("PIPELINE_ENGINE", "langgraph")


def test_the_runtime_hands_an_injected_checkpointer_to_every_project(tmp_path, graph_engine):
    for name in ("alpha", "beta"):
        (tmp_path / name).mkdir()
    saver = InMemorySaver()

    async def scenario():
        runtime = FactoryRuntime(
            projects_dir=tmp_path, task_store="db", task_repo=InMemoryTaskRepository(),
            run_repo=InMemoryRunRepository(), checkpointer=saver,
        )
        await runtime.startup()
        try:
            return {pid: o._graph_runner._checkpointer for pid, o in runtime.orchestrators.items()}
        finally:
            await runtime.shutdown()

    assert asyncio.run(scenario()) == {"alpha": saver, "beta": saver}


@pytest.mark.parametrize("store", ["file", "db"])
def test_without_postgres_the_runtime_opens_no_shared_checkpointer(tmp_path, graph_engine, store):
    (tmp_path / "alpha").mkdir()

    async def scenario():
        runtime = FactoryRuntime(projects_dir=tmp_path, task_store=store, task_repo=InMemoryTaskRepository(),
                                 run_repo=InMemoryRunRepository())
        await runtime.startup()
        try:
            return runtime.checkpointer, runtime.orchestrators["alpha"]._graph_runner._checkpointer
        finally:
            await runtime.shutdown()

    shared, per_runner = asyncio.run(scenario())
    assert shared is None and isinstance(per_runner, InMemorySaver)


def test_a_legacy_runtime_never_hands_a_checkpointer_to_its_orchestrators(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_ENGINE", "legacy")
    (tmp_path / "alpha").mkdir()

    async def scenario():
        runtime = FactoryRuntime(
            projects_dir=tmp_path, task_store="db", task_repo=InMemoryTaskRepository(),
            run_repo=InMemoryRunRepository(), checkpointer=InMemorySaver(),
        )
        await runtime.startup()
        try:
            return runtime.orchestrators["alpha"].engine, runtime._init_failed
        finally:
            await runtime.shutdown()

    assert asyncio.run(scenario()) == ("legacy", set())


# -- AC23: where langgraph may be imported ----------------------------------------------------

_LANGGRAPH_IMPORT = re.compile(r"^\s*(from|import) langgraph", re.MULTILINE)


def test_only_the_graph_and_checkpointing_modules_import_langgraph():
    found = set()
    for path in BACKEND_DIR.rglob("*.py"):
        rel = path.relative_to(BACKEND_DIR)
        if rel.parts[0] in {"tests", ".venv"}:
            continue
        if _LANGGRAPH_IMPORT.search(path.read_text(encoding="utf-8")):
            found.add(rel.as_posix())

    assert found == {"core/pipeline_graph.py", "core/checkpointing.py"}


def test_with_the_legacy_engine_importing_the_runtime_does_not_load_langgraph():
    code = "import sys; import core.pipeline, core.runtime; print('langgraph' in sys.modules)"
    env = {**os.environ, "PIPELINE_ENGINE": "legacy"}
    out = subprocess.run([sys.executable, "-c", code], cwd=BACKEND_DIR, env=env,
                         capture_output=True, text=True, check=True)

    assert out.stdout.strip() == "False"
