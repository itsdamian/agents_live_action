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
import contextlib
import logging
from collections import deque
from datetime import datetime
from pathlib import Path

import config
from agents.base_agent import BaseAgent
from agents.custom_agent import CustomAgent, custom_agent_config
from agents.devops_agent import DevOpsAgent, DevOpsAlert, EVENT_DEVOPS_ALERT
from agents.pm_agent import PMAgent
from agents.qa_agent import QAAgent
from agents.swe_agent import SWEAgent
from core.agent_config import (
    DEFAULT_AGENT_CONFIGS,
    create_default_agents,
    load_agent_configs,
)
from core.lineup_yaml import import_from_agent_files
from core.pipeline import EVENT_AGENT_LEFT, EVENT_AGENT_PHASE, PipelineOrchestrator
from core.project_scanner import (
    DEFAULT_PROJECTS_DIR,
    detect_structure,
    scan_projects,
)
from core.project_migration import migrate_project
from core.stores import (
    AgentRecord,
    LineupRepository,
    PipelineRecord,
    ProjectRepository,
    RunRepository,
    TaskRepository,
    UsageRepository,
)
from core.event_bus import EventBus, InProcessEventBus
from core.history_view import LiveState, build_history, count_statuses, task_runs_view
from core.task_history import DEFAULT_SEATS, HistoryRecorder, TrackerUsage
from core.task_importer import TaskImporter
from core.task_watcher import TaskWatcher, parse_tasks_yaml, parse_todo_md
from core.usage_tracker import UsageStore, UsageTracker
from core.ws_manager import WSManager
from models.agent import AgentConfig
from models.lineup import (
    BUILTIN_SEATS,
    DEFAULT_LINEUP,
    DEFAULT_PIPELINE_ID,
    DEFAULT_PIPELINE_NAME,
    AgentView,
    InvalidLineup,
    Lineup,
    Seat,
    lineup_errors,
)
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

# Events after which the usage records kept by a failed write are written first, so the history
# recorder, which re-sums a run's cost from the store on them, sees every call (plan D20).
_USAGE_SUM_EVENTS = frozenset({"usage:update", "task:completed", "task:failed"})
_TASK_END_EVENTS = frozenset({"task:completed", "task:failed"})
# Built-in seats by id, and the agent config file each built-in is made from.
_BUILTIN_SEAT = {seat.id: seat for seat in BUILTIN_SEATS}
_BUILTIN_CONFIG = {"pm": "PM_agent", "swe": "SWE_agent", "qa": "QA_agent"}

REASON_INTERRUPTED = "interrupted by restart"
REASON_GATE_LOST = "gate lost after restart"


class ProjectInitFailedError(RuntimeError):
    """DB mode: the project's startup failed, so its worker is not running and it takes no new work."""

    def __init__(self, project_id: str) -> None:
        super().__init__(
            f"Project {project_id} failed to initialise in the database, so it can not run tasks; "
            "see the server log, then restart the server to retry"
        )
        self.project_id = project_id


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
        usage_store: UsageStore | None = None,
        usage_repo: UsageRepository | None = None,
        lineup_repo: LineupRepository | None = None,
        checkpointer=None,
        event_bus: EventBus | None = None,
    ) -> None:
        """Args beyond ``projects_dir`` are for DB mode (``TASK_STORE=db``).

        ``task_store`` defaults to the ``TASK_STORE`` environment variable (``file``).
        In DB mode the repositories are normally built from ``DATABASE_URL`` during
        :meth:`startup`; tests inject in-memory ones instead.

        ``checkpointer`` (DB mode, graph engine) is a LangGraph checkpoint saver shared by every
        project's graph (plan D7). Normally ``None``: with ``PIPELINE_CHECKPOINTER=postgres``
        the runtime opens an ``AsyncPostgresSaver`` on ``DATABASE_URL`` before the first project
        starts (and shutdown closes it); otherwise each graph keeps its own in-memory saver.
        Tests inject one to survive a simulated restart.

        ``event_bus`` (plan D8) is where project events leave the runtime; by default an
        :class:`~core.event_bus.InProcessEventBus`. The WebSocket delivery subscribes to it.

        ``lineup_repo`` (DB mode) holds the roster and the seats (plan D4, D17). Built from
        ``DATABASE_URL`` with the other repositories; tests that inject theirs without it run
        the default lineup from the agent YAML files, as before the lineup existed.
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
        self._usage_store = usage_store
        # DB mode: what the history recorder sums a run's cost from (plan D20). Built with the
        # repositories; when tests inject theirs without it, the tracker's records stand in.
        self.usage_repo = usage_repo
        self.lineup_repo = lineup_repo
        # DB mode with a lineup repository: the roster as stored, every agent instance by id and
        # the default pipeline's name, per project (kept current by every roster change).
        self._rosters: dict[str, list[AgentRecord]] = {}
        self._agent_instances: dict[str, dict[str, BaseAgent]] = {}
        self._pipeline_names: dict[str, str] = {}
        # Snapshot ``live``: the latest agent:phase of every seat, per project (plan §6.4).
        self._live: dict[str, dict[str, dict]] = {}
        self.history: HistoryRecorder | None = None
        self.checkpointer = checkpointer
        self._checkpointer_stack: contextlib.AsyncExitStack | None = None  # owned saver, if opened
        self._checkpointer_lock = asyncio.Lock()
        self._usage_loaded = False  # stored usage is loaded once, before the first project starts
        self._usage_lock = asyncio.Lock()
        self._sync_engine = None  # owned, when startup built the usage store
        self._db_engine = None  # owned engine, when startup built the repositories
        self._loop: asyncio.AbstractEventLoop | None = None
        self._init_futures: dict[str, object] = {}
        self._deferred_init: list[str] = []
        self._init_failed: set[str] = set()  # projects whose DB-mode startup raised (idle until restart)
        self._project_locks: dict[str, asyncio.Lock] = {}
        self._task_cache: dict[str, list[Task]] = {}
        self.ws_manager = WSManager()
        # Every project event is published here; WebSocket clients get it as a subscriber.
        self.event_bus: EventBus = event_bus if event_bus is not None else InProcessEventBus()
        self.event_bus.subscribe(self._deliver_to_clients)
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
            if self.run_repo is not None:
                self.history = HistoryRecorder(
                    self.run_repo,
                    usage=self.usage_repo or TrackerUsage(self.usage_tracker),
                    seats=self.current_seats,
                )

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
        if self.usage_tracker.unsaved_count:  # usage a failed write kept: one more try (plan D20)
            await self.usage_tracker.aflush()
        if self._checkpointer_stack is not None:  # after the workers: no graph uses it any more
            await self._checkpointer_stack.aclose()
            self._checkpointer_stack = None
        if self._db_engine is not None:
            await self._db_engine.dispose()
            self._db_engine = None
        if self._sync_engine is not None:
            self._sync_engine.dispose()
            self._sync_engine = None
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
            lineup_repo=self.lineup_repo if self._lineup_in_db() else None,
            on_live_event=self._make_live_forwarder(project_id),
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
        from core.stores.pg import (
            PgLineupRepository,
            PgProjectRepository,
            PgRunRepository,
            PgTaskRepository,
            PgUsageRepository,
        )
        from core.stores.usage_store import PgUsageStore
        from db.session import create_engine_for, create_sync_engine_for, session_factory

        self._db_engine = create_engine_for(config.get_database_url())
        sessions = session_factory(self._db_engine)
        self.task_repo = PgTaskRepository(sessions)
        if self.run_repo is None:
            self.run_repo = PgRunRepository(sessions)
        if self.project_repo is None:
            self.project_repo = PgProjectRepository(sessions)
        if self.usage_repo is None:
            self.usage_repo = PgUsageRepository(sessions)
        if self.lineup_repo is None:
            self.lineup_repo = PgLineupRepository(sessions)
        if self._usage_store is None:
            # Creating an engine does not connect; the first query does (in _ensure_usage_loaded).
            self._sync_engine = create_sync_engine_for(config.get_database_url())
            self._usage_store = PgUsageStore(self._sync_engine)
        logger.info("Database connected (%s)", config.mask_database_url(config.get_database_url()))

    async def _attach_checkpointer(self, orchestrator: PipelineOrchestrator) -> None:
        """Give a DB-mode graph the shared checkpointer, opening it first if need be (plan D7).

        ``PIPELINE_CHECKPOINTER=postgres``: the ``AsyncPostgresSaver`` is opened (and its tables
        set up) once, before the first project's worker starts, and closed by :meth:`shutdown`.
        Lazy like the usage load, so a runtime with no project never touches the database. If
        opening fails it raises: the project stays idle like any other init failure.
        """

        if orchestrator.engine != "langgraph":
            return  # legacy has no graph (and legacy + postgres never starts, plan D23)
        if self.checkpointer is None and config.get_pipeline_checkpointer() == "postgres":
            async with self._checkpointer_lock:
                if self.checkpointer is None:
                    # Imported here: only this mode needs it (legacy never loads langgraph, AC23).
                    from core.checkpointing import open_postgres_checkpointer

                    stack = contextlib.AsyncExitStack()
                    self.checkpointer = await stack.enter_async_context(
                        open_postgres_checkpointer(config.get_database_url())
                    )
                    self._checkpointer_stack = stack
                    logger.info("LangGraph checkpointer: PostgreSQL (tables ready)")
        if self.checkpointer is not None:
            orchestrator.use_checkpointer(self.checkpointer)

    async def _ensure_usage_loaded(self) -> None:
        """Make usage durable (AC6): load past records once, then write every new one through.

        Runs before the first project's worker starts, so the totals and the budget pause are
        those from before the restart by the time any task can spend. Lazy (not in startup) so a
        runtime with no project never touches the database. If it fails, it raises: the calling
        project stays idle like any other init failure, and the next project init retries.
        """

        if self._usage_store is None or self._usage_loaded:
            return
        async with self._usage_lock:
            if self._usage_loaded:
                return
            # The store is synchronous: read it off the event loop. No worker is running yet,
            # so nothing records while the totals are rebuilt.
            loaded = await asyncio.to_thread(self.usage_tracker.attach_store, self._usage_store)
            self._usage_loaded = True
            logger.info("Loaded %d stored usage record(s)", loaded)

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
                self._init_failed.add(project_id)

    async def _init_db_project_locked(self, project_id, orchestrator, watcher, importer) -> None:
        await self._ensure_usage_loaded()
        await self._attach_checkpointer(orchestrator)
        if self.project_repo is not None:
            # First: the project row. The lineup import below appends its notes to it.
            await migrate_project(project_id, self.project_path(project_id), self.project_repo)
        pending_seats = None
        if self._lineup_in_db():
            pending_seats = await self._load_lineup(project_id, orchestrator)
        for path in await asyncio.to_thread(watcher.existing_task_files):
            await importer.import_file(path)
        await self._recover_in_flight(project_id)
        if pending_seats is not None:
            # After the gates are restored: a resumed task keeps the line busy until it is done,
            # so request_lineup defers the change to the next task (plan D17, 2026-11-23).
            await self._apply_stored_pending(project_id, orchestrator, pending_seats)
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

    # -- DB mode: the lineup (plan D4, D17) ---------------------------------------------------------

    def _lineup_in_db(self) -> bool:
        return self.task_store == "db" and self.lineup_repo is not None

    async def _load_lineup(self, project_id: str, orchestrator: PipelineOrchestrator):
        """Roster and seats from the database (imported from ``agents/*.yaml`` the first time).

        The orchestrator starts on the stored seats, which are the ones a task restored at its
        gate was running on; the seats still waiting for a task (``pending_seats``) are returned
        for :meth:`_apply_stored_pending`, which runs after the gates are restored.
        """

        await import_from_agent_files(project_id, self.project_path(project_id), self.lineup_repo)
        await self._refresh_roster(project_id)
        record = await self.lineup_repo.get_pipeline(project_id, DEFAULT_PIPELINE_ID)
        if record is None:  # the import always writes it; a database edited by hand may not have it
            record = PipelineRecord(DEFAULT_PIPELINE_ID, DEFAULT_PIPELINE_NAME, True, DEFAULT_LINEUP.ids)
            await self.lineup_repo.save_pipeline(project_id, record)
        self._pipeline_names[project_id] = record.name
        try:
            orchestrator.adopt_lineup(self._lineup_of(project_id, record.seats), self._agent_instances[project_id])
        except ValueError as exc:
            logger.warning("Project %s: the stored seats %s can not run (%s); using %s",
                           project_id, list(record.seats), exc, list(DEFAULT_LINEUP.ids))
            orchestrator.adopt_lineup(DEFAULT_LINEUP, self._agent_instances[project_id])
        return record.pending_seats

    async def _apply_stored_pending(self, project_id: str, orchestrator, seat_ids) -> None:
        """Hand the seats the previous process left pending to ``request_lineup``."""

        try:
            lineup = self._lineup_of(project_id, seat_ids)
            effective = await orchestrator.request_lineup(
                lineup, self._agent_instances[project_id], roster=self.roster_views(project_id)
            )
        except ValueError as exc:
            logger.warning("Project %s: dropping the pending seats %s, they can not run (%s)",
                           project_id, list(seat_ids), exc)
            await self.lineup_repo.set_pending_seats(project_id, DEFAULT_PIPELINE_ID, None)
            return
        logger.info("Project %s: pending seats %s applied %s", project_id, list(seat_ids),
                    "now" if effective == "now" else "with the next task")

    async def _refresh_roster(self, project_id: str) -> None:
        """Re-read the roster and (re)build every agent instance (file I/O in a thread)."""

        records = await self.lineup_repo.list_agents(project_id)
        agents = await asyncio.to_thread(self._build_roster_agents, project_id, records)
        self._rosters[project_id] = records
        self._agent_instances[project_id] = agents

    def _build_roster_agents(self, project_id: str, records: list[AgentRecord]) -> dict[str, BaseAgent]:
        """One agent per roster entry: built-ins from their YAML config with the stored prompt,
        custom seats as :class:`CustomAgent`."""

        configs = {c.name: c for c in load_agent_configs(self.project_path(project_id))}
        agents: dict[str, BaseAgent] = {}
        for record in records:
            if record.builtin:
                name = _BUILTIN_CONFIG[record.id]
                base = configs.get(name) or DEFAULT_AGENT_CONFIGS[name]
                agent_config = base.model_copy(
                    update={"system_prompt": record.system_prompt, "max_retries": record.max_retries}
                )
                agents[record.id] = _PIPELINE_AGENT_CLASSES[name](agent_config, projects_dir=self.projects_dir)
                continue
            llm = record.llm if isinstance(record.llm, dict) else {"provider": "mock"}
            agent_config = custom_agent_config(
                self._seat_of(record), record.system_prompt,
                llm_provider=llm.get("provider", "mock"), llm_model=llm.get("model", "mock-model"),
                max_retries=record.max_retries,
            )
            agents[record.id] = CustomAgent(self._seat_of(record), agent_config, projects_dir=self.projects_dir)
        return agents

    @staticmethod
    def _seat_of(record: AgentRecord) -> Seat:
        if record.builtin:
            return _BUILTIN_SEAT[record.id]
        return Seat(record.id, record.kind, False, record.display_name, record.duty, record.look)

    def _lineup_of(self, project_id: str, seat_ids, pipeline_id: str = DEFAULT_PIPELINE_ID) -> Lineup:
        """The ``Lineup`` of stored seat ids; raises ``InvalidLineup`` for an id not in the roster."""

        by_id = {record.id: record for record in self._rosters.get(project_id, [])}
        unknown = [seat_id for seat_id in seat_ids if seat_id not in by_id]
        if unknown:
            raise InvalidLineup([{"field": "seats", "message": f"沒有這個 agent：{'、'.join(unknown)}"}])
        return Lineup(pipeline_id, tuple(self._seat_of(by_id[seat_id]) for seat_id in seat_ids))

    async def request_seats(self, project_id: str, pipeline_id: str, seat_ids) -> tuple[dict, str]:
        """Change a pipeline's seats (``PUT /pipelines/{pid}``): ``(PipelineView, effective)``.

        Validated first (``InvalidLineup`` with the errors, nothing changes); then the orchestrator
        applies it now or with the next task and is the only one writing ``pipelines``
        (``seats`` when applied, ``pending_seats`` while deferred). ``KeyError`` for an unknown
        pipeline.
        """

        await self.wait_ready(project_id)
        self._raise_if_init_failed(project_id)
        async with self._lock_for(project_id):
            if pipeline_id != DEFAULT_PIPELINE_ID or await self.lineup_repo.get_pipeline(project_id, pipeline_id) is None:
                raise KeyError(pipeline_id)
            seat_ids = list(seat_ids)
            lineup = self._lineup_of(project_id, seat_ids, pipeline_id)
            errors = lineup_errors(lineup)
            if errors:
                raise InvalidLineup(errors)
            orchestrator = self.orchestrators[project_id]
            effective = await orchestrator.request_lineup(
                lineup, self._agent_instances[project_id], roster=self.roster_views(project_id)
            )
        return self.pipeline_views(project_id)[0], effective

    # -- AgentView / PipelineView (plan §6.3, §7.1a) -------------------------------------------------

    def roster_views(self, project_id: str) -> list[dict]:
        """The project's whole roster as AgentView dicts (seated or not)."""

        if project_id in self._rosters:
            agents = self._agent_instances.get(project_id, {})
            return [
                AgentView.from_seat(
                    self._seat_of(record), llm=record.llm,
                    effective_llm=self._effective_llm(agents.get(record.id), record.llm),
                ).to_dict()
                for record in self._rosters[project_id]
            ]
        orchestrator = self.orchestrators.get(project_id)
        agents = orchestrator._agents if orchestrator is not None else {}
        return [
            AgentView.from_seat(seat, llm=None, effective_llm=self._effective_llm(agents.get(seat.id), None)).to_dict()
            for seat in BUILTIN_SEATS
        ]

    @staticmethod
    def _effective_llm(agent: BaseAgent | None, llm) -> dict:
        """What the agent's client really is (``source``: its own setting or the project's)."""

        client = getattr(agent, "llm_client", None)
        mock = client is None or bool(getattr(client, "is_mock", True))
        reason = None
        if mock:
            reason = "force_mock" if config.is_force_mock() else "configured_mock"
        return {
            "provider": "mock" if mock else getattr(client, "provider", "unknown"),
            "model": getattr(client, "model", "mock-model"),
            "source": "project" if llm is None else "agent",
            "mock_reason": reason,
        }

    def pipeline_views(self, project_id: str) -> list[dict]:
        """``[PipelineView]``: the seats running now and the ones waiting for the next task."""

        orchestrator = self.orchestrators.get(project_id)
        lineup = orchestrator.lineup if orchestrator is not None else DEFAULT_LINEUP
        pending = orchestrator.pending_lineup if orchestrator is not None else None
        return [{
            "id": lineup.pipeline_id,
            "name": self._pipeline_names.get(project_id, DEFAULT_PIPELINE_NAME),
            "seats": list(lineup.ids),
            "pending_seats": None if pending is None else list(pending.ids),
        }]

    async def _recover_in_flight(self, project_id: str) -> None:
        """Deal with the tasks the previous process left in flight (plan D3 (1), D7).

        ``awaiting_approval`` tasks whose graph the checkpointer still holds at the gate
        (``PIPELINE_CHECKPOINTER=postgres``) get their gate back: the orchestrator registers it
        and its worker resumes the task before anything else, with no event re-sent and the
        history run left open (the recorder continues it). The others lost their gate (the
        in-memory checkpointer died with the process). They and the ``in_progress`` tasks
        (mid-LLM-call; continuing those is phase 2b) are marked failed, and any unfinished
        history run of theirs is closed with the reason.
        """

        orchestrator = self.orchestrators[project_id]
        for status, reason in (
            (Status.IN_PROGRESS, REASON_INTERRUPTED),
            (Status.AWAITING_APPROVAL, REASON_GATE_LOST),
        ):
            for task in await self.task_repo.list_tasks(project_id, statuses=[status]):
                if status is Status.AWAITING_APPROVAL and await orchestrator.restore_gate(task):
                    logger.info("Task %s of project %s waits at its approval gate again",
                                task.id, project_id)
                    continue
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

        Raises ``ValueError`` for an empty title and ``ProjectInitFailedError`` when the
        project's startup failed (nothing is stored then).
        """

        await self.wait_ready(project_id)  # never race the startup enqueue of this project
        self._raise_if_init_failed(project_id)
        async with self._lock_for(project_id):
            task = await self.task_repo.create(project_id, title, description, priority, approval)
            self.orchestrators[project_id].enqueue(task)
            await self._refresh_task_cache(project_id)
        return task

    async def rerun_task(self, project_id: str, task_id: str) -> Task:
        """DB mode: put a finished task back in the to-do tray and enqueue it again.

        Raises ``KeyError`` for an unknown task, ``ValueError`` unless it is done or failed and
        ``ProjectInitFailedError`` when the project's startup failed.
        """

        await self.wait_ready(project_id)
        self._raise_if_init_failed(project_id)
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

    def _raise_if_init_failed(self, project_id: str) -> None:
        # Without a worker an accepted task would stay pending forever: refuse it instead.
        if project_id in self._init_failed:
            raise ProjectInitFailedError(project_id)

    async def list_tasks_db(self, project_id: str) -> list[Task]:
        return await self.task_repo.list_tasks(project_id)

    async def get_task_db(self, project_id: str, task_id: str) -> Task | None:
        return await self.task_repo.get(project_id, task_id)

    # -- sticky-note history (plan D20, §7 GET /history, §7.1a) ---------------------------------

    def live_state(self, project_id: str) -> LiveState:
        """What the project's orchestrator is doing now (overrides the stored statuses)."""

        orchestrator = self.orchestrators.get(project_id)
        if orchestrator is None:
            return LiveState()
        return LiveState(running=orchestrator.running_task_ids(), awaiting=orchestrator.awaiting_task_ids())

    def current_seats(self, project_id: str) -> list[dict]:
        """The seats the project runs now, as seat snapshots (``task_runs.seats``, plan D20)."""

        orchestrator = self.orchestrators.get(project_id)
        if orchestrator is None:
            return [dict(s) for s in DEFAULT_SEATS]
        return [seat.snapshot() for seat in orchestrator.lineup.seats]

    def _history_run_repo(self) -> RunRepository | None:
        return self.run_repo if self.task_store == "db" else None

    async def list_history(self, project_id: str, **filters) -> dict:
        """``GET /history`` of one project; ``filters`` are those of :func:`build_history`."""

        if self.task_store == "db":
            await self.wait_ready(project_id)
            tasks = await self.task_repo.list_tasks(project_id)
        else:
            tasks = await asyncio.to_thread(self.load_tasks, project_id)
        return await build_history(
            tasks,
            project_id=project_id,
            run_repo=self._history_run_repo(),
            project_path=self.project_path(project_id),
            live=self.live_state(project_id),
            current_seats=self.current_seats(project_id),
            **filters,
        )

    async def task_runs(self, project_id: str, task_id: str) -> list[dict]:
        """The task detail's ``runs`` (newest first); ``[]`` in file mode."""

        return await task_runs_view(self._history_run_repo(), project_id, task_id)

    def history_counts(self, project_id: str) -> dict[str, int]:
        """Snapshot ``history_counts``: tasks per note-wall state (DB mode: from the task cache)."""

        return count_statuses(self.load_tasks(project_id), self.live_state(project_id))

    # -- event plumbing -----------------------------------------------------------

    def _make_event_forwarder(self, project_id: str):
        async def forward(event: str, data: dict) -> None:
            # Budget alerts raised by the pipeline are system-wide DevOps
            # alerts: record them in history and broadcast to every client so
            # the DevOps panel shows them like any other alert (task 16). They are not
            # project events, so they do not go through the event bus.
            if event == EVENT_DEVOPS_ALERT:
                self.recent_alerts.append(data)
                await self.ws_manager.broadcast_all(EVENT_DEVOPS_ALERT, data)
                return
            if event in _USAGE_SUM_EVENTS and self.usage_tracker.unsaved_count:
                await self.usage_tracker.aflush()  # rows a failed write kept, before the re-sum
            if self.history is not None:
                # Before the bus publishes it, so the history already has it (never raises).
                await self.history.handle(project_id, event, data)
            if event in _TASK_END_EVENTS:
                self._forget_live(project_id, task_id=(data.get("task") or {}).get("id"))
            if self.task_store == "db" and event in _TASK_STATE_EVENTS:
                # The status was stored before the event was emitted, so the database is current.
                await self._refresh_task_cache(project_id)
            await self.event_bus.publish(project_id, event, data)

        return forward

    def _make_live_forwarder(self, project_id: str):
        """The orchestrator's live channel (plan D16): to the event bus, never to the history."""

        async def forward_live(event: str, data: dict) -> None:
            if event == EVENT_AGENT_PHASE:
                self._live.setdefault(project_id, {})[data["station"]] = {
                    "task_id": data.get("task_id"), "phase": data.get("phase"), "text": data.get("text"),
                }
            elif event == EVENT_AGENT_LEFT:
                self._live.get(project_id, {}).pop(data.get("agent_id"), None)
            await self.event_bus.publish(project_id, event, data)

        return forward_live

    def _forget_live(self, project_id: str, *, task_id: str | None) -> None:
        live = self._live.get(project_id, {})
        for station in [s for s, entry in live.items() if entry.get("task_id") == task_id]:
            del live[station]

    async def _deliver_to_clients(self, project_id: str, event: str, data: dict) -> None:
        """Event bus subscriber: send the event to the project's WebSocket clients."""

        await self.ws_manager.broadcast(project_id, event, data)

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
            "history_counts": self.history_counts(project_id),
            # Plan §6.4 (AC21): only new keys; everything above is as it was.
            "engine": orchestrator.engine if orchestrator is not None else config.get_pipeline_engine(),
            "task_store": self.task_store,
            "force_mock": config.is_force_mock(),
            "agents": self.roster_views(project_id),
            "pipelines": self.pipeline_views(project_id),
            "live": {station: dict(entry) for station, entry in self._live.get(project_id, {}).items()},
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
