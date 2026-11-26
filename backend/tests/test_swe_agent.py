"""Unit tests for :class:`agents.swe_agent.SWEAgent`.

All tests run with a :class:`~core.llm_client.MockLLMClient` so no real API keys
are required. They focus on the SWE agent's contract (Requirements 2.1, 6.2,
6.3):

    * ``process`` returns code (usable as downstream QA context),
    * the task and the PM plan (upstream context) are embedded in the prompt,
    * on rework the QA failure report is folded into the prompt and the prompt
      is framed as a rework,
    * ``run`` (inherited) wraps the output in a successful AgentResult and
      records the interaction.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from agents.swe_agent import DEFAULT_SWE_SYSTEM_PROMPT, SWEAgent
from core.llm_client import MOCK_MODE_MARKER, LLMClient, MockLLMClient
from models.agent import AgentConfig
from models.task import Priority, Status, Task


def _make_config(**overrides) -> AgentConfig:
    base = dict(
        name="SWE_agent",
        role="Software Engineer",
        description="Write code according to the PM plan.",
        llm_provider="mock",
        llm_model="mock-model",
        api_key_env="OPENAI_API_KEY",
        max_retries=3,
        system_prompt="You are SWE_agent.",
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
def swe_agent() -> SWEAgent:
    client = RecordingMockClient(role="Software Engineer", name="SWE_agent")
    return SWEAgent(config=_make_config(), llm_client=client, retry_wait_multiplier=0)


# ---------------------------------------------------------------------------
# process() output (Requirement 2.1)
# ---------------------------------------------------------------------------


def test_process_returns_mock_marked_output(swe_agent):
    out = swe_agent.process(_make_task())
    assert out.startswith(MOCK_MODE_MARKER)


def test_process_output_contains_code(swe_agent):
    """SWE output must carry an implementation (a code block) for QA."""
    out = swe_agent.process(_make_task())
    assert "SWE implementation" in out
    assert "```python" in out


def test_process_embeds_task_details_in_prompt(swe_agent):
    task = _make_task(title="UNIQUE-TITLE-XYZ", description="UNIQUE-DESC-ABC")
    swe_agent.process(task)
    prompt = swe_agent.llm_client.last_prompt
    assert "UNIQUE-TITLE-XYZ" in prompt
    assert "UNIQUE-DESC-ABC" in prompt
    assert task.id in prompt
    assert task.priority.value in prompt


def test_process_uses_configured_system_prompt(swe_agent):
    swe_agent.process(_make_task())
    assert swe_agent.llm_client.last_system_prompt == "You are SWE_agent."


def test_process_falls_back_to_default_system_prompt():
    client = RecordingMockClient(role="Software Engineer", name="SWE_agent")
    agent = SWEAgent(
        config=_make_config(system_prompt=""),
        llm_client=client,
        retry_wait_multiplier=0,
    )
    agent.process(_make_task())
    assert client.last_system_prompt == DEFAULT_SWE_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# PM plan context handling (Requirement 6.2)
# ---------------------------------------------------------------------------


def test_process_includes_pm_plan(swe_agent):
    swe_agent.process(
        _make_task(), context={"pm_output": "PM-PLAN-STEP-ONE-THEN-TWO"}
    )
    prompt = swe_agent.llm_client.last_prompt
    assert "PM execution plan" in prompt
    assert "PM-PLAN-STEP-ONE-THEN-TWO" in prompt


def test_process_without_pm_plan_omits_plan_section(swe_agent):
    swe_agent.process(_make_task())
    assert "PM execution plan" not in swe_agent.llm_client.last_prompt


def test_process_frames_first_pass_as_implementation(swe_agent):
    swe_agent.process(_make_task(), context={"pm_output": "plan"})
    prompt = swe_agent.llm_client.last_prompt
    assert "Implement the following task" in prompt
    assert "REWORK" not in prompt


def test_process_includes_additional_context(swe_agent):
    swe_agent.process(_make_task(), context={"note": "USE-EXISTING-AUTH-MODULE"})
    prompt = swe_agent.llm_client.last_prompt
    assert "Additional context" in prompt
    assert "USE-EXISTING-AUTH-MODULE" in prompt


def test_process_skips_empty_context_values(swe_agent):
    swe_agent.process(_make_task(), context={"empty": "", "none": None})
    prompt = swe_agent.llm_client.last_prompt
    assert "Additional context" not in prompt


# ---------------------------------------------------------------------------
# rework scenario: QA failure report as supplementary context (Requirement 6.3)
# ---------------------------------------------------------------------------


def test_rework_includes_qa_failure_report(swe_agent):
    context = {
        "pm_output": "plan",
        "qa_output": "FAIL: login accepts empty password (QA-ISSUE-42)",
    }
    swe_agent.process(_make_task(rework_count=1), context=context)
    prompt = swe_agent.llm_client.last_prompt
    assert "QA failure report" in prompt
    assert "QA-ISSUE-42" in prompt


def test_rework_prompt_is_framed_as_rework(swe_agent):
    context = {"qa_output": "FAIL: something broke"}
    swe_agent.process(_make_task(rework_count=1), context=context)
    prompt = swe_agent.llm_client.last_prompt
    assert "REWORK" in prompt
    assert "Rework attempt: 2" in prompt


def test_rework_detected_from_rework_count_without_report(swe_agent):
    swe_agent.process(_make_task(rework_count=2), context={"pm_output": "plan"})
    prompt = swe_agent.llm_client.last_prompt
    assert "REWORK" in prompt
    assert "Rework attempt: 3" in prompt


def test_rework_accepts_alias_report_keys(swe_agent):
    for key in ("qa_report", "qa_failure_report"):
        context = {key: f"FAIL-VIA-{key.upper()}"}
        swe_agent.process(_make_task(rework_count=1), context=context)
        prompt = swe_agent.llm_client.last_prompt
        assert "QA failure report" in prompt
        assert f"FAIL-VIA-{key.upper()}" in prompt


def test_rework_report_not_duplicated_in_additional_context(swe_agent):
    context = {"qa_output": "FAIL-REPORT-ONCE"}
    swe_agent.process(_make_task(rework_count=1), context=context)
    prompt = swe_agent.llm_client.last_prompt
    # Appears in the QA failure report section but not repeated as "additional".
    assert "Additional context" not in prompt
    assert prompt.count("FAIL-REPORT-ONCE") == 1


# ---------------------------------------------------------------------------
# run() integration (inherited orchestration)
# ---------------------------------------------------------------------------


def test_run_produces_successful_result(swe_agent, tmp_path):
    result = swe_agent.run(
        _make_task(), context={"pm_output": "plan"}, project_path=tmp_path
    )
    assert result.success is True
    assert result.agent_name == "SWE_agent"
    assert result.task_id == "task-001"
    assert result.output is not None
    assert "SWE implementation" in result.output
    assert len(result.interactions) == 1


def test_run_rework_produces_successful_result(swe_agent, tmp_path):
    context = {"pm_output": "plan", "qa_output": "FAIL: fix it"}
    result = swe_agent.run(
        _make_task(rework_count=1), context=context, project_path=tmp_path
    )
    assert result.success is True
    assert result.output is not None
    # The rework prompt (with the QA report) was the one sent to the LLM.
    assert "QA failure report" in result.interactions[0][0]


def test_run_writes_log_file(swe_agent, tmp_path):
    result = swe_agent.run(_make_task(), project_path=tmp_path)
    assert result.log_path is not None
    log_dir = tmp_path / "logs"
    assert log_dir.exists()
    assert any(log_dir.iterdir())


def test_swe_agent_is_llm_client_backed(swe_agent):
    assert isinstance(swe_agent.llm_client, LLMClient)
    assert swe_agent.llm_client.is_mock is True
