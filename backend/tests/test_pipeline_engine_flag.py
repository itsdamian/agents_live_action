"""PIPELINE_ENGINE flag: legacy by default, langgraph on request (AC1, AC7)."""

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


def test_default_engine_is_legacy(tmp_path, monkeypatch):
    monkeypatch.delenv("PIPELINE_ENGINE", raising=False)

    assert config.get_pipeline_engine() == "legacy"
    assert _orchestrator_from_env(tmp_path).engine == "legacy"


def test_empty_value_means_legacy(monkeypatch):
    monkeypatch.setenv("PIPELINE_ENGINE", "  ")

    assert config.get_pipeline_engine() == "legacy"


def test_langgraph_engine_selected_by_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_ENGINE", "langgraph")

    assert _orchestrator_from_env(tmp_path).engine == "langgraph"


def test_value_is_trimmed_and_case_insensitive(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_ENGINE", "LangGraph ")

    assert config.get_pipeline_engine() == "langgraph"
    assert _orchestrator_from_env(tmp_path).engine == "langgraph"


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


def test_legacy_does_not_import_langgraph():
    """AC7: importing the pipeline with the default engine never loads langgraph."""

    env = {k: v for k, v in os.environ.items() if k != "PIPELINE_ENGINE"}
    out = subprocess.run(
        [sys.executable, "-c", "import core.pipeline, sys; print('langgraph' in sys.modules)"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    assert out.stdout.strip() == "False"
