"""Unit tests for the LLM client abstraction layer.

Uses monkeypatch to fully control provider env vars so the tests are
deterministic and independent of the developer's real environment. The focus
is mock-mode output format and factory selection (Requirements 4.3, 4.4, 4.5).
"""

from __future__ import annotations

import pytest

from config import PROVIDER_ENV_VARS
from core.llm_client import (
    MOCK_MODE_MARKER,
    AnthropicClient,
    LLMClient,
    MissingAPIKeyError,
    MockLLMClient,
    OpenAIClient,
    get_llm_client,
)
from models.agent import AgentConfig

ALL_ENV_VARS = list(PROVIDER_ENV_VARS.values())


@pytest.fixture(autouse=True)
def _clear_provider_env(monkeypatch):
    """Start every test with all provider keys unset."""
    for env_var in ALL_ENV_VARS:
        monkeypatch.delenv(env_var, raising=False)


def _make_config(**overrides) -> AgentConfig:
    """Build an AgentConfig with sensible defaults for tests."""
    base = dict(
        name="PM_agent",
        role="Project Manager",
        description="desc",
        llm_provider="mock",
        llm_model="gpt-4o-mini",
        api_key_env="OPENAI_API_KEY",
        max_retries=3,
        system_prompt="You are a PM.",
    )
    base.update(overrides)
    return AgentConfig(**base)


# ---------------------------------------------------------------------------
# MockLLMClient output format (Requirement 4.4)
# ---------------------------------------------------------------------------


def test_mock_client_is_llm_client_and_marked_mock():
    client = MockLLMClient()
    assert isinstance(client, LLMClient)
    assert client.is_mock is True


def test_mock_output_starts_with_mock_marker():
    client = MockLLMClient(role="Project Manager", name="PM_agent")
    out = client.call("Build a login page")
    assert out.startswith(MOCK_MODE_MARKER)


@pytest.mark.parametrize(
    "name,role,marker",
    [
        ("PM_agent", "Project Manager", "PM task analysis"),
        ("SWE_agent", "Senior Software Engineer", "SWE implementation"),
        ("QA_agent", "Senior QA Engineer", "QA test report"),
        ("DevOps_agent", "DevOps Engineer", "DevOps monitoring report"),
    ],
)
def test_mock_output_is_role_specific(name, role, marker):
    client = MockLLMClient(role=role, name=name)
    out = client.call("some task")
    assert MOCK_MODE_MARKER in out
    assert marker in out


def test_mock_pm_includes_execution_plan():
    client = MockLLMClient(role="Project Manager", name="PM_agent")
    out = client.call("do work")
    assert "Execution plan" in out


def test_mock_swe_includes_code_block():
    client = MockLLMClient(role="Senior Software Engineer", name="SWE_agent")
    out = client.call("implement it")
    assert "```" in out


def test_mock_qa_reports_pass_fail():
    client = MockLLMClient(role="Senior QA Engineer", name="QA_agent")
    out = client.call("test it")
    assert "PASS" in out


def test_mock_unknown_role_falls_back_to_generic():
    client = MockLLMClient(role="Unknown", name="Weird_agent")
    out = client.call("hello")
    assert out.startswith(MOCK_MODE_MARKER)
    assert "Simulated response" in out


def test_mock_output_embeds_prompt():
    client = MockLLMClient(role="Project Manager", name="PM_agent")
    out = client.call("UNIQUE-PROMPT-XYZ")
    assert "UNIQUE-PROMPT-XYZ" in out


# ---------------------------------------------------------------------------
# Factory: mock provider (Requirement 4.3)
# ---------------------------------------------------------------------------


def test_factory_returns_mock_when_provider_is_mock():
    config = _make_config(llm_provider="mock")
    client = get_llm_client(config)
    assert isinstance(client, MockLLMClient)
    assert client.is_mock is True


def test_factory_falls_back_to_mock_when_key_missing():
    # openai provider but OPENAI_API_KEY is unset -> mock mode
    config = _make_config(llm_provider="openai", api_key_env="OPENAI_API_KEY")
    client = get_llm_client(config)
    assert isinstance(client, MockLLMClient)


def test_factory_falls_back_to_mock_for_unimplemented_provider(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gm-key")
    config = _make_config(llm_provider="gemini", api_key_env="GEMINI_API_KEY")
    client = get_llm_client(config)
    assert isinstance(client, MockLLMClient)


def test_factory_mock_client_carries_role():
    config = _make_config(
        name="QA_agent", role="Senior QA Engineer", llm_provider="mock"
    )
    client = get_llm_client(config)
    out = client.call("verify")
    assert "QA test report" in out


# ---------------------------------------------------------------------------
# Factory: real providers when key is set
# ---------------------------------------------------------------------------


def test_factory_returns_openai_client_when_key_set(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    config = _make_config(llm_provider="openai", api_key_env="OPENAI_API_KEY")
    client = get_llm_client(config)
    assert isinstance(client, OpenAIClient)
    assert client.is_mock is False


def test_factory_returns_anthropic_client_when_key_set(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    config = _make_config(
        name="SWE_agent",
        llm_provider="anthropic",
        api_key_env="ANTHROPIC_API_KEY",
    )
    client = get_llm_client(config)
    assert isinstance(client, AnthropicClient)


def test_empty_key_treated_as_missing_returns_mock(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "   ")
    config = _make_config(llm_provider="openai", api_key_env="OPENAI_API_KEY")
    client = get_llm_client(config)
    assert isinstance(client, MockLLMClient)


# ---------------------------------------------------------------------------
# Real client key validation at init (Requirement 4.5)
# ---------------------------------------------------------------------------


def test_openai_client_raises_clear_error_without_key():
    with pytest.raises(MissingAPIKeyError) as exc_info:
        OpenAIClient(model="gpt-4o-mini", api_key_env="OPENAI_API_KEY")
    assert "OPENAI_API_KEY" in str(exc_info.value)


def test_real_client_call_is_placeholder(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    client = OpenAIClient(model="gpt-4o-mini", api_key_env="OPENAI_API_KEY")
    with pytest.raises(NotImplementedError):
        client.call("hello")
