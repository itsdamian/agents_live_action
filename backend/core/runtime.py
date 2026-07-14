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
from core.task_watcher import TaskWatcher, parse_tasks_yaml, parse_todo_md
from core.usage_tracker import UsageTracker
from core.ws_manager import WSManager
from models.agent import AgentConfig
from models.project import Project
from models.task import Status, Task

logger = logging.getLogger(__name__)

# Pipeline station agent classes keyed by their canonical config name.
_PIPELINE_AGENT_CLASSES = {
    "PM_agent": PMAgent,
    "SWE_agent": SWEAgent,
    "QA_agent": QAAgent,
}

# How many recent DevOps alerts are kept for the initial WS snapshot.
_ALERT_HISTORY = 20


class FactoryRuntime:
    """Owns and coordinates scanners, orchestrators, watchers, and monitors."""

    def __init__(self, projects_dir: str | Path | None = None) -> None:
        self.projects_dir = (
            Path(projects_dir) if projects_dir is not None else DEFAULT_PROJECTS_DIR
        )
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

        for orchestrator in self.orchestrators.values():
            await orchestrator.stop()
        self.orchestrators.clear()
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
        )
        self.orchestrators[project_id] = orchestrator

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
        """All tasks for a project from tasks.yaml plus TODO.md (Req 5.1, 5.3)."""

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
