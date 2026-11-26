"""Characterization of BaseAgent.run before it is changed again (Task 4).

Expected values were printed from the current code, checked line by line, and
then written down. They pin: the AgentResult fields on success and failure, the
task status, the log file name and header, the retry counts (including the
``retryable`` attribute rule from the Anthropic work) and per-attempt usage
accumulation. ``llm_client`` behaviour is covered by the anthropic characterization
tests and is not repeated here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from agents.base_agent import AgentResult, BaseAgent
from core.llm_client import LLMClient, LLMUsage
from models.agent import AgentConfig
from models.task import Status
from tests.pipeline_harness import make_task


class StubClient(LLMClient):
    """Plays ``steps`` in order (the last one repeats): str = answer, Exception = raise.

    A step may be ``(LLMUsage, outcome)``; the usage is set before the outcome, like a
    provider that bills the call.
    """

    provider = "stub"

    def __init__(self, steps):
        super().__init__("stub-model")
        self.steps = list(steps)
        self.calls = 0
        self.seen: list[tuple[str, str | None]] = []

    def call(self, prompt, system_prompt=None):
        self.seen.append((prompt, system_prompt))
        step = self.steps[min(self.calls, len(self.steps) - 1)]
        self.calls += 1
        usage = None
        if isinstance(step, tuple):
            usage, step = step
        if usage is not None:
            self.last_usage = usage
        if isinstance(step, Exception):
            raise step
        return step


class EchoAgent(BaseAgent):
    """Smallest concrete agent: one LLM call whose prompt names the task and context."""

    system_prompt_override: str | None = None

    def process(self, task, context=None):
        return self.call_llm(
            f"do {task.id} ctx={context}", system_prompt=self.system_prompt_override
        )


class ProcessRaises(BaseAgent):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.process_calls = 0

    def process(self, task, context=None):
        self.process_calls += 1
        raise ValueError("process blew up")


def _config(max_retries=3) -> AgentConfig:
    return AgentConfig(
        name="Echo_agent", role="Tester", description="d", llm_provider="mock",
        llm_model="m-1", api_key_env="X", max_retries=max_retries, system_prompt="SYS",
    )


def _agent(steps, max_retries=3, cls=EchoAgent, **kwargs):
    client = StubClient(steps)
    agent = cls(_config(max_retries), llm_client=client, retry_wait_multiplier=0, **kwargs)
    return agent, client


def _only_log(project: Path) -> Path:
    (path,) = list((project / "logs").iterdir())
    return path


# -- success ----------------------------------------------------------------------------


def test_success_result_fields(tmp_path):
    agent, client = _agent([(LLMUsage(10, 4), "answer")])
    task = make_task()

    result = agent.run(task, {"k": "v"}, tmp_path)

    assert isinstance(result, AgentResult)
    assert (result.agent_name, result.task_id, result.success) == ("Echo_agent", "task-001", True)
    assert (result.output, result.error) == ("answer", None)
    assert result.execution_time >= 0
    assert result.interactions == [("do task-001 ctx={'k': 'v'}", "answer")]
    assert (result.provider, result.llm_model) == ("stub", "m-1")  # provider: client; model: config
    assert (result.input_tokens, result.output_tokens) == (10, 4)
    assert Path(result.log_path) == _only_log(tmp_path)
    assert client.calls == 1


def test_success_leaves_the_task_status_alone(tmp_path):
    agent, _ = _agent(["answer"])
    task = make_task()

    agent.run(task, None, tmp_path)

    assert task.status is Status.PENDING  # marking done is the pipeline's job


def test_default_system_prompt_comes_from_the_config(tmp_path):
    agent, client = _agent(["answer"])

    agent.run(make_task(), None, tmp_path)

    assert client.seen == [("do task-001 ctx=None", "SYS")]


@pytest.mark.parametrize("override", ["OTHER", ""])
def test_explicit_system_prompt_overrides_the_config_even_when_empty(tmp_path, override):
    agent, client = _agent(["answer"])
    agent.system_prompt_override = override

    agent.run(make_task(), None, tmp_path)

    assert client.seen[0][1] == override


# -- log file ------------------------------------------------------------------------------


def test_success_log_name_location_and_full_layout(tmp_path):
    agent, _ = _agent([(LLMUsage(10, 4), "answer")])

    result = agent.run(make_task(), {"k": "v"}, tmp_path)

    log = _only_log(tmp_path)
    assert log.parent == tmp_path / "logs"
    assert re.fullmatch(r"\d{8}_\d{6}_Echo_agent_task-001\.log", log.name)
    lines = Path(result.log_path).read_text(encoding="utf-8").split("\n")
    # First seven lines: the header keys and their values.
    assert lines[:7] == [
        "agent: Echo_agent",
        "role: Tester",
        "task_id: task-001",
        "task_title: Add login page",
        "llm_model: m-1",
        "mock_mode: False",
        "status: SUCCESS",
    ]
    assert re.fullmatch(r"execution_time_seconds: \d+\.\d{4}", lines[7])
    assert re.fullmatch(r"logged_at: \d{4}-\d{2}-\d{2}T[\d:.]+", lines[8])
    assert lines[9:] == [
        "",
        "=== interaction 1 ===",
        "--- input prompt ---",
        "do task-001 ctx={'k': 'v'}",
        "--- output response ---",
        "answer",
        "",
    ]


# -- failure ---------------------------------------------------------------------------------


def test_failure_result_fields_status_and_log(tmp_path):
    agent, client = _agent([RuntimeError("boom")])
    task = make_task()

    result = agent.run(task, None, tmp_path)

    assert (result.agent_name, result.task_id, result.success) == ("Echo_agent", "task-001", False)
    assert (result.output, result.error) == (None, "boom")
    assert result.execution_time >= 0
    assert result.interactions == []  # no successful exchange was recorded
    assert (result.provider, result.llm_model) == ("stub", "m-1")
    assert (result.input_tokens, result.output_tokens) == (0, 0)
    assert task.status is Status.FAILED  # BaseAgent itself flips it
    assert client.calls == 3

    lines = _only_log(tmp_path).read_text(encoding="utf-8").split("\n")
    assert lines[:7] == [
        "agent: Echo_agent",
        "role: Tester",
        "task_id: task-001",
        "task_title: Add login page",
        "llm_model: m-1",
        "mock_mode: False",
        "status: FAILED",
    ]
    assert lines[9:] == [
        "",
        "(no LLM interaction recorded)",
        "",
        "--- error ---",
        "RuntimeError: boom",
        "",
    ]


def test_an_exception_raised_by_process_itself_fails_the_run_without_retry(tmp_path):
    agent = ProcessRaises(_config(), llm_client=StubClient(["x"]), retry_wait_multiplier=0)
    task = make_task()

    result = agent.run(task, None, tmp_path)

    assert (result.success, result.error) == (False, "process blew up")
    assert agent.process_calls == 1
    assert task.status is Status.FAILED
    assert "ValueError: process blew up" in _only_log(tmp_path).read_text(encoding="utf-8")


def test_log_write_failure_never_breaks_the_run(tmp_path):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x", encoding="utf-8")
    agent, _ = _agent(["answer"])

    result = agent.run(make_task(), None, blocker)  # logs/ cannot be created under a file

    assert result.success is True and result.output == "answer"
    assert result.log_path is None


def test_without_project_path_logs_go_under_projects_dir(tmp_path):
    agent, _ = _agent(["answer"], projects_dir=tmp_path)

    agent.run(make_task(), None)

    assert _only_log(tmp_path).parent == tmp_path / "logs"


# -- retry counts --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "max_retries, expected_calls",
    [(3, 3), (5, 5), (2, 2), (1, 1), (0, 1)],  # at least one attempt, always
)
def test_always_failing_client_is_called_max_retries_times(tmp_path, max_retries, expected_calls):
    agent, client = _agent([RuntimeError("down")], max_retries=max_retries)

    result = agent.run(make_task(), None, tmp_path)

    assert result.success is False
    assert client.calls == expected_calls


def test_recovers_when_a_later_attempt_succeeds(tmp_path):
    agent, client = _agent([RuntimeError("1"), RuntimeError("2"), "third time lucky"])

    result = agent.run(make_task(), None, tmp_path)

    assert (result.success, result.output) == (True, "third time lucky")
    assert client.calls == 3
    assert result.interactions == [("do task-001 ctx=None", "third time lucky")]  # only the winner


class _Permanent(Exception):
    retryable = False


class _Transient(Exception):
    retryable = True


class _Plain(Exception):
    pass


@pytest.mark.parametrize(
    "error, expected_calls",
    [
        (_Permanent("no"), 1),  # retryable = False: not retried
        (_Transient("maybe"), 3),  # retryable = True: retried
        (_Plain("maybe"), 3),  # no attribute: retried
        (RuntimeError("maybe"), 3),
    ],
    ids=["retryable-false", "retryable-true", "no-attribute", "runtimeerror"],
)
def test_retryable_attribute_decides_whether_an_error_is_retried(tmp_path, error, expected_calls):
    agent, client = _agent([error])

    result = agent.run(make_task(), None, tmp_path)

    assert result.success is False and result.error == str(error)
    assert client.calls == expected_calls


# -- usage accumulation -----------------------------------------------------------------------------


def test_usage_is_added_per_attempt_including_failed_attempts(tmp_path):
    agent, _ = _agent(
        [
            (LLMUsage(10, 5), RuntimeError("1")),
            (LLMUsage(20, 6), RuntimeError("2")),
            (LLMUsage(30, 7), "answer"),
        ]
    )

    result = agent.run(make_task(), None, tmp_path)

    assert (result.input_tokens, result.output_tokens) == (60, 18)


def test_failure_carries_the_usage_of_its_failed_attempts(tmp_path):
    agent, _ = _agent([(LLMUsage(10, 5), RuntimeError("x"))])

    result = agent.run(make_task(), None, tmp_path)

    assert (result.input_tokens, result.output_tokens) == (30, 15)


def test_clients_that_report_no_usage_contribute_zero(tmp_path):
    agent, _ = _agent(["answer"])

    result = agent.run(make_task(), None, tmp_path)

    assert (result.input_tokens, result.output_tokens) == (0, 0)


def test_per_run_state_is_reset_between_runs(tmp_path):
    agent, _ = _agent([(LLMUsage(5, 2), "first"), (LLMUsage(6, 3), "second")])

    first = agent.run(make_task("task-001"), None, tmp_path)
    second = agent.run(make_task("task-002"), None, tmp_path)

    assert (first.input_tokens, second.input_tokens) == (5, 6)
    assert (len(first.interactions), len(second.interactions)) == (1, 1)
    assert second.interactions[0][1] == "second"
