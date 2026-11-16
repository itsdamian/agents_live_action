"""Environment variable management and mock-mode detection.

On startup the system scans the environment for known LLM provider API keys
(see .env.example). Providers whose key is set are considered "configured" and
become available for agents to use. When NO provider key is set, the system
runs in mock mode: the PM -> SWE -> QA pipeline still works end-to-end using a
MockLLMClient, and all output is tagged with [MOCK MODE].

Requirements: 4.1 (scan known provider env vars on startup),
4.2 (only expose providers whose env var is set),
4.3 (fall back to mock mode when no key is set).
"""

from __future__ import annotations

import os

from pydantic import BaseModel

# Registry of known LLM providers -> the environment variable holding their API
# key. Kept in sync with .env.example. A hardcoded registry (rather than parsing
# .env.example at runtime) keeps detection deterministic and import-cheap.
PROVIDER_ENV_VARS: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
}


class ConfiguredProvider(BaseModel):
    """A known LLM provider whose API key env var is set (non-empty)."""

    name: str
    api_key_env: str


def _is_set(value: str | None) -> bool:
    """Return True if an env value counts as "set".

    Missing variables (``None``) and empty / whitespace-only strings are
    treated as not set, so an ``OPENAI_API_KEY=`` line in .env does not
    accidentally enable a provider.
    """

    return value is not None and value.strip() != ""


def get_configured_providers() -> list[ConfiguredProvider]:
    """Scan the environment and return the providers whose API key is set.

    The result is ordered to match ``PROVIDER_ENV_VARS`` for deterministic
    output. An empty list means no provider is configured (mock mode).
    """

    return [
        ConfiguredProvider(name=name, api_key_env=env_var)
        for name, env_var in PROVIDER_ENV_VARS.items()
        if _is_set(os.environ.get(env_var))
    ]


def is_mock_mode() -> bool:
    """Return True when NO provider API key is set.

    In mock mode the pipeline runs against the MockLLMClient so the system
    remains fully functional without any real credentials.
    """

    return len(get_configured_providers()) == 0


# ---------------------------------------------------------------------------
# Cost / budget configuration (task 16)
# ---------------------------------------------------------------------------

# Environment variables controlling LLM spend ceilings. When a budget is
# exceeded the pipeline pauses enqueueing new tasks and broadcasts a critical
# devops:alert. Both are optional — unset (or non-positive / unparseable)
# means "no limit".
DAILY_BUDGET_ENV = "DAILY_BUDGET_USD"
PROJECT_BUDGET_ENV = "PROJECT_BUDGET_USD"


def _get_positive_float_env(name: str) -> float | None:
    """Parse a positive float from env var ``name``.

    Returns ``None`` when the variable is unset, empty, unparseable, or not a
    positive number, so a malformed budget simply disables the guard rather
    than crashing startup.
    """

    raw = os.environ.get(name)
    if not _is_set(raw):
        return None
    try:
        value = float(raw.strip())
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def get_daily_budget_usd() -> float | None:
    """Daily spend ceiling in USD, or ``None`` when unset/invalid."""

    return _get_positive_float_env(DAILY_BUDGET_ENV)


def get_project_budget_usd() -> float | None:
    """Per-project spend ceiling in USD, or ``None`` when unset/invalid."""

    return _get_positive_float_env(PROJECT_BUDGET_ENV)


# ---------------------------------------------------------------------------
# Pipeline engine selection
# ---------------------------------------------------------------------------

# ``legacy`` is the original ``while`` loop in core/pipeline.py; ``langgraph``
# runs the same flow as a LangGraph StateGraph (core/pipeline_graph.py).
PIPELINE_ENGINE_ENV = "PIPELINE_ENGINE"
PIPELINE_ENGINES = ("legacy", "langgraph")


def normalize_pipeline_engine(value: str | None) -> str:
    """Normalize an engine name: strip, lowercase, empty/``None`` -> ``legacy``.

    Raises:
        ValueError: If the result is not one of :data:`PIPELINE_ENGINES`.
    """

    engine = (value or "").strip().lower() or "legacy"
    if engine not in PIPELINE_ENGINES:
        raise ValueError(
            f"{PIPELINE_ENGINE_ENV} must be one of: "
            f"{', '.join(PIPELINE_ENGINES)} (got '{engine}')"
        )
    return engine


def get_pipeline_engine() -> str:
    """Engine selected by ``PIPELINE_ENGINE`` (default ``legacy``).

    Raises:
        ValueError: If the variable holds an unknown engine name.
    """

    return normalize_pipeline_engine(os.environ.get(PIPELINE_ENGINE_ENV))
