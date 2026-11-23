"""LLM client abstraction layer.

Defines a provider-agnostic :class:`LLMClient` interface plus concrete
implementations:

    * :class:`MockLLMClient`  — returns role-specific, fixed-format fake output
      tagged with ``[MOCK MODE]`` so the PM -> SWE -> QA pipeline works
      end-to-end without any real API credentials.
    * :class:`AnthropicClient` — real Claude calls through the official
      ``anthropic`` SDK (non-streaming). The SDK is imported lazily in the
      constructor, so Mock mode and tests never load it.
    * :class:`OpenAIClient` — framework only; its API call is still a placeholder.

The :func:`get_llm_client` factory chooses an implementation from an
:class:`~models.agent.AgentConfig`: it returns a :class:`MockLLMClient` when the
config explicitly requests the ``mock`` provider or when the referenced API-key
env var is not set, and otherwise returns the matching real-provider client.

Requirements:
    4.3 (fall back to mock mode when the referenced env var is not set so the
         pipeline still produces simulated responses),
    4.4 (mock output is clearly marked with [MOCK MODE]),
    4.5 (validate at init whether the configured api_key_env is set and give a
         clear message when it is missing).
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass

from config import _is_set, is_force_mock, is_mock_check_fail_first
from core.usage_tracker import estimate_tokens
from models.agent import AgentConfig

logger = logging.getLogger(__name__)

# Marker prepended to every mock response (Requirement 4.4). Kept as a module
# constant so tests and the frontend can reference the exact string.
MOCK_MODE_MARKER = "[MOCK MODE]"
# The section a custom agent's prompt has once its work was sent back (agents/custom_agent.py).
MOCK_REWORK_MARKER = "[退件意見]"


@dataclass(frozen=True)
class LLMUsage:
    """Token usage for a single LLM call (task 16 cost tracking)."""

    input_tokens: int
    output_tokens: int


class MissingAPIKeyError(RuntimeError):
    """Raised when a real-provider client is constructed without its API key.

    Carries the offending env-var name so callers can surface a clear,
    actionable message (Requirement 4.5).
    """

    def __init__(self, provider: str, api_key_env: str) -> None:
        self.provider = provider
        self.api_key_env = api_key_env
        super().__init__(
            f"LLM provider '{provider}' requires the environment variable "
            f"'{api_key_env}' to be set, but it is missing or empty. "
            f"Set '{api_key_env}' or use the 'mock' provider."
        )


class LLMCallError(RuntimeError):
    """A provider call finished but its result must not be used as agent output.

    ``retryable = False`` tells BaseAgent's retry loop not to try again: the same
    request would only be refused/truncated again (and billed again).
    """

    retryable = False


class LLMRefusalError(LLMCallError):
    """The model refused the request (``stop_reason == "refusal"``). Not retried."""


class LLMTruncatedError(LLMCallError):
    """Output hit ``max_tokens`` and was cut off (``stop_reason == "max_tokens"``)."""


class LLMUnexpectedStopError(LLMCallError):
    """Unexpected ``stop_reason`` (or no text at all) in a response."""


class LLMClient(ABC):
    """Abstract base class for all LLM clients.

    A client wraps a single provider/model and exposes one operation,
    :meth:`call`, which sends a prompt (with an optional system prompt) and
    returns the model's text response. After each call, :attr:`last_usage`
    holds the token usage of that call (task 16 cost tracking), or ``None`` if
    the call did not report usage.
    """

    # Provider name used for cost lookup; overridden by concrete clients.
    provider: str = "unknown"

    def __init__(self, model: str) -> None:
        self.model = model
        self.last_usage: LLMUsage | None = None

    @abstractmethod
    def call(self, prompt: str, system_prompt: str | None = None) -> str:
        """Send ``prompt`` to the model and return its text response.

        Args:
            prompt: The user/task prompt to send.
            system_prompt: Optional system prompt establishing the agent's role
                and behavior.

        Returns:
            The model's text response.
        """
        raise NotImplementedError

    @property
    def is_mock(self) -> bool:
        """Whether this client produces simulated (mock) output."""

        return False


# ---------------------------------------------------------------------------
# Mock client
# ---------------------------------------------------------------------------


class MockLLMClient(LLMClient):
    """Return role-specific, fixed-format fake output tagged ``[MOCK MODE]``.

    The role is derived from the :class:`AgentConfig` (``role`` / ``name``) so
    each pipeline station produces plausible, distinguishable output:

        * PM_agent    -> task analysis + execution plan
        * SWE_agent   -> a code block
        * QA_agent    -> a pass/fail test report
        * DevOps_agent-> a monitoring summary

    Unknown roles fall back to a generic acknowledgement. Every response begins
    with :data:`MOCK_MODE_MARKER` (Requirement 4.4).
    """

    provider = "mock"

    def __init__(
        self, model: str = "mock-model", role: str = "", name: str = "", role_key: str | None = None
    ) -> None:
        super().__init__(model)
        self.role = role
        self.name = name
        # Custom agents (plan D17) pass their kind ("make" / "check") so the answer does not
        # depend on guessing from the agent's name.
        self.role_key = role_key

    @property
    def is_mock(self) -> bool:
        return True

    def call(self, prompt: str, system_prompt: str | None = None) -> str:
        body = self._render_body(prompt)
        response = f"{MOCK_MODE_MARKER} {body}"
        # Estimate token usage so cost tracking works end-to-end in mock mode
        # (mock pricing is $0, but token counts remain meaningful for the UI).
        prompt_text = f"{system_prompt or ''}\n{prompt}"
        self.last_usage = LLMUsage(
            input_tokens=estimate_tokens(prompt_text),
            output_tokens=estimate_tokens(response),
        )
        return response

    def _role_key(self) -> str:
        """Normalize role/name into one of the known pipeline role keys."""

        if self.role_key is not None:
            return self.role_key
        haystack = f"{self.name} {self.role}".lower()
        if "pm" in haystack or "project manager" in haystack:
            return "pm"
        if "qa" in haystack or "quality" in haystack or "test" in haystack:
            return "qa"
        if "devops" in haystack or "ops" in haystack or "monitor" in haystack:
            return "devops"
        if "swe" in haystack or "engineer" in haystack or "developer" in haystack:
            return "swe"
        return "generic"

    def _render_body(self, prompt: str) -> str:
        """Build the role-specific fixed-format body for a mock response."""

        role_key = self._role_key()
        renderer = {
            "pm": self._render_pm,
            "swe": self._render_swe,
            "qa": self._render_qa,
            "devops": self._render_devops,
            "make": self._render_make,
            "check": self._render_check,
        }.get(role_key, self._render_generic)
        return renderer(prompt)

    @staticmethod
    def _render_pm(prompt: str) -> str:
        return (
            "PM task analysis\n"
            f"Task: {prompt}\n"
            "Analysis: Broke the task into deliverable work items.\n"
            "Execution plan:\n"
            "  1. Clarify requirements and acceptance criteria.\n"
            "  2. Hand off implementation work to SWE_agent.\n"
            "  3. Route the result to QA_agent for verification.\n"
            "Acceptance criteria: pipeline completes and QA reports pass."
        )

    @staticmethod
    def _render_swe(prompt: str) -> str:
        return (
            "SWE implementation\n"
            f"Task: {prompt}\n"
            "```python\n"
            "def solution():\n"
            '    """Mock implementation produced in mock mode."""\n'
            '    return "ok"\n'
            "```"
        )

    @staticmethod
    def _render_qa(prompt: str) -> str:
        return (
            "QA test report\n"
            f"Task: {prompt}\n"
            "Result: PASS\n"
            "Tests run: 3, Passed: 3, Failed: 0\n"
            "Notes: All mock checks passed."
        )

    @staticmethod
    def _render_devops(prompt: str) -> str:
        return (
            "DevOps monitoring report\n"
            "CPU: 12% | Memory: 34% | Disk: 41% | Services: healthy\n"
            "Status: OK\n"
            "Alerts: none"
        )

    @staticmethod
    def _render_make(prompt: str) -> str:
        return (
            "Work output\n"
            f"Task: {prompt}\n"
            "Deliverable: the requested work, produced in mock mode."
        )

    @staticmethod
    def _render_check(prompt: str) -> str:
        # MOCK_CHECK_FAIL_FIRST: reject until the prompt carries rejection feedback, so a Mock
        # run can show a rework deterministically (plan D17).
        passed = not (is_mock_check_fail_first() and MOCK_REWORK_MARKER not in prompt)
        return (
            "Check report\n"
            f"Task: {prompt}\n"
            + ("Findings: none.\nVERDICT: PASS" if passed else "Findings:\n- mock finding to fix\nVERDICT: FAIL")
        )

    @staticmethod
    def _render_generic(prompt: str) -> str:
        return f"Simulated response for: {prompt}"


# ---------------------------------------------------------------------------
# Real-provider client frameworks
# ---------------------------------------------------------------------------


class _RealProviderClient(LLMClient):
    """Shared scaffolding for real-provider clients.

    Validates the API key at construction time (Requirement 4.5) and reads the
    key from the environment. The concrete provider subclasses only implement
    :meth:`call`, where the actual SDK request belongs.
    """

    provider: str = ""

    def __init__(self, model: str, api_key_env: str) -> None:
        super().__init__(model)
        self.api_key_env = api_key_env
        api_key = os.environ.get(api_key_env)
        if not _is_set(api_key):
            # Requirement 4.5: clear message when the configured key is missing.
            raise MissingAPIKeyError(self.provider, api_key_env)
        self._api_key = api_key


class OpenAIClient(_RealProviderClient):
    """OpenAI provider client framework.

    The real SDK call is deferred; the placeholder below marks exactly where
    the ``openai`` request should be issued once the dependency is wired in.
    """

    provider = "openai"

    def call(self, prompt: str, system_prompt: str | None = None) -> str:
        # PLACEHOLDER: issue the real OpenAI chat/completions request here, e.g.
        #   from openai import OpenAI
        #   client = OpenAI(api_key=self._api_key)
        #   resp = client.chat.completions.create(model=self.model, messages=[...])
        #   return resp.choices[0].message.content
        raise NotImplementedError(
            "OpenAIClient real API call is not wired yet. "
            "Configure the OpenAI SDK or use the 'mock' provider."
        )


# The Claude models a project or an agent may be set to (plan D21). Nothing else is
# accepted by the API, so a typo can never silently turn into Mock. Every one of them
# has a price in ``usage_tracker.MODEL_PRICES`` (tests/test_lineup_model.py).
ANTHROPIC_MODELS: tuple[str, ...] = (
    "claude-opus-5-5",
    "claude-sonnet-5-5",
    "claude-haiku-5-5",
    "claude-fable-5-2",
)

# Models that accept ``output_config.effort`` and the server-side fallback beta
# (see specs/anthropic-provider/spec.md D3/D4). Anything else -- Haiku, unknown or
# future models -- goes through the plain ``messages.create`` with no extras, so
# an unsupported parameter never causes a 400. Model ids carry no date suffix.
SERVER_SIDE_FALLBACK_MODELS = frozenset(
    {"claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-2"}
)
ANTHROPIC_FALLBACK_BETA = "server-side-fallback-2026-07-01"
ANTHROPIC_MAX_TOKENS = 16000
ANTHROPIC_EFFORT = "medium"
# 4xx statuses that are worth retrying (request timeout, conflict, rate limit).
# Every other 4xx (400/401/403/404/413/422...) fails the same way each time.
ANTHROPIC_RETRYABLE_4XX = frozenset({408, 409, 429})


class AnthropicClient(_RealProviderClient):
    """Claude via the official ``anthropic`` SDK (non-streaming).

    Request shape (D3/D4): ``max_tokens=16000`` and a single user message; for
    models in :data:`SERVER_SIDE_FALLBACK_MODELS` also ``output_config={"effort":
    "medium"}``, the server-side-fallback beta and ``fallbacks="default"`` via
    ``client.beta.messages.create``. Never sends ``thinking``, ``temperature``,
    ``top_p``, ``top_k`` or ``budget_tokens``.

    The SDK client is built with ``max_retries=0``: retrying is BaseAgent's job,
    so attempts are not multiplied. The API key is read from the environment
    only and never logged or put into an exception message.

    Args:
        sdk_client: Optional pre-built SDK client (tests inject a fake so nothing
            touches the network). When omitted, ``anthropic.Anthropic`` is built
            here with a lazy import.
    """

    provider = "anthropic"

    def __init__(self, model: str, api_key_env: str, *, sdk_client=None) -> None:
        super().__init__(model, api_key_env)
        if sdk_client is None:
            import anthropic  # lazy: Mock mode and tests never load the SDK

            # No ``timeout``: keep the SDK default (10 min read/write, 5 s connect).
            sdk_client = anthropic.Anthropic(api_key=self._api_key, max_retries=0)
        self._sdk = sdk_client

    @property
    def supports_server_side_fallback(self) -> bool:
        return self.model in SERVER_SIDE_FALLBACK_MODELS

    def _build_request(self, prompt: str, system_prompt: str | None) -> dict:
        request: dict = {
            "model": self.model,
            "max_tokens": ANTHROPIC_MAX_TOKENS,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system_prompt:
            request["system"] = system_prompt
        if self.supports_server_side_fallback:
            request["output_config"] = {"effort": ANTHROPIC_EFFORT}
            request["betas"] = [ANTHROPIC_FALLBACK_BETA]
            request["fallbacks"] = "default"
        return request

    def call(self, prompt: str, system_prompt: str | None = None) -> str:
        # Clear first so a failed call never leaves the previous call's usage.
        self.last_usage = None
        request = self._build_request(prompt, system_prompt)
        try:
            if self.supports_server_side_fallback:
                response = self._sdk.beta.messages.create(**request)
            else:
                response = self._sdk.messages.create(**request)
        except Exception as exc:
            self._mark_non_retryable(exc)
            raise

        # Usage is billed even when the output is unusable, so record it first.
        self.last_usage = self._usage_from(response)
        logger.debug(
            "Anthropic call model=%s stop_reason=%s usage=%s",
            self.model,
            getattr(response, "stop_reason", None),
            self.last_usage,
        )

        stop_reason = getattr(response, "stop_reason", None)
        if stop_reason in ("end_turn", "stop_sequence"):
            return self._text_from(response)
        if stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None) or "unspecified"
            raise LLMRefusalError(
                f"Claude refused the request (model {self.model}, category: {category})."
            )
        if stop_reason == "max_tokens":
            raise LLMTruncatedError(
                f"Claude's output was truncated at max_tokens={ANTHROPIC_MAX_TOKENS} "
                f"(model {self.model}); shorten the task or split it up."
            )
        raise LLMUnexpectedStopError(
            f"Unexpected stop_reason {stop_reason!r} from Claude (model {self.model})."
        )

    @staticmethod
    def _mark_non_retryable(exc: Exception) -> None:
        """Tag permanent 4xx failures so BaseAgent does not retry them.

        The original exception object is re-raised unchanged (same type and
        message); only a ``retryable = False`` attribute is added. Rate limits
        (429), connection errors (no status), timeouts (408), conflicts (409) and
        5xx keep the default and stay retryable.
        """

        status = getattr(exc, "status_code", None)
        if (
            isinstance(status, int)
            and 400 <= status < 500
            and status not in ANTHROPIC_RETRYABLE_4XX
        ):
            exc.retryable = False

    @staticmethod
    def _text_from(response) -> str:
        # The first block may be a thinking block: only join the text blocks.
        parts = [
            block.text
            for block in (getattr(response, "content", None) or [])
            if getattr(block, "type", None) == "text"
        ]
        text = "".join(parts)
        if not text:
            raise LLMUnexpectedStopError("Claude returned no text content.")
        return text

    @staticmethod
    def _usage_from(response) -> LLMUsage | None:
        usage = getattr(response, "usage", None)
        if usage is None:
            return None
        input_tokens = (
            (getattr(usage, "input_tokens", 0) or 0)
            + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
            + (getattr(usage, "cache_read_input_tokens", 0) or 0)
        )
        return LLMUsage(
            input_tokens=input_tokens,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
        )


# Registry of real-provider clients, keyed by provider name.
_REAL_CLIENTS: dict[str, type[_RealProviderClient]] = {
    "openai": OpenAIClient,
    "anthropic": AnthropicClient,
}


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_llm_client(agent_config: AgentConfig, mock_role_key: str | None = None) -> LLMClient:
    """Return the appropriate :class:`LLMClient` for an agent config.

    Selection rules:
        * ``FORCE_MOCK`` is set (``1``/``true``/``yes``) -> :class:`MockLLMClient`,
          before any other rule and without looking at API keys.
        * ``llm_provider == "mock"`` -> :class:`MockLLMClient` (explicit opt-in).
        * The referenced ``api_key_env`` is not set -> :class:`MockLLMClient`,
          so the pipeline still runs with simulated output (Requirement 4.3).
        * A provider without a real-client implementation (e.g. ``gemini``)
          -> :class:`MockLLMClient` as a safe fallback.
        * Otherwise -> the matching real-provider client
          (:class:`OpenAIClient` / :class:`AnthropicClient`).

    Args:
        agent_config: The agent's parsed configuration.
        mock_role_key: When a Mock is returned, the renderer it uses (custom agents pass
            ``"make"`` / ``"check"``); ``None`` keeps guessing from the name and role.

    Returns:
        A ready-to-use LLM client. Never raises for a missing key: it degrades
        to mock mode instead.
    """

    provider = (agent_config.llm_provider or "").lower()

    # FORCE_MOCK wins over everything, before any API-key check: no real client is
    # ever constructed, whatever the agent config or the environment says.
    if is_force_mock():
        logger.info(
            "FORCE_MOCK is set; agent '%s' (provider '%s') uses MockLLMClient.",
            agent_config.name,
            provider,
        )
        return _mock_for(agent_config, mock_role_key)

    if provider == "mock":
        logger.info(
            "Agent '%s' uses the mock provider; returning MockLLMClient.",
            agent_config.name,
        )
        return _mock_for(agent_config, mock_role_key)

    api_key_env = agent_config.api_key_env
    if not _is_set(os.environ.get(api_key_env)):
        logger.info(
            "API key env '%s' for agent '%s' (provider '%s') is not set; "
            "falling back to mock mode.",
            api_key_env,
            agent_config.name,
            provider,
        )
        return _mock_for(agent_config, mock_role_key)

    client_cls = _REAL_CLIENTS.get(provider)
    if client_cls is None:
        logger.warning(
            "No real client implemented for provider '%s' (agent '%s'); "
            "falling back to mock mode.",
            provider,
            agent_config.name,
        )
        return _mock_for(agent_config, mock_role_key)

    return client_cls(model=agent_config.llm_model, api_key_env=api_key_env)


def _mock_for(agent_config: AgentConfig, role_key: str | None = None) -> MockLLMClient:
    """Build a :class:`MockLLMClient` seeded from an agent config."""

    return MockLLMClient(
        model=agent_config.llm_model or "mock-model",
        role=agent_config.role,
        name=agent_config.name,
        role_key=role_key,
    )
