"""End-to-end: three agents on AnthropicClient with injected fake SDK clients (A6).

Runs on both pipeline engines. Nothing touches the network: every agent gets its
own scripted fake SDK, so we can check what the pipeline does with Claude-shaped
responses (thinking blocks, cache tokens, refusals) and how usage is priced.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agents.pm_agent import PMAgent
from agents.qa_agent import QAAgent
from agents.swe_agent import SWEAgent
from core.llm_client import AnthropicClient
from core.pipeline import PipelineOrchestrator
from core.usage_tracker import UsageTracker
from models.agent import AgentConfig
from models.task import Status
from tests.pipeline_harness import make_project, make_task
from tests.test_anthropic_client import FakeSDK, message, text, thinking, usage

ENGINES = ["legacy", "langgraph"]
MODEL = "claude-opus-5-5"
# claude-opus-5-5 list price per 1k tokens (spec D11): input 0.004, output 0.020.
OPUS_IN, OPUS_OUT = 0.004, 0.020


class ScriptedSDK(FakeSDK):
    """FakeSDK answering from a queue: one result per call (exceptions raised)."""

    def __init__(self, results):
        super().__init__()
        self.queue = list(results)

    def _answer(self):
        result = self.queue.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


PM_TEXT = "PM plan: 1. build 2. test"
SWE_TEXT = "Implementation:\n```python\ndef login():\n    return True\n```"
QA_PASS = "QA report\nResult: PASS\nTests run: 3, Passed: 3, Failed: 0"
QA_FAIL = "QA report\nResult: FAIL\nTests run: 3, Passed: 1, Failed: 2"


def _reply(body, input_tokens, output_tokens, **cache):
    # A thinking block comes first, like the newest models return.
    return message([thinking(), text(body)], usage_=usage(input_tokens, output_tokens, **cache))


@pytest.fixture(autouse=True)
def _fake_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-FAKE-e2e-key")


def _build(tmp_path, engine, pm_results, swe_results, qa_results, task_kwargs=None):
    task = make_task(**(task_kwargs or {}))
    project = make_project(tmp_path, [task])
    sdks = SimpleNamespace(
        pm=ScriptedSDK(pm_results), swe=ScriptedSDK(swe_results), qa=ScriptedSDK(qa_results)
    )

    def agent(cls, name, role, sdk):
        config = AgentConfig(
            name=name, role=role, description=role, llm_provider="anthropic", llm_model=MODEL,
            api_key_env="ANTHROPIC_API_KEY", max_retries=3, system_prompt=f"You are {name}.",
        )
        client = AnthropicClient(MODEL, "ANTHROPIC_API_KEY", sdk_client=sdk)
        return cls(config, llm_client=client, retry_wait_multiplier=0)

    events: list[tuple[str, dict]] = []
    tracker = UsageTracker()
    orch = PipelineOrchestrator(
        project_id=project.name,
        project_path=project,
        pm_agent=agent(PMAgent, "PM_agent", "Project Manager", sdks.pm),
        swe_agent=agent(SWEAgent, "SWE_agent", "Software Engineer", sdks.swe),
        qa_agent=agent(QAAgent, "QA_agent", "Quality Assurance", sdks.qa),
        on_event=lambda event, data: events.append((event, data)),
        usage_tracker=tracker,
        engine=engine,
    )
    return task, project, orch, events, sdks, tracker


@pytest.mark.parametrize("engine", ENGINES)
def test_task_completes_and_usage_is_priced(tmp_path, engine):
    task, project, orch, events, sdks, tracker = _build(
        tmp_path,
        engine,
        pm_results=[_reply(PM_TEXT, 100, 50, cache_creation=10, cache_read=5)],
        swe_results=[_reply(SWE_TEXT, 200, 80)],
        qa_results=[_reply(QA_PASS, 300, 20, cache_read=100)],
    )

    result = asyncio.run(orch.process_task(task))

    assert result.status is Status.DONE
    # Thinking blocks were skipped; only the text reached the task outputs.
    assert result.pm_output == PM_TEXT
    assert result.swe_output == SWE_TEXT
    assert QAAgent.is_pass(result.qa_output)
    # Each agent called Claude once, on the beta endpoint with the fallback request.
    for sdk in (sdks.pm, sdks.swe, sdks.qa):
        (endpoint, kwargs), = sdk.calls
        assert endpoint == "beta"
        assert kwargs["model"] == MODEL and kwargs["fallbacks"] == "default"

    records = [data["record"] for name, data in events if name == "usage:update"]
    # input tokens include the cache tokens (D7): 100+10+5, 200, 300+100.
    assert [(r["provider"], r["model"], r["input_tokens"], r["output_tokens"]) for r in records] == [
        ("anthropic", MODEL, 115, 50),
        ("anthropic", MODEL, 200, 80),
        ("anthropic", MODEL, 400, 20),
    ]
    expected_costs = [
        115 / 1000 * OPUS_IN + 50 / 1000 * OPUS_OUT,
        200 / 1000 * OPUS_IN + 80 / 1000 * OPUS_OUT,
        400 / 1000 * OPUS_IN + 20 / 1000 * OPUS_OUT,
    ]
    assert [r["cost"] for r in records] == pytest.approx(expected_costs)
    project_usage = tracker.project_usage(project.name)
    assert project_usage.call_count == 3
    assert project_usage.cost == pytest.approx(sum(expected_costs))
    assert (project_usage.input_tokens, project_usage.output_tokens) == (715, 150)


@pytest.mark.parametrize("engine", ENGINES)
def test_qa_failure_reworks_and_every_call_is_billed(tmp_path, engine):
    task, _project, orch, events, sdks, tracker = _build(
        tmp_path,
        engine,
        pm_results=[_reply(PM_TEXT, 10, 5)],
        swe_results=[_reply(SWE_TEXT, 20, 8), _reply(SWE_TEXT + "\n# fixed", 30, 9)],
        qa_results=[_reply(QA_FAIL, 40, 7), _reply(QA_PASS, 50, 6)],
    )

    result = asyncio.run(orch.process_task(task))

    assert result.status is Status.DONE
    assert result.rework_count == 1
    assert (len(sdks.pm.calls), len(sdks.swe.calls), len(sdks.qa.calls)) == (1, 2, 2)
    assert tracker.project_usage(orch.project_id).call_count == 5
    # The rework prompt carried QA's failure report to SWE.
    assert "QA failure report" in sdks.swe.calls[1][1]["messages"][0]["content"]


@pytest.mark.parametrize("engine", ENGINES)
def test_a_refusal_fails_the_task_after_a_single_call(tmp_path, engine):
    refusal = message(
        [], stop_reason="refusal", stop_details=SimpleNamespace(category="cyber"),
        usage_=usage(60, 0),
    )
    task, _project, orch, events, sdks, tracker = _build(
        tmp_path,
        engine,
        pm_results=[_reply(PM_TEXT, 10, 5)],
        swe_results=[refusal],
        qa_results=[],
    )

    result = asyncio.run(orch.process_task(task))

    assert result.status is Status.FAILED
    assert len(sdks.swe.calls) == 1  # not retried, so billed once
    assert sdks.qa.calls == []
    failed = [data for name, data in events if name == "task:failed"]
    assert len(failed) == 1 and "refused" in failed[0]["reason"] and "cyber" in failed[0]["reason"]
    assert orch.failed_count == 1


# KNOWN GAP (reported, not fixed: needs another base_agent.py change). BaseAgent
# only adds a call's tokens to its totals after ``call_llm`` succeeds, so a call
# that Claude billed but whose output was unusable (refusal, max_tokens) reaches
# the usage tracker as 0 tokens. The budget guard therefore under-counts, worst
# for max_tokens (up to 16000 output tokens). strict=True: this starts failing
# the day the gap is closed, as a reminder to drop the marker.
@pytest.mark.xfail(strict=True, reason="billed-but-unusable calls are not tracked (BaseAgent)")
@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize(
    "unusable",
    [
        message([], stop_reason="refusal", stop_details=SimpleNamespace(category="cyber"),
                usage_=usage(60, 0)),
        message([text("cut")], stop_reason="max_tokens", usage_=usage(60, 16000)),
    ],
    ids=["refusal", "max_tokens"],
)
def test_billed_tokens_of_an_unusable_call_are_tracked(tmp_path, engine, unusable):
    task, project, orch, _events, _sdks, tracker = _build(
        tmp_path, engine,
        pm_results=[_reply(PM_TEXT, 10, 5)], swe_results=[unusable], qa_results=[],
    )

    asyncio.run(orch.process_task(task))

    usage_total = tracker.project_usage(project.name)
    assert (usage_total.input_tokens, usage_total.output_tokens) == (10 + 60, 5 + unusable.usage.output_tokens)
