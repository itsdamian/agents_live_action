"""Pipeline orchestrator.

Runs the PM -> SWE -> QA assembly line for one project. Pending tasks are
enqueued (typically by the :class:`~core.task_watcher.TaskWatcher` callback)
into a priority queue and processed by an async worker loop:

* **Priority scheduling** — tasks are ordered critical > high > medium > low;
  ties are FIFO by enqueue order (Requirement 6.6).
* **Station flow** — each task passes PM -> SWE -> QA, every station receiving
  the previous station's output as context (Requirements 6.1, 6.2).
* **Rework loop** — when QA fails, the task and QA's failure report are re-sent
  to SWE; after ``task.max_reworks`` failed attempts the task is marked failed
  (Requirements 6.3, 6.4).
* **Fault isolation** — a failing agent (``AgentResult.success == False``)
  fails that task only; the worker loop keeps serving the queue
  (Requirement 6.5).
* **Events** — every state change is reported through an ``on_event`` callback
  so the WebSocket layer can broadcast it to the frontend (Requirement 7.1),
  and every status change is written back to ``tasks.yaml``
  (Requirement 5.5).

Agent ``run`` calls are synchronous (they block on LLM I/O), so they are
executed with :func:`asyncio.to_thread` to keep the event loop responsive.
"""

from __future__ import annotations

import asyncio
import inspect
import itertools
import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from agents.base_agent import AgentResult, BaseAgent
from agents.qa_agent import QAAgent
from core.task_watcher import write_task_status
from models.task import Priority, Status, Task

logger = logging.getLogger(__name__)

# Ordinal used by the priority queue: lower value = served first.
PRIORITY_ORDER: dict[Priority, int] = {
    Priority.CRITICAL: 0,
    Priority.HIGH: 1,
    Priority.MEDIUM: 2,
    Priority.LOW: 3,
}

# Event names pushed through ``on_event`` (see design "WebSocket 事件格式").
EVENT_TASK_CREATED = "task:created"
EVENT_STATION_UPDATE = "station:update"
EVENT_TASK_COMPLETED = "task:completed"
EVENT_TASK_FAILED = "task:failed"

# Station keys used in events and snapshots.
STATION_PM = "pm"
STATION_SWE = "swe"
STATION_QA = "qa"
STATIONS = (STATION_PM, STATION_SWE, STATION_QA)

# Callback invoked as ``on_event(event_name, data)``; may be sync or async.
EventCallback = Callable[[str, dict], object]


class PipelineOrchestrator:
    """Priority-ordered PM -> SWE -> QA pipeline for a single project.

    Args:
        project_id: The project's id (folder name); included in events.
        project_path: Project root directory. Used for agent log files and for
            writing task status back to ``tasks.yaml``.
        pm_agent / swe_agent / qa_agent: The three station agents.
        on_event: Optional callback receiving ``(event_name, data)`` for every
            pipeline state change. Awaited when it returns an awaitable.
        write_status: Whether to write status changes back to ``tasks.yaml``
            (Requirement 5.5). Tests without a tasks file may disable it.
    """

    def __init__(
        self,
        project_id: str,
        project_path: str | Path,
        pm_agent: BaseAgent,
        swe_agent: BaseAgent,
        qa_agent: BaseAgent,
        on_event: EventCallback | None = None,
        write_status: bool = True,
    ) -> None:
        self.project_id = project_id
        self.project_path = Path(project_path)
        self._agents = {
            STATION_PM: pm_agent,
            STATION_SWE: swe_agent,
            STATION_QA: qa_agent,
        }
        self._on_event = on_event
        self._write_status = write_status

        self._queue: asyncio.PriorityQueue[tuple[int, int, Task]] = (
            asyncio.PriorityQueue()
        )
        # FIFO tiebreaker for tasks sharing a priority (Requirement 6.6).
        self._seq = itertools.count()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._worker: asyncio.Task | None = None

        # Snapshot state pushed to newly-connected WebSocket clients.
        self.station_states: dict[str, dict] = {
            station: {"status": "idle", "task_id": None, "task_title": None}
            for station in STATIONS
        }
        self.done_count = 0
        self.failed_count = 0

    # -- enqueueing -----------------------------------------------------------

    def enqueue(self, task: Task) -> None:
        """Queue a task for processing, ordered by priority then FIFO.

        Thread-safe: the TaskWatcher fires its callback from a watchdog thread,
        so when the orchestrator's loop is running the put is marshalled onto
        it with ``call_soon_threadsafe``.
        """

        item = (PRIORITY_ORDER[task.priority], next(self._seq), task)
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(self._queue.put_nowait, item)
        else:
            self._queue.put_nowait(item)
        logger.info(
            "Enqueued task %s (%s, priority=%s) for project %s",
            task.id,
            task.title,
            task.priority.value,
            self.project_id,
        )

    @property
    def queue_size(self) -> int:
        """Number of tasks waiting in the queue."""

        return self._queue.qsize()

    def snapshot(self) -> dict:
        """Current pipeline state, for the initial WebSocket push."""

        return {
            "project_id": self.project_id,
            "stations": {k: dict(v) for k, v in self.station_states.items()},
            "queue_size": self.queue_size,
            "done_count": self.done_count,
            "failed_count": self.failed_count,
        }

    # -- worker lifecycle ------------------------------------------------------

    def start(self) -> None:
        """Start the worker loop on the running event loop."""

        if self._worker is not None and not self._worker.done():
            return
        self._loop = asyncio.get_running_loop()
        self._worker = self._loop.create_task(
            self._worker_loop(), name=f"pipeline:{self.project_id}"
        )
        logger.info("Pipeline worker started for project %s", self.project_id)

    async def stop(self) -> None:
        """Cancel the worker loop and wait for it to finish."""

        if self._worker is None:
            return
        self._worker.cancel()
        try:
            await self._worker
        except asyncio.CancelledError:
            pass
        self._worker = None
        logger.info("Pipeline worker stopped for project %s", self.project_id)

    async def _worker_loop(self) -> None:
        while True:
            _, _, task = await self._queue.get()
            try:
                await self.process_task(task)
            except asyncio.CancelledError:
                raise
            except Exception:  # Requirement 6.5: one bad task never kills the loop.
                logger.exception(
                    "Unexpected pipeline error on task %s (project %s)",
                    task.id,
                    self.project_id,
                )
                await self._fail_task(task, "internal pipeline error")
            finally:
                self._queue.task_done()

    # -- task processing --------------------------------------------------------

    async def process_task(self, task: Task) -> Task:
        """Run one task through PM -> SWE -> QA including the rework loop.

        Exposed publicly so tests can drive a single task without the queue.
        """

        await self._emit(
            EVENT_TASK_CREATED,
            {"task": self._task_summary(task), "queue_size": self.queue_size},
        )
        await self._set_task_status(task, Status.IN_PROGRESS)

        # Station 1: PM analyses the task (Requirement 6.1).
        pm_result = await self._run_station(STATION_PM, task, context=None)
        if not pm_result.success:
            return await self._fail_task(task, pm_result.error or "PM agent failed")
        task.pm_output = pm_result.output

        # Stations 2+3: SWE implements, QA verifies, with rework on QA failure
        # (Requirements 6.2, 6.3, 6.4).
        while True:
            swe_context = {"pm_output": task.pm_output}
            if task.qa_output is not None:
                swe_context["qa_output"] = task.qa_output
            swe_result = await self._run_station(STATION_SWE, task, swe_context)
            if not swe_result.success:
                return await self._fail_task(
                    task, swe_result.error or "SWE agent failed"
                )
            task.swe_output = swe_result.output

            qa_context = {
                "swe_output": task.swe_output,
                "pm_output": task.pm_output,
            }
            qa_result = await self._run_station(STATION_QA, task, qa_context)
            if not qa_result.success:
                return await self._fail_task(task, qa_result.error or "QA agent failed")
            task.qa_output = qa_result.output

            if QAAgent.is_pass(task.qa_output):
                break

            # QA failed the work: rework, bounded by max_reworks.
            task.rework_count += 1
            if task.rework_count > task.max_reworks:
                return await self._fail_task(
                    task,
                    f"QA still failing after {task.max_reworks} reworks",
                )
            logger.info(
                "Task %s sent back to SWE for rework %d/%d (project %s)",
                task.id,
                task.rework_count,
                task.max_reworks,
                self.project_id,
            )
            await self._emit(
                EVENT_STATION_UPDATE,
                {
                    "station": STATION_SWE,
                    "status": "rework",
                    "task": self._task_summary(task),
                },
            )

        await self._set_task_status(task, Status.DONE)
        self.done_count += 1
        await self._emit(
            EVENT_TASK_COMPLETED,
            {"task": self._task_summary(task), "done_count": self.done_count},
        )
        logger.info("Task %s completed (project %s)", task.id, self.project_id)
        return task

    async def _run_station(
        self, station: str, task: Task, context: dict | None
    ) -> AgentResult:
        """Run one agent station in a thread and report its state transitions."""

        agent = self._agents[station]
        self._set_station(station, "working", task)
        await self._emit(
            EVENT_STATION_UPDATE,
            {
                "station": station,
                "status": "working",
                "task": self._task_summary(task),
            },
        )

        # Agent.run is synchronous (blocking LLM I/O): run it off-loop.
        result = await asyncio.to_thread(
            agent.run, task, context, self.project_path
        )

        self._set_station(station, "idle", None)
        await self._emit(
            EVENT_STATION_UPDATE,
            {
                "station": station,
                "status": "done" if result.success else "error",
                "task": self._task_summary(task),
                "success": result.success,
                "error": result.error,
                "execution_time": result.execution_time,
            },
        )
        return result

    async def _fail_task(self, task: Task, reason: str) -> Task:
        """Mark a task failed, write it back, and broadcast the failure."""

        await self._set_task_status(task, Status.FAILED)
        self.failed_count += 1
        await self._emit(
            EVENT_TASK_FAILED,
            {
                "task": self._task_summary(task),
                "reason": reason,
                "failed_count": self.failed_count,
            },
        )
        logger.warning(
            "Task %s failed (project %s): %s", task.id, self.project_id, reason
        )
        return task

    # -- helpers -----------------------------------------------------------------

    def _set_station(self, station: str, status: str, task: Task | None) -> None:
        self.station_states[station] = {
            "status": status,
            "task_id": task.id if task is not None else None,
            "task_title": task.title if task is not None else None,
        }

    async def _set_task_status(self, task: Task, status: Status) -> None:
        task.status = status
        task.updated_at = datetime.now()
        if self._write_status:
            # tasks.yaml is the source of truth; TODO.md-sourced tasks simply
            # have no matching entry and write_task_status returns False.
            await asyncio.to_thread(
                write_task_status, self.project_path, task.id, status
            )

    def _task_summary(self, task: Task) -> dict:
        return {
            "id": task.id,
            "title": task.title,
            "priority": task.priority.value,
            "status": task.status.value,
            "rework_count": task.rework_count,
        }

    async def _emit(self, event: str, data: dict) -> None:
        """Invoke the event callback, awaiting it when async; never raise."""

        if self._on_event is None:
            return
        try:
            result = self._on_event(event, data)
            if inspect.isawaitable(result):
                await result
        except Exception:  # pragma: no cover - defensive
            logger.exception("Pipeline event callback raised for %s", event)
