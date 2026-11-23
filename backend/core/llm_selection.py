"""Which LLM an agent really runs on (plan D21, D22, AC25).

A project has a default (``projects.settings.llm``: ``"mock"`` or a whitelisted Claude model) and
every agent may override it (``agents.llm``; ``None`` follows the project). :func:`resolve_llm`
turns the two into what is actually used, with the reason when that is the Mock:

* ``force_mock`` -- ``FORCE_MOCK`` is set; it wins over every setting, also over a configured Mock;
* ``missing_key`` -- Claude was chosen but the server has no ``ANTHROPIC_API_KEY``;
* ``configured_mock`` -- someone chose the Mock on purpose (the UI shows no warning for it).

The key's environment variable is fixed by the provider (``config.PROVIDER_ENV_VARS``); requests
never name one. Only whether the key is set is ever looked at, never its value.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass

import config
from core.llm_client import ANTHROPIC_MODELS

MOCK_MODEL = "mock-model"
MODEL_LABELS: dict[str, str] = {
    "claude-opus-5-5": "Claude Opus 5.5",
    "claude-sonnet-5-5": "Claude Sonnet 5.5",
    "claude-haiku-5-5": "Claude Haiku 5.5",
    "claude-fable-5-2": "Claude Fable 5.2",
}


@dataclass(frozen=True)
class EffectiveLLM:
    provider: str  # "mock" | "anthropic"
    model: str
    source: str  # "agent" | "project"
    mock_reason: str | None  # None | "force_mock" | "missing_key" | "configured_mock"

    def to_dict(self) -> dict:
        return asdict(self)

    def agent_config_fields(self) -> dict:
        """``llm_provider`` / ``llm_model`` / ``api_key_env`` for an ``AgentConfig``."""

        return {
            "llm_provider": self.provider,
            "llm_model": self.model,
            "api_key_env": config.PROVIDER_ENV_VARS.get(self.provider, ""),
        }


def _is_mock(choice) -> bool:
    return choice is None or choice == "mock" or (isinstance(choice, dict) and choice.get("provider") == "mock")


def key_present(provider: str, env: Mapping[str, str]) -> bool:
    """Whether the provider's API key variable is set (non-blank) in ``env``."""

    name = config.PROVIDER_ENV_VARS.get(provider)
    return bool(name) and config._is_set(env.get(name))


def resolve_llm(agent_llm, project_llm, *, force_mock: bool, env: Mapping[str, str]) -> EffectiveLLM:
    """The LLM an agent runs on: its own choice, else the project's (``None`` = ``"mock"``).

    Pure: the caller passes ``config.is_force_mock()`` and ``os.environ``. The choices are
    expected to be valid already (``validate_llm_choice``).
    """

    source = "project" if agent_llm is None else "agent"
    choice = project_llm if agent_llm is None else agent_llm
    if force_mock:
        return EffectiveLLM("mock", MOCK_MODEL, source, "force_mock")
    if _is_mock(choice):
        return EffectiveLLM("mock", MOCK_MODEL, source, "configured_mock")
    provider, model = choice["provider"], choice["model"]
    if not key_present(provider, env):
        return EffectiveLLM("mock", MOCK_MODEL, source, "missing_key")
    return EffectiveLLM(provider, model, source, None)


def model_options() -> list[dict]:
    """The whitelist with display names, in whitelist order."""

    return [{"id": model, "label": MODEL_LABELS[model]} for model in ANTHROPIC_MODELS]
