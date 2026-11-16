"""The smoke script's logic, tested in-process with no real SDK and no network.

The script is never executed as a program here: ``main`` is called directly, with
the key absent (exit 2 before anything is built) or with an injected fake SDK.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_anthropic_client import FakeSDK, message, text, thinking, usage

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "anthropic_smoke.py"
FAKE_KEY = "sk-ant-FAKE-smoke-key"


@pytest.fixture
def smoke():
    spec = importlib.util.spec_from_file_location("anthropic_smoke_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _forbidden_factory(api_key):
    raise AssertionError("the SDK must not be built without a key")


def test_no_key_exits_2_with_a_clear_message_and_builds_nothing(smoke, monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    code = smoke.main([], sdk_factory=_forbidden_factory)

    captured = capsys.readouterr()
    assert code == 2
    assert "ANTHROPIC_API_KEY" in captured.err and "Nothing was sent" in captured.err
    # It must say plainly that .env is not read, and how to load it by hand.
    assert "does not read .env" in captured.err
    assert "set -a; source ../.env; set +a" in captured.err
    assert "read -rs ANTHROPIC_API_KEY" in captured.err  # history-safe way to set it
    assert captured.out == ""


def test_blank_key_counts_as_missing(smoke, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")

    assert smoke.main([], sdk_factory=_forbidden_factory) == 2


def test_success_prints_reply_stop_reason_model_usage_and_cost(smoke, monkeypatch, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    long_reply = "Hello! " + "x" * 400
    response = message([thinking(), text(long_reply)], usage_=usage(1000, 500))
    response.model = "claude-opus-5-5"
    sdk = FakeSDK(response)

    code = smoke.main([], sdk_factory=lambda key: sdk)

    out = capsys.readouterr().out
    assert code == 0
    assert repr(long_reply[:200]) in out and long_reply[:201] not in out  # first 200 chars only
    assert "stop_reason: end_turn" in out
    assert "model: claude-opus-5-5" in out
    assert "input_tokens=1000 output_tokens=500" in out
    assert "estimated cost: $0.014000" in out  # 1k*0.004 + 0.5k*0.020
    assert FAKE_KEY not in out
    (endpoint, kwargs), = sdk.calls
    assert endpoint == "beta" and kwargs["model"] == "claude-opus-5-5"


def test_model_flag_selects_another_model_and_endpoint(smoke, monkeypatch, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    response = message(usage_=usage(1000, 1000))
    response.model = "claude-haiku-5-5"
    sdk = FakeSDK(response)

    code = smoke.main(["--model", "claude-haiku-5-5"], sdk_factory=lambda key: sdk)

    assert code == 0
    assert sdk.calls[0][0] == "plain"  # Haiku has no server-side fallback
    assert "estimated cost: $0.000600" in capsys.readouterr().out


def test_failed_or_unusable_call_exits_1_without_leaking_the_key(smoke, monkeypatch, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    refusal = message([], stop_reason="refusal", stop_details=SimpleNamespace(category="cyber"))

    code = smoke.main([], sdk_factory=lambda key: FakeSDK(refusal))

    captured = capsys.readouterr()
    assert code == 1
    assert "LLMRefusalError" in captured.err and "cyber" in captured.err
    assert FAKE_KEY not in captured.err + captured.out
