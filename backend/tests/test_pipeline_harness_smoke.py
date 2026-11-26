"""Smoke test: the characterization harness can drive a normal S1 run."""

from __future__ import annotations

import asyncio

from models.task import Status
from tests.pipeline_harness import make_orchestrator, make_project, make_task


def test_harness_runs_normal_flow(tmp_path):
    task = make_task()
    project = make_project(tmp_path, [task])
    orch, events = make_orchestrator(project)

    result = asyncio.run(orch.process_task(task))

    assert result.status is Status.DONE
    assert events  # on_event wiring works
