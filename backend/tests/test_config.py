"""Unit tests for environment-variable scanning and mock-mode detection.

Uses monkeypatch to fully control the relevant env vars so the tests are
deterministic and independent of the developer's real environment.
"""

from __future__ import annotations

import pytest

from config import (
    PROVIDER_ENV_VARS,
    ConfiguredProvider,
    get_configured_providers,
    is_mock_mode,
)

ALL_ENV_VARS = list(PROVIDER_ENV_VARS.values())


@pytest.fixture(autouse=True)
def _clear_provider_env(monkeypatch):
    """Start every test from a clean slate with all provider keys unset."""
    for env_var in ALL_ENV_VARS:
        monkeypatch.delenv(env_var, raising=False)


# ---------------------------------------------------------------------------
# Registry sanity
# ---------------------------------------------------------------------------


def test_registry_matches_known_providers():
    assert PROVIDER_ENV_VARS == {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "gemini": "GEMINI_API_KEY",
    }


# ---------------------------------------------------------------------------
# No keys set -> mock mode
# ---------------------------------------------------------------------------


def test_no_keys_set_is_mock_mode():
    assert is_mock_mode() is True


def test_no_keys_set_returns_empty_providers():
    assert get_configured_providers() == []


# ---------------------------------------------------------------------------
# A single key set
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "provider,env_var",
    list(PROVIDER_ENV_VARS.items()),
)
def test_single_key_set_detected(monkeypatch, provider, env_var):
    monkeypatch.setenv(env_var, "sk-test-123")

    providers = get_configured_providers()

    assert is_mock_mode() is False
    assert len(providers) == 1
    assert providers[0] == ConfiguredProvider(name=provider, api_key_env=env_var)


# ---------------------------------------------------------------------------
# Multiple keys set
# ---------------------------------------------------------------------------


def test_multiple_keys_set_all_reported(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setenv("GEMINI_API_KEY", "gm-gemini")

    providers = get_configured_providers()
    names = {p.name for p in providers}

    assert is_mock_mode() is False
    assert names == {"openai", "gemini"}


def test_all_keys_set_all_reported(monkeypatch):
    for env_var in ALL_ENV_VARS:
        monkeypatch.setenv(env_var, "value")

    providers = get_configured_providers()

    assert is_mock_mode() is False
    assert len(providers) == len(PROVIDER_ENV_VARS)
    assert {p.name for p in providers} == set(PROVIDER_ENV_VARS.keys())


def test_configured_providers_preserve_registry_order(monkeypatch):
    for env_var in ALL_ENV_VARS:
        monkeypatch.setenv(env_var, "value")

    providers = get_configured_providers()

    assert [p.name for p in providers] == list(PROVIDER_ENV_VARS.keys())


# ---------------------------------------------------------------------------
# Empty / whitespace values count as not set
# ---------------------------------------------------------------------------


def test_empty_string_treated_as_not_set(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")

    assert is_mock_mode() is True
    assert get_configured_providers() == []


def test_whitespace_only_treated_as_not_set(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")

    assert is_mock_mode() is True
    assert get_configured_providers() == []


def test_empty_key_ignored_but_set_key_detected(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "real-key")

    providers = get_configured_providers()

    assert is_mock_mode() is False
    assert len(providers) == 1
    assert providers[0].name == "anthropic"
