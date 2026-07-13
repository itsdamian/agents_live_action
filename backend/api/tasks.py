"""Task REST endpoints.

* ``GET /api/projects/{project_id}/tasks`` — the project's tasks parsed from
  ``tasks.yaml`` and ``TODO.md`` (Requirements 1.3, 5.1, 5.3).
* ``POST /api/projects/{project_id}/tasks`` — create a task from the UI. The
  task is appended to ``tasks.yaml`` (the file stays the single source of
  truth) and the running TaskWatcher enqueues it into the pipeline exactly as
  if it had been added by hand (Requirement 5.4).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api.projects import get_runtime
from core.task_watcher import append_task
from models.task import Priority

router = APIRouter(prefix="/api", tags=["tasks"])


class NewTaskRequest(BaseModel):
    """Payload for creating a task from the frontend."""

    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    priority: Priority = Priority.MEDIUM


@router.get("/projects/{project_id}/tasks")
def list_tasks(project_id: str, request: Request) -> dict:
    runtime = get_runtime(request)
    if runtime.ensure_project(project_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown project: {project_id}")
    return {
        "tasks": [
            t.model_dump(mode="json") for t in runtime.load_tasks(project_id)
        ]
    }


@router.post("/projects/{project_id}/tasks", status_code=201)
def create_task(project_id: str, payload: NewTaskRequest, request: Request) -> dict:
    runtime = get_runtime(request)
    if runtime.ensure_project(project_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown project: {project_id}")
    try:
        task = append_task(
            runtime.project_path(project_id),
            title=payload.title,
            description=payload.description,
            priority=payload.priority,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"task": task.model_dump(mode="json")}
