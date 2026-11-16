"""Retry classification for the Anthropic provider (A4, D8). Fully offline.

BaseAgent retries every exception unless it carries ``retryable = False``;
exceptions without the attribute behave exactly as before.
"""

from __future__ import annotations

import types

import anthropic
import httpx2
import pytest

from agents.pm_agent import PMAgent
from core.llm_client import (
    AnthropicClient,
    LLMRefusalError,
    LLMTruncatedError,
    LLMUnexpectedStopError,
)
from models.agent import AgentConfig
from tests.pipeline_harness import make_task
from tests.test_anthropic_client import FakeSDK, message, text

MAX_RETRIES = 3
REQUEST = httpx2.Request("POST", "https://api.invalid/v1/messages")


@pytest.fixture(autouse=True)
def _fake_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-FAKE-retry-test-key")


def _config() -> AgentConfig:
    return AgentConfig(
        name="PM_agent", role="Project Manager", description="d", llm_provider="anthropic",
        llm_model="claude-opus-5-5", api_key_env="ANTHROPIC_API_KEY", max_retries=MAX_RETRIES,
        system_prompt="You are PM.",
    )


class CountingSDK(FakeSDK):
    """FakeSDK whose results are consumed one per call (exceptions raised)."""

    def __init__(self, results):
        super().__init__()
        self.results = list(results)

    def _answer(self):
        result = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(result, Exception):
            raise result
        return result

    @property
    def call_count(self):
        return len(self.calls)


def _agent(results):
    sdk = CountingSDK(results)
    client = AnthropicClient("claude-opus-5-5", "ANTHROPIC_API_KEY", sdk_client=sdk)
    return PMAgent(_config(), llm_client=client, retry_wait_multiplier=0), sdk


def _status_error(status: int) -> anthropic.APIStatusError:
    response = httpx2.Response(status, request=REQUEST)
    cls = {
        400: anthropic.BadRequestError, 401: anthropic.AuthenticationError,
        403: anthropic.PermissionDeniedError, 404: anthropic.NotFoundError,
        429: anthropic.RateLimitError,
    }.get(status, anthropic.InternalServerError if status >= 500 else anthropic.APIStatusError)
    return cls(f"status {status}", response=response, body=None)


# -- exceptions without ``retryable`` behave exactly as before -----------------------


class _Plain(Exception):
    pass


@pytest.mark.parametrize("exc", [RuntimeError("x"), ValueError("x"), _Plain("x"), OSError("x")])
def test_exceptions_without_retryable_attribute_are_retried_to_the_limit(exc):
    attempts = []

    def failing(prompt, system_prompt=None):
        attempts.append(1)
        raise exc

    agent, _ = _agent([message()])
    agent.llm_client.call = failing

    with pytest.raises(type(exc)):
        agent.call_llm("p")

    assert len(attempts) == MAX_RETRIES  # same as before the change


def test_explicit_retryable_flags_on_any_exception():
    class Permanent(Exception):
        retryable = False

    class Transient(Exception):
        retryable = True

    for exc, expected in ((Permanent("x"), 1), (Transient("x"), MAX_RETRIES)):
        attempts = []

        def failing(prompt, system_prompt=None, exc=exc):
            attempts.append(1)
            raise exc

        agent, _ = _agent([message()])
        agent.llm_client.call = failing
        with pytest.raises(type(exc)):
            agent.call_llm("p")
        assert len(attempts) == expected


# -- SDK errors ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [400, 401, 403, 404, 413, 422])
def test_permanent_4xx_errors_are_not_retried_and_raised_unchanged(status):
    error = _status_error(status)
    agent, sdk = _agent([error])

    with pytest.raises(anthropic.APIStatusError) as excinfo:
        agent.call_llm("p")

    assert excinfo.value is error  # the original object, not a wrapper
    assert str(excinfo.value) == f"status {status}"
    assert error.retryable is False
    assert sdk.call_count == 1


@pytest.mark.parametrize("status", [408, 409, 429, 500, 502, 503, 529])
def test_transient_status_errors_are_retried(status):
    error = _status_error(status)
    agent, sdk = _agent([error])

    with pytest.raises(anthropic.APIStatusError):
        agent.call_llm("p")

    assert not hasattr(error, "retryable")  # untouched
    assert sdk.call_count == MAX_RETRIES


@pytest.mark.parametrize(
    "error", [anthropic.APIConnectionError(request=REQUEST), anthropic.APITimeoutError(request=REQUEST)],
    ids=["connection", "timeout"],
)
def test_connection_errors_are_retried(error):
    agent, sdk = _agent([error])

    with pytest.raises(anthropic.APIConnectionError):
        agent.call_llm("p")

    assert sdk.call_count == MAX_RETRIES


def test_rate_limit_then_success_recovers():
    agent, sdk = _agent([_status_error(429), _status_error(529), message([text("done")])])

    assert agent.call_llm("p") == "done"
    assert sdk.call_count == 3


# -- the D6 exceptions: one call only (a refusal is billed once) -----------------------------


@pytest.mark.parametrize(
    "result, exc_type",
    [
        (message(stop_reason="refusal", stop_details=types.SimpleNamespace(category="cyber")), LLMRefusalError),
        (message(stop_reason="max_tokens"), LLMTruncatedError),
        (message(stop_reason="tool_use"), LLMUnexpectedStopError),
        (message([]), LLMUnexpectedStopError),
    ],
    ids=["refusal", "max_tokens", "unexpected", "no-text"],
)
def test_unusable_outputs_call_the_sdk_exactly_once(result, exc_type):
    agent, sdk = _agent([result])

    with pytest.raises(exc_type):
        agent.call_llm("p")

    assert sdk.call_count == 1


def test_refusal_through_agent_run_is_one_call_and_a_failed_result(tmp_path):
    agent, sdk = _agent(
        [message(stop_reason="refusal", stop_details=types.SimpleNamespace(category="bio"))]
    )

    result = agent.run(make_task(), None, tmp_path)

    assert result.success is False
    assert "refused" in (result.error or "")
    assert sdk.call_count == 1
