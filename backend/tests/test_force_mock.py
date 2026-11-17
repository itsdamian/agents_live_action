"""FORCE_MOCK: a kill switch that keeps every agent on the mock client (D22, AC7b)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import config
from core import llm_client
from core.llm_client import (
    AnthropicClient,
    MockLLMClient,
    OpenAIClient,
    get_llm_client,
)
from main import create_app
from models.agent import AgentConfig

FAKE_KEYS = {
    "ANTHROPIC_API_KEY": "sk-ant-FAKE-force-mock",
    "OPENAI_API_KEY": "sk-FAKE-force-mock",
    "GEMINI_API_KEY": "gm-FAKE-force-mock",
}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("FORCE_MOCK", raising=False)
    for name in FAKE_KEYS:
        monkeypatch.delenv(name, raising=False)


def _set_keys(monkeypatch):
    for name, value in FAKE_KEYS.items():
        monkeypatch.setenv(name, value)


def _agent(provider="anthropic", api_key_env="ANTHROPIC_API_KEY", model="claude-opus-5-5") -> AgentConfig:
    return AgentConfig(
        name="SWE_agent", role="Software Engineer", description="d", llm_provider=provider,
        llm_model=model, api_key_env=api_key_env, max_retries=1, system_prompt="s",
    )


# -- the flag ------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["true", "1", "yes", "TRUE", "True", "YES", "Yes", " true ", "\ttrue\n"])
def test_truthy_values_enable_force_mock(monkeypatch, value):
    monkeypatch.setenv("FORCE_MOCK", value)

    assert config.is_force_mock() is True


@pytest.mark.parametrize("value", ["", "  ", "0", "false", "no", "off", "2", "truee", "y", "on"])
def test_other_values_do_not_enable_force_mock(monkeypatch, value):
    monkeypatch.setenv("FORCE_MOCK", value)

    assert config.is_force_mock() is False


def test_unset_means_not_forced():
    assert config.is_force_mock() is False


# -- is_mock_mode ---------------------------------------------------------------------


def test_force_mock_makes_mock_mode_true_even_with_keys(monkeypatch):
    _set_keys(monkeypatch)
    assert config.is_mock_mode() is False  # unchanged without the flag

    monkeypatch.setenv("FORCE_MOCK", "true")

    assert config.is_mock_mode() is True
    # The key scan itself is untouched: providers are still reported.
    assert [p.name for p in config.get_configured_providers()] == ["openai", "anthropic", "gemini"]


# -- get_llm_client -------------------------------------------------------------------


@pytest.fixture
def exploding_real_clients(monkeypatch):
    """Any construction of a real client fails the test."""

    def boom(self, *args, **kwargs):
        raise AssertionError("a real LLM client was constructed under FORCE_MOCK")

    monkeypatch.setattr(AnthropicClient, "__init__", boom)
    monkeypatch.setattr(OpenAIClient, "__init__", boom)


@pytest.mark.parametrize(
    "provider, key_env",
    [
        ("anthropic", "ANTHROPIC_API_KEY"),
        ("openai", "OPENAI_API_KEY"),
        ("gemini", "GEMINI_API_KEY"),
        ("mock", "ANTHROPIC_API_KEY"),
        ("ANTHROPIC", "ANTHROPIC_API_KEY"),
        ("anthropic", "SOME_OTHER_KEY_ENV"),
    ],
)
def test_force_mock_returns_mock_and_never_builds_a_real_client(
    monkeypatch, exploding_real_clients, provider, key_env
):
    _set_keys(monkeypatch)
    monkeypatch.setenv("FORCE_MOCK", "true")

    client = get_llm_client(_agent(provider, key_env))

    assert type(client) is MockLLMClient
    assert client.model == "claude-opus-5-5"  # config values still carried over
    assert (client.name, client.role) == ("SWE_agent", "Software Engineer")


def test_force_mock_does_not_even_look_at_the_key_variables(monkeypatch, exploding_real_clients):
    """The switch comes before any key check: keys are not consulted at all."""

    monkeypatch.setenv("FORCE_MOCK", "1")
    monkeypatch.setattr(llm_client, "_is_set", lambda value: (_ for _ in ()).throw(AssertionError("key checked")))

    assert type(get_llm_client(_agent())) is MockLLMClient


@pytest.mark.parametrize("value", ["true", "1", "yes", "TRUE", "Yes"])
def test_every_truthy_spelling_blocks_the_real_client(monkeypatch, exploding_real_clients, value):
    _set_keys(monkeypatch)
    monkeypatch.setenv("FORCE_MOCK", value)

    assert type(get_llm_client(_agent())) is MockLLMClient


def test_falsy_flag_leaves_selection_unchanged(monkeypatch):
    import anthropic

    _set_keys(monkeypatch)
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: object())  # no real SDK client
    monkeypatch.setenv("FORCE_MOCK", "false")

    assert type(get_llm_client(_agent("anthropic", "ANTHROPIC_API_KEY"))) is AnthropicClient
    assert type(get_llm_client(_agent("openai", "OPENAI_API_KEY"))) is OpenAIClient


# -- selection rules with the flag unset: exactly as before ----------------------------


def test_unset_rule_explicit_mock_provider(monkeypatch):
    _set_keys(monkeypatch)

    assert type(get_llm_client(_agent("mock"))) is MockLLMClient


def test_unset_rule_missing_key_falls_back_to_mock():
    assert type(get_llm_client(_agent("anthropic"))) is MockLLMClient


def test_unset_rule_provider_without_real_client_falls_back_to_mock(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEYS["GEMINI_API_KEY"])

    assert type(get_llm_client(_agent("gemini", "GEMINI_API_KEY"))) is MockLLMClient


def test_unset_rule_key_set_returns_real_client(monkeypatch):
    import anthropic

    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEYS["ANTHROPIC_API_KEY"])
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: object())

    client = get_llm_client(_agent("anthropic"))

    assert type(client) is AnthropicClient and client.is_mock is False


# -- /api/health ------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(projects_dir=str(tmp_path))) as test_client:
        yield test_client


def test_health_reports_force_mock_false_by_default(client):
    body = client.get("/api/health").json()

    assert body["force_mock"] is False
    assert body["status"] == "ok" and body["mock_mode"] is True  # no keys: mock anyway


def test_health_reports_force_mock_true_with_keys_present(monkeypatch, tmp_path):
    _set_keys(monkeypatch)
    monkeypatch.setenv("FORCE_MOCK", "true")

    with TestClient(create_app(projects_dir=str(tmp_path))) as test_client:
        body = test_client.get("/api/health").json()

    assert body["force_mock"] is True
    assert body["mock_mode"] is True
    assert "anthropic" in body["providers"]  # keys are still reported, just not used


def test_health_without_force_and_with_keys_is_not_mock(monkeypatch, tmp_path):
    _set_keys(monkeypatch)

    with TestClient(create_app(projects_dir=str(tmp_path))) as test_client:
        body = test_client.get("/api/health").json()

    assert (body["force_mock"], body["mock_mode"]) == (False, False)
