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

import logging
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


FORCE_MOCK_ENV = "FORCE_MOCK"
_TRUTHY = frozenset({"1", "true", "yes"})


def is_force_mock() -> bool:
    """Return True when ``FORCE_MOCK`` is ``1``/``true``/``yes`` (any case).

    A kill switch for real LLM spend: with it set, every agent uses the mock
    client even when provider API keys are present in the environment. All
    acceptance and recording runs set it (plan D22).
    """

    return (os.environ.get(FORCE_MOCK_ENV) or "").strip().lower() in _TRUTHY


MOCK_CHECK_FAIL_FIRST_ENV = "MOCK_CHECK_FAIL_FIRST"


def is_mock_check_fail_first() -> bool:
    """Return True when ``MOCK_CHECK_FAIL_FIRST`` is ``1``/``true``/``yes`` (any case).

    Makes the Mock answer of a custom ``check`` agent FAIL until its prompt carries rejection
    feedback, so a Mock run shows a rework and then passes (plan D17).
    """

    return (os.environ.get(MOCK_CHECK_FAIL_FIRST_ENV) or "").strip().lower() in _TRUTHY


MOCK_STEP_DELAY_MS_ENV = "MOCK_STEP_DELAY_MS"
_warned_mock_step_delay: set[str] = set()


def get_mock_step_delay_ms() -> int:
    """Milliseconds every Mock LLM call waits before returning (plan D17 "Mock 節奏").

    Default 0 (tests stay fast); docker-compose sets 1500 so the HUD shows a ticket moving
    station by station. A value that is not a non-negative integer counts as 0, with one
    warning per distinct value.
    """

    raw = (os.environ.get(MOCK_STEP_DELAY_MS_ENV) or "").strip()
    if not raw:
        return 0
    try:
        value = int(raw)
    except ValueError:
        value = -1
    if value < 0:
        if raw not in _warned_mock_step_delay:
            _warned_mock_step_delay.add(raw)
            logging.getLogger(__name__).warning(
                "%s=%r is not a non-negative integer; using 0.", MOCK_STEP_DELAY_MS_ENV, raw
            )
        return 0
    return value


def is_mock_mode() -> bool:
    """Return True when no real LLM will be used.

    That is the case when ``FORCE_MOCK`` is set, or when NO provider API key is
    set. In mock mode the pipeline runs against the MockLLMClient so the system
    remains fully functional without any real credentials.
    """

    if is_force_mock():
        return True
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

# ``langgraph`` (the default) runs the pipeline as a LangGraph StateGraph
# (core/pipeline_graph.py); ``legacy`` is the original ``while`` loop in
# core/pipeline.py, kept so it can be switched back with PIPELINE_ENGINE=legacy.
PIPELINE_ENGINE_ENV = "PIPELINE_ENGINE"
PIPELINE_ENGINES = ("legacy", "langgraph")
DEFAULT_PIPELINE_ENGINE = "langgraph"


def normalize_pipeline_engine(value: str | None) -> str:
    """Normalize an engine name: strip, lowercase, empty/``None`` -> the default.

    Raises:
        ValueError: If the result is not one of :data:`PIPELINE_ENGINES`.
    """

    engine = (value or "").strip().lower() or DEFAULT_PIPELINE_ENGINE
    if engine not in PIPELINE_ENGINES:
        raise ValueError(
            f"{PIPELINE_ENGINE_ENV} must be one of: "
            f"{', '.join(PIPELINE_ENGINES)} (got '{engine}')"
        )
    return engine


def get_pipeline_engine() -> str:
    """Engine selected by ``PIPELINE_ENGINE`` (default ``langgraph``).

    Raises:
        ValueError: If the variable holds an unknown engine name.
    """

    return normalize_pipeline_engine(os.environ.get(PIPELINE_ENGINE_ENV))


# ---------------------------------------------------------------------------
# Task store, database URL, checkpointer, and the supported combinations (plan D2, D5, D7, D23)
# ---------------------------------------------------------------------------

TASK_STORE_ENV = "TASK_STORE"
TASK_STORES = ("file", "db")
DEFAULT_TASK_STORE = "file"

DATABASE_URL_ENV = "DATABASE_URL"
# SQLAlchemy URL for psycopg 3 (plan D6): one driver for SQLAlchemy and the checkpointer.
DATABASE_URL_SCHEME = "postgresql+psycopg"
DATABASE_URL_EXAMPLE = "postgresql+psycopg://factory:factory@postgres:5432/factory"

PIPELINE_CHECKPOINTER_ENV = "PIPELINE_CHECKPOINTER"
PIPELINE_CHECKPOINTERS = ("memory", "postgres")
DEFAULT_PIPELINE_CHECKPOINTER = "memory"

# (engine, task store, checkpointer) combinations that start (plan D23).
SUPPORTED_RUNTIME_COMBINATIONS = (
    ("legacy", "file", "memory"),
    ("legacy", "db", "memory"),
    ("langgraph", "file", "memory"),
    ("langgraph", "db", "memory"),
    ("langgraph", "db", "postgres"),
)


def _choice_from_env(env_name: str, allowed: tuple[str, ...], default: str, value: str | None) -> str:
    choice = (value or "").strip().lower() or default
    if choice not in allowed:
        raise ValueError(f"{env_name} must be one of: {', '.join(allowed)} (got '{choice}')")
    return choice


def normalize_task_store(value: str | None) -> str:
    """Normalize a task store name (``file`` or ``db``); empty/``None`` -> the default."""

    return _choice_from_env(TASK_STORE_ENV, TASK_STORES, DEFAULT_TASK_STORE, value)


def get_task_store() -> str:
    """Where tasks live: ``file`` (tasks.yaml, the default) or ``db`` (PostgreSQL)."""

    return normalize_task_store(os.environ.get(TASK_STORE_ENV))


def get_pipeline_checkpointer() -> str:
    """LangGraph checkpointer: ``memory`` (the default) or ``postgres``."""

    return _choice_from_env(
        PIPELINE_CHECKPOINTER_ENV,
        PIPELINE_CHECKPOINTERS,
        DEFAULT_PIPELINE_CHECKPOINTER,
        os.environ.get(PIPELINE_CHECKPOINTER_ENV),
    )


def mask_database_url(url: str) -> str:
    """``url`` with the password replaced by ``***``; safe to show in a message or log.

    Never raises: a string that does not parse as a URL still has anything that looks
    like ``user:password@`` masked.
    """

    try:
        from sqlalchemy.engine import make_url

        return make_url(url).render_as_string(hide_password=True)
    except Exception:  # noqa: BLE001 - masking must work on arbitrary garbage
        import re

        return re.sub(r"(://[^:/@\s]*:)[^@\s]*(@)", r"\1***\2", url or "")


def get_database_url() -> str:
    """The configured ``DATABASE_URL``, validated.

    Raises:
        ValueError: If it is missing, is not a ``postgresql+psycopg://`` URL, or does
            not parse. The password is never part of the message.
    """

    url = (os.environ.get(DATABASE_URL_ENV) or "").strip()
    if not url:
        raise ValueError(
            f"{DATABASE_URL_ENV} is not set; it is required when {TASK_STORE_ENV}=db "
            f"(for example {DATABASE_URL_EXAMPLE})"
        )
    try:
        from sqlalchemy.engine import make_url

        parsed = make_url(url)
    except Exception:  # noqa: BLE001 - never echo the raw string: it may hold a password
        raise ValueError(
            f"{DATABASE_URL_ENV} is not a valid database URL "
            f"(expected {DATABASE_URL_SCHEME}://user:password@host:port/database)"
        ) from None
    if parsed.drivername != DATABASE_URL_SCHEME:
        raise ValueError(
            f"{DATABASE_URL_ENV} must use the '{DATABASE_URL_SCHEME}' driver "
            f"(got '{parsed.drivername}' in {mask_database_url(url)})"
        )
    return url


def _combination_problem(engine: str, store: str, checkpointer: str) -> str | None:
    """Why ``(engine, store, checkpointer)`` is not supported, or ``None`` if it is."""

    if checkpointer == "postgres" and engine == "legacy":
        return "the legacy engine does not use a graph or a checkpointer"
    if checkpointer == "postgres" and store == "file":
        return (
            "the postgres checkpointer needs TASK_STORE=db "
            "(restart recovery reads the tasks table)"
        )
    return None


def validate_runtime_flags(
    engine: str | None = None, store: str | None = None, checkpointer: str | None = None
) -> dict[str, str]:
    """Check that the engine / task store / checkpointer combination can run (plan D23).

    Arguments default to the environment (``PIPELINE_ENGINE``, ``TASK_STORE``,
    ``PIPELINE_CHECKPOINTER``). With ``TASK_STORE=db`` a valid ``DATABASE_URL`` is
    required too. Called from ``create_app()`` so a bad combination fails at startup.

    Returns:
        The resolved ``{"engine", "task_store", "checkpointer"}``.

    Raises:
        ValueError: On an unknown value, an unsupported combination (the message gives
            the reason and the supported combinations) or a bad ``DATABASE_URL``.
    """

    resolved_engine = (
        get_pipeline_engine() if engine is None else normalize_pipeline_engine(engine)
    )
    resolved_store = (
        get_task_store()
        if store is None
        else _choice_from_env(TASK_STORE_ENV, TASK_STORES, DEFAULT_TASK_STORE, store)
    )
    resolved_checkpointer = (
        get_pipeline_checkpointer()
        if checkpointer is None
        else _choice_from_env(
            PIPELINE_CHECKPOINTER_ENV, PIPELINE_CHECKPOINTERS, DEFAULT_PIPELINE_CHECKPOINTER, checkpointer
        )
    )

    problem = _combination_problem(resolved_engine, resolved_store, resolved_checkpointer)
    if problem is not None:
        supported = ", ".join("/".join(combo) for combo in SUPPORTED_RUNTIME_COMBINATIONS)
        raise ValueError(
            f"Unsupported combination {PIPELINE_ENGINE_ENV}={resolved_engine}, "
            f"{TASK_STORE_ENV}={resolved_store}, "
            f"{PIPELINE_CHECKPOINTER_ENV}={resolved_checkpointer}: {problem}. "
            f"Supported (engine/store/checkpointer): {supported}"
        )

    if resolved_store == "db":
        get_database_url()  # raises with a clear message when missing or malformed

    return {
        "engine": resolved_engine,
        "task_store": resolved_store,
        "checkpointer": resolved_checkpointer,
    }
