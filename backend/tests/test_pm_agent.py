"""Unit tests for :class:`agents.pm_agent.PMAgent`.

All tests run with a :class:`~core.llm_client.MockLLMClient` so no real API keys
are required. They focus on the PM agent's contract (Requirements 2.3, 6.2):

    * ``process`` returns a task summary + execution plan usable as downstream
      context,
    * the task and any upstream context are embedded in the LLM prompt,
    * ``run`` (inherited) wraps the output in a successful AgentResult and
      records the interaction.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from agents.pm_agent import DEFAULT_PM_SYSTEM_PROMPT, PMAgent
from core.llm_client import MOCK_MODE_MARKER, LLMClient, MockLLMClient
from models.agent import AgentConfig
from models.task import Priority, Status, Task


def _make_config(**overrides) -> AgentConfig:
    base = dict(
        name="PM_agent",
        role="Project Manager",
        description="Analyse tasks and produce an execution plan.",
        llm_provider="mock",
        llm_model="mock-model",
        api_key_env="OPENAI_API_KEY",
        max_retries=3,
        system_prompt="You are PM_agent.",
    )
    base.update(overrides)
    return AgentConfig(**base)


def _make_task(**overrides) -> Task:
    now = datetime(2026, 1, 1, 12, 0, 0)
    base = dict(
        id="task-001",
        title="Add login page",
        description="Users should be able to log in with email and password.",
        priority=Priority.HIGH,
        status=Status.PENDING,
        created_at=now,
        updated_at=now,
    )
    base.update(overrides)
    return Task(**base)


class RecordingMockClient(MockLLMClient):
    """MockLLMClient that records the last prompt/system_prompt it received."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.last_prompt: str | None = None
        self.last_system_prompt: str | None = None

    def call(self, prompt: str, system_prompt: str | None = None) -> str:
        self.last_prompt = prompt
        self.last_system_prompt = system_prompt
        return super().call(prompt, system_prompt)


@pytest.fixture
def pm_agent() -> PMAgent:
    client = RecordingMockClient(role="Project Manager", name="PM_agent")
    return PMAgent(config=_make_config(), llm_client=client, retry_wait_multiplier=0)


# ---------------------------------------------------------------------------
# process() output (Requirement 2.3)
# ---------------------------------------------------------------------------


def test_process_returns_mock_marked_output(pm_agent):
    out = pm_agent.process(_make_task())
    assert out.startswith(MOCK_MODE_MARKER)


def test_process_output_includes_summary_and_execution_plan(pm_agent):
    """Output must carry a task analysis + execution plan for downstream agents."""
    out = pm_agent.process(_make_task())
    assert "PM task analysis" in out
    assert "Execution plan" in out


def test_process_embeds_task_details_in_prompt(pm_agent):
    task = _make_task(title="UNIQUE-TITLE-XYZ", description="UNIQUE-DESC-ABC")
    pm_agent.process(task)
    prompt = pm_agent.llm_client.last_prompt
    assert "UNIQUE-TITLE-XYZ" in prompt
    assert "UNIQUE-DESC-ABC" in prompt
    assert task.id in prompt
    assert task.priority.value in prompt


def test_process_uses_configured_system_prompt(pm_agent):
    pm_agent.process(_make_task())
    assert pm_agent.llm_client.last_system_prompt == "You are PM_agent."


def test_process_falls_back_to_default_system_prompt():
    client = RecordingMockClient(role="Project Manager", name="PM_agent")
    agent = PMAgent(
        config=_make_config(system_prompt=""),
        llm_client=client,
        retry_wait_multiplier=0,
    )
    agent.process(_make_task())
    assert client.last_system_prompt == DEFAULT_PM_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# context handling (Requirement 6.2)
# ---------------------------------------------------------------------------


def test_process_without_context_omits_additional_context_section(pm_agent):
    pm_agent.process(_make_task())
    assert "Additional context" not in pm_agent.llm_client.last_prompt


def test_process_includes_provided_context(pm_agent):
    pm_agent.process(_make_task(), context={"note": "REPLAN-BECAUSE-SCOPE-CHANGE"})
    prompt = pm_agent.llm_client.last_prompt
    assert "Additional context" in prompt
    assert "REPLAN-BECAUSE-SCOPE-CHANGE" in prompt


def test_process_skips_empty_context_values(pm_agent):
    pm_agent.process(_make_task(), context={"empty": "", "none": None})
    assert "Additional context" not in pm_agent.llm_client.last_prompt


# ---------------------------------------------------------------------------
# run() integration (inherited orchestration)
# ---------------------------------------------------------------------------


def test_run_produces_successful_result(pm_agent, tmp_path):
    result = pm_agent.run(_make_task(), project_path=tmp_path)
    assert result.success is True
    assert result.agent_name == "PM_agent"
    assert result.task_id == "task-001"
    assert result.output is not None
    assert "Execution plan" in result.output
    assert len(result.interactions) == 1


def test_run_writes_log_file(pm_agent, tmp_path):
    result = pm_agent.run(_make_task(), project_path=tmp_path)
    assert result.log_path is not None
    log_dir = tmp_path / "logs"
    assert log_dir.exists()
    assert any(log_dir.iterdir())


def test_pm_agent_is_llm_client_backed(pm_agent):
    assert isinstance(pm_agent.llm_client, LLMClient)
    assert pm_agent.llm_client.is_mock is True
