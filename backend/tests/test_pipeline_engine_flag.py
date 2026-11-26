"""PIPELINE_ENGINE flag: langgraph by default, legacy on request (AC1, AC7)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import config
from core.pipeline import PipelineOrchestrator
from tests.pipeline_harness import make_orchestrator, make_project, make_task

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _orchestrator_from_env(tmp_path):
    """Build an orchestrator without passing ``engine`` (so the env decides)."""

    project = make_project(tmp_path, [make_task()])
    # make_orchestrator always passes engine=...; build directly instead.
    orch, _ = make_orchestrator(project)
    return PipelineOrchestrator(
        project_id=project.name,
        project_path=project,
        pm_agent=orch._agents["pm"],
        swe_agent=orch._agents["swe"],
        qa_agent=orch._agents["qa"],
    )


def test_default_engine_is_langgraph(tmp_path, monkeypatch):
    monkeypatch.delenv("PIPELINE_ENGINE", raising=False)

    assert config.get_pipeline_engine() == "langgraph"
    orch = _orchestrator_from_env(tmp_path)
    assert orch.engine == "langgraph"
    assert orch._graph_runner is not None


def test_empty_value_means_default(monkeypatch):
    monkeypatch.setenv("PIPELINE_ENGINE", "  ")

    assert config.get_pipeline_engine() == "langgraph"


def test_legacy_engine_selected_by_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_ENGINE", "legacy")

    orch = _orchestrator_from_env(tmp_path)

    assert orch.engine == "legacy"
    assert not hasattr(orch, "_graph_runner")


def test_value_is_trimmed_and_case_insensitive(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_ENGINE", " LEGACY ")

    assert config.get_pipeline_engine() == "legacy"
    assert _orchestrator_from_env(tmp_path).engine == "legacy"

    monkeypatch.setenv("PIPELINE_ENGINE", "LangGraph ")
    assert config.get_pipeline_engine() == "langgraph"


def test_unknown_engine_raises_with_valid_values(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_ENGINE", "bogus")

    with pytest.raises(ValueError, match="legacy, langgraph"):
        config.get_pipeline_engine()
    with pytest.raises(ValueError, match="legacy, langgraph"):
        _orchestrator_from_env(tmp_path)


def test_explicit_engine_argument_overrides_env_and_is_validated(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_ENGINE", "langgraph")
    project = make_project(tmp_path, [make_task()])

    orch, _ = make_orchestrator(project, engine="legacy")
    assert orch.engine == "legacy"

    with pytest.raises(ValueError, match="legacy, langgraph"):
        make_orchestrator(tmp_path / "other-unused", engine="bogus")


def _loaded_langgraph_after_building_orchestrator(engine_env: str | None) -> str:
    """Build an orchestrator in a fresh interpreter; report 'langgraph' in sys.modules."""

    env = {k: v for k, v in os.environ.items() if k != "PIPELINE_ENGINE"}
    if engine_env is not None:
        env["PIPELINE_ENGINE"] = engine_env
    code = (
        "import sys; from core.pipeline import PipelineOrchestrator; "
        "PipelineOrchestrator('p', '.', None, None, None); "
        "print('langgraph' in sys.modules)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def test_legacy_engine_does_not_import_langgraph():
    """AC7: with PIPELINE_ENGINE=legacy, langgraph is never imported."""

    assert _loaded_langgraph_after_building_orchestrator("legacy") == "False"


def test_langgraph_is_imported_by_default_and_when_selected():
    assert _loaded_langgraph_after_building_orchestrator(None) == "True"
    assert _loaded_langgraph_after_building_orchestrator("langgraph") == "True"


def test_importing_the_pipeline_module_alone_never_loads_langgraph():
    """The import is lazy: it happens when an orchestrator picks the langgraph engine."""

    env = {k: v for k, v in os.environ.items() if k != "PIPELINE_ENGINE"}
    out = subprocess.run(
        [sys.executable, "-c", "import core.pipeline, sys; print('langgraph' in sys.modules)"],
        cwd=BACKEND_DIR, env=env, capture_output=True, text=True, check=True,
    )

    assert out.stdout.strip() == "False"
