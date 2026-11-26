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


# -- calls that Claude billed but whose output was unusable still count ---------------
# (BaseAgent adds usage per attempt, so refusal / max_tokens reach the tracker.)

UNUSABLE = [
    pytest.param(
        lambda: message([], stop_reason="refusal", stop_details=SimpleNamespace(category="cyber"),
                        usage_=usage(60, 0)),
        id="refusal",
    ),
    pytest.param(
        lambda: message([text("cut")], stop_reason="max_tokens", usage_=usage(60, 16000)),
        id="max_tokens",
    ),
]


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("make_unusable", UNUSABLE)
def test_billed_tokens_of_an_unusable_call_are_tracked(tmp_path, engine, make_unusable):
    unusable = make_unusable()
    task, project, orch, events, _sdks, tracker = _build(
        tmp_path, engine,
        pm_results=[_reply(PM_TEXT, 10, 5)], swe_results=[unusable], qa_results=[],
    )

    result = asyncio.run(orch.process_task(task))

    assert result.status is Status.FAILED  # the task still fails
    usage_total = tracker.project_usage(project.name)
    assert usage_total.call_count == 2
    assert (usage_total.input_tokens, usage_total.output_tokens) == (70, 5 + unusable.usage.output_tokens)
    # usage:update for the failed station comes right after its error event.
    names = [(n, d.get("station"), d.get("status")) for n, d in events if n in ("station:update", "usage:update")]
    swe_error = names.index(("station:update", "swe", "error"))
    assert names[swe_error + 1][0] == "usage:update"
    swe_record = [d["record"] for n, d in events if n == "usage:update"][-1]
    assert (swe_record["provider"], swe_record["model"]) == ("anthropic", MODEL)
    assert swe_record["cost"] == pytest.approx(60 / 1000 * OPUS_IN + unusable.usage.output_tokens / 1000 * OPUS_OUT)


@pytest.mark.parametrize("engine", ENGINES)
def test_budget_crossed_by_a_failed_call_alerts_once_and_pauses_enqueue(tmp_path, engine):
    truncated = message([text("cut")], stop_reason="max_tokens", usage_=usage(60, 16000))  # ~$0.32
    task, project, orch, events, _sdks, tracker = _build(
        tmp_path, engine,
        pm_results=[_reply(PM_TEXT, 10, 5)], swe_results=[truncated], qa_results=[],
    )
    tracker.project_budget_usd = 0.01
    assert orch.is_budget_paused() is False

    result = asyncio.run(orch.process_task(task))

    assert result.status is Status.FAILED
    alerts = [d for n, d in events if n == "devops:alert"]
    assert len(alerts) == 1
    assert (alerts[0]["level"], alerts[0]["metric"]) == ("critical", "budget")
    order = [n for n, _ in events if n in ("usage:update", "devops:alert", "task:failed")]
    # PM usage, then the failed SWE call's usage crosses the budget -> alert -> task fails.
    assert order == ["usage:update", "usage:update", "devops:alert", "task:failed"]
    assert orch.is_budget_paused() is True
    orch.enqueue(make_task("task-002"))
    assert orch.queue_size == 0  # dropped: enqueue is paused


@pytest.mark.parametrize("engine", ENGINES)
def test_failing_client_without_usage_adds_no_usage_event(tmp_path, engine):
    """Clients that report no usage on failure: the event sequence is unchanged."""

    from tests.pipeline_harness import ScriptedClient, make_orchestrator

    task = make_task()
    project = make_project(tmp_path, [task])
    swe = ScriptedClient("Software Engineer", "SWE_agent", [RuntimeError("provider unreachable")])
    orch, events = make_orchestrator(project, engine=engine, swe_client=swe, usage_tracker=UsageTracker())

    asyncio.run(orch.process_task(task))

    usage_events = [d for n, d in events if n == "usage:update"]
    assert [d["record"]["task_id"] for d in usage_events] == ["task-001"]  # PM only
    swe_events = [(n, d.get("status")) for n, d in events if d.get("station") == "swe"]
    assert swe_events == [("station:update", "working"), ("station:update", "error")]


@pytest.mark.parametrize("engine", ENGINES)
def test_retried_rate_limits_then_success_bills_every_attempt_that_has_usage(tmp_path, engine):
    """429, 429, success: three SDK calls. A 429 is rejected before generation, so it
    carries no usage; only the successful attempt is billed. (Per-attempt addition of
    usage-bearing failures is covered in test_agent_usage_accumulation.py.)"""

    import anthropic
    import httpx2

    def rate_limit():
        response = httpx2.Response(429, request=httpx2.Request("POST", "https://api.invalid"))
        return anthropic.RateLimitError("slow down", response=response, body=None)

    task, project, orch, _events, sdks, tracker = _build(
        tmp_path, engine,
        pm_results=[rate_limit(), rate_limit(), _reply(PM_TEXT, 100, 50)],
        swe_results=[_reply(SWE_TEXT, 20, 8)],
        qa_results=[_reply(QA_PASS, 30, 9)],
    )

    result = asyncio.run(orch.process_task(task))

    assert result.status is Status.DONE
    assert len(sdks.pm.calls) == 3
    pm_record = tracker.project_usage(project.name)
    assert (pm_record.call_count, pm_record.input_tokens, pm_record.output_tokens) == (3, 150, 67)
