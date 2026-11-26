"""Task 26c: real LLMs are told the pipeline cannot run code; Mock prompts are unchanged."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from agents.base_agent import NO_EXECUTION_NOTE
from agents.custom_agent import CHECK_SYSTEM_SUFFIX, CustomAgent, custom_agent_config
from agents.qa_agent import DEFAULT_QA_SYSTEM_PROMPT, QAAgent
from agents.swe_agent import DEFAULT_SWE_SYSTEM_PROMPT, SWEAgent
from core.llm_client import AnthropicClient, MockLLMClient
from models.agent import AgentConfig
from models.lineup import Seat
from models.task import Priority, Status, Task

SECURITY = Seat("security", "check", False, "資安", "找漏洞", "antenna")
DESIGNER = Seat("designer", "make", False, "設計", "畫圖", "pencil")


class FakeSDK:
    """Records every request; nothing touches the network."""

    def __init__(self, text: str = "Result: PASS") -> None:
        self.calls: list[dict] = []
        self._text = text
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **request):
        self.calls.append(request)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=self._text)],
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=10, output_tokens=5,
                                  cache_creation_input_tokens=None, cache_read_input_tokens=None),
            stop_details=None,
        )


class RecordingMock(MockLLMClient):
    def call(self, prompt, system_prompt=None):
        self.last_system_prompt = system_prompt
        return super().call(prompt, system_prompt)


def _task() -> Task:
    now = datetime(2026, 1, 1, 12, 0, 0)
    return Task(id="task-001", title="Add a Go handler", description="Write it in Go.",
                priority=Priority.HIGH, status=Status.PENDING, created_at=now, updated_at=now)


def _config(name: str, system_prompt: str) -> AgentConfig:
    return AgentConfig(name=name, role=name, description="d", llm_provider="anthropic",
                       llm_model="claude-sonnet-5-5", api_key_env="ANTHROPIC_API_KEY",
                       max_retries=1, system_prompt=system_prompt)


@pytest.fixture
def fake_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-FAKE-test-key")
    monkeypatch.delenv("FORCE_MOCK", raising=False)


def _anthropic(sdk) -> AnthropicClient:
    return AnthropicClient("claude-sonnet-5-5", "ANTHROPIC_API_KEY", sdk_client=sdk)


def test_note_is_short_and_says_nothing_can_run():
    assert "No agent can run code" in NO_EXECUTION_NOTE["en"]
    assert "沒有任何 agent 能執行程式" in NO_EXECUTION_NOTE["zh"]
    assert all(len(note) < 400 for note in NO_EXECUTION_NOTE.values())


@pytest.mark.parametrize("agent_cls,name", [(QAAgent, "QA_agent"), (SWEAgent, "SWE_agent")])
def test_built_in_agents_tell_a_real_llm(fake_key, tmp_path, agent_cls, name):
    sdk = FakeSDK()
    agent = agent_cls(_config(name, f"You are {name}."), llm_client=_anthropic(sdk), retry_wait_multiplier=0)
    assert agent.run(_task(), {}, tmp_path).success
    (request,) = sdk.calls
    assert request["system"] == f"You are {name}.\n\n" + NO_EXECUTION_NOTE["en"]
    assert NO_EXECUTION_NOTE["en"] not in request["messages"][0]["content"]


def test_qa_default_system_prompt_also_gets_the_note(fake_key, tmp_path):
    sdk = FakeSDK()
    agent = QAAgent(_config("QA_agent", ""), llm_client=_anthropic(sdk), retry_wait_multiplier=0)
    agent.run(_task(), {}, tmp_path)
    assert sdk.calls[0]["system"] == DEFAULT_QA_SYSTEM_PROMPT + "\n\n" + NO_EXECUTION_NOTE["en"]


def test_swe_default_system_prompt_also_gets_the_note(fake_key, tmp_path):
    sdk = FakeSDK("package main")
    agent = SWEAgent(_config("SWE_agent", ""), llm_client=_anthropic(sdk), retry_wait_multiplier=0)
    assert agent.run(_task(), {}, tmp_path).success
    (request,) = sdk.calls
    assert request["system"] == DEFAULT_SWE_SYSTEM_PROMPT + "\n\n" + NO_EXECUTION_NOTE["en"]
    assert NO_EXECUTION_NOTE["en"] not in request["messages"][0]["content"]


def test_custom_check_agent_tells_a_real_llm(fake_key, tmp_path):
    sdk = FakeSDK("VERDICT: PASS")
    config = custom_agent_config(SECURITY, "你是資安審查員", llm_provider="anthropic", llm_model="claude-sonnet-5-5")
    agent = CustomAgent(SECURITY, config, llm_client=_anthropic(sdk), retry_wait_multiplier=0)
    assert agent.run(_task(), {"upstream": [("swe", "package main")]}, tmp_path).success
    assert sdk.calls[0]["system"] == "你是資安審查員\n\n" + CHECK_SYSTEM_SUFFIX + "\n\n" + NO_EXECUTION_NOTE["zh"]


def test_custom_make_agent_tells_a_real_llm(fake_key, tmp_path):
    sdk = FakeSDK("done")
    config = custom_agent_config(DESIGNER, "你是設計師", llm_provider="anthropic", llm_model="claude-sonnet-5-5")
    CustomAgent(DESIGNER, config, llm_client=_anthropic(sdk), retry_wait_multiplier=0).run(_task(), {}, tmp_path)
    assert sdk.calls[0]["system"] == "你是設計師\n\n" + NO_EXECUTION_NOTE["zh"]


@pytest.mark.parametrize("agent_cls,name", [(QAAgent, "QA_agent"), (SWEAgent, "SWE_agent")])
def test_mock_built_in_agents_get_no_note(tmp_path, agent_cls, name):
    client = RecordingMock(role=name, name=name)
    agent = agent_cls(_config(name, f"You are {name}."), llm_client=client, retry_wait_multiplier=0)
    agent.run(_task(), {}, tmp_path)
    assert client.last_system_prompt == f"You are {name}."


def test_mock_check_agent_gets_no_note(tmp_path):
    client = RecordingMock(role_key="check")
    agent = CustomAgent(SECURITY, custom_agent_config(SECURITY, "p"), llm_client=client, retry_wait_multiplier=0)
    agent.run(_task(), {}, tmp_path)
    assert client.last_system_prompt == "p\n\n" + CHECK_SYSTEM_SUFFIX


def test_mock_make_agent_gets_no_note(tmp_path):
    client = RecordingMock(role_key="make")
    agent = CustomAgent(DESIGNER, custom_agent_config(DESIGNER, "p"), llm_client=client, retry_wait_multiplier=0)
    agent.run(_task(), {}, tmp_path)
    assert client.last_system_prompt == "p"
