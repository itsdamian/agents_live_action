"""Mock pacing: ``MOCK_STEP_DELAY_MS`` (Task 26b, plan D17 "Mock 節奏").

Every Mock LLM call waits that long before returning, so a real backend on Mock shows the
ticket moving station by station. Default 0; invalid values count as 0; the wait happens in
the worker thread the stations already run in, so the event loop keeps going; real clients
never wait.
"""

from __future__ import annotations

import asyncio
import logging
import time
from types import SimpleNamespace

import pytest

import config
from core import llm_client as llm_client_module
from core.llm_client import AnthropicClient, MockLLMClient
from models.task import Status
from tests.pipeline_harness import make_orchestrator, make_project, make_task

ENV = "MOCK_STEP_DELAY_MS"
ENGINES = ["legacy", "langgraph"]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    config._warned_mock_step_delay.clear()


def _record_sleeps(monkeypatch) -> list[float]:
    sleeps: list[float] = []
    real_sleep = time.sleep

    def recording_sleep(seconds):
        sleeps.append(seconds)
        real_sleep(seconds)

    monkeypatch.setattr(llm_client_module.time, "sleep", recording_sleep)
    return sleeps


# -- parsing ----------------------------------------------------------------------


def test_unset_means_zero():
    assert config.get_mock_step_delay_ms() == 0


@pytest.mark.parametrize("value, expected", [("0", 0), ("50", 50), (" 1500 ", 1500)])
def test_valid_values(monkeypatch, value, expected):
    monkeypatch.setenv(ENV, value)
    assert config.get_mock_step_delay_ms() == expected


@pytest.mark.parametrize("value", ["-1", "-1500", "abc", "1.5", "1500ms"])
def test_invalid_values_count_as_zero_with_one_warning(monkeypatch, caplog, value):
    monkeypatch.setenv(ENV, value)
    with caplog.at_level(logging.WARNING, logger="config"):
        assert config.get_mock_step_delay_ms() == 0
        assert config.get_mock_step_delay_ms() == 0
    warnings = [r for r in caplog.records if ENV in r.getMessage()]
    assert len(warnings) == 1


# -- the Mock client ----------------------------------------------------------------


def test_default_mock_call_does_not_wait(monkeypatch):
    sleeps = _record_sleeps(monkeypatch)
    out = MockLLMClient(role="Project Manager", name="PM_agent").call("do it")
    assert out.startswith("[MOCK MODE]")
    assert sleeps == []


@pytest.mark.parametrize("value", ["-5", "nope"])
def test_invalid_value_mock_call_does_not_wait(monkeypatch, value):
    monkeypatch.setenv(ENV, value)
    sleeps = _record_sleeps(monkeypatch)
    MockLLMClient(role="Project Manager", name="PM_agent").call("do it")
    assert sleeps == []


def test_mock_call_waits_at_least_the_delay(monkeypatch):
    monkeypatch.setenv(ENV, "50")
    client = MockLLMClient(role="Software Engineer", name="SWE_agent")
    expected = MockLLMClient(role="Software Engineer", name="SWE_agent")
    start = time.perf_counter()
    out = client.call("do it")
    elapsed = time.perf_counter() - start
    assert elapsed >= 0.05
    # Output and usage are unchanged by the wait.
    monkeypatch.setenv(ENV, "0")
    assert out == expected.call("do it")
    assert client.last_usage == expected.last_usage


def test_custom_agent_mock_kinds_wait_too(monkeypatch):
    monkeypatch.setenv(ENV, "50")
    sleeps = _record_sleeps(monkeypatch)
    MockLLMClient(role_key="make").call("x")
    MockLLMClient(role_key="check").call("x")
    assert sleeps == [0.05, 0.05]


def test_the_wait_does_not_block_the_event_loop(monkeypatch):
    """Stations run the Mock call through asyncio.to_thread; the loop keeps ticking meanwhile."""

    monkeypatch.setenv(ENV, "200")
    client = MockLLMClient(role="Quality Assurance", name="QA_agent")

    async def scenario():
        ticks = 0
        done = asyncio.Event()

        async def ticker():
            nonlocal ticks
            while not done.is_set():
                ticks += 1
                await asyncio.sleep(0.01)

        ticking = asyncio.create_task(ticker())
        start = time.perf_counter()
        await asyncio.to_thread(client.call, "check it")
        elapsed = time.perf_counter() - start
        done.set()
        await ticking
        return ticks, elapsed

    ticks, elapsed = asyncio.run(scenario())
    assert elapsed >= 0.2
    assert ticks >= 5  # ~20 possible in 200 ms; a blocked loop would give 1


# -- real client ------------------------------------------------------------------


def test_real_client_never_waits(monkeypatch):
    monkeypatch.setenv(ENV, "5000")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-FAKE-test-key-0123456789")
    sleeps = _record_sleeps(monkeypatch)
    response = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="hello")],
        stop_reason="end_turn",
        usage=SimpleNamespace(
            input_tokens=10, output_tokens=5,
            cache_creation_input_tokens=None, cache_read_input_tokens=None,
        ),
        stop_details=None,
    )
    create = lambda **kwargs: response  # noqa: E731
    sdk = SimpleNamespace(
        messages=SimpleNamespace(create=create), beta=SimpleNamespace(messages=SimpleNamespace(create=create))
    )
    client = AnthropicClient(model="claude-haiku-5-5", api_key_env="ANTHROPIC_API_KEY", sdk_client=sdk)

    start = time.perf_counter()
    assert client.call("hi") == "hello"
    assert time.perf_counter() - start < 1
    assert sleeps == []


# -- both engines, end to end ----------------------------------------------------------


@pytest.mark.parametrize("engine", ENGINES)
def test_pipeline_stations_are_paced_and_loop_stays_free(tmp_path, monkeypatch, engine):
    monkeypatch.setenv(ENV, "50")
    task = make_task()
    project = make_project(tmp_path, [task])
    orch, events = make_orchestrator(project, engine=engine)

    async def scenario():
        ticks = 0
        done = asyncio.Event()

        async def ticker():
            nonlocal ticks
            while not done.is_set():
                ticks += 1
                await asyncio.sleep(0.005)

        ticking = asyncio.create_task(ticker())
        start = time.perf_counter()
        result = await orch.process_task(task)
        elapsed = time.perf_counter() - start
        done.set()
        await ticking
        return result, ticks, elapsed

    result, ticks, elapsed = asyncio.run(scenario())
    assert result.status is Status.DONE
    assert elapsed >= 3 * 0.05  # PM, SWE, QA each waited
    assert ticks >= 10

    # The ordinary station order is unchanged.
    working = [d["station"] for e, d in events if e == "station:update" and d["status"] == "working"]
    assert working == ["pm", "swe", "qa"]
