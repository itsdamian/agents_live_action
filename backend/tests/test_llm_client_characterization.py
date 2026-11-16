"""Characterization of llm_client.py before the Anthropic provider lands (A1).

Pins what must NOT change: the four ``get_llm_client`` selection rules, the exact
MockLLMClient output and ``last_usage`` per role, and ``MissingAPIKeyError``.
Written against the unmodified code; no network, no real SDK.
"""

from __future__ import annotations

import pytest

from core.llm_client import (
    AnthropicClient,
    LLMUsage,
    MissingAPIKeyError,
    MockLLMClient,
    OpenAIClient,
    get_llm_client,
)
from core.usage_tracker import estimate_tokens
from models.agent import AgentConfig

SECRET = "sk-ant-characterization-secret"


@pytest.fixture(autouse=True)
def _no_provider_keys(monkeypatch):
    for env_var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(env_var, raising=False)


@pytest.fixture(autouse=True)
def _never_build_a_real_sdk_client(monkeypatch):
    """Once AnthropicClient builds an SDK client, keep these tests off the real one."""

    try:
        import anthropic
    except ImportError:  # pragma: no cover - SDK not installed
        return
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: object())


def _config(provider: str, api_key_env: str, name="PM_agent", role="Project Manager") -> AgentConfig:
    return AgentConfig(
        name=name,
        role=role,
        description="d",
        llm_provider=provider,
        llm_model="some-model",
        api_key_env=api_key_env,
        max_retries=1,
        system_prompt="You are an agent.",
    )


# -- the four selection rules ----------------------------------------------------


def test_rule1_explicit_mock_provider_is_mock_even_with_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)

    client = get_llm_client(_config("mock", "ANTHROPIC_API_KEY"))

    assert type(client) is MockLLMClient
    assert client.model == "some-model"


def test_rule2_missing_or_blank_key_falls_back_to_mock(monkeypatch):
    assert type(get_llm_client(_config("anthropic", "ANTHROPIC_API_KEY"))) is MockLLMClient

    monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")
    assert type(get_llm_client(_config("anthropic", "ANTHROPIC_API_KEY"))) is MockLLMClient


def test_rule3_provider_without_real_client_falls_back_to_mock(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", SECRET)

    assert type(get_llm_client(_config("gemini", "GEMINI_API_KEY"))) is MockLLMClient


def test_rule4_key_set_returns_the_matching_real_client(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)

    anthropic_client = get_llm_client(_config("anthropic", "ANTHROPIC_API_KEY"))
    openai_client = get_llm_client(_config("OpenAI", "OPENAI_API_KEY"))  # case-insensitive

    assert type(anthropic_client) is AnthropicClient
    assert type(openai_client) is OpenAIClient
    assert anthropic_client.provider == "anthropic"
    assert anthropic_client.is_mock is False
    assert anthropic_client.model == "some-model"
    assert anthropic_client.last_usage is None


# -- MockLLMClient output and usage ------------------------------------------------

MOCK_EXPECTED = {
    "pm": (
        "PM_agent",
        "Project Manager",
        "[MOCK MODE] PM task analysis\n"
        "Task: do it\n"
        "Analysis: Broke the task into deliverable work items.\n"
        "Execution plan:\n"
        "  1. Clarify requirements and acceptance criteria.\n"
        "  2. Hand off implementation work to SWE_agent.\n"
        "  3. Route the result to QA_agent for verification.\n"
        "Acceptance criteria: pipeline completes and QA reports pass.",
    ),
    "swe": (
        "SWE_agent",
        "Software Engineer",
        "[MOCK MODE] SWE implementation\n"
        "Task: do it\n"
        "```python\n"
        "def solution():\n"
        '    """Mock implementation produced in mock mode."""\n'
        '    return "ok"\n'
        "```",
    ),
    "qa": (
        "QA_agent",
        "Quality Assurance",
        "[MOCK MODE] QA test report\n"
        "Task: do it\n"
        "Result: PASS\n"
        "Tests run: 3, Passed: 3, Failed: 0\n"
        "Notes: All mock checks passed.",
    ),
}


@pytest.mark.parametrize("key", sorted(MOCK_EXPECTED))
def test_mock_output_and_usage_for_each_pipeline_role(key):
    name, role, expected = MOCK_EXPECTED[key]
    client = MockLLMClient(role=role, name=name)
    assert client.last_usage is None

    out = client.call("do it", "SYS")

    assert out == expected
    assert client.last_usage == LLMUsage(
        input_tokens=estimate_tokens("SYS\ndo it"),
        output_tokens=estimate_tokens(expected),
    )
    assert client.provider == "mock" and client.is_mock is True


def test_mock_usage_treats_missing_system_prompt_as_empty():
    client = MockLLMClient(role="Quality Assurance", name="QA_agent")

    client.call("do it")

    assert client.last_usage.input_tokens == estimate_tokens("\ndo it")


# -- MissingAPIKeyError --------------------------------------------------------------


@pytest.mark.parametrize("cls, provider", [(AnthropicClient, "anthropic"), (OpenAIClient, "openai")])
def test_real_client_without_key_raises_missing_api_key_error(cls, provider):
    with pytest.raises(MissingAPIKeyError) as excinfo:
        cls(model="m", api_key_env="SOME_UNSET_KEY_ENV")

    err = excinfo.value
    assert (err.provider, err.api_key_env) == (provider, "SOME_UNSET_KEY_ENV")
    assert str(err) == (
        f"LLM provider '{provider}' requires the environment variable "
        "'SOME_UNSET_KEY_ENV' to be set, but it is missing or empty. "
        "Set 'SOME_UNSET_KEY_ENV' or use the 'mock' provider."
    )
    assert isinstance(err, RuntimeError)


def test_blank_key_also_raises(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "  ")

    with pytest.raises(MissingAPIKeyError):
        AnthropicClient(model="m", api_key_env="ANTHROPIC_API_KEY")
