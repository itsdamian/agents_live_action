"""Unit tests for the abstract BaseAgent.

Covers the three pieces of behaviour BaseAgent centralizes for every concrete
agent: the ``process`` contract, ``tenacity`` retry on the LLM call, per-task
log writing (Requirements 9.1, 9.2), and failure handling that marks the task
failed without crashing the pipeline (Requirement 6.5).

A tiny concrete subclass and a couple of fake LLM clients keep the tests
independent of the real PM/SWE/QA agents and of any network access.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from agents.base_agent import AgentResult, BaseAgent
from core.llm_client import LLMClient
from models.agent import AgentConfig
from models.task import Priority, Status, Task


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _EchoLLM(LLMClient):
    """LLM client that echoes the prompt back; records every call."""

    def __init__(self) -> None:
        super().__init__("echo-model")
        self.calls: list[tuple[str, str | None]] = []

    def call(self, prompt: str, system_prompt: str | None = None) -> str:
        self.calls.append((prompt, system_prompt))
        return f"echo: {prompt}"


class _FlakyLLM(LLMClient):
    """Fails ``fail_times`` times, then succeeds. Counts total attempts."""

    def __init__(self, fail_times: int) -> None:
        super().__init__("flaky-model")
        self.fail_times = fail_times
        self.attempts = 0

    def call(self, prompt: str, system_prompt: str | None = None) -> str:
        self.attempts += 1
        if self.attempts <= self.fail_times:
            raise RuntimeError(f"transient failure #{self.attempts}")
        return "recovered"


class _AlwaysFailLLM(LLMClient):
    """Always raises; counts attempts."""

    def __init__(self) -> None:
        super().__init__("broken-model")
        self.attempts = 0

    def call(self, prompt: str, system_prompt: str | None = None) -> str:
        self.attempts += 1
        raise RuntimeError("permanent failure")


class _EchoAgent(BaseAgent):
    """Minimal concrete agent that prompts the LLM with the task title."""

    def process(self, task: Task, context: dict | None = None) -> str:
        context = context or {}
        prompt = f"{task.title}|{context.get('upstream', '')}"
        return self.call_llm(prompt)


class _BoomAgent(BaseAgent):
    """Concrete agent whose process always raises before any LLM call."""

    def process(self, task: Task, context: dict | None = None) -> str:
        raise ValueError("boom in process")


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides) -> AgentConfig:
    base = dict(
        name="PM_agent",
        role="Project Manager",
        description="desc",
        llm_provider="mock",
        llm_model="test-model",
        api_key_env="OPENAI_API_KEY",
        max_retries=3,
        system_prompt="You are a PM.",
    )
    base.update(overrides)
    return AgentConfig(**base)


def _make_task(task_id: str = "task-001", status: Status = Status.IN_PROGRESS) -> Task:
    now = datetime.now()
    return Task(
        id=task_id,
        title="Build a login page",
        description="desc",
        priority=Priority.HIGH,
        status=status,
        created_at=now,
        updated_at=now,
    )


def _make_agent(llm: LLMClient, **config_overrides) -> _EchoAgent:
    return _EchoAgent(
        config=_make_config(**config_overrides),
        llm_client=llm,
        retry_wait_multiplier=0.0,
        retry_wait_max=0.0,
    )


# ---------------------------------------------------------------------------
# process() contract + successful run
# ---------------------------------------------------------------------------


def test_base_agent_is_abstract():
    with pytest.raises(TypeError):
        BaseAgent(_make_config())  # type: ignore[abstract]


def test_run_success_returns_output(tmp_path):
    agent = _make_agent(_EchoLLM())
    task = _make_task()
    result = agent.run(task, context={"upstream": "ctx"}, project_path=tmp_path)

    assert isinstance(result, AgentResult)
    assert result.success is True
    assert result.output == "echo: Build a login page|ctx"
    assert result.error is None
    assert result.agent_name == "PM_agent"
    assert result.task_id == "task-001"


def test_call_llm_uses_configured_system_prompt():
    llm = _EchoLLM()
    agent = _make_agent(llm)
    agent.run(_make_task(), project_path=None)
    # Even without a project_path the LLM should have been called once.
    assert len(llm.calls) == 1
    _, system_prompt = llm.calls[0]
    assert system_prompt == "You are a PM."


# ---------------------------------------------------------------------------
# Logging (Requirements 9.1, 9.2)
# ---------------------------------------------------------------------------


def test_log_file_created_with_expected_name(tmp_path):
    agent = _make_agent(_EchoLLM())
    task = _make_task(task_id="task-042")
    result = agent.run(task, project_path=tmp_path)

    logs_dir = tmp_path / "logs"
    assert logs_dir.is_dir()
    log_files = list(logs_dir.glob("*.log"))
    assert len(log_files) == 1

    name = log_files[0].name
    # Format: {timestamp}_{agent_name}_{task_id}.log
    assert name.endswith("_PM_agent_task-042.log")
    assert result.log_path == str(log_files[0])


def test_log_contains_prompt_output_and_time(tmp_path):
    agent = _make_agent(_EchoLLM())
    task = _make_task()
    agent.run(task, context={"upstream": "ctx"}, project_path=tmp_path)

    content = (tmp_path / "logs").glob("*.log").__next__().read_text(encoding="utf-8")
    assert "input prompt" in content
    assert "Build a login page|ctx" in content  # the prompt
    assert "output response" in content
    assert "echo: Build a login page|ctx" in content  # the response
    assert "execution_time_seconds:" in content
    assert "status: SUCCESS" in content


# ---------------------------------------------------------------------------
# Retry (design "LLM 呼叫失敗")
# ---------------------------------------------------------------------------


def test_retry_recovers_before_limit(tmp_path):
    llm = _FlakyLLM(fail_times=2)  # succeed on the 3rd attempt
    agent = _make_agent(llm, max_retries=3)
    result = agent.run(_make_task(), project_path=tmp_path)

    assert result.success is True
    assert result.output == "recovered"
    assert llm.attempts == 3


def test_retry_stops_at_max_retries(tmp_path):
    llm = _AlwaysFailLLM()
    agent = _make_agent(llm, max_retries=3)
    result = agent.run(_make_task(), project_path=tmp_path)

    assert result.success is False
    assert llm.attempts == 3  # exactly max_retries attempts, no more


def test_max_retries_zero_still_attempts_once(tmp_path):
    llm = _AlwaysFailLLM()
    agent = _make_agent(llm, max_retries=0)
    agent.run(_make_task(), project_path=tmp_path)
    assert llm.attempts == 1


# ---------------------------------------------------------------------------
# Failure handling (Requirement 6.5)
# ---------------------------------------------------------------------------


def test_process_exception_marks_task_failed_without_raising(tmp_path):
    agent = _BoomAgent(
        config=_make_config(),
        llm_client=_EchoLLM(),
        retry_wait_multiplier=0.0,
        retry_wait_max=0.0,
    )
    task = _make_task()
    # Must not raise.
    result = agent.run(task, project_path=tmp_path)

    assert result.success is False
    assert result.output is None
    assert "boom in process" in (result.error or "")
    assert task.status is Status.FAILED


def test_failure_still_writes_log_with_error(tmp_path):
    llm = _AlwaysFailLLM()
    agent = _make_agent(llm, max_retries=2)
    task = _make_task(task_id="task-err")
    agent.run(task, project_path=tmp_path)

    log_files = list((tmp_path / "logs").glob("*_task-err.log"))
    assert len(log_files) == 1
    content = log_files[0].read_text(encoding="utf-8")
    assert "status: FAILED" in content
    assert "permanent failure" in content
