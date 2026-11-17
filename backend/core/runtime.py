"""Factory runtime: wires every core component together for one process.

The :class:`FactoryRuntime` owns the moving parts the API layer needs to talk
to:

* a fresh project scan on every query (Requirement 1.4),
* per-project :class:`~core.pipeline.PipelineOrchestrator` instances built from
  the project's agent YAML configs (auto-created when missing, Requirement 2.2),
* per-project :class:`~core.task_watcher.TaskWatcher` instances feeding the
  orchestrators (Requirement 5.4),
* one global :class:`~agents.devops_agent.DevOpsAgent` monitor whose alerts are
  broadcast to every connected client (Requirement 2.5),
* the shared :class:`~core.ws_manager.WSManager` used by the WebSocket layer.

Projects dropped into ``projects/`` after startup are picked up lazily: the
first API/WS access to an unknown project id calls :meth:`ensure_project`,
which bootstraps its agents, orchestrator, and watcher on the spot.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime
from pathlib import Path

import config
from agents.devops_agent import DevOpsAgent, DevOpsAlert, EVENT_DEVOPS_ALERT
from agents.pm_agent import PMAgent
from agents.qa_agent import QAAgent
from agents.swe_agent import SWEAgent
from core.agent_config import (
    DEFAULT_AGENT_CONFIGS,
    create_default_agents,
    load_agent_configs,
)
from core.pipeline import PipelineOrchestrator
from core.project_scanner import (
    DEFAULT_PROJECTS_DIR,
    detect_structure,
    scan_projects,
)
from core.project_migration import migrate_project
from core.stores import ProjectRepository, RunRepository, TaskRepository
from core.task_importer import TaskImporter
from core.task_watcher import TaskWatcher, parse_tasks_yaml, parse_todo_md
from core.usage_tracker import UsageTracker
from core.ws_manager import WSManager
from models.agent import AgentConfig
from models.project import Project
from models.task import Approval, Priority, Status, Task

logger = logging.getLogger(__name__)

# Pipeline station agent classes keyed by their canonical config name.
_PIPELINE_AGENT_CLASSES = {
    "PM_agent": PMAgent,
    "SWE_agent": SWEAgent,
    "QA_agent": QAAgent,
}

# How many recent DevOps alerts are kept for the initial WS snapshot.
_ALERT_HISTORY = 20

# Pipeline events after which a task's stored status has (just) changed; in DB mode the
# runtime re-reads the project's tasks for its snapshot cache when one of them is forwarded.
_TASK_STATE_EVENTS = frozenset(
    {
        "task:created",
        "task:awaiting_approval",
        "task:approved",
        "task:rejected",
        "task:completed",
        "task:failed",
    }
)

REASON_INTERRUPTED = "interrupted by restart"
REASON_GATE_LOST = "gate lost after restart"


class FactoryRuntime:
    """Owns and coordinates scanners, orchestrators, watchers, and monitors."""

    def __init__(
        self,
        projects_dir: str | Path | None = None,
        *,
        task_store: str | None = None,
        task_repo: TaskRepository | None = None,
        run_repo: RunRepository | None = None,
        project_repo: ProjectRepository | None = None,
    ) -> None:
        """Args beyond ``projects_dir`` are for DB mode (``TASK_STORE=db``).

        ``task_store`` defaults to the ``TASK_STORE`` environment variable (``file``).
        In DB mode the repositories are normally built from ``DATABASE_URL`` during
        :meth:`startup`; tests inject in-memory ones instead.
        """

        self.projects_dir = (
            Path(projects_dir) if projects_dir is not None else DEFAULT_PROJECTS_DIR
        )
        self.task_store = (
            config.get_task_store() if task_store is None else config.normalize_task_store(task_store)
        )
        self.task_repo = task_repo
        self.run_repo = run_repo
        self.project_repo = project_repo
        self._db_engine = None  # owned engine, when startup built the repositories
        self._loop: asyncio.AbstractEventLoop | None = None
        self._init_futures: dict[str, object] = {}
        self._deferred_init: list[str] = []
        self._project_locks: dict[str, asyncio.Lock] = {}
        self._task_cache: dict[str, list[Task]] = {}
        self.ws_manager = WSManager()
        # Shared cost tracker with budget ceilings read from the environment
        # (task 16). One tracker spans all projects so the daily budget is a
        # true system-wide guard.
        self.usage_tracker = UsageTracker(
            daily_budget_usd=config.get_daily_budget_usd(),
            project_budget_usd=config.get_project_budget_usd(),
        )
        self.orchestrators: dict[str, PipelineOrchestrator] = {}
        self.watchers: dict[str, TaskWatcher] = {}
        self.recent_alerts: deque[dict] = deque(maxlen=_ALERT_HISTORY)
        self.devops_agent: DevOpsAgent | None = None
        self._devops_task: asyncio.Task | None = None

    # -- lifecycle -------------------------------------------------------------

    async def startup(self) -> None:
        """Bootstrap every project on disk and start the global monitor."""

        self._loop = asyncio.get_running_loop()
        if self.task_store == "db":
            await self._open_database()

        providers = config.get_configured_providers()
        if providers:
            logger.info(
                "LLM providers configured: %s",
                ", ".join(p.name for p in providers),
            )
        else:
            logger.warning(
                "No LLM provider API key set — running in [MOCK MODE]; agents "
                "return simulated output."
            )

        self.projects_dir.mkdir(parents=True, exist_ok=True)
        for project in scan_projects(self.projects_dir):
            self.ensure_project(project.id)
        for project_id in self._deferred_init:  # DB-mode projects created before the loop existed
            self._schedule_init(project_id)
        self._deferred_init.clear()
        await self.wait_ready()  # DB mode: import, recover and enqueue before serving

        self.devops_agent = DevOpsAgent(
            on_alert=self._on_devops_alert,
            failed_task_provider=self._total_failed_tasks,
            projects_dir=self.projects_dir,
        )
        self._devops_task = asyncio.get_running_loop().create_task(
            self.devops_agent.start(), name="devops-monitor"
        )
        logger.info(
            "Factory runtime started: %d project(s) under %s",
            len(self.orchestrators),
            self.projects_dir,
        )

    async def shutdown(self) -> None:
        """Gracefully stop watchers, monitors, and pipeline workers (Req 8.6)."""

        for watcher in self.watchers.values():
            watcher.stop()
        self.watchers.clear()

        if self.devops_agent is not None:
            self.devops_agent.stop()
        if self._devops_task is not None:
            self._devops_task.cancel()
            try:
                await self._devops_task
            except asyncio.CancelledError:
                pass
            self._devops_task = None

        for future in self._init_futures.values():
            future.cancel()
        self._init_futures.clear()

        for orchestrator in self.orchestrators.values():
            await orchestrator.stop()
        self.orchestrators.clear()
        if self._db_engine is not None:
            await self._db_engine.dispose()
            self._db_engine = None
        logger.info("Factory runtime shut down.")

    # -- project bootstrap -------------------------------------------------------

    def project_path(self, project_id: str) -> Path:
        return self.projects_dir / project_id

    def project_exists(self, project_id: str) -> bool:
        path = self.project_path(project_id)
        return path.is_dir() and not project_id.startswith(".")

    def ensure_project(self, project_id: str) -> PipelineOrchestrator | None:
        """Bootstrap (or return the existing) orchestrator for a project.

        Returns ``None`` when no such folder exists. Safe to call repeatedly;
        API/WS handlers use it so projects added after startup work lazily.
        """

        if project_id in self.orchestrators:
            return self.orchestrators[project_id]
        if not self.project_exists(project_id):
            return None

        path = self.project_path(project_id)
        if not detect_structure(path).has_agents_dir:
            create_default_agents(path)  # Requirement 2.2
            logger.info("Created default agent configs for project %s", project_id)

        agents = self._build_pipeline_agents(load_agent_configs(path))
        orchestrator = PipelineOrchestrator(
            project_id=project_id,
            project_path=path,
            pm_agent=agents["PM_agent"],
            swe_agent=agents["SWE_agent"],
            qa_agent=agents["QA_agent"],
            on_event=self._make_event_forwarder(project_id),
            usage_tracker=self.usage_tracker,
            task_repo=self.task_repo if self.task_store == "db" else None,
        )
        self.orchestrators[project_id] = orchestrator

        if self.task_store == "db":
            # The watcher is created here but only started by _init_db_project, after the
            # startup steps of plan D3 (1) have run for this project.
            self.watchers[project_id] = TaskWatcher(
                orchestrator.enqueue,
                watch_dir=path,
                on_file_changed=self._file_changed_handler(project_id),
            )
            self._schedule_init(project_id)
            return orchestrator

        watcher = TaskWatcher(orchestrator.enqueue, watch_dir=path)
        watcher.start()
        self.watchers[project_id] = watcher

        try:
            loop_running = asyncio.get_running_loop().is_running()
        except RuntimeError:
            loop_running = False
        if loop_running:
            orchestrator.start()
            # Pick up pending tasks that already exist on disk.
            dispatched = watcher.scan_now()
            if dispatched:
                logger.info(
                    "Enqueued %d existing pending task(s) for project %s",
                    len(dispatched),
                    project_id,
                )
        return orchestrator

    def _build_pipeline_agents(self, configs: list[AgentConfig]) -> dict:
        """Instantiate PM/SWE/QA agents from configs, defaulting missing ones."""

        by_name = {c.name: c for c in configs}
        agents = {}
        for name, cls in _PIPELINE_AGENT_CLASSES.items():
            agent_config = by_name.get(name) or DEFAULT_AGENT_CONFIGS[name]
            agents[name] = cls(agent_config, projects_dir=self.projects_dir)
        return agents

    # -- DB mode: startup order, importing, recovery (plan D3 (1), D24) -------------------

    async def _open_database(self) -> None:
        """Build the repositories from ``DATABASE_URL`` unless they were injected."""

        if self.task_repo is not None:
            return
        from core.stores.pg import PgProjectRepository, PgRunRepository, PgTaskRepository
        from db.session import create_engine_for, session_factory

        self._db_engine = create_engine_for(config.get_database_url())
        sessions = session_factory(self._db_engine)
        self.task_repo = PgTaskRepository(sessions)
        if self.run_repo is None:
            self.run_repo = PgRunRepository(sessions)
        if self.project_repo is None:
            self.project_repo = PgProjectRepository(sessions)
        logger.info("Database connected (%s)", config.mask_database_url(config.get_database_url()))

    def _schedule_init(self, project_id: str) -> None:
        """Run :meth:`_init_db_project` on the event loop, whichever thread we are on."""

        if self._loop is None:  # no loop yet (before startup): startup schedules it
            self._deferred_init.append(project_id)
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        coro = self._init_db_project(project_id)
        if running is self._loop:
            future = asyncio.ensure_future(coro)
        elif self._loop.is_running():
            future = asyncio.run_coroutine_threadsafe(coro, self._loop)  # called from a threadpool
        else:  # pragma: no cover - loop stopped
            coro.close()
            return
        self._init_futures[project_id] = future

    async def wait_ready(self, project_id: str | None = None) -> None:
        """Wait until DB-mode initialisation of one project (or all) has finished."""

        ids = [project_id] if project_id is not None else list(self._init_futures)
        for pid in ids:
            future = self._init_futures.get(pid)
            if future is None:
                continue
            if isinstance(future, asyncio.Future):
                await future
            else:
                await asyncio.wrap_future(future)

    def _lock_for(self, project_id: str) -> asyncio.Lock:
        return self._project_locks.setdefault(project_id, asyncio.Lock())

    async def _init_db_project(self, project_id: str) -> None:
        """The startup order of plan D3 (1), for one project.

        0. a project the database has never seen is migrated first (plan D3 (3)),
        1. import every task file *without* enqueueing,
        2. deal with tasks the previous run left in flight,
        3. enqueue the pending tasks from the database, once, most urgent first,
        4. start the worker and the watcher (it only reacts to later changes).
        """

        orchestrator = self.orchestrators[project_id]
        watcher = self.watchers[project_id]
        importer = TaskImporter(self.task_repo, project_id)
        async with self._lock_for(project_id):
            try:
                await self._init_db_project_locked(project_id, orchestrator, watcher, importer)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Nothing was half-applied (a migration rolls back); the project simply stays
                # idle until the next start retries. Other projects are unaffected.
                logger.exception("Could not initialise project %s in the database; it stays idle", project_id)

    async def _init_db_project_locked(self, project_id, orchestrator, watcher, importer) -> None:
        if self.project_repo is not None:
            await migrate_project(project_id, self.project_path(project_id), self.project_repo)
        for path in await asyncio.to_thread(watcher.existing_task_files):
            await importer.import_file(path)
        await self._recover_in_flight(project_id)
        pending = await self.task_repo.list_pending(project_id)
        for task in pending:
            orchestrator.enqueue(task)
        if pending:
            logger.info("Enqueued %d pending task(s) from the database for project %s",
                        len(pending), project_id)
        orchestrator.start()
        await asyncio.to_thread(watcher.prime)
        watcher.start()
        await self._refresh_task_cache(project_id)

    async def _recover_in_flight(self, project_id: str) -> None:
        """Tasks the previous process left running can not continue: mark them failed.

        ``in_progress`` tasks were mid-LLM-call. ``awaiting_approval`` tasks lost their gate
        (the in-memory checkpointer is gone); resuming gates after a restart is Task 14, which
        replaces the second loop. Any unfinished history run of such a task is closed with the
        reason.
        """

        for status, reason in (
            (Status.IN_PROGRESS, REASON_INTERRUPTED),
            (Status.AWAITING_APPROVAL, REASON_GATE_LOST),
        ):
            for task in await self.task_repo.list_tasks(project_id, statuses=[status]):
                await self.task_repo.set_status(project_id, task.id, Status.FAILED)
                if self.run_repo is not None:
                    await self.run_repo.supersede_open_runs(
                        project_id, task.id, reason=reason, finished_at=datetime.now()
                    )
                logger.warning("Task %s of project %s marked failed: %s", task.id, project_id, reason)

    def _file_changed_handler(self, project_id: str):
        """Watcher callback (watchdog thread): hand the changed file to the event loop."""

        def handler(path: Path) -> None:
            loop = self._loop
            if loop is None or not loop.is_running():
                return
            future = asyncio.run_coroutine_threadsafe(self._import_changed(project_id, path), loop)
            future.add_done_callback(self._log_import_failure)

        return handler

    @staticmethod
    def _log_import_failure(future) -> None:
        if not future.cancelled() and future.exception() is not None:
            logger.error("Importing a changed task file failed", exc_info=future.exception())

    async def _import_changed(self, project_id: str, path: Path) -> None:
        """Import one changed file and enqueue whatever is newly pending (on the event loop)."""

        orchestrator = self.orchestrators.get(project_id)
        if orchestrator is None or not path.is_file():
            return
        async with self._lock_for(project_id):
            result = await TaskImporter(self.task_repo, project_id).import_file(path)
            for task in result.to_enqueue:
                orchestrator.enqueue(task)
            await self._refresh_task_cache(project_id)

    async def _refresh_task_cache(self, project_id: str) -> None:
        self._task_cache[project_id] = await self.task_repo.list_tasks(project_id)

    # -- DB mode: operations used by the REST layer ---------------------------------------------
    # The endpoints are synchronous (they run in a threadpool), the repositories are async and
    # belong to the event loop: run_on_loop bridges the two.

    def run_on_loop(self, coro, timeout: float = 30.0):
        """Run ``coro`` on the runtime's event loop and wait for its result (from a worker thread)."""

        if self._loop is None or not self._loop.is_running():
            coro.close()
            raise RuntimeError("the runtime's event loop is not running")
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    async def create_task(
        self,
        project_id: str,
        title: str,
        description: str = "",
        priority: Priority = Priority.MEDIUM,
        approval: Approval = Approval.NONE,
    ) -> Task:
        """DB mode: store a task created from the UI and enqueue it (tasks.yaml is not involved).

        Raises ``ValueError`` for an empty title.
        """

        await self.wait_ready(project_id)  # never race the startup enqueue of this project
        async with self._lock_for(project_id):
            task = await self.task_repo.create(project_id, title, description, priority, approval)
            self.orchestrators[project_id].enqueue(task)
            await self._refresh_task_cache(project_id)
        return task

    async def rerun_task(self, project_id: str, task_id: str) -> Task:
        """DB mode: put a finished task back in the to-do tray and enqueue it again.

        Raises ``KeyError`` for an unknown task and ``ValueError`` unless it is done or failed.
        """

        await self.wait_ready(project_id)
        async with self._lock_for(project_id):
            task = await self.task_repo.get(project_id, task_id)
            if task is None:
                raise KeyError(task_id)
            if task.status not in (Status.DONE, Status.FAILED):
                raise ValueError(f"Task {task_id} is {task.status.value}; only done or failed tasks can be rerun")
            await self.task_repo.set_rework_count(project_id, task_id, 0)
            await self.task_repo.set_status(project_id, task_id, Status.PENDING)
            fresh = await self.task_repo.get(project_id, task_id)
            self.orchestrators[project_id].enqueue(fresh)
            await self._refresh_task_cache(project_id)
        return fresh

    async def list_tasks_db(self, project_id: str) -> list[Task]:
        return await self.task_repo.list_tasks(project_id)

    async def get_task_db(self, project_id: str, task_id: str) -> Task | None:
        return await self.task_repo.get(project_id, task_id)

    # -- event plumbing -----------------------------------------------------------

    def _make_event_forwarder(self, project_id: str):
        async def forward(event: str, data: dict) -> None:
            # Budget alerts raised by the pipeline are system-wide DevOps
            # alerts: record them in history and broadcast to every client so
            # the DevOps panel shows them like any other alert (task 16).
            if event == EVENT_DEVOPS_ALERT:
                self.recent_alerts.append(data)
                await self.ws_manager.broadcast_all(EVENT_DEVOPS_ALERT, data)
                return
            if self.task_store == "db" and event in _TASK_STATE_EVENTS:
                # The status was stored before the event was emitted, so the database is current.
                await self._refresh_task_cache(project_id)
            await self.ws_manager.broadcast(project_id, event, data)

        return forward

    async def _on_devops_alert(self, alert: DevOpsAlert) -> None:
        data = alert.to_event_data()
        self.recent_alerts.append(data)
        await self.ws_manager.broadcast_all(EVENT_DEVOPS_ALERT, data)

    def _total_failed_tasks(self) -> int:
        return sum(o.failed_count for o in self.orchestrators.values())

    # -- queries used by the API layer --------------------------------------------

    def list_projects(self) -> list[Project]:
        """Fresh scan of ``projects/`` (Requirement 1.4), enriched with state."""

        projects = scan_projects(self.projects_dir)
        for project in projects:
            tasks = self.load_tasks(project.id)
            project.task_count = len(tasks)
            project.active_task_count = sum(
                1 for t in tasks if t.status is Status.IN_PROGRESS
            )
            orchestrator = self.orchestrators.get(project.id)
            if orchestrator is not None:
                working = any(
                    s["status"] == "working"
                    for s in orchestrator.station_states.values()
                )
                project.status = "running" if working else "idle"
        return projects

    def load_tasks(self, project_id: str) -> list[Task]:
        """All tasks for a project from tasks.yaml plus TODO.md (Req 5.1, 5.3).

        DB mode: the tasks as the database has them, from a cache the runtime refreshes
        after every import and every task event (this method is synchronous, so it can not
        query; async code should call the repository instead).
        """

        if self.task_store == "db":
            return [t.model_copy() for t in self._task_cache.get(project_id, [])]
        path = self.project_path(project_id)
        tasks: list[Task] = []
        if (path / "tasks.yaml").is_file():
            tasks.extend(parse_tasks_yaml(path / "tasks.yaml"))
        if (path / "TODO.md").is_file():
            tasks.extend(parse_todo_md(path / "TODO.md"))
        return tasks

    def snapshot(self, project_id: str) -> dict:
        """Initial state pushed to a newly-connected WS client (Req 7.1)."""

        orchestrator = self.orchestrators.get(project_id)
        pipeline = (
            orchestrator.snapshot()
            if orchestrator is not None
            else {"project_id": project_id, "stations": {}, "queue_size": 0}
        )
        return {
            "pipeline": pipeline,
            "tasks": [t.model_dump(mode="json") for t in self.load_tasks(project_id)],
            "mock_mode": config.is_mock_mode(),
            "providers": [p.name for p in config.get_configured_providers()],
            "devops_alerts": list(self.recent_alerts),
            "usage": self.usage_summary(project_id),
        }

    def usage_summary(self, project_id: str) -> dict:
        """Accumulated-spend summary for a project (task 16)."""

        return {
            "project": self.usage_tracker.project_usage(project_id).to_dict(),
            "daily_cost": self.usage_tracker.daily_cost(),
            "daily_budget_usd": self.usage_tracker.daily_budget_usd,
            "project_budget_usd": self.usage_tracker.project_budget_usd,
            "budget_paused": (
                orchestrator.is_budget_paused()
                if (orchestrator := self.orchestrators.get(project_id)) is not None
                else self.usage_tracker.is_over_budget(project_id)
            ),
        }
