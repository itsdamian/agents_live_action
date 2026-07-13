"""Unit tests for :class:`core.pipeline.PipelineOrchestrator`.

All tests run against :class:`~core.llm_client.MockLLMClient` (or small stub
clients) so no real API keys are required. Async pipeline methods are driven
with ``asyncio.run`` so the suite needs no pytest-asyncio configuration
(matching ``test_devops_agent.py``).

Covered scenarios (Requirements 6.1-6.6):

    * normal flow — PM -> SWE -> QA, all outputs populated, task done, events
      emitted, status written back to tasks.yaml,
    * rework flow — QA fails once, task returns to SWE and then passes,
    * rework ceiling — QA keeps failing, task is failed after max_reworks,
    * agent failure — a raising agent fails that task without crashing,
    * priority scheduling — critical tasks are served before low, FIFO ties.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

import yaml

from agents.pm_agent import PMAgent
from agents.qa_agent import QAAgent
from agents.swe_agent import SWEAgent
from core.llm_client import MockLLMClient
from core.pipeline import (
    EVENT_TASK_COMPLETED,
    EVENT_TASK_FAILED,
    PipelineOrchestrator,
)
from models.agent import AgentConfig
from models.task import Priority, Status, Task


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


def _make_task(task_id: str = "task-001", **overrides) -> Task:
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


class SequencedQAClient(MockLLMClient):
    """QA mock returning FAIL for the first ``fail_times`` calls, then PASS."""

    def __init__(self, fail_times: int) -> None:
        super().__init__(role="Quality Assurance", name="QA_agent")
        self.fail_times = fail_times
        self.calls = 0

    def call(self, prompt: str, system_prompt: str | None = None) -> str:
        self.calls += 1
        if self.calls <= self.fail_times:
            return (
                "[MOCK MODE] QA test report\n"
                "Result: FAIL\n"
                "Tests run: 3, Passed: 1, Failed: 2\n"
                "Notes: login form rejects valid emails."
            )
        return super().call(prompt, system_prompt)


class ExplodingClient(MockLLMClient):
    """Client that always raises, driving the agent-failure path."""

    def call(self, prompt: str, system_prompt: str | None = None) -> str:
        raise RuntimeError("provider unreachable")


def _make_agents(qa_client: MockLLMClient | None = None, swe_client=None):
    pm = PMAgent(
        _make_config("PM_agent", "Project Manager"),
        llm_client=MockLLMClient(role="Project Manager", name="PM_agent"),
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
        llm_client=qa_client or MockLLMClient(role="Quality Assurance", name="QA_agent"),
        retry_wait_multiplier=0,
    )
    return pm, swe, qa


def _make_project(tmp_path: Path, tasks: list[Task]) -> Path:
    """Create a project dir with a tasks.yaml holding ``tasks``."""

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


def _make_orchestrator(
    project: Path,
    events: list[tuple[str, dict]],
    qa_client: MockLLMClient | None = None,
    swe_client=None,
) -> PipelineOrchestrator:
    pm, swe, qa = _make_agents(qa_client=qa_client, swe_client=swe_client)
    return PipelineOrchestrator(
        project_id=project.name,
        project_path=project,
        pm_agent=pm,
        swe_agent=swe,
        qa_agent=qa,
        on_event=lambda event, data: events.append((event, data)),
    )


def _read_status(project: Path, task_id: str) -> str:
    raw = yaml.safe_load((project / "tasks.yaml").read_text(encoding="utf-8"))
    for entry in raw["tasks"]:
        if entry["id"] == task_id:
            return entry["status"]
    raise AssertionError(f"task {task_id} not found in tasks.yaml")


# ---------------------------------------------------------------------------
# Normal flow
# ---------------------------------------------------------------------------


def test_normal_flow_completes_task(tmp_path):
    task = _make_task()
    project = _make_project(tmp_path, [task])
    events: list[tuple[str, dict]] = []
    orchestrator = _make_orchestrator(project, events)

    result = asyncio.run(orchestrator.process_task(task))

    assert result.status is Status.DONE
    assert result.pm_output and "PM task analysis" in result.pm_output
    assert result.swe_output and "SWE implementation" in result.swe_output
    assert result.qa_output and "Result: PASS" in result.qa_output
    assert result.rework_count == 0
    assert orchestrator.done_count == 1
    assert _read_status(project, task.id) == "done"

    event_names = [name for name, _ in events]
    assert EVENT_TASK_COMPLETED in event_names
    assert EVENT_TASK_FAILED not in event_names


def test_normal_flow_writes_agent_logs(tmp_path):
    task = _make_task()
    project = _make_project(tmp_path, [task])
    orchestrator = _make_orchestrator(project, [])

    asyncio.run(orchestrator.process_task(task))

    logs = list((project / "logs").glob("*.log"))
    logged_agents = {p.name.split("_", 2)[2] for p in logs}
    # One log per station: PM, SWE, QA (Requirement 9.1).
    assert len(logs) == 3
    assert logged_agents == {
        f"PM_agent_{task.id}.log",
        f"SWE_agent_{task.id}.log",
        f"QA_agent_{task.id}.log",
    }


# ---------------------------------------------------------------------------
# Rework flow
# ---------------------------------------------------------------------------


def test_qa_failure_triggers_rework_then_passes(tmp_path):
    task = _make_task()
    project = _make_project(tmp_path, [task])
    qa_client = SequencedQAClient(fail_times=1)
    events: list[tuple[str, dict]] = []
    orchestrator = _make_orchestrator(project, events, qa_client=qa_client)

    result = asyncio.run(orchestrator.process_task(task))

    assert result.status is Status.DONE
    assert result.rework_count == 1
    assert qa_client.calls == 2
    assert _read_status(project, task.id) == "done"
    rework_events = [
        data
        for name, data in events
        if name == "station:update" and data.get("status") == "rework"
    ]
    assert len(rework_events) == 1


def test_rework_context_carries_qa_report_to_swe(tmp_path):
    task = _make_task()
    project = _make_project(tmp_path, [task])

    class RecordingSWEClient(MockLLMClient):
        def __init__(self):
            super().__init__(role="Software Engineer", name="SWE_agent")
            self.prompts: list[str] = []

        def call(self, prompt, system_prompt=None):
            self.prompts.append(prompt)
            return super().call(prompt, system_prompt)

    swe_client = RecordingSWEClient()
    orchestrator = _make_orchestrator(
        project, [], qa_client=SequencedQAClient(fail_times=1), swe_client=swe_client
    )

    asyncio.run(orchestrator.process_task(task))

    assert len(swe_client.prompts) == 2
    # The rework prompt must contain QA's failure report (Requirement 6.3).
    assert "REWORK" in swe_client.prompts[1]
    assert "QA failure report" in swe_client.prompts[1]


def test_exceeding_max_reworks_fails_task(tmp_path):
    task = _make_task(max_reworks=2)
    project = _make_project(tmp_path, [task])
    qa_client = SequencedQAClient(fail_times=99)
    events: list[tuple[str, dict]] = []
    orchestrator = _make_orchestrator(project, events, qa_client=qa_client)

    result = asyncio.run(orchestrator.process_task(task))

    assert result.status is Status.FAILED
    assert result.rework_count == task.max_reworks + 1
    # Initial pass + max_reworks rework passes.
    assert qa_client.calls == task.max_reworks + 1
    assert orchestrator.failed_count == 1
    assert _read_status(project, task.id) == "failed"
    assert any(name == EVENT_TASK_FAILED for name, _ in events)


# ---------------------------------------------------------------------------
# Agent failure isolation
# ---------------------------------------------------------------------------


def test_agent_exception_fails_task_without_raising(tmp_path):
    task = _make_task()
    project = _make_project(tmp_path, [task])
    events: list[tuple[str, dict]] = []
    orchestrator = _make_orchestrator(project, events, swe_client=ExplodingClient())

    result = asyncio.run(orchestrator.process_task(task))

    assert result.status is Status.FAILED
    assert _read_status(project, task.id) == "failed"
    failed = [data for name, data in events if name == EVENT_TASK_FAILED]
    assert failed and "provider unreachable" in failed[0]["reason"]


# ---------------------------------------------------------------------------
# Priority scheduling
# ---------------------------------------------------------------------------


def test_queue_serves_by_priority_then_fifo(tmp_path):
    tasks = [
        _make_task("task-low", priority=Priority.LOW),
        _make_task("task-med-1", priority=Priority.MEDIUM),
        _make_task("task-critical", priority=Priority.CRITICAL),
        _make_task("task-med-2", priority=Priority.MEDIUM),
    ]
    project = _make_project(tmp_path, tasks)
    processed: list[str] = []

    async def run() -> None:
        orchestrator = _make_orchestrator(project, [])

        original = orchestrator.process_task

        async def tracking_process(task: Task) -> Task:
            processed.append(task.id)
            return await original(task)

        orchestrator.process_task = tracking_process  # type: ignore[method-assign]

        for task in tasks:
            orchestrator.enqueue(task)
        orchestrator.start()
        await orchestrator._queue.join()
        await orchestrator.stop()

    asyncio.run(run())

    assert processed == ["task-critical", "task-med-1", "task-med-2", "task-low"]


def test_enqueue_is_threadsafe_while_running(tmp_path):
    """TaskWatcher callbacks arrive from a watchdog thread; enqueue must work."""

    import threading

    task = _make_task()
    project = _make_project(tmp_path, [task])
    orchestrator = _make_orchestrator(project, [])

    async def run() -> None:
        orchestrator.start()
        thread = threading.Thread(target=orchestrator.enqueue, args=(task,))
        thread.start()
        thread.join()
        await asyncio.wait_for(orchestrator._queue.join(), timeout=10)
        await orchestrator.stop()

    asyncio.run(run())
    assert orchestrator.done_count == 1
