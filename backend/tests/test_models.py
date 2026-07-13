"""Unit tests for the Pydantic data models."""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from models.agent import AgentConfig
from models.project import Project
from models.task import Priority, Status, Task


# ---------------------------------------------------------------------------
# Task model
# ---------------------------------------------------------------------------


def _make_task(**overrides) -> Task:
    base = dict(
        id="task-001",
        title="Implement user authentication",
        description="Add JWT-based auth with refresh token support",
        priority="high",
        status="pending",
        created_at=datetime(2026, 6, 13, 10, 0, 0),
        updated_at=datetime(2026, 6, 13, 10, 0, 0),
    )
    base.update(overrides)
    return Task(**base)


def test_task_valid_construction():
    task = _make_task()
    assert task.id == "task-001"
    assert task.title == "Implement user authentication"
    assert task.priority == Priority.HIGH
    assert task.status == Status.PENDING
    assert isinstance(task.created_at, datetime)
    assert isinstance(task.updated_at, datetime)


def test_task_default_values():
    task = _make_task()
    assert task.rework_count == 0
    assert task.max_reworks == 3
    assert task.pm_output is None
    assert task.swe_output is None
    assert task.qa_output is None


def test_task_outputs_can_be_set():
    task = _make_task(
        pm_output="plan", swe_output="code", qa_output="report"
    )
    assert task.pm_output == "plan"
    assert task.swe_output == "code"
    assert task.qa_output == "report"


@pytest.mark.parametrize("priority", ["critical", "high", "medium", "low"])
def test_task_accepts_all_valid_priorities(priority):
    task = _make_task(priority=priority)
    assert task.priority == priority


@pytest.mark.parametrize(
    "status", ["pending", "in_progress", "done", "failed"]
)
def test_task_accepts_all_valid_statuses(status):
    task = _make_task(status=status)
    assert task.status == status


def test_task_invalid_priority_raises():
    with pytest.raises(ValidationError):
        _make_task(priority="urgent")


def test_task_invalid_status_raises():
    with pytest.raises(ValidationError):
        _make_task(status="blocked")


def test_task_missing_required_field_raises():
    with pytest.raises(ValidationError):
        Task(
            id="t1",
            title="x",
            description="y",
            priority="low",
            status="pending",
            # created_at / updated_at missing
        )


def test_task_parses_iso_datetime_strings():
    task = _make_task(
        created_at="2026-06-13T10:00:00", updated_at="2026-06-13T11:30:00"
    )
    assert task.created_at == datetime(2026, 6, 13, 10, 0, 0)
    assert task.updated_at == datetime(2026, 6, 13, 11, 30, 0)


# ---------------------------------------------------------------------------
# AgentConfig model
# ---------------------------------------------------------------------------


def _make_agent_config(**overrides) -> AgentConfig:
    base = dict(
        name="PM_agent",
        role="Project Manager",
        description="分析任務需求、拆解工作項目、協調執行順序",
        llm_provider="openai",
        llm_model="gpt-4o-mini",
        api_key_env="OPENAI_API_KEY",
        system_prompt="You are a senior project manager.",
    )
    base.update(overrides)
    return AgentConfig(**base)


def test_agent_config_valid_construction():
    cfg = _make_agent_config()
    assert cfg.name == "PM_agent"
    assert cfg.role == "Project Manager"
    assert cfg.llm_provider == "openai"
    assert cfg.llm_model == "gpt-4o-mini"
    assert cfg.api_key_env == "OPENAI_API_KEY"
    assert cfg.system_prompt == "You are a senior project manager."


def test_agent_config_default_max_retries():
    cfg = _make_agent_config()
    assert cfg.max_retries == 3


def test_agent_config_override_max_retries():
    cfg = _make_agent_config(max_retries=5)
    assert cfg.max_retries == 5


def test_agent_config_mock_provider():
    cfg = _make_agent_config(llm_provider="mock")
    assert cfg.llm_provider == "mock"


def test_agent_config_missing_required_field_raises():
    with pytest.raises(ValidationError):
        AgentConfig(name="PM_agent", role="Project Manager")


# ---------------------------------------------------------------------------
# Project model
# ---------------------------------------------------------------------------


def test_project_valid_construction():
    project = Project(
        id="my-project",
        name="My Project",
        path="/app/projects/my-project",
        agents=[],
        task_count=0,
        active_task_count=0,
        status="idle",
    )
    assert project.id == "my-project"
    assert project.name == "My Project"
    assert project.path == "/app/projects/my-project"
    assert project.agents == []
    assert project.status == "idle"


def test_project_contains_list_of_agent_configs():
    agents = [
        _make_agent_config(name="PM_agent", role="Project Manager"),
        _make_agent_config(
            name="SWE_agent",
            role="Software Engineer",
            api_key_env="ANTHROPIC_API_KEY",
            llm_provider="anthropic",
        ),
    ]
    project = Project(
        id="my-project",
        name="My Project",
        path="/app/projects/my-project",
        agents=agents,
        task_count=5,
        active_task_count=2,
        status="running",
    )
    assert len(project.agents) == 2
    assert all(isinstance(a, AgentConfig) for a in project.agents)
    assert project.agents[0].name == "PM_agent"
    assert project.agents[1].llm_provider == "anthropic"
    assert project.task_count == 5
    assert project.active_task_count == 2


def test_project_coerces_agent_dicts_to_agent_config():
    project = Project(
        id="p",
        name="P",
        path="/app/projects/p",
        agents=[
            {
                "name": "QA_agent",
                "role": "QA Engineer",
                "description": "tests",
                "llm_provider": "mock",
                "llm_model": "mock",
                "api_key_env": "NONE",
                "system_prompt": "test things",
            }
        ],
        task_count=1,
        active_task_count=0,
        status="idle",
    )
    assert isinstance(project.agents[0], AgentConfig)
    assert project.agents[0].name == "QA_agent"


def test_project_missing_required_field_raises():
    with pytest.raises(ValidationError):
        Project(id="p", name="P")
