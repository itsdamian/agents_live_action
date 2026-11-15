"""Shared helpers for pipeline characterization tests.

Not a test module (pytest does not collect it). Provides scripted LLM clients,
task/project builders, an orchestrator factory that records events, the event
signature used to compare engines (plan §5), and a driver for approval gates.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import yaml

from agents.pm_agent import PMAgent
from agents.qa_agent import QAAgent
from agents.swe_agent import SWEAgent
from core.llm_client import MockLLMClient
from core.pipeline import PipelineOrchestrator
from core.usage_tracker import UsageTracker
from models.agent import AgentConfig
from models.task import Approval, Priority, Status, Task

# Same FAIL report as ``SequencedQAClient`` in tests/test_pipeline.py.
QA_FAIL_REPORT = (
    "[MOCK MODE] QA test report\n"
    "Result: FAIL\n"
    "Tests run: 3, Passed: 1, Failed: 2\n"
    "Notes: login form rejects valid emails."
)


class ScriptedClient(MockLLMClient):
    """Mock client whose i-th ``call`` follows ``script[i]``.

    * ``str``       -> returned as the LLM output,
    * ``Exception`` -> raised,
    * ``None`` or past the end of the script -> the normal mock response.

    Every prompt is recorded in ``self.prompts``.
    """

    def __init__(
        self, role: str, name: str, script: list[str | Exception | None] | None = None
    ) -> None:
        super().__init__(role=role, name=name)
        self.script: list[str | Exception | None] = list(script or [])
        self.prompts: list[str] = []

    def call(self, prompt: str, system_prompt: str | None = None) -> str:
        index = len(self.prompts)
        self.prompts.append(prompt)
        step = self.script[index] if index < len(self.script) else None
        if isinstance(step, Exception):
            raise step
        if isinstance(step, str):
            return step
        return super().call(prompt, system_prompt)


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


def make_task(task_id: str = "task-001", **overrides) -> Task:
    now = datetime(2026, 1, 1, 12, 0, 0)
    base = dict(
        id=task_id,
        title="Add login page",
        description="Users should be able to log in.",
        priority=Priority.HIGH,
        status=Status.PENDING,
        created_at=now,
        updated_at=now,
    )
    base.update(overrides)
    return Task(**base)


def make_project(tmp_path: Path, tasks: list[Task]) -> Path:
    """Create a project dir with a tasks.yaml holding ``tasks``."""

    project = tmp_path / "demo-project"
    project.mkdir()
    entries = []
    for t in tasks:
        entry = {
            "id": t.id,
            "title": t.title,
            "description": t.description,
            "priority": t.priority.value,
            "status": t.status.value,
        }
        if t.approval is not Approval.NONE:
            entry["approval"] = t.approval.value
        entries.append(entry)
    (project / "tasks.yaml").write_text(
        yaml.safe_dump({"tasks": entries}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return project


def make_orchestrator(
    project: Path,
    *,
    engine: str = "legacy",
    pm_client: MockLLMClient | None = None,
    swe_client: MockLLMClient | None = None,
    qa_client: MockLLMClient | None = None,
    usage_tracker: UsageTracker | None = None,
) -> tuple[PipelineOrchestrator, list[tuple[str, dict]]]:
    """Build an orchestrator wired to real agents and mock clients.

    Returns ``(orchestrator, events)``; ``events`` collects ``(event, data)``.
    """

    # The ``engine`` constructor argument arrives in Task 8.
    assert engine == "legacy"

    pm = PMAgent(
        _make_config("PM_agent", "Project Manager"),
        llm_client=pm_client or MockLLMClient(role="Project Manager", name="PM_agent"),
        retry_wait_multiplier=0,
    )
    swe = SWEAgent(
        _make_config("SWE_agent", "Software Engineer"),
        llm_client=swe_client
        or MockLLMClient(role="Software Engineer", name="SWE_agent"),
        retry_wait_multiplier=0,
    )
    qa = QAAgent(
        _make_config("QA_agent", "Quality Assurance"),
        llm_client=qa_client
        or MockLLMClient(role="Quality Assurance", name="QA_agent"),
        retry_wait_multiplier=0,
    )
    events: list[tuple[str, dict]] = []
    orchestrator = PipelineOrchestrator(
        project_id=project.name,
        project_path=project,
        pm_agent=pm,
        swe_agent=swe,
        qa_agent=qa,
        on_event=lambda event, data: events.append((event, data)),
        write_status=True,
        write_deliverables=True,
        usage_tracker=usage_tracker,
    )
    return orchestrator, events


def event_signature(event: str, data: dict) -> tuple:
    """Comparable fingerprint of one event (plan §5).

    Deliberately excludes execution_time, full error text, token counts, cost
    and timestamps.
    """

    task = data.get("task") or {}
    sig = [
        event,
        tuple(sorted(data.keys())),
        data.get("station"),
        data.get("status"),
        task.get("id"),
        task.get("status"),
        task.get("rework_count"),
    ]
    if event == "task:created":
        sig.append(data["queue_size"])
    elif event == "task:completed":
        sig += [data["done_count"], tuple(Path(p).name for p in data["deliverables"])]
    elif event == "task:failed":
        sig += [data["failed_count"], data["reason"]]
    elif event == "task:rejected":
        sig.append(data["note"])
    elif event == "usage:update":
        sig += [data["task_usage"]["call_count"], data["project_usage"]["call_count"]]
    elif event == "devops:alert":
        sig += [data["level"], data["metric"]]
    return tuple(sig)


def signatures(events: list[tuple[str, dict]]) -> list[tuple]:
    return [event_signature(event, data) for event, data in events]


def install_status_recorder(monkeypatch) -> list[str]:
    """Record every status written to tasks.yaml, still calling the original."""

    import core.pipeline as pipeline_module

    original = pipeline_module.write_task_status
    recorded: list[str] = []

    def recording(project_path, task_id, status):
        recorded.append(Status(status).value)
        return original(project_path, task_id, status)

    monkeypatch.setattr(pipeline_module, "write_task_status", recording)
    return recorded


def deliverable_names(project: Path, task_id: str) -> list[str]:
    """Sorted deliverable file names for a task; ``[]`` if none were written."""

    task_dir = project / "deliverables" / task_id
    if not task_dir.is_dir():
        return []
    return sorted(p.name for p in task_dir.iterdir())


async def run_with_gate_actions(
    orch: PipelineOrchestrator,
    task: Task,
    actions: list[tuple[str, str]],
    on_pause: Callable[[PipelineOrchestrator, Task], None] | None = None,
) -> Task:
    """Run ``process_task`` and answer each approval gate with ``actions``.

    ``actions`` items are ``("approve", "")`` or ``("reject", note)``. Before
    each decision ``on_pause(orch, task)`` (if given) runs while the task is
    paused, so callers can assert on snapshot state.
    """

    # ``is_awaiting_approval`` turns True as soon as the gate is *registered*,
    # which is before the ``awaiting_approval`` status is written to tasks.yaml
    # and before ``task:awaiting_approval`` is emitted. Polling that alone is
    # racy, so also wait until the event for this gate entry has been emitted.
    gate_events = 0
    original_on_event = orch._on_event

    def counting_on_event(event, data):
        nonlocal gate_events
        if event == "task:awaiting_approval":
            gate_events += 1
        return original_on_event(event, data) if original_on_event else None

    orch._on_event = counting_on_event
    try:
        proc = asyncio.create_task(orch.process_task(task))
        for entry, (action, note) in enumerate(actions, start=1):
            for _ in range(500):
                if orch.is_awaiting_approval(task.id) and gate_events >= entry:
                    break
                await asyncio.sleep(0.005)
            else:
                raise AssertionError(f"task {task.id} never reached the approval gate")
            if on_pause is not None:
                on_pause(orch, task)
            if action == "approve":
                assert orch.approve(task.id) is True
            elif action == "reject":
                assert orch.reject(task.id, note) is True
            else:
                raise AssertionError(f"unknown gate action {action!r}")
        return await asyncio.wait_for(proc, timeout=10)
    finally:
        orch._on_event = original_on_event
