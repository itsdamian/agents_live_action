"""DevOps agent — a standalone periodic system-health monitor.

Unlike the PM / SWE / QA agents, :class:`DevOpsAgent` is **not** part of the
PM -> SWE -> QA pipeline and does **not** call an LLM. It runs independently as
a periodic task (every 30 seconds by default), sampling system health and
emitting ``devops:alert`` events when a metric crosses a warning/critical
threshold or when failed tasks pile up (Requirement 2.5, design "DevOps Agent
設計").

Each monitoring cycle samples four metrics:

* **CPU usage** (percent) — via :mod:`psutil`.
* **Memory usage** (percent) — via :mod:`psutil`.
* **Disk usage** (percent) — via :mod:`psutil`, for the projects directory.
* **Failed task count** — supplied by the pipeline via an injected provider.

Anomalies produce a :class:`DevOpsAlert` whose payload matches the WebSocket
``devops:alert`` data shape in design.md: ``{level, message, metric, value}``.
Alerts are handed to an injected ``on_alert`` callback rather than a concrete
WebSocket manager, because ``core/ws_manager.py`` does not exist yet (task 8.1);
``main.py`` / the pipeline will wire the callback to the real broadcaster later.

Everything that makes the agent hard to test in a normal test suite is
**injectable**:

* the monitoring ``interval`` (tests pass a tiny value so the loop does not
  block on a real 30-second sleep),
* the ``metrics_collector`` (tests inject fake metrics so results do not depend
  on the real machine's CPU/memory/disk),
* the ``failed_task_provider`` (tests control the failed-task count),
* the ``on_alert`` callback (tests record emitted alerts; sync or async).

Requirements:
    2.5 (DevOps_agent detects system anomalies — resource limits exceeded,
         failed-task pile-up, low disk — records the event and surfaces a
         warning to the front-end monitoring panel).
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from core.project_scanner import DEFAULT_PROJECTS_DIR

logger = logging.getLogger(__name__)

# WebSocket event name for DevOps alerts (design "統一事件格式").
EVENT_DEVOPS_ALERT = "devops:alert"

# Default monitoring interval in seconds (design "每 30 秒").
DEFAULT_MONITOR_INTERVAL_SECONDS = 30.0


class AlertLevel(StrEnum):
    """Severity of a DevOps alert (design ``devops:alert`` ``level`` field)."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True)
class HealthMetrics:
    """A single sample of system-health metrics.

    Percentages are 0-100. ``failed_task_count`` is a running total of tasks the
    pipeline has marked failed.
    """

    cpu_percent: float
    memory_percent: float
    disk_percent: float
    failed_task_count: int


@dataclass(frozen=True)
class Thresholds:
    """Warning/critical thresholds used to classify :class:`HealthMetrics`.

    Percentages are compared with ``>=``. Defaults are conservative sane values;
    callers may override any of them. ``*_critical`` should be >= ``*_warning``.
    """

    cpu_warning: float = 75.0
    cpu_critical: float = 90.0
    memory_warning: float = 75.0
    memory_critical: float = 90.0
    disk_warning: float = 80.0
    disk_critical: float = 95.0
    failed_tasks_warning: int = 3
    failed_tasks_critical: int = 10


@dataclass(frozen=True)
class DevOpsAlert:
    """A DevOps alert, shaped to the WebSocket ``devops:alert`` data payload.

    Fields mirror design.md exactly: ``{level, message, metric, value}``.
    """

    level: AlertLevel
    message: str
    metric: str
    value: float

    def to_event_data(self) -> dict:
        """Return the ``data`` payload for a ``devops:alert`` WebSocket event."""

        return {
            "level": self.level.value,
            "message": self.message,
            "metric": self.metric,
            "value": self.value,
        }


# A metrics collector takes no arguments and returns a fresh sample. The default
# implementation uses psutil; tests inject a fake returning canned values.
MetricsCollector = Callable[[], HealthMetrics]

# A failed-task provider returns the current failed-task count (supplied by the
# pipeline). Kept separate from the collector so the resource sampling and the
# pipeline bookkeeping can be injected independently.
FailedTaskProvider = Callable[[], int]

# An alert sink receives each emitted alert. It may be sync or async; DevOpsAgent
# awaits the result when it is awaitable, so a WebSocket broadcast coroutine can
# be wired in directly (task 8.1 / 9).
AlertCallback = Callable[[DevOpsAlert], None | Awaitable[None]]


class PsutilMetricsCollector:
    """Default :data:`MetricsCollector` backed by :mod:`psutil`.

    Samples CPU, memory, and disk usage for ``disk_path`` (the projects
    directory by default). ``failed_task_provider`` supplies the failed-task
    count, since that is pipeline state rather than an OS metric.

    ``psutil`` is imported lazily so importing this module never fails in an
    environment where psutil is unavailable — only *using* the default
    collector requires it (tests inject their own collector and never hit this).
    """

    def __init__(
        self,
        disk_path: str | Path | None = None,
        failed_task_provider: FailedTaskProvider | None = None,
    ) -> None:
        self._disk_path = str(disk_path) if disk_path is not None else str(DEFAULT_PROJECTS_DIR)
        self._failed_task_provider = failed_task_provider

    def __call__(self) -> HealthMetrics:
        import psutil  # lazy import: only required when actually sampling

        # A non-blocking CPU sample (interval=None) compares against the last
        # call; good enough for a 30s monitoring loop and never blocks.
        cpu_percent = float(psutil.cpu_percent(interval=None))
        memory_percent = float(psutil.virtual_memory().percent)
        disk_percent = float(psutil.disk_usage(self._resolve_disk_path()).percent)
        failed = (
            int(self._failed_task_provider())
            if self._failed_task_provider is not None
            else 0
        )
        return HealthMetrics(
            cpu_percent=cpu_percent,
            memory_percent=memory_percent,
            disk_percent=disk_percent,
            failed_task_count=failed,
        )

    def _resolve_disk_path(self) -> str:
        """Return an existing path for disk usage, falling back to root.

        The projects directory may not exist yet at startup; psutil raises on a
        missing path, so fall back to ``/`` to keep monitoring resilient.
        """

        path = Path(self._disk_path)
        if path.exists():
            return str(path)
        return "/"


class DevOpsAgent:
    """Standalone periodic monitor emitting ``devops:alert`` events.

    The agent samples :class:`HealthMetrics` every ``interval`` seconds,
    classifies each metric against :class:`Thresholds`, and emits a
    :class:`DevOpsAlert` per anomalous metric via ``on_alert`` (Requirement
    2.5). It does not touch an LLM and is not scheduled by the pipeline.

    Args:
        on_alert: Callback invoked with each emitted alert. May be sync or
            async (awaited when awaitable). When omitted, alerts are only
            logged — useful for a monitor running before the WS layer exists.
        metrics_collector: Callable returning a fresh :class:`HealthMetrics`
            sample. Defaults to a :class:`PsutilMetricsCollector`. Inject a fake
            in tests for deterministic results.
        failed_task_provider: Supplies the failed-task count for the default
            collector. Ignored when a custom ``metrics_collector`` is given.
        thresholds: Warning/critical thresholds. Defaults to :class:`Thresholds`.
        interval: Seconds between monitoring cycles (default 30). Tests pass a
            tiny value so :meth:`start` does not block on a real sleep.
        projects_dir: Directory whose disk usage the default collector samples.
    """

    def __init__(
        self,
        on_alert: AlertCallback | None = None,
        metrics_collector: MetricsCollector | None = None,
        failed_task_provider: FailedTaskProvider | None = None,
        thresholds: Thresholds | None = None,
        interval: float = DEFAULT_MONITOR_INTERVAL_SECONDS,
        projects_dir: str | Path | None = None,
    ) -> None:
        self._on_alert = on_alert
        self.thresholds = thresholds if thresholds is not None else Thresholds()
        self.interval = interval
        self._collect = (
            metrics_collector
            if metrics_collector is not None
            else PsutilMetricsCollector(
                disk_path=projects_dir,
                failed_task_provider=failed_task_provider,
            )
        )
        self._running = False

    # -- classification (pure, easily testable) ------------------------------

    def evaluate(self, metrics: HealthMetrics) -> list[DevOpsAlert]:
        """Classify ``metrics`` into a list of alerts (empty when all healthy).

        Each of the four monitored dimensions produces at most one alert, at
        the highest severity it reaches. A metric below its warning threshold
        produces no alert. This method is pure (no I/O, no emission) so tests
        can assert on anomaly detection deterministically (Requirement 2.5).
        """

        alerts: list[DevOpsAlert] = []
        t = self.thresholds

        cpu_alert = self._percent_alert(
            metric="cpu",
            value=metrics.cpu_percent,
            warning=t.cpu_warning,
            critical=t.cpu_critical,
            label="CPU usage",
        )
        if cpu_alert is not None:
            alerts.append(cpu_alert)

        memory_alert = self._percent_alert(
            metric="memory",
            value=metrics.memory_percent,
            warning=t.memory_warning,
            critical=t.memory_critical,
            label="Memory usage",
        )
        if memory_alert is not None:
            alerts.append(memory_alert)

        disk_alert = self._percent_alert(
            metric="disk",
            value=metrics.disk_percent,
            warning=t.disk_warning,
            critical=t.disk_critical,
            label="Disk usage",
        )
        if disk_alert is not None:
            alerts.append(disk_alert)

        failed_alert = self._failed_tasks_alert(metrics.failed_task_count)
        if failed_alert is not None:
            alerts.append(failed_alert)

        return alerts

    def _percent_alert(
        self,
        metric: str,
        value: float,
        warning: float,
        critical: float,
        label: str,
    ) -> DevOpsAlert | None:
        """Build an alert for a 0-100 percent metric, or ``None`` if healthy."""

        if value >= critical:
            level = AlertLevel.CRITICAL
        elif value >= warning:
            level = AlertLevel.WARNING
        else:
            return None

        return DevOpsAlert(
            level=level,
            message=f"{label} at {value:.0f}%",
            metric=metric,
            value=value,
        )

    def _failed_tasks_alert(self, count: int) -> DevOpsAlert | None:
        """Build an alert for the failed-task count, or ``None`` if healthy."""

        t = self.thresholds
        if count >= t.failed_tasks_critical:
            level = AlertLevel.CRITICAL
        elif count >= t.failed_tasks_warning:
            level = AlertLevel.WARNING
        else:
            return None

        return DevOpsAlert(
            level=level,
            message=f"{count} failed tasks piling up",
            metric="failed_tasks",
            value=float(count),
        )

    # -- one monitoring cycle ------------------------------------------------

    async def run_once(self) -> list[DevOpsAlert]:
        """Run a single monitoring cycle: sample, classify, emit.

        Collects a metrics sample, evaluates it, and emits every resulting
        alert through ``on_alert``. Sampling/emission errors are caught and
        logged so a transient failure never crashes the monitoring loop (the
        agent must keep watching). Returns the alerts emitted this cycle.
        """

        try:
            metrics = self._collect()
        except Exception as exc:  # keep the loop alive on collector failure
            logger.warning("DevOps metric collection failed: %s", exc)
            return []

        alerts = self.evaluate(metrics)
        for alert in alerts:
            await self._emit(alert)
        return alerts

    async def _emit(self, alert: DevOpsAlert) -> None:
        """Emit a single alert via ``on_alert`` (awaiting async callbacks)."""

        logger.info(
            "devops:alert [%s] %s (%s=%s)",
            alert.level.value,
            alert.message,
            alert.metric,
            alert.value,
        )
        if self._on_alert is None:
            return
        try:
            result = self._on_alert(alert)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:  # a bad sink must not break monitoring
            logger.warning("DevOps alert callback failed: %s", exc)

    # -- periodic loop -------------------------------------------------------

    async def start(self) -> None:
        """Run the monitoring loop until :meth:`stop` is called.

        Runs one cycle, then sleeps ``interval`` seconds, repeatedly. Intended
        to be launched as a background asyncio task at application startup
        (task 9). The interval is injectable so tests never block on a real
        30-second sleep.
        """

        if self._running:
            return
        self._running = True
        logger.info("DevOpsAgent monitoring started (interval=%ss)", self.interval)
        try:
            while self._running:
                await self.run_once()
                if not self._running:
                    break
                await asyncio.sleep(self.interval)
        except asyncio.CancelledError:  # graceful shutdown via task cancel
            logger.info("DevOpsAgent monitoring cancelled")
            raise
        finally:
            self._running = False

    def stop(self) -> None:
        """Signal the monitoring loop to stop after the current cycle."""

        self._running = False

    @property
    def is_running(self) -> bool:
        """Whether the monitoring loop is currently active."""

        return self._running
