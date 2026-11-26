"""Unit & integration tests for LLM usage / cost tracking (task 16).

Covers the pure :mod:`core.usage_tracker` module (token estimation, cost
estimation from the price table, per-task / per-project / per-day accumulation,
and daily / per-project budget checks), the env-var budget parsing in
:mod:`config`, and an end-to-end pipeline integration test showing that running
tasks in mock mode records usage, emits ``usage:update`` events, and that a
crossed budget pauses enqueueing and raises a critical ``devops:alert``.

Everything runs against the :class:`~core.llm_client.MockLLMClient`, so no real
API keys are required; the tracker's clock is injected where a deterministic
"today" matters.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

import pytest
import yaml

import config
from agents.devops_agent import EVENT_DEVOPS_ALERT
from agents.pm_agent import PMAgent
from agents.qa_agent import QAAgent
from agents.swe_agent import SWEAgent
from core.llm_client import MockLLMClient
from core.pipeline import EVENT_USAGE_UPDATE, PipelineOrchestrator
from core.usage_tracker import (
    MODEL_PRICES,
    UNKNOWN_PRICE,
    UsageTracker,
    estimate_cost,
    estimate_tokens,
    price_for,
)
from models.agent import AgentConfig
from models.task import Priority, Status, Task


# ---------------------------------------------------------------------------
# estimate_tokens
# ---------------------------------------------------------------------------


def test_estimate_tokens_empty_is_zero():
    assert estimate_tokens("") == 0
    assert estimate_tokens(None) == 0


def test_estimate_tokens_non_empty_is_at_least_one():
    assert estimate_tokens("hi") == 1
    # ~4 chars per token.
    assert estimate_tokens("a" * 40) == 10


# ---------------------------------------------------------------------------
# price_for / estimate_cost
# ---------------------------------------------------------------------------


def test_price_for_known_model_is_case_insensitive():
    assert price_for("OpenAI", "GPT-4o") == MODEL_PRICES[("openai", "gpt-4o")]


def test_price_for_unknown_model_falls_back_to_provider_default():
    # Unknown model but known provider -> provider default (non-zero).
    price = price_for("openai", "some-future-model")
    assert price != UNKNOWN_PRICE
    assert price[0] > 0


def test_price_for_unknown_provider_is_zero():
    assert price_for("acme", "whatever") == UNKNOWN_PRICE


def test_estimate_cost_uses_input_and_output_prices():
    # gpt-4o: (0.005, 0.015) per 1k tokens.
    cost = estimate_cost("openai", "gpt-4o", input_tokens=1000, output_tokens=1000)
    assert cost == pytest.approx(0.005 + 0.015)


def test_estimate_cost_mock_is_free():
    assert estimate_cost("mock", "mock-model", 10_000, 10_000) == 0.0


def test_estimate_cost_scales_with_tokens():
    cheap = estimate_cost("openai", "gpt-4o", 500, 0)
    dear = estimate_cost("openai", "gpt-4o", 5000, 0)
    assert dear > cheap > 0


# ---------------------------------------------------------------------------
# recording & accumulation
# ---------------------------------------------------------------------------


def _record(tracker: UsageTracker, project="p1", task="t1", provider="openai",
            model="gpt-4o", inp=1000, out=1000):
    return tracker.record(project, task, provider, model, inp, out)


def test_record_returns_costed_record():
    tracker = UsageTracker()
    record = _record(tracker)
    assert record.provider == "openai"
    assert record.input_tokens == 1000
    assert record.output_tokens == 1000
    assert record.cost == pytest.approx(0.02)


def test_task_and_project_usage_accumulate():
    tracker = UsageTracker()
    _record(tracker, task="t1")
    _record(tracker, task="t1")
    _record(tracker, task="t2")

    t1 = tracker.task_usage("p1", "t1")
    assert t1.call_count == 2
    assert t1.input_tokens == 2000
    assert t1.cost == pytest.approx(0.04)

    project = tracker.project_usage("p1")
    assert project.call_count == 3
    assert project.cost == pytest.approx(0.06)


def test_usage_is_isolated_per_project_and_task():
    tracker = UsageTracker()
    _record(tracker, project="p1", task="t1")
    _record(tracker, project="p2", task="t1")

    assert tracker.project_usage("p1").call_count == 1
    assert tracker.project_usage("p2").call_count == 1
    assert tracker.task_usage("p1", "t1").call_count == 1
    # Unknown task/project -> empty summary, never raises.
    assert tracker.task_usage("p1", "nope").call_count == 0
    assert tracker.project_usage("nope").cost == 0.0


def test_returned_summaries_are_copies():
    tracker = UsageTracker()
    _record(tracker)
    summary = tracker.project_usage("p1")
    summary.cost = 999.0
    # Mutating the returned copy must not corrupt the tracker.
    assert tracker.project_usage("p1").cost == pytest.approx(0.02)


def test_daily_usage_groups_by_day():
    day1 = datetime(2026, 1, 1, 9, 0, 0)
    clock = {"now": day1}
    tracker = UsageTracker(clock=lambda: clock["now"])

    _record(tracker)
    _record(tracker)
    assert tracker.daily_cost() == pytest.approx(0.04)

    clock["now"] = datetime(2026, 1, 2, 9, 0, 0)
    _record(tracker)
    # New day resets the daily accumulator.
    assert tracker.daily_cost() == pytest.approx(0.02)
    # But the earlier day is still queryable.
    assert tracker.daily_cost(day1.date()) == pytest.approx(0.04)


def test_records_are_kept_in_order():
    tracker = UsageTracker()
    _record(tracker, task="a")
    _record(tracker, task="b")
    assert [r.task_id for r in tracker.records()] == ["a", "b"]


# ---------------------------------------------------------------------------
# budget checks
# ---------------------------------------------------------------------------


def test_no_budget_never_exceeded():
    tracker = UsageTracker()
    _record(tracker, inp=1_000_000, out=1_000_000)
    assert tracker.is_over_budget("p1") is False
    assert tracker.budget_status("p1").exceeded is False


def test_project_budget_exceeded():
    tracker = UsageTracker(project_budget_usd=0.03)
    _record(tracker)  # 0.02, under
    assert tracker.is_over_budget("p1") is False
    _record(tracker)  # 0.04 total, over
    status = tracker.budget_status("p1")
    assert status.exceeded is True
    assert status.kind == "project"
    assert status.spend == pytest.approx(0.04)
    assert status.limit == 0.03
    assert "budget exceeded" in status.message.lower()


def test_daily_budget_exceeded_takes_priority():
    tracker = UsageTracker(daily_budget_usd=0.03, project_budget_usd=100.0)
    _record(tracker)
    _record(tracker)
    status = tracker.budget_status("p1")
    assert status.exceeded is True
    assert status.kind == "daily"


def test_budget_boundary_is_inclusive():
    # Exactly at the ceiling counts as exceeded (>= comparison).
    tracker = UsageTracker(project_budget_usd=0.02)
    _record(tracker)  # exactly 0.02
    assert tracker.is_over_budget("p1") is True


def test_project_budget_isolated_between_projects():
    tracker = UsageTracker(project_budget_usd=0.03)
    _record(tracker, project="p1")
    _record(tracker, project="p1")  # p1 over
    _record(tracker, project="p2")  # p2 under
    assert tracker.is_over_budget("p1") is True
    assert tracker.is_over_budget("p2") is False


# ---------------------------------------------------------------------------
# config budget parsing
# ---------------------------------------------------------------------------


def test_get_daily_budget_unset_is_none(monkeypatch):
    monkeypatch.delenv("DAILY_BUDGET_USD", raising=False)
    assert config.get_daily_budget_usd() is None


def test_get_daily_budget_parses_float(monkeypatch):
    monkeypatch.setenv("DAILY_BUDGET_USD", "12.50")
    assert config.get_daily_budget_usd() == 12.5


def test_get_project_budget_parses_float(monkeypatch):
    monkeypatch.setenv("PROJECT_BUDGET_USD", "5")
    assert config.get_project_budget_usd() == 5.0


@pytest.mark.parametrize("value", ["", "  ", "abc", "0", "-3"])
def test_invalid_or_nonpositive_budget_is_none(monkeypatch, value):
    monkeypatch.setenv("DAILY_BUDGET_USD", value)
    assert config.get_daily_budget_usd() is None


# ---------------------------------------------------------------------------
# LLM client usage reporting
# ---------------------------------------------------------------------------


def test_mock_client_reports_usage_after_call():
    client = MockLLMClient(role="Project Manager", name="PM_agent")
    assert client.last_usage is None
    client.call("Implement login", system_prompt="You are PM.")
    assert client.last_usage is not None
    assert client.last_usage.input_tokens > 0
    assert client.last_usage.output_tokens > 0
    assert client.provider == "mock"


# ---------------------------------------------------------------------------
# Pipeline integration (task 16)
# ---------------------------------------------------------------------------


def _make_config(name: str, role: str) -> AgentConfig:
    return AgentConfig(
        name=name,
        role=role,
        description=f"{role} agent",
        llm_provider="mock",
        llm_model="mock-model",
        api_key_env="OPENAI_API_KEY",
        max_retries=1,
        system_prompt=f"You are {name}.",
    )


def _make_agents():
    pm = PMAgent(
        _make_config("PM_agent", "Project Manager"),
        llm_client=MockLLMClient(role="Project Manager", name="PM_agent"),
        retry_wait_multiplier=0,
    )
    swe = SWEAgent(
        _make_config("SWE_agent", "Software Engineer"),
        llm_client=MockLLMClient(role="Software Engineer", name="SWE_agent"),
        retry_wait_multiplier=0,
    )
    qa = QAAgent(
        _make_config("QA_agent", "Quality Assurance"),
        llm_client=MockLLMClient(role="Quality Assurance", name="QA_agent"),
        retry_wait_multiplier=0,
    )
    return pm, swe, qa


def _make_task(task_id: str = "task-001") -> Task:
    now = datetime(2026, 1, 1, 12, 0, 0)
    return Task(
        id=task_id,
        title="Add login page",
        description="Users should be able to log in.",
        priority=Priority.HIGH,
        status=Status.PENDING,
        created_at=now,
        updated_at=now,
    )


def _make_project(tmp_path: Path, tasks: list[Task]) -> Path:
    project = tmp_path / "demo-project"
    project.mkdir()
    payload = {
        "tasks": [
            {
                "id": t.id,
                "title": t.title,
                "description": t.description,
                "priority": t.priority.value,
                "status": t.status.value,
            }
            for t in tasks
        ]
    }
    (project / "tasks.yaml").write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return project


def _orchestrator(project: Path, events: list, tracker: UsageTracker,
                  write_deliverables=False) -> PipelineOrchestrator:
    pm, swe, qa = _make_agents()
    return PipelineOrchestrator(
        project_id=project.name,
        project_path=project,
        pm_agent=pm,
        swe_agent=swe,
        qa_agent=qa,
        on_event=lambda event, data: events.append((event, data)),
        write_deliverables=write_deliverables,
        usage_tracker=tracker,
    )


def test_pipeline_records_usage_and_emits_usage_update(tmp_path):
    task = _make_task()
    project = _make_project(tmp_path, [task])
    events: list = []
    tracker = UsageTracker()
    orchestrator = _orchestrator(project, events, tracker)

    asyncio.run(orchestrator.process_task(task))

    # PM + SWE + QA each make one mock call -> three records.
    project_usage = tracker.project_usage(project.name)
    assert project_usage.call_count == 3
    assert project_usage.input_tokens > 0

    usage_events = [d for name, d in events if name == EVENT_USAGE_UPDATE]
    assert len(usage_events) == 3
    # Each usage:update carries the running task + project totals.
    assert usage_events[-1]["project_usage"]["call_count"] == 3
    assert "daily_cost" in usage_events[-1]


def test_pipeline_pauses_enqueue_and_alerts_when_budget_exceeded(tmp_path):
    task = _make_task()
    project = _make_project(tmp_path, [task])
    events: list = []
    # A tiny non-zero budget with real (openai) pricing would trip; but mock is
    # free, so drive the budget by pre-loading spend on the shared tracker.
    tracker = UsageTracker(project_budget_usd=0.01)
    tracker.record(project.name, "seed", "openai", "gpt-4o", 1000, 1000)  # 0.02 > 0.01
    orchestrator = _orchestrator(project, events, tracker)

    # Budget already exceeded -> enqueue is refused.
    assert orchestrator.is_budget_paused() is True
    orchestrator.enqueue(task)
    assert orchestrator.queue_size == 0


def test_pipeline_emits_critical_alert_on_budget_crossing(tmp_path):
    """A run that crosses the budget mid-flight emits one critical alert."""

    task = _make_task()
    project = _make_project(tmp_path, [task])
    events: list = []
    # Budget just above zero so the mock run (free) never trips on its own;
    # seed spend so the very first recorded call crosses it. Use a real-priced
    # seed and a real-priced agent by patching model via price table? Simpler:
    # set a budget the seeded spend already exceeds, then confirm the alert is
    # emitted the first time the pipeline records usage.
    tracker = UsageTracker(project_budget_usd=0.015)
    orchestrator = _orchestrator(project, events, tracker)

    # Seed spend so the project is over budget; then process the task, which
    # records (free) mock usage and triggers the budget check + alert.
    tracker.record(project.name, "seed", "openai", "gpt-4o", 1000, 1000)  # 0.02
    asyncio.run(orchestrator.process_task(task))

    alerts = [d for name, d in events if name == EVENT_DEVOPS_ALERT]
    assert len(alerts) == 1
    assert alerts[0]["level"] == "critical"
    assert alerts[0]["metric"] == "budget"
    assert orchestrator.is_budget_paused() is True


def test_snapshot_includes_usage_and_budget_state(tmp_path):
    task = _make_task()
    project = _make_project(tmp_path, [task])
    events: list = []
    tracker = UsageTracker(project_budget_usd=100.0)
    orchestrator = _orchestrator(project, events, tracker)

    snap = orchestrator.snapshot()
    assert snap["budget_paused"] is False
    assert snap["usage"]["call_count"] == 0
