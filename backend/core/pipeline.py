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

import config
from agents.base_agent import AgentResult, BaseAgent
from agents.devops_agent import EVENT_DEVOPS_ALERT, AlertLevel
from agents.qa_agent import QAAgent
from core.deliverables import write_deliverable
from core.stores import TaskRepository
from core.task_watcher import write_task_status
from core.usage_tracker import UsageTracker
from models.task import Approval, Priority, Status, Task

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
# Emitted after each LLM call is costed so the UI can show accumulated spend.
EVENT_USAGE_UPDATE = "usage:update"
# Human-in-the-loop approval gate events (task 17): the pipeline pauses and
# emits ``task:awaiting_approval``; the approve/reject decision then produces a
# ``task:approved`` / ``task:rejected`` event as the pipeline resumes.
EVENT_TASK_AWAITING_APPROVAL = "task:awaiting_approval"
EVENT_TASK_APPROVED = "task:approved"
EVENT_TASK_REJECTED = "task:rejected"

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
        write_deliverables: Whether to persist each successful station's output
            as deliverable files under ``deliverables/{task_id}/``. Tests that
            only care about scheduling may disable it.
        engine: ``"legacy"`` (the original loop) or ``"langgraph"``. ``None``
            reads the ``PIPELINE_ENGINE`` environment variable (default
            ``langgraph``). An unknown name raises :class:`ValueError`.
        task_repo: Where task status changes are stored. ``None`` (file mode, the
            default) keeps writing them back to ``tasks.yaml`` through
            ``write_task_status``. With a repository (``TASK_STORE=db``) the status goes
            to the repository and ``tasks.yaml`` is never written (plan D3).
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
        write_deliverables: bool = True,
        usage_tracker: UsageTracker | None = None,
        engine: str | None = None,
        task_repo: TaskRepository | None = None,
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
        self._task_repo = task_repo
        self._write_deliverables = write_deliverables
        self._usage_tracker = usage_tracker
        # Set once a budget ceiling is crossed; blocks further enqueueing until
        # cleared. A dedicated critical devops:alert is emitted only on the
        # transition into the paused state (task 16).
        self._budget_paused = False

        self.engine = (
            config.get_pipeline_engine()
            if engine is None
            else config.normalize_pipeline_engine(engine)
        )
        if self.engine == "langgraph":
            # Imported lazily so the legacy engine never loads langgraph.
            from core.pipeline_graph import PipelineGraphRunner

            self._graph_runner = PipelineGraphRunner(self)

        self._queue: asyncio.PriorityQueue[tuple[int, int, Task]] = (
            asyncio.PriorityQueue()
        )
        # FIFO tiebreaker for tasks sharing a priority (Requirement 6.6).
        self._seq = itertools.count()
        # Restart recovery (plan D7): tasks whose gate was restored from the checkpointer. The
        # worker resumes them before taking anything from the queue, so the line still runs one
        # task at a time, exactly as when the worker waited at the gate before the restart.
        self._resume_first: list[Task] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._worker: asyncio.Task | None = None

        # Human-in-the-loop approval gates (task 17). When a task pauses at a
        # gate an :class:`asyncio.Event` is registered here keyed by task id;
        # the approve/reject endpoints resolve it by recording a decision and
        # setting the event. ``_awaiting`` mirrors the currently-paused tasks so
        # a freshly-connected client's snapshot can render the gate.
        self._approval_gates: dict[str, asyncio.Event] = {}
        self._approval_decisions: dict[str, tuple[str, str]] = {}
        self._awaiting: dict[str, dict] = {}
        # ``_resolve_gate`` pops the gate from ``_approval_gates`` the moment a
        # decision arrives, so the waiter keeps its own reference to the Event
        # here (set by ``_gate_enter``, cleared by ``_gate_wait``).
        self._gate_events: dict[str, asyncio.Event] = {}
        # Ids of the tasks inside process_task right now (the note wall's live status, plan §7).
        self._running: set[str] = set()

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

        When a budget ceiling has been crossed the task is dropped rather than
        enqueued, so no further LLM spend is incurred until the budget is reset
        (task 16).
        """

        if self.is_budget_paused():
            logger.warning(
                "Budget exceeded for project %s — enqueue paused; skipping task %s (%s)",
                self.project_id,
                task.id,
                task.title,
            )
            return

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

    def use_checkpointer(self, checkpointer) -> None:
        """Graph engine: checkpoint into ``checkpointer`` from now on (plan D7).

        The runtime calls this before the worker starts, with the saver shared by every project
        (``AsyncPostgresSaver`` when ``PIPELINE_CHECKPOINTER=postgres``). Without it the graph
        keeps its own in-memory saver. The legacy engine has no graph: ``ValueError``.
        """

        if self.engine != "langgraph":
            raise ValueError("the legacy engine has no graph, so it can not use a checkpointer")
        self._graph_runner.use_checkpointer(checkpointer)

    def is_budget_paused(self) -> bool:
        """Whether enqueueing is currently paused due to a budget ceiling.

        Consults the sticky ``_budget_paused`` flag and, defensively, the
        tracker's current status (in case spend crossed a limit out-of-band).
        """

        if self._budget_paused:
            return True
        if self._usage_tracker is not None:
            return self._usage_tracker.budget_status(self.project_id).exceeded
        return False

    # -- human-in-the-loop approval gate (task 17) ----------------------------

    def is_awaiting_approval(self, task_id: str) -> bool:
        """Whether ``task_id`` is currently paused at an approval gate."""

        return task_id in self._approval_gates

    def running_task_ids(self) -> frozenset[str]:
        """Tasks being processed right now (including one paused at its gate)."""

        return frozenset(self._running)

    def awaiting_task_ids(self) -> frozenset[str]:
        """Tasks paused at an approval gate right now."""

        return frozenset(self._awaiting)

    def approve(self, task_id: str) -> bool:
        """Resume a task paused at an approval gate (task 17).

        Records an *approve* decision and wakes the paused pipeline worker so it
        continues past the gate (on to QA, or to done). Thread-safe: the REST
        approve endpoint runs in a worker thread, so the event is set on the
        orchestrator's loop with ``call_soon_threadsafe``.

        Returns ``True`` when a matching gate was found, ``False`` otherwise
        (e.g. the task is not currently awaiting approval).
        """

        return self._resolve_gate(task_id, "approve", "")

    def reject(self, task_id: str, note: str = "") -> bool:
        """Reject a task paused at an approval gate, routing it to rework (task 17).

        Records a *reject* decision plus a reviewer ``note`` and wakes the paused
        worker, which then increments ``rework_count`` and re-runs SWE with the
        note as context (respecting ``max_reworks``). Thread-safe like
        :meth:`approve`.

        Returns ``True`` when a matching gate was found, ``False`` otherwise.
        """

        return self._resolve_gate(task_id, "reject", note or "")

    def _resolve_gate(self, task_id: str, decision: str, note: str) -> bool:
        # Pop the gate as we record the decision so the task stops counting as
        # "awaiting approval" immediately — before the worker has resumed. This
        # prevents a second decision (or a poller) from re-resolving the same
        # gate, and lets callers reliably wait for the *next* gate on rework.
        event = self._approval_gates.pop(task_id, None)
        if event is None:
            return False
        self._awaiting.pop(task_id, None)
        self._approval_decisions[task_id] = (decision, note)
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(event.set)
        else:
            event.set()
        return True

    def snapshot(self) -> dict:
        """Current pipeline state, for the initial WebSocket push."""

        snap = {
            "project_id": self.project_id,
            "stations": {k: dict(v) for k, v in self.station_states.items()},
            "queue_size": self.queue_size,
            "done_count": self.done_count,
            "failed_count": self.failed_count,
            "budget_paused": self.is_budget_paused(),
            # Tasks currently paused at a human approval gate (task 17).
            "awaiting_approval": [dict(v) for v in self._awaiting.values()],
        }
        if self._usage_tracker is not None:
            snap["usage"] = self._usage_tracker.project_usage(self.project_id).to_dict()
        return snap

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
            if self._resume_first:
                await self._resume_restored(self._resume_first.pop(0))
                continue
            _, _, task = await self._queue.get()
            try:
                if self._task_repo is not None:
                    refreshed = await self._refresh_from_store(task)
                    if refreshed is None:
                        continue  # the finally below still marks the queue item done
                    task = refreshed
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

    async def _resume_restored(self, task: Task) -> None:
        """Run a task restored at its gate to the end (same error isolation as the queue)."""

        try:
            await self.process_task(task)  # the graph sees the waiting gate and resumes it
        except asyncio.CancelledError:
            raise
        except Exception:  # Requirement 6.5: one bad task never kills the loop.
            logger.exception("Unexpected pipeline error on restored task %s (project %s)",
                             task.id, self.project_id)
            await self._fail_task(task, "internal pipeline error")

    async def restore_gate(self, task: Task) -> bool:
        """Restart recovery (plan D7): pick up ``task`` where it waits at its approval gate.

        ``task`` is the task as stored (``awaiting_approval``). If the checkpointer still holds
        its graph stopped at the gate, the outputs it had are put back on ``task``, the gate is
        registered again (snapshot, approve and reject work at once) without writing a status or
        emitting an event (``task:awaiting_approval`` is not sent again, E5), and the worker
        will resume it before anything in the queue. Returns ``False`` when there is nothing to
        resume (legacy engine, an in-memory checkpointer that died with the old process, or a
        graph that never got to the gate); the caller then fails the task.
        """

        if self.engine != "langgraph":
            return False
        station = await self._graph_runner.restore_gate(task)
        if station is None:
            return False
        self._gate_register(task, station)
        self._resume_first.append(task)
        return True

    async def _refresh_from_store(self, task: Task) -> Task | None:
        """DB mode: re-read a queued task just before it starts (plan D24).

        The queue holds the object from enqueue time, but the content of a pending task may
        have changed since (a tasks.yaml edit imported in the meantime). Returns the task as
        the database has it now, or ``None`` when it is gone or no longer pending (then the
        worker skips it silently). A task already in the queue keeps its queue position even
        if its priority was edited; only what it contains is refreshed.
        """

        fresh = await self._task_repo.get(self.project_id, task.id)
        if fresh is None:
            logger.info("Task %s is no longer in the database; skipping it (project %s)",
                        task.id, self.project_id)
            return None
        if fresh.status is not Status.PENDING:
            logger.info("Task %s is %s in the database, not pending; skipping it (project %s)",
                        task.id, fresh.status.value, self.project_id)
            return None
        return fresh

    # -- task processing --------------------------------------------------------

    async def process_task(self, task: Task) -> Task:
        """Run one task through PM -> SWE -> QA including the rework loop.

        Exposed publicly so tests can drive a single task without the queue.
        """

        self._running.add(task.id)
        try:
            if self.engine == "langgraph":
                return await self._graph_runner.run(task)
            return await self._process_task_legacy(task)
        finally:
            self._running.discard(task.id)

    async def _start_task(self, task: Task) -> None:
        """Announce a task (``task:created``) and mark it in progress."""

        await self._emit(
            EVENT_TASK_CREATED,
            {"task": self._task_summary(task), "queue_size": self.queue_size},
        )
        await self._set_task_status(task, Status.IN_PROGRESS)

    def _build_swe_context(self, task: Task, reject_note: str | None) -> dict:
        """Context handed to SWE: PM plan, QA's last report, reviewer feedback."""

        swe_context = {"pm_output": task.pm_output}
        if task.qa_output is not None:
            swe_context["qa_output"] = task.qa_output
        if reject_note:
            # A human rejected the previous attempt: hand the note to SWE so
            # the rework is guided by the reviewer's feedback (task 17).
            swe_context["approval_feedback"] = reject_note
        return swe_context

    async def _complete_task(self, task: Task, deliverables: list[str]) -> Task:
        """Mark a task done, count it, and broadcast ``task:completed``."""

        await self._set_task_status(task, Status.DONE)
        self.done_count += 1
        await self._emit(
            EVENT_TASK_COMPLETED,
            {
                "task": self._task_summary(task),
                "done_count": self.done_count,
                "deliverables": deliverables,
            },
        )
        logger.info("Task %s completed (project %s)", task.id, self.project_id)
        return task

    async def _process_task_legacy(self, task: Task) -> Task:
        """The original ``while`` loop implementation of :meth:`process_task`."""

        await self._start_task(task)

        # Deliverable file paths (relative to the project) accumulated as each
        # station succeeds, reported on task:completed so the UI/API can offer
        # them for download (Requirement: users must obtain the deliverables).
        deliverables: list[str] = []

        # Station 1: PM analyses the task (Requirement 6.1).
        pm_result = await self._run_station(STATION_PM, task, context=None)
        if not pm_result.success:
            return await self._fail_task(task, pm_result.error or "PM agent failed")
        task.pm_output = pm_result.output
        deliverables += await self._persist_deliverable(
            task, STATION_PM, pm_result.output
        )

        # Stations 2+3: SWE implements, QA verifies, with rework on QA failure
        # (Requirements 6.2, 6.3, 6.4). A human approval gate can pause the flow
        # after SWE or after QA passes (task 17); a rejection re-enters the same
        # rework loop, feeding the reviewer's note back to SWE.
        reject_note: str | None = None
        while True:
            swe_context = self._build_swe_context(task, reject_note)
            swe_result = await self._run_station(STATION_SWE, task, swe_context)
            if not swe_result.success:
                return await self._fail_task(
                    task, swe_result.error or "SWE agent failed"
                )
            task.swe_output = swe_result.output
            reject_note = None
            deliverables += await self._persist_deliverable(
                task, STATION_SWE, swe_result.output
            )

            # Approval gate after SWE (task 17): pause before QA verifies.
            if task.approval is Approval.AFTER_SWE:
                decision, note = await self._await_approval(task, STATION_SWE)
                if decision == "reject":
                    failed = await self._enter_rework(
                        task, "Rejected at approval gate (after SWE)"
                    )
                    if failed is not None:
                        return failed
                    reject_note = note
                    continue

            qa_context = {
                "swe_output": task.swe_output,
                "pm_output": task.pm_output,
            }
            qa_result = await self._run_station(STATION_QA, task, qa_context)
            if not qa_result.success:
                return await self._fail_task(task, qa_result.error or "QA agent failed")
            task.qa_output = qa_result.output
            deliverables += await self._persist_deliverable(
                task, STATION_QA, qa_result.output
            )

            if QAAgent.is_pass(task.qa_output):
                # Approval gate after QA (task 17): pause before marking done.
                if task.approval is Approval.AFTER_QA:
                    decision, note = await self._await_approval(task, STATION_QA)
                    if decision == "reject":
                        failed = await self._enter_rework(
                            task, "Rejected at approval gate (after QA)"
                        )
                        if failed is not None:
                            return failed
                        reject_note = note
                        continue
                break

            # QA failed the work: rework, bounded by max_reworks.
            failed = await self._enter_rework(
                task, f"QA still failing after {task.max_reworks} reworks"
            )
            if failed is not None:
                return failed

        return await self._complete_task(task, deliverables)

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
        await self._record_usage(task, result)
        return result

    async def _record_usage(self, task: Task, result: AgentResult) -> None:
        """Record this station's LLM usage, emit ``usage:update``, guard budget.

        No-ops when no tracker is wired or the agent reported no token usage
        (e.g. a client that does not surface usage). After recording, an
        accumulated-spend snapshot is broadcast and the budget is re-checked
        (task 16).
        """

        if self._usage_tracker is None:
            return
        if result.input_tokens == 0 and result.output_tokens == 0:
            return

        record = self._usage_tracker.record(
            project_id=self.project_id,
            task_id=task.id,
            provider=result.provider,
            model=result.llm_model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )
        task_usage = self._usage_tracker.task_usage(self.project_id, task.id)
        project_usage = self._usage_tracker.project_usage(self.project_id)
        await self._emit(
            EVENT_USAGE_UPDATE,
            {
                "task_id": task.id,
                "task": self._task_summary(task),
                "record": record.to_dict(),
                "task_usage": task_usage.to_dict(),
                "project_usage": project_usage.to_dict(),
                "daily_cost": self._usage_tracker.daily_cost(),
            },
        )
        await self._check_budget()

    async def _check_budget(self) -> None:
        """Pause enqueueing and emit a critical alert when a budget is crossed.

        The critical ``devops:alert`` fires only on the transition into the
        paused state, so a run that keeps spending after the ceiling does not
        spam identical alerts (task 16).
        """

        if self._usage_tracker is None or self._budget_paused:
            return
        status = self._usage_tracker.budget_status(self.project_id)
        if not status.exceeded:
            return
        self._budget_paused = True
        logger.warning(
            "Budget ceiling crossed for project %s: %s",
            self.project_id,
            status.message,
        )
        await self._emit(
            EVENT_DEVOPS_ALERT,
            {
                "level": AlertLevel.CRITICAL.value,
                "message": status.message,
                "metric": "budget",
                "value": status.spend,
            },
        )

    async def _persist_deliverable(
        self, task: Task, station: str, content: str | None
    ) -> list[str]:
        """Write a station's output to disk and return the project-relative paths.

        File I/O is run off-loop. Persisting deliverables must never break the
        pipeline, so any failure is logged and treated as "no files written".
        """

        if not self._write_deliverables or content is None:
            return []
        agent = self._agents[station]
        try:
            paths = await asyncio.to_thread(
                write_deliverable,
                self.project_path,
                task.id,
                agent.name,
                content,
            )
        except OSError:  # pragma: no cover - defensive I/O guard
            logger.exception(
                "Failed to write deliverable for task %s at station %s (project %s)",
                task.id,
                station,
                self.project_id,
            )
            return []
        return [self._relative_path(p) for p in paths]

    def _relative_path(self, path: Path) -> str:
        """Return ``path`` relative to the project root when possible."""

        try:
            return str(path.relative_to(self.project_path))
        except ValueError:  # pragma: no cover - path outside project
            return str(path)

    async def _enter_rework(
        self, task: Task, fail_reason: str, station: str = STATION_SWE
    ) -> Task | None:
        """Increment the rework counter and emit a rework event.

        Shared by the QA-failure path and the approval-rejection path (task 17).
        Returns the (already-failed) task when the rework ceiling is exceeded so
        the caller can ``return`` it; returns ``None`` when the task should be
        reworked (the caller then re-runs SWE). ``station`` is the seat the work
        goes back to (a custom lineup may send it to another make seat, plan D17).
        """

        task.rework_count += 1
        if task.rework_count > task.max_reworks:
            return await self._fail_task(task, fail_reason)
        logger.info(
            "Task %s sent back to %s for rework %d/%d (project %s)",
            task.id,
            station.upper(),
            task.rework_count,
            task.max_reworks,
            self.project_id,
        )
        await self._emit(
            EVENT_STATION_UPDATE,
            {
                "station": station,
                "status": "rework",
                "task": self._task_summary(task),
            },
        )
        return None

    async def _await_approval(self, task: Task, station: str) -> tuple[str, str]:
        """Pause the pipeline at a human approval gate and wait for a decision.

        Sets the task's status to ``awaiting_approval`` (persisted to
        ``tasks.yaml`` and broadcast so the UI can render the gate), then blocks
        on an :class:`asyncio.Event` until :meth:`approve` or :meth:`reject`
        resolves it. Returns ``(decision, note)`` where ``decision`` is
        ``"approve"`` or ``"reject"`` (task 17).

        Composed of three steps (:meth:`_gate_enter`, :meth:`_gate_wait`,
        :meth:`_gate_exit`) so another engine can run them as separate steps.
        """

        await self._gate_enter(task, station)
        decision, note = await self._gate_wait(task)
        await self._gate_exit(task, station, decision, note)
        return decision, note

    async def _gate_enter(self, task: Task, station: str) -> None:
        """Register the gate and announce it (first half of the approval gate)."""

        # The summary is taken before the status flips, as it always was.
        summary = self._task_summary(task)
        try:
            # Persist ``awaiting_approval`` first and register the gate only
            # afterwards: anyone who sees the gate registered (is_awaiting_approval,
            # snapshot, approve) can then rely on tasks.yaml already saying so.
            await self._set_task_status(task, Status.AWAITING_APPROVAL)
            self._gate_register(task, station, summary)
            await self._emit(
                EVENT_TASK_AWAITING_APPROVAL,
                {
                    "task": self._task_summary(task),
                    "station": station,
                    "approval": task.approval.value,
                },
            )
        except BaseException:
            # Cancelled (e.g. stop()) or failed after the gate was registered
            # but before anyone waits on it: nothing else would clear it.
            self._discard_gate(task.id)
            raise
        logger.info(
            "Task %s paused at approval gate after %s (project %s)",
            task.id,
            station,
            self.project_id,
        )

    def _gate_register(self, task: Task, station: str, summary: dict | None = None) -> None:
        """Register a gate and show the station waiting; no status write, no event.

        Used by :meth:`_gate_enter` and, after a restart, by :meth:`restore_gate`.
        """

        event = asyncio.Event()
        self._approval_gates[task.id] = event
        self._gate_events[task.id] = event
        self._awaiting[task.id] = {
            "task": summary if summary is not None else self._task_summary(task),
            "station": station,
            "approval": task.approval.value,
        }
        self._set_station(station, "awaiting_approval", task)

    def _discard_gate(self, task_id: str) -> None:
        """Forget every trace of a task's gate (cancellation / failure cleanup)."""

        self._gate_events.pop(task_id, None)
        self._approval_gates.pop(task_id, None)
        self._awaiting.pop(task_id, None)
        self._approval_decisions.pop(task_id, None)

    async def _gate_wait(self, task: Task) -> tuple[str, str]:
        """Block until the gate is resolved; return ``(decision, note)``."""

        event = self._gate_events[task.id]
        try:
            await event.wait()
        finally:
            # Normally already removed by _resolve_gate; pop defensively in case
            # the wait was cancelled (e.g. pipeline shutdown) before a decision.
            self._gate_events.pop(task.id, None)
            self._approval_gates.pop(task.id, None)
            self._awaiting.pop(task.id, None)

        return self._approval_decisions.pop(task.id, ("approve", ""))

    async def _gate_exit(
        self, task: Task, station: str, decision: str, note: str
    ) -> None:
        """Resume after a decision (second half of the approval gate)."""

        self._set_station(station, "idle", None)
        # Resume: the task is in progress again until the next state change.
        await self._set_task_status(task, Status.IN_PROGRESS)

        if decision == "reject":
            await self._emit(
                EVENT_TASK_REJECTED,
                {"task": self._task_summary(task), "note": note, "station": station},
            )
        else:
            await self._emit(
                EVENT_TASK_APPROVED,
                {"task": self._task_summary(task), "station": station},
            )
        logger.info(
            "Task %s %s at approval gate (project %s)",
            task.id,
            "rejected" if decision == "reject" else "approved",
            self.project_id,
        )

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
        if self._task_repo is not None:
            # DB mode: the repository is the source of truth; tasks.yaml is left alone.
            # (An unknown task id returns False, like a TODO.md task in file mode.)
            await self._task_repo.set_status(self.project_id, task.id, status)
        elif self._write_status:
            # File mode: tasks.yaml is the source of truth. This call must keep going through
            # the module-level name ``write_task_status``: the characterization tests
            # monkeypatch it to record the write sequence. TODO.md-sourced tasks simply
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
