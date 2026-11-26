"""Migrating an existing project into a real PostgreSQL (Task 11d, AC2b): the second start migrates nothing."""

from __future__ import annotations

import asyncio
from pathlib import Path

import psycopg
import pytest
import yaml

from core.runtime import FactoryRuntime

pytestmark = pytest.mark.integration

PROJECT = "legacy-proj"


def _entry(task_id, status):
    return {"id": task_id, "title": f"title {task_id}", "description": "d", "priority": "medium", "status": status,
            "created_at": "2026-03-01T00:00:00", "updated_at": "2026-03-04T05:06:07"}


def _inputs(project: Path):
    files = [project / "tasks.yaml", project / "TODO.md", *sorted((project / "agents").glob("*.yaml"))]
    return {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in files if p.is_file()}


def test_first_start_migrates_the_project_and_the_second_start_does_not(tmp_path, monkeypatch, migrated_db_sqlalchemy_url, migrated_db_url):
    monkeypatch.setenv("FORCE_MOCK", "true")
    monkeypatch.setenv("TASK_STORE", "db")
    monkeypatch.setenv("PIPELINE_CHECKPOINTER", "memory")
    monkeypatch.setenv("DATABASE_URL", migrated_db_sqlalchemy_url)
    project = tmp_path / PROJECT
    (project / "agents").mkdir(parents=True)
    (project / "agents" / "PM_agent.yaml").write_text(
        "name: PM_agent\nrole: Project Manager\ndescription: d\nllm_provider: mock\nllm_model: m\napi_key_env: X\nmax_retries: 1\nsystem_prompt: s\n", encoding="utf-8")
    (project / "tasks.yaml").write_text(yaml.safe_dump({"tasks": [
        _entry("task-pending", "pending"), _entry("task-done", "done"), _entry("task-failed", "failed"),
        _entry("task-progress", "in_progress"), _entry("task-gate", "awaiting_approval")]}), encoding="utf-8")
    (project / "TODO.md").write_text("- [x] from the todo list\n", encoding="utf-8")
    before = _inputs(project)

    def snapshot():
        with psycopg.connect(migrated_db_url) as conn:
            tasks = dict(conn.execute("SELECT id, status FROM tasks").fetchall())
            runs = conn.execute(
                "SELECT task_id, run_no, outcome, reason, imported FROM task_runs ORDER BY task_id, run_no").fetchall()
            projects = conn.execute("SELECT id, settings FROM projects").fetchall()
        return tasks, runs, projects

    async def one_start(wait_for_pending: bool):
        runtime = FactoryRuntime(projects_dir=tmp_path)
        await runtime.startup()
        try:
            deadline = asyncio.get_running_loop().time() + 30
            while wait_for_pending and asyncio.get_running_loop().time() < deadline:
                if snapshot()[0].get("task-pending") == "done":
                    break
                await asyncio.sleep(0.1)
            await asyncio.sleep(0.3)
        finally:
            await runtime.shutdown()

    asyncio.run(one_start(True))
    tasks, runs, projects = snapshot()
    assert projects == [(PROJECT, {"llm": "mock"})]
    todo_id = next(t for t in tasks if t.startswith("from-the") or t not in {"task-pending", "task-done", "task-failed", "task-progress", "task-gate"})
    assert tasks == {"task-pending": "done", "task-done": "done", "task-failed": "failed",
                     "task-progress": "failed", "task-gate": "failed", todo_id: "done"}
    imported = [r for r in runs if r[4]]
    assert sorted((r[0], r[2], r[3]) for r in imported) == sorted([
        ("task-done", "done", None), ("task-failed", "failed", None),
        ("task-progress", "failed", "interrupted before migration to database"),
        ("task-gate", "failed", "interrupted before migration to database"), (todo_id, "done", None)])

    asyncio.run(one_start(False))  # second start: nothing is migrated or dispatched again

    assert snapshot() == (tasks, runs, projects)
    assert _inputs(project) == before  # tasks.yaml, TODO.md, agents/*.yaml: bytes and mtimes
