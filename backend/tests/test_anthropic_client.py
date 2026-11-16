"""AnthropicClient with an injected fake SDK client (A3). Fully offline.

No test here builds a real ``anthropic.Anthropic`` that could talk to the
network: either a fake is injected via ``sdk_client=`` or ``anthropic.Anthropic``
itself is replaced by a recorder.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import core.llm_client as llm_client
from core.llm_client import (
    ANTHROPIC_FALLBACK_BETA,
    SERVER_SIDE_FALLBACK_MODELS,
    AnthropicClient,
    LLMCallError,
    LLMRefusalError,
    LLMTruncatedError,
    LLMUnexpectedStopError,
    LLMUsage,
    get_llm_client,
)
from models.agent import AgentConfig

FAKE_KEY = "sk-ant-FAKE-test-key-0123456789"
BACKEND_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _fake_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)


# -- fakes ------------------------------------------------------------------------


def text(value):
    return SimpleNamespace(type="text", text=value)


def thinking(value="pondering"):
    return SimpleNamespace(type="thinking", thinking=value)


def usage(input_tokens=10, output_tokens=5, cache_creation=None, cache_read=None):
    return SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_input_tokens=cache_creation,
        cache_read_input_tokens=cache_read,
    )


def message(content=None, stop_reason="end_turn", usage_=None, stop_details=None):
    return SimpleNamespace(
        content=[text("hello")] if content is None else content,
        stop_reason=stop_reason,
        usage=usage() if usage_ is None else usage_,
        stop_details=stop_details,
    )


class FakeSDK:
    """Records calls to both endpoints and answers with a configured result."""

    def __init__(self, result=None):
        self.result = message() if result is None else result
        self.calls: list[tuple[str, dict]] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._beta_create))
        self.messages = SimpleNamespace(create=self._plain_create)

    def _answer(self):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def _beta_create(self, **kwargs):
        self.calls.append(("beta", kwargs))
        return self._answer()

    def _plain_create(self, **kwargs):
        self.calls.append(("plain", kwargs))
        return self._answer()


def make_client(model="claude-opus-5-5", result=None):
    sdk = FakeSDK(result)
    return AnthropicClient(model=model, api_key_env="ANTHROPIC_API_KEY", sdk_client=sdk), sdk


# -- AC2: exact request shape -----------------------------------------------------

FORBIDDEN = {"thinking", "temperature", "top_p", "top_k", "budget_tokens"}


@pytest.mark.parametrize("model", ["claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-2"])
def test_fallback_models_use_beta_endpoint_with_full_request(model):
    client, sdk = make_client(model)

    client.call("the prompt", "the system")

    (endpoint, kwargs), = sdk.calls
    assert endpoint == "beta"
    assert kwargs == {
        "model": model,
        "max_tokens": 16000,
        "system": "the system",
        "messages": [{"role": "user", "content": "the prompt"}],
        "output_config": {"effort": "medium"},
        "betas": ["server-side-fallback-2026-07-01"],
        "fallbacks": "default",
    }
    assert not FORBIDDEN & set(kwargs)


@pytest.mark.parametrize("model", ["claude-haiku-5-5", "claude-some-future-model", "gpt-4o"])
def test_other_models_use_plain_endpoint_without_extras(model):
    client, sdk = make_client(model)

    client.call("p", "s")

    (endpoint, kwargs), = sdk.calls
    assert endpoint == "plain"  # never the beta endpoint
    assert kwargs == {
        "model": model,
        "max_tokens": 16000,
        "system": "s",
        "messages": [{"role": "user", "content": "p"}],
    }
    assert not FORBIDDEN & set(kwargs)
    assert not {"betas", "fallbacks", "output_config"} & set(kwargs)


@pytest.mark.parametrize("system", [None, ""])
def test_system_is_omitted_when_absent(system):
    client, sdk = make_client()

    client.call("p", system)

    assert "system" not in sdk.calls[0][1]


def test_capability_table_contents():
    assert {"claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-2"} <= SERVER_SIDE_FALLBACK_MODELS
    assert "claude-haiku-5-5" not in SERVER_SIDE_FALLBACK_MODELS
    assert ANTHROPIC_FALLBACK_BETA == "server-side-fallback-2026-07-01"


# -- AC3 / D5: text extraction ------------------------------------------------------


def test_first_block_may_be_thinking():
    client, _ = make_client(result=message([thinking(), text("the answer")]))

    assert client.call("p") == "the answer"


def test_text_blocks_are_concatenated_in_order_and_others_skipped():
    blocks = [thinking(), text("one "), SimpleNamespace(type="fallback"), text("two"), thinking("x"), text("!")]
    client, _ = make_client(result=message(blocks))

    assert client.call("p") == "one two!"


@pytest.mark.parametrize("content", [[], [thinking()], [SimpleNamespace(type="tool_use")], [text("")]])
def test_no_text_content_is_an_error_not_an_empty_result(content):
    client, _ = make_client(result=message(content))

    with pytest.raises(LLMUnexpectedStopError, match="no text"):
        client.call("p")


def test_content_none_is_an_error():
    result = message()
    result.content = None
    client, _ = make_client(result=result)

    with pytest.raises(LLMUnexpectedStopError):
        client.call("p")


def test_works_with_real_sdk_response_types():
    """Field names match the installed SDK (built offline, no client involved)."""

    from anthropic.types.beta import BetaMessage

    result = BetaMessage.model_validate(
        {
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": [
                {"type": "thinking", "thinking": "hmm", "signature": "sig"},
                {"type": "text", "text": "Hello"},
            ],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5,
                      "cache_creation_input_tokens": 2, "cache_read_input_tokens": 3},
        }
    )
    client, _ = make_client(result=result)

    assert client.call("p") == "Hello"
    assert client.last_usage == LLMUsage(input_tokens=15, output_tokens=5)

    refusal = BetaMessage.model_validate(
        {
            "id": "msg_2", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": [], "stop_reason": "refusal",
            "stop_details": {"type": "refusal", "category": "cyber", "explanation": None},
            "usage": {"input_tokens": 1, "output_tokens": 0},
        }
    )
    client, _ = make_client(result=refusal)
    with pytest.raises(LLMRefusalError, match="cyber"):
        client.call("p")


# -- AC3 / D6: stop_reason -----------------------------------------------------------


@pytest.mark.parametrize("stop_reason", ["end_turn", "stop_sequence"])
def test_normal_stop_reasons_return_text(stop_reason):
    client, _ = make_client(result=message(stop_reason=stop_reason))

    assert client.call("p") == "hello"


def test_refusal_raises_with_category():
    details = SimpleNamespace(category="bio")
    client, _ = make_client(result=message(stop_reason="refusal", stop_details=details))

    with pytest.raises(LLMRefusalError) as excinfo:
        client.call("p")

    assert "bio" in str(excinfo.value)
    assert isinstance(excinfo.value, LLMCallError)


@pytest.mark.parametrize("details", [None, SimpleNamespace(category=None)])
def test_refusal_without_details_or_category_still_raises(details):
    client, _ = make_client(result=message(stop_reason="refusal", stop_details=details))

    with pytest.raises(LLMRefusalError, match="unspecified"):
        client.call("p")


def test_max_tokens_raises_truncated_error():
    client, _ = make_client(result=message([text("cut off")], stop_reason="max_tokens"))

    with pytest.raises(LLMTruncatedError, match="truncated") as excinfo:
        client.call("p")

    assert isinstance(excinfo.value, LLMCallError)


@pytest.mark.parametrize(
    "stop_reason", ["tool_use", "pause_turn", "model_context_window_exceeded", "compaction", None, "???"]
)
def test_other_stop_reasons_raise_unexpected(stop_reason):
    client, _ = make_client(result=message(stop_reason=stop_reason))

    with pytest.raises(LLMUnexpectedStopError) as excinfo:
        client.call("p")

    assert isinstance(excinfo.value, LLMCallError)


def test_error_family_shares_one_base():
    for cls in (LLMRefusalError, LLMTruncatedError, LLMUnexpectedStopError):
        assert issubclass(cls, LLMCallError)
    assert not issubclass(LLMRefusalError, LLMTruncatedError)


# -- AC3 / D7: usage ------------------------------------------------------------------


def test_usage_sums_cache_tokens_into_input():
    client, _ = make_client(
        result=message(usage_=usage(input_tokens=100, output_tokens=40, cache_creation=7, cache_read=3))
    )

    client.call("p")

    assert client.last_usage == LLMUsage(input_tokens=110, output_tokens=40)


def test_usage_without_cache_fields():
    client, _ = make_client(result=message(usage_=usage(input_tokens=12, output_tokens=3)))

    client.call("p")

    assert client.last_usage == LLMUsage(input_tokens=12, output_tokens=3)


def test_usage_none_leaves_last_usage_none():
    result = message()
    result.usage = None
    client, _ = make_client(result=result)

    assert client.call("p") == "hello"
    assert client.last_usage is None


def test_usage_is_recorded_even_when_output_is_unusable():
    client, _ = make_client(result=message(stop_reason="max_tokens", usage_=usage(8, 16000)))

    with pytest.raises(LLMTruncatedError):
        client.call("p")

    assert client.last_usage == LLMUsage(input_tokens=8, output_tokens=16000)


def test_failed_call_does_not_leave_previous_usage_behind():
    client, sdk = make_client()
    client.call("p")
    assert client.last_usage is not None

    sdk.result = RuntimeError("network down")
    with pytest.raises(RuntimeError):
        client.call("p")

    assert client.last_usage is None


# -- D9 / D10: construction ------------------------------------------------------------


def test_default_sdk_client_is_built_with_no_retries_and_default_timeout(monkeypatch):
    import anthropic

    built: list[dict] = []

    class RecordingAnthropic:
        def __init__(self, **kwargs):
            built.append(kwargs)

    monkeypatch.setattr(anthropic, "Anthropic", RecordingAnthropic)

    AnthropicClient(model="claude-opus-5-5", api_key_env="ANTHROPIC_API_KEY")

    # No timeout kwarg: the SDK default (read/write 600 s, connect 5 s) applies.
    assert built == [{"api_key": FAKE_KEY, "max_retries": 0}]
    assert "timeout" not in built[0]


def test_factory_builds_anthropic_client(monkeypatch):
    import anthropic

    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: FakeSDK())
    config = AgentConfig(
        name="PM_agent", role="Project Manager", description="d", llm_provider="anthropic",
        llm_model="claude-opus-5-5", api_key_env="ANTHROPIC_API_KEY", max_retries=1,
        system_prompt="s",
    )

    client = get_llm_client(config)

    assert type(client) is AnthropicClient
    assert client.call("p") == "hello"


def test_sdk_is_not_imported_unless_a_client_needs_it():
    code = (
        "import sys, os\n"
        "from core.llm_client import AnthropicClient, MockLLMClient, get_llm_client\n"
        "MockLLMClient(role='r', name='n').call('x')\n"
        "assert 'anthropic' not in sys.modules, 'mock path imported the SDK'\n"
        "AnthropicClient('claude-opus-5-5', 'ANTHROPIC_API_KEY', sdk_client=object())\n"
        "assert 'anthropic' not in sys.modules, 'injected client imported the SDK'\n"
        "print('ok')\n"
    )
    env = dict(os.environ, ANTHROPIC_API_KEY=FAKE_KEY)
    out = subprocess.run([sys.executable, "-c", code], cwd=BACKEND_DIR, env=env,
                         capture_output=True, text=True)

    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "ok"


def test_import_anthropic_only_inside_constructor():
    source = Path(llm_client.__file__).read_text(encoding="utf-8")
    lines = [i for i, line in enumerate(source.splitlines(), 1) if "import anthropic" in line]

    assert len(lines) == 1
    assert source.splitlines()[lines[0] - 1].startswith(" " * 12)  # inside __init__'s if-block


# -- AC6 / D12: the key never leaks ------------------------------------------------------


class _SdkError(Exception):
    pass


@pytest.mark.parametrize(
    "result",
    [
        message(stop_reason="refusal", stop_details=SimpleNamespace(category="cyber")),
        message(stop_reason="max_tokens"),
        message(stop_reason="tool_use"),
        message([]),
        message(),  # success path
        _SdkError("upstream failure"),
    ],
    ids=["refusal", "max_tokens", "unexpected", "no-text", "success", "sdk-error"],
)
def test_api_key_never_appears_in_logs_or_exception_text(caplog, result):
    caplog.set_level(logging.DEBUG)
    client, _ = make_client(result=result)

    try:
        client.call("a prompt", "a system prompt")
    except Exception as exc:  # noqa: BLE001
        assert FAKE_KEY not in str(exc)
        assert FAKE_KEY not in repr(exc)

    assert FAKE_KEY not in caplog.text
    assert FAKE_KEY not in repr(client)
