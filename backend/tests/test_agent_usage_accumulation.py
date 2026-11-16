"""BaseAgent adds token usage per LLM attempt, successful or not (A4b)."""

from __future__ import annotations

import pytest

from agents.pm_agent import PMAgent
from core.llm_client import LLMClient, LLMUsage
from models.agent import AgentConfig
from tests.pipeline_harness import make_task


class ScriptedUsageClient(LLMClient):
    """Each attempt follows a step: ``(usage_or_None, outcome)``.

    ``outcome`` is a string (returned) or an Exception (raised). The usage is
    set on ``last_usage`` before the outcome, like a provider that bills the call.
    ``("keep", outcome)`` leaves ``last_usage`` untouched (a client that raised
    without producing new numbers).
    """

    provider = "stub"

    def __init__(self, steps):
        super().__init__("stub-model")
        self.steps = list(steps)
        self.calls = 0

    def call(self, prompt, system_prompt=None):
        usage, outcome = self.steps[self.calls]
        self.calls += 1
        if usage != "keep":
            self.last_usage = usage
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _agent(client, max_retries=3):
    config = AgentConfig(
        name="PM_agent", role="Project Manager", description="d", llm_provider="mock",
        llm_model="stub-model", api_key_env="X", max_retries=max_retries, system_prompt="s",
    )
    return PMAgent(config, llm_client=client, retry_wait_multiplier=0)


def _run(agent, tmp_path):
    return agent.run(make_task(), None, tmp_path)


def test_every_attempt_is_added_when_retries_end_in_success(tmp_path):
    client = ScriptedUsageClient(
        [
            (LLMUsage(10, 5), RuntimeError("1")),
            (LLMUsage(20, 6), RuntimeError("2")),
            (LLMUsage(30, 7), "answer"),
        ]
    )

    result = _run(_agent(client), tmp_path)

    assert result.success is True
    assert (result.input_tokens, result.output_tokens) == (60, 18)  # not just the last attempt
    assert client.calls == 3


def test_failure_carries_the_accumulated_usage_provider_and_model(tmp_path):
    client = ScriptedUsageClient([(LLMUsage(10, 5), RuntimeError("x"))] * 3)

    result = _run(_agent(client), tmp_path)

    assert result.success is False
    assert (result.input_tokens, result.output_tokens) == (30, 15)
    assert (result.provider, result.llm_model) == ("stub", "stub-model")


def test_attempts_without_usage_contribute_zero(tmp_path):
    client = ScriptedUsageClient(
        [(None, RuntimeError("a")), (LLMUsage(7, 3), RuntimeError("b")), (None, "answer")]
    )

    result = _run(_agent(client), tmp_path)

    assert result.success is True
    # attempt 3 reports no usage; attempt 2's numbers belong to attempt 2 only.
    assert (result.input_tokens, result.output_tokens) == (7, 3)


def test_a_failure_that_leaves_last_usage_untouched_is_not_billed_again(tmp_path):
    """Stale numbers from an earlier call must not be re-added by a later failure."""

    client = ScriptedUsageClient([(LLMUsage(40, 9), "first answer")])
    agent = _agent(client)
    assert _run(agent, tmp_path).input_tokens == 40

    client.steps = [("keep", RuntimeError("down"))] * 3
    client.calls = 0
    result = _run(agent, tmp_path)

    assert result.success is False
    assert (result.input_tokens, result.output_tokens) == (0, 0)


def test_usage_resets_between_runs(tmp_path):
    client = ScriptedUsageClient([(LLMUsage(5, 5), "a"), (LLMUsage(6, 6), "b")])
    agent = _agent(client)

    first, second = _run(agent, tmp_path), _run(agent, tmp_path)

    assert (first.input_tokens, second.input_tokens) == (5, 6)


@pytest.mark.parametrize("max_retries", [0, 1])
def test_single_attempt_failure_still_reports_its_usage(tmp_path, max_retries):
    client = ScriptedUsageClient([(LLMUsage(11, 4), RuntimeError("x"))])

    result = _run(_agent(client, max_retries), tmp_path)

    assert (result.input_tokens, result.output_tokens) == (11, 4)
    assert client.calls == 1
