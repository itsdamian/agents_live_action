"""Unit tests for :class:`agents.qa_agent.QAAgent`.

All tests run with a :class:`~core.llm_client.MockLLMClient` (or a small
subclass) so no real API keys are required. They cover the QA agent's contract
(Requirements 2.4, 6.3, 6.4):

    * ``process`` verifies the SWE implementation it receives as context and
      returns a report carrying an explicit PASS / FAIL verdict,
    * pass/fail is detectable via ``is_pass`` / ``is_fail`` and aligns with the
      mock QA renderer's output,
    * on failure ``process`` returns a *structured* failure report usable by
      SWE_agent on rework,
    * ``run`` (inherited) wraps the output in a successful AgentResult and
      records the interaction.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from agents.qa_agent import (
    DEFAULT_QA_SYSTEM_PROMPT,
    VERDICT_FAIL,
    QAAgent,
)
from core.llm_client import MOCK_MODE_MARKER, LLMClient, MockLLMClient
from models.agent import AgentConfig
from models.task import Priority, Status, Task


def _make_config(**overrides) -> AgentConfig:
    base = dict(
        name="QA_agent",
        role="Quality Assurance",
        description="Design and run tests, report pass/fail.",
        llm_provider="mock",
        llm_model="mock-model",
        api_key_env="OPENAI_API_KEY",
        max_retries=3,
        system_prompt="You are QA_agent.",
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


class FailingMockClient(RecordingMockClient):
    """Mock client that returns a failing QA report (mock QA always passes)."""

    def call(self, prompt: str, system_prompt: str | None = None) -> str:
        self.last_prompt = prompt
        self.last_system_prompt = system_prompt
        return (
            f"{MOCK_MODE_MARKER} QA test report\n"
            "Result: FAIL\n"
            "Tests run: 3, Passed: 2, Failed: 1\n"
            "Issue: login accepts an empty password (QA-ISSUE-42)."
        )


@pytest.fixture
def qa_agent() -> QAAgent:
    """QA agent whose mock client renders a PASS report (mock QA default)."""
    client = RecordingMockClient(role="Quality Assurance", name="QA_agent")
    return QAAgent(config=_make_config(), llm_client=client, retry_wait_multiplier=0)


@pytest.fixture
def failing_qa_agent() -> QAAgent:
    """QA agent whose mock client renders a FAIL report."""
    client = FailingMockClient(role="Quality Assurance", name="QA_agent")
    return QAAgent(config=_make_config(), llm_client=client, retry_wait_multiplier=0)


# ---------------------------------------------------------------------------
# process() output — PASS scenario (Requirement 2.4)
# ---------------------------------------------------------------------------


def test_process_returns_mock_marked_output(qa_agent):
    out = qa_agent.process(_make_task())
    assert out.startswith(MOCK_MODE_MARKER)


def test_process_pass_output_is_a_test_report(qa_agent):
    out = qa_agent.process(_make_task())
    assert "QA test report" in out
    assert QAAgent.is_pass(out) is True
    assert QAAgent.is_fail(out) is False


def test_process_embeds_task_details_in_prompt(qa_agent):
    task = _make_task(title="UNIQUE-TITLE-XYZ", description="UNIQUE-DESC-ABC")
    qa_agent.process(task)
    prompt = qa_agent.llm_client.last_prompt
    assert "UNIQUE-TITLE-XYZ" in prompt
    assert "UNIQUE-DESC-ABC" in prompt
    assert task.id in prompt
    assert task.priority.value in prompt


def test_process_includes_swe_output_to_verify(qa_agent):
    qa_agent.process(_make_task(), context={"swe_output": "SWE-CODE-BLOCK-123"})
    prompt = qa_agent.llm_client.last_prompt
    assert "Implementation to verify" in prompt
    assert "SWE-CODE-BLOCK-123" in prompt


def test_process_includes_pm_plan_as_acceptance_criteria(qa_agent):
    qa_agent.process(_make_task(), context={"pm_output": "PM-ACCEPTANCE-CRITERIA-9"})
    prompt = qa_agent.llm_client.last_prompt
    assert "PM plan / acceptance criteria" in prompt
    assert "PM-ACCEPTANCE-CRITERIA-9" in prompt


def test_process_includes_additional_context(qa_agent):
    qa_agent.process(_make_task(), context={"note": "CHECK-RATE-LIMITING"})
    prompt = qa_agent.llm_client.last_prompt
    assert "Additional context" in prompt
    assert "CHECK-RATE-LIMITING" in prompt


def test_process_skips_empty_context_values(qa_agent):
    qa_agent.process(_make_task(), context={"empty": "", "none": None})
    prompt = qa_agent.llm_client.last_prompt
    assert "Additional context" not in prompt


def test_process_uses_configured_system_prompt(qa_agent):
    qa_agent.process(_make_task())
    assert qa_agent.llm_client.last_system_prompt == "You are QA_agent."


def test_process_falls_back_to_default_system_prompt():
    client = RecordingMockClient(role="Quality Assurance", name="QA_agent")
    agent = QAAgent(
        config=_make_config(system_prompt=""),
        llm_client=client,
        retry_wait_multiplier=0,
    )
    agent.process(_make_task())
    assert client.last_system_prompt == DEFAULT_QA_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# process() output — FAIL scenario / structured failure report (Req 6.3, 6.4)
# ---------------------------------------------------------------------------


def test_process_fail_is_detected_as_failure(failing_qa_agent):
    out = failing_qa_agent.process(_make_task())
    assert QAAgent.is_fail(out) is True
    assert QAAgent.is_pass(out) is False


def test_process_fail_returns_structured_failure_report(failing_qa_agent):
    task = _make_task()
    out = failing_qa_agent.process(task)
    assert "QA FAILURE REPORT" in out
    assert f"Result: {VERDICT_FAIL}" in out
    assert task.id in out
    # Original failing details are preserved for SWE to act on.
    assert "QA-ISSUE-42" in out
    # There is explicit rework guidance aimed at SWE_agent.
    assert "SWE_agent" in out


def test_failure_report_preserves_mock_marker(failing_qa_agent):
    out = failing_qa_agent.process(_make_task())
    assert out.startswith(MOCK_MODE_MARKER)


def test_failure_report_is_usable_as_swe_rework_context(failing_qa_agent):
    """The QA failure output feeds SWE under its qa_output/qa_report keys."""
    from agents.swe_agent import SWEAgent

    report = failing_qa_agent.process(_make_task())
    swe_client = RecordingMockClient(role="Software Engineer", name="SWE_agent")
    swe = SWEAgent(config=_make_config(name="SWE_agent"), llm_client=swe_client)
    swe.process(_make_task(rework_count=1), context={"qa_output": report})
    prompt = swe_client.last_prompt
    assert "QA failure report" in prompt
    assert "QA-ISSUE-42" in prompt


# ---------------------------------------------------------------------------
# is_pass / is_fail verdict detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "report, expected_pass",
    [
        ("Result: PASS\nTests run: 3, Passed: 3, Failed: 0", True),
        ("result: pass", True),
        ("Verdict - PASS", True),
        ("Result: FAIL\nIssue: broke", False),
        ("result: fail", False),
        # "Failed: 0" must not flip a pass verdict to fail.
        ("Result: PASS\nFailed: 0", True),
        # Explicit result line wins even if body mentions the other word.
        ("Result: FAIL\nPreviously this would pass", False),
        # No explicit verdict line: lone PASS word => pass.
        ("All checks pass", True),
        # No explicit verdict line: lone FAIL word => fail.
        ("A check did fail here", False),
        # Ambiguous / empty => conservative fail.
        ("", False),
        (None, False),
        ("some unrelated text", False),
    ],
)
def test_is_pass_detection(report, expected_pass):
    assert QAAgent.is_pass(report) is expected_pass
    assert QAAgent.is_fail(report) is (not expected_pass)


def test_is_pass_aligns_with_mock_qa_renderer():
    """The default mock QA renderer must be classified as a pass."""
    client = MockLLMClient(role="Quality Assurance", name="QA_agent")
    report = client.call("verify task-001")
    assert QAAgent.is_pass(report) is True


# ---------------------------------------------------------------------------
# run() integration (inherited orchestration)
# ---------------------------------------------------------------------------


def test_run_pass_produces_successful_result(qa_agent, tmp_path):
    result = qa_agent.run(
        _make_task(), context={"swe_output": "code"}, project_path=tmp_path
    )
    assert result.success is True
    assert result.agent_name == "QA_agent"
    assert result.task_id == "task-001"
    assert result.output is not None
    assert QAAgent.is_pass(result.output) is True
    assert len(result.interactions) == 1


def test_run_fail_produces_successful_result_with_failure_report(
    failing_qa_agent, tmp_path
):
    """A FAIL verdict is a normal outcome: run() still succeeds (no exception)."""
    result = failing_qa_agent.run(
        _make_task(), context={"swe_output": "code"}, project_path=tmp_path
    )
    assert result.success is True
    assert result.output is not None
    assert QAAgent.is_fail(result.output) is True
    assert "QA FAILURE REPORT" in result.output


def test_run_writes_log_file(qa_agent, tmp_path):
    result = qa_agent.run(_make_task(), project_path=tmp_path)
    assert result.log_path is not None
    log_dir = tmp_path / "logs"
    assert log_dir.exists()
    assert any(log_dir.iterdir())


def test_qa_agent_is_llm_client_backed(qa_agent):
    assert isinstance(qa_agent.llm_client, LLMClient)
    assert qa_agent.llm_client.is_mock is True
