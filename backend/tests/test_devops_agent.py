"""Unit tests for :class:`agents.devops_agent.DevOpsAgent`.

The DevOps agent is a standalone periodic monitor (not a pipeline/LLM agent),
so these tests never touch a real LLM and never depend on the host machine's
real CPU/memory/disk: metrics are injected via a fake collector and the failed
task count via a provider, making anomaly detection fully deterministic
(Requirement 2.5).

Async methods (``run_once`` / ``start``) are driven with ``asyncio.run`` so the
suite needs no pytest-asyncio configuration, and the monitoring ``interval`` is
kept tiny so the loop never blocks on a real 30-second sleep.

Coverage:
    * ``evaluate`` classifies each metric (cpu/memory/disk/failed_tasks) into
      info-clean / warning / critical against thresholds,
    * a simulated anomaly triggers a ``devops:alert`` with the design's
      ``{level, message, metric, value}`` payload,
    * ``run_once`` emits alerts through sync and async callbacks and survives a
      failing collector / callback,
    * the periodic loop runs cycles and stops cleanly.
"""

from __future__ import annotations

import asyncio

import pytest

from agents.devops_agent import (
    DEFAULT_MONITOR_INTERVAL_SECONDS,
    EVENT_DEVOPS_ALERT,
    AlertLevel,
    DevOpsAgent,
    DevOpsAlert,
    HealthMetrics,
    Thresholds,
)


def _metrics(
    cpu: float = 10.0,
    memory: float = 10.0,
    disk: float = 10.0,
    failed: int = 0,
) -> HealthMetrics:
    """Build a healthy-by-default metrics sample; override to simulate anomalies."""

    return HealthMetrics(
        cpu_percent=cpu,
        memory_percent=memory,
        disk_percent=disk,
        failed_task_count=failed,
    )


def _agent_with_metrics(metrics: HealthMetrics, **kwargs) -> DevOpsAgent:
    """DevOpsAgent whose collector always returns ``metrics`` (no psutil)."""

    return DevOpsAgent(metrics_collector=lambda: metrics, **kwargs)


# ---------------------------------------------------------------------------
# evaluate() — pure classification (Requirement 2.5)
# ---------------------------------------------------------------------------


def test_healthy_metrics_produce_no_alerts():
    agent = DevOpsAgent()
    assert agent.evaluate(_metrics()) == []


@pytest.mark.parametrize(
    "field, metric, warning_value, critical_value",
    [
        ("cpu", "cpu", 80.0, 95.0),
        ("memory", "memory", 80.0, 95.0),
        ("disk", "disk", 85.0, 97.0),
    ],
)
def test_percent_metric_warning_and_critical(field, metric, warning_value, critical_value):
    agent = DevOpsAgent()

    # Build metrics explicitly by field name to keep it readable.
    kwargs_warn = {field: warning_value}
    kwargs_crit = {field: critical_value}

    warn_alerts = agent.evaluate(_metrics(**kwargs_warn))
    assert len(warn_alerts) == 1
    assert warn_alerts[0].metric == metric
    assert warn_alerts[0].level is AlertLevel.WARNING
    assert warn_alerts[0].value == warning_value

    crit_alerts = agent.evaluate(_metrics(**kwargs_crit))
    assert len(crit_alerts) == 1
    assert crit_alerts[0].metric == metric
    assert crit_alerts[0].level is AlertLevel.CRITICAL


def test_failed_task_pileup_triggers_warning_then_critical():
    agent = DevOpsAgent(thresholds=Thresholds(failed_tasks_warning=3, failed_tasks_critical=10))

    assert agent.evaluate(_metrics(failed=2)) == []

    warn = agent.evaluate(_metrics(failed=3))
    assert len(warn) == 1
    assert warn[0].metric == "failed_tasks"
    assert warn[0].level is AlertLevel.WARNING
    assert warn[0].value == 3.0

    crit = agent.evaluate(_metrics(failed=12))
    assert len(crit) == 1
    assert crit[0].level is AlertLevel.CRITICAL


def test_multiple_anomalies_produce_multiple_alerts():
    agent = DevOpsAgent()
    alerts = agent.evaluate(_metrics(cpu=99.0, memory=99.0, disk=99.0, failed=99))
    metrics = {a.metric for a in alerts}
    assert metrics == {"cpu", "memory", "disk", "failed_tasks"}
    assert all(a.level is AlertLevel.CRITICAL for a in alerts)


def test_boundary_value_is_inclusive():
    """A value exactly at the warning threshold is an anomaly (>= comparison)."""
    agent = DevOpsAgent(thresholds=Thresholds(cpu_warning=75.0, cpu_critical=90.0))
    alerts = agent.evaluate(_metrics(cpu=75.0))
    assert len(alerts) == 1
    assert alerts[0].level is AlertLevel.WARNING


def test_custom_thresholds_are_respected():
    strict = DevOpsAgent(thresholds=Thresholds(cpu_warning=20.0, cpu_critical=40.0))
    alerts = strict.evaluate(_metrics(cpu=25.0))
    assert len(alerts) == 1
    assert alerts[0].level is AlertLevel.WARNING


# ---------------------------------------------------------------------------
# DevOpsAlert payload shape (design devops:alert data)
# ---------------------------------------------------------------------------


def test_alert_event_data_matches_design_shape():
    alert = DevOpsAlert(
        level=AlertLevel.WARNING,
        message="Memory usage at 85%",
        metric="memory",
        value=85.0,
    )
    assert alert.to_event_data() == {
        "level": "warning",
        "message": "Memory usage at 85%",
        "metric": "memory",
        "value": 85.0,
    }


def test_event_name_constant():
    assert EVENT_DEVOPS_ALERT == "devops:alert"


def test_default_interval_is_thirty_seconds():
    assert DEFAULT_MONITOR_INTERVAL_SECONDS == 30.0
    assert DevOpsAgent().interval == 30.0


# ---------------------------------------------------------------------------
# run_once() — emission through injected callbacks (Requirement 2.5)
# ---------------------------------------------------------------------------


def test_run_once_emits_alert_on_anomaly_sync_callback():
    received: list[DevOpsAlert] = []
    agent = _agent_with_metrics(
        _metrics(memory=88.0),
        on_alert=received.append,
    )

    returned = asyncio.run(agent.run_once())

    assert len(received) == 1
    assert received[0].metric == "memory"
    assert received[0].level is AlertLevel.WARNING
    assert returned == received


def test_run_once_emits_nothing_when_healthy():
    received: list[DevOpsAlert] = []
    agent = _agent_with_metrics(_metrics(), on_alert=received.append)

    returned = asyncio.run(agent.run_once())

    assert received == []
    assert returned == []


def test_run_once_supports_async_callback():
    received: list[DevOpsAlert] = []

    async def on_alert(alert: DevOpsAlert) -> None:
        received.append(alert)

    agent = _agent_with_metrics(_metrics(cpu=95.0), on_alert=on_alert)
    asyncio.run(agent.run_once())

    assert len(received) == 1
    assert received[0].metric == "cpu"
    assert received[0].level is AlertLevel.CRITICAL


def test_run_once_survives_failing_collector():
    def broken() -> HealthMetrics:
        raise RuntimeError("psutil exploded")

    agent = DevOpsAgent(metrics_collector=broken)
    # Should not raise; returns no alerts.
    assert asyncio.run(agent.run_once()) == []


def test_run_once_survives_failing_callback():
    def broken(alert: DevOpsAlert) -> None:
        raise RuntimeError("ws broadcast failed")

    agent = _agent_with_metrics(_metrics(disk=99.0), on_alert=broken)
    # The alert is still returned even though the callback blew up.
    returned = asyncio.run(agent.run_once())
    assert len(returned) == 1
    assert returned[0].metric == "disk"


def test_run_once_without_callback_does_not_raise():
    agent = _agent_with_metrics(_metrics(cpu=99.0))
    returned = asyncio.run(agent.run_once())
    assert len(returned) == 1


# ---------------------------------------------------------------------------
# failed_task_provider wiring into the default collector path
# ---------------------------------------------------------------------------


def test_failed_task_provider_feeds_evaluation(monkeypatch):
    """The provider's count drives failed-task alerts via the default collector.

    psutil resource sampling is stubbed so the test stays deterministic and
    independent of the real machine.
    """
    import psutil

    monkeypatch.setattr(psutil, "cpu_percent", lambda interval=None: 1.0)
    monkeypatch.setattr(
        psutil, "virtual_memory", lambda: type("M", (), {"percent": 1.0})()
    )
    monkeypatch.setattr(
        psutil, "disk_usage", lambda path: type("D", (), {"percent": 1.0})()
    )

    count = {"value": 15}
    received: list[DevOpsAlert] = []
    agent = DevOpsAgent(
        on_alert=received.append,
        failed_task_provider=lambda: count["value"],
    )

    asyncio.run(agent.run_once())

    assert len(received) == 1
    assert received[0].metric == "failed_tasks"
    assert received[0].level is AlertLevel.CRITICAL
    assert received[0].value == 15.0


# ---------------------------------------------------------------------------
# periodic loop start()/stop()
# ---------------------------------------------------------------------------


def test_start_runs_cycles_until_stopped():
    received: list[DevOpsAlert] = []
    calls = {"count": 0}

    def collector() -> HealthMetrics:
        calls["count"] += 1
        return _metrics(cpu=99.0)

    agent = DevOpsAgent(
        metrics_collector=collector,
        on_alert=received.append,
        interval=0.001,
    )

    async def drive() -> None:
        task = asyncio.create_task(agent.start())
        # Let a few cycles run, then stop the loop.
        await asyncio.sleep(0.05)
        agent.stop()
        await asyncio.wait_for(task, timeout=1.0)

    asyncio.run(drive())

    assert calls["count"] >= 1
    assert len(received) >= 1
    assert agent.is_running is False


def test_start_is_idempotent_while_running():
    agent = _agent_with_metrics(_metrics(), interval=0.001)

    async def drive() -> None:
        task = asyncio.create_task(agent.start())
        await asyncio.sleep(0.01)
        assert agent.is_running is True
        # Second start() returns immediately without launching another loop.
        await agent.start()
        agent.stop()
        await asyncio.wait_for(task, timeout=1.0)

    asyncio.run(drive())
    assert agent.is_running is False
