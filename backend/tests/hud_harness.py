"""Helpers for the live-action HUD tests (plan D2, D3): run the S1-S12 characterization
scenarios against either storage mode and observe everything that must stay identical.

Built on ``tests/pipeline_harness.py``, which is not modified. A scenario is run twice, once
in file mode (``tasks.yaml`` writes, the world the existing characterization tests pin) and once
in DB mode (a ``TaskRepository`` is injected and ``tasks.yaml`` must stay untouched); the two
observations must be equal.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from agents.pm_agent import PMAgent
from agents.qa_agent import QAAgent
from agents.swe_agent import SWEAgent
from core.llm_client import MockLLMClient
from core.pipeline import PipelineOrchestrator
from core.usage_tracker import UsageTracker
from models.task import Approval, Status, Task
from tests.pipeline_harness import (
    QA_FAIL_REPORT,
    ScriptedClient,
    _make_config,
    deliverable_names,
    install_status_recorder,
    make_project,
    make_task,
    run_with_gate_actions,
    signatures,
)

APPROVE = ("approve", "")


@dataclass
class Scenario:
    """One characterization scenario (plan section 6, S1-S12)."""

    task: dict = field(default_factory=dict)
    actions: list[tuple[str, str]] = field(default_factory=list)  # approval gate answers
    pm: list = field(default_factory=list)  # ScriptedClient scripts
    swe: list = field(default_factory=list)
    qa: list = field(default_factory=list)
    budget: bool = False  # S11: a tracker with a budget the seeded spend already exceeds


SCENARIOS: dict[str, Scenario] = {
    "S1": Scenario(),
    "S2": Scenario(qa=[QA_FAIL_REPORT]),
    "S3": Scenario(task={"max_reworks": 2}, qa=[QA_FAIL_REPORT] * 3),
    "S4": Scenario(swe=[RuntimeError("provider unreachable")]),
    "S5": Scenario(pm=[RuntimeError("provider unreachable")]),
    "S6": Scenario(task={"approval": Approval.AFTER_SWE}, actions=[APPROVE]),
    "S7": Scenario(task={"approval": Approval.AFTER_QA}, actions=[APPROVE]),
    "S8": Scenario(task={"approval": Approval.AFTER_SWE}, actions=[("reject", "Please add input validation"), APPROVE]),
    "S9": Scenario(task={"approval": Approval.AFTER_SWE, "max_reworks": 1}, actions=[("reject", "nope"), ("reject", "still nope")]),
    "S10": Scenario(task={"approval": Approval.AFTER_QA}, actions=[("reject", "tighten tests"), APPROVE]),
    "S11": Scenario(budget=True),
    "S12": Scenario(task={"approval": Approval.AFTER_SWE}, actions=[("reject", ""), APPROVE]),
}


def make_orchestrator_with_repo(
    project: Path,
    *,
    engine: str,
    task_repo=None,
    pm_client: MockLLMClient | None = None,
    swe_client: MockLLMClient | None = None,
    qa_client: MockLLMClient | None = None,
    usage_tracker: UsageTracker | None = None,
) -> tuple[PipelineOrchestrator, list[tuple[str, dict]]]:
    """Like ``pipeline_harness.make_orchestrator`` but able to inject ``task_repo``."""

    pm = PMAgent(_make_config("PM_agent", "Project Manager"),
                 llm_client=pm_client or MockLLMClient(role="Project Manager", name="PM_agent"),
                 retry_wait_multiplier=0)
    swe = SWEAgent(_make_config("SWE_agent", "Software Engineer"),
                   llm_client=swe_client or MockLLMClient(role="Software Engineer", name="SWE_agent"),
                   retry_wait_multiplier=0)
    qa = QAAgent(_make_config("QA_agent", "Quality Assurance"),
                 llm_client=qa_client or MockLLMClient(role="Quality Assurance", name="QA_agent"),
                 retry_wait_multiplier=0)
    events: list[tuple[str, dict]] = []
    orch = PipelineOrchestrator(
        project_id=project.name,
        project_path=project,
        pm_agent=pm,
        swe_agent=swe,
        qa_agent=qa,
        on_event=lambda event, data: events.append((event, data)),
        write_status=True,
        write_deliverables=True,
        usage_tracker=usage_tracker,
        engine=engine,
        task_repo=task_repo,
    )
    return orch, events


def record_repo_statuses(repo) -> list[str]:
    """Wrap ``repo.set_status`` so every status written to it is recorded (still stored)."""

    recorded: list[str] = []
    original = repo.set_status

    async def recording(project_id, task_id, status):
        recorded.append(Status(status).value)
        return await original(project_id, task_id, status)

    repo.set_status = recording
    return recorded


def _yaml_status(project: Path, task_id: str) -> str:
    raw = yaml.safe_load((project / "tasks.yaml").read_text(encoding="utf-8"))
    return next(e["status"] for e in raw["tasks"] if e["id"] == task_id)


def run_scenario(spec: Scenario, *, mode: str, engine: str, workdir: Path, monkeypatch, repo=None):
    """Run ``spec`` in ``mode`` ("file" or "db"); return ``(observation, extras)``.

    ``observation`` is what must be identical in both modes. ``extras`` holds what is specific
    to a mode (file writes seen in DB mode, whether tasks.yaml changed).
    """

    assert mode in ("file", "db")
    workdir.mkdir(parents=True, exist_ok=True)
    task = make_task(**spec.task)
    project = make_project(workdir, [task])
    yaml_before = (project / "tasks.yaml").read_bytes()

    tracker = None
    if spec.budget:
        tracker = UsageTracker(project_budget_usd=0.015)
        tracker.record(project.name, "seed", "openai", "gpt-4o", 1000, 1000)  # 0.02 > 0.015

    clients = (
        ScriptedClient("Project Manager", "PM_agent", spec.pm),
        ScriptedClient("Software Engineer", "SWE_agent", spec.swe),
        ScriptedClient("Quality Assurance", "QA_agent", spec.qa),
    )

    with monkeypatch.context() as mp:
        file_writes = install_status_recorder(mp)  # tasks.yaml writes (core.pipeline.write_task_status)
        repo_writes: list[str] = []
        if mode == "db":
            assert repo is not None
            asyncio.run(repo.insert_if_absent(project.name, task, source="yaml"))
            repo_writes = record_repo_statuses(repo)
        orch, events = make_orchestrator_with_repo(
            project, engine=engine, task_repo=repo if mode == "db" else None,
            pm_client=clients[0], swe_client=clients[1], qa_client=clients[2], usage_tracker=tracker,
        )

        def storage_status() -> str:
            return _yaml_status(project, task.id) if mode == "file" else repo_writes[-1]

        pauses: list[dict] = []

        def on_pause(o, t):
            pauses.append({
                "awaiting": o.snapshot()["awaiting_approval"],
                "stations": {k: v["status"] for k, v in o.station_states.items()},
                "stored_status": storage_status(),
                "rework_count": t.rework_count,
                "swe_output": t.swe_output is not None,
                "qa_output": t.qa_output is not None,
            })

        if spec.actions:
            result = asyncio.run(run_with_gate_actions(orch, task, spec.actions, on_pause))
        else:
            result = asyncio.run(orch.process_task(task))

    if mode == "db":
        stored_status = asyncio.run(repo.get(project.name, task.id)).status.value
    else:
        stored_status = _yaml_status(project, task.id)

    observation = {
        "signatures": signatures(events),
        "status_writes": file_writes if mode == "file" else repo_writes,
        "deliverables": deliverable_names(project, task.id),
        "final_status": result.status.value,
        "stored_status": stored_status,
        "rework_count": result.rework_count,
        "outputs": (result.pm_output is not None, result.swe_output is not None, result.qa_output is not None),
        "counts": (orch.done_count, orch.failed_count),
        "prompts": tuple(len(c.prompts) for c in clients),
        "pauses": pauses,
        "gate_entries": sum(1 for name, _ in events if name == "task:awaiting_approval"),
        "awaiting_after": orch.snapshot()["awaiting_approval"],
        "budget_paused": orch.is_budget_paused(),
    }
    extras = {
        "file_writes": file_writes,
        "yaml_unchanged": (project / "tasks.yaml").read_bytes() == yaml_before,
    }
    return observation, extras


# -- custom lineups (Task 22, plan section 9) ---------------------------------------------------------


class ScriptedCustomClient(ScriptedClient):
    """A ``ScriptedClient`` answering as a custom seat: unscripted calls use the ``make`` /
    ``check`` Mock renderer of ``kind``. System prompts are recorded in ``system_prompts``."""

    def __init__(self, seat_id: str, kind: str, script: list | None = None) -> None:
        super().__init__(f"custom {kind} agent", seat_id, script)
        self.role_key = kind
        self.system_prompts: list[str | None] = []

    def call(self, prompt: str, system_prompt: str | None = None) -> str:
        self.system_prompts.append(system_prompt)
        return super().call(prompt, system_prompt)


def custom_seat(seat_id: str, kind: str = "make", look: str = "beret") -> "Seat":
    from models.lineup import Seat

    return Seat(seat_id, kind, False, seat_id.capitalize(), f"{seat_id} duty", look)


def make_lineup(*specs, scripts: dict | None = None, pipeline_id: str = "main"):
    """``(lineup, agents)`` from seat specs.

    A spec is a built-in id (``"pm"``), a ``(id, kind)`` pair for a custom seat, or a ``Seat``.
    Each custom seat gets a :class:`~agents.custom_agent.CustomAgent` on a
    :class:`ScriptedCustomClient` (``scripts[id]`` is its script).
    """

    from agents.custom_agent import CustomAgent, custom_agent_config
    from models.lineup import BUILTIN_SEATS, Lineup, Seat

    builtins = {seat.id: seat for seat in BUILTIN_SEATS}
    scripts = scripts or {}
    seats, agents = [], {}
    for spec in specs:
        if isinstance(spec, str):
            seats.append(builtins[spec])
            continue
        seat = spec if isinstance(spec, Seat) else custom_seat(*spec)
        seats.append(seat)
        client = ScriptedCustomClient(seat.id, seat.kind, scripts.get(seat.id))
        agents[seat.id] = CustomAgent(seat, custom_agent_config(seat, f"You are {seat.id}.", max_retries=1),
                                      llm_client=client, retry_wait_multiplier=0)
    return Lineup(pipeline_id, tuple(seats)), agents
