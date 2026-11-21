"""Task REST endpoints.

* ``GET /api/projects/{project_id}/tasks`` — the project's tasks parsed from
  ``tasks.yaml`` and ``TODO.md`` (Requirements 1.3, 5.1, 5.3).
* ``POST /api/projects/{project_id}/tasks`` — create a task from the UI. The
  task is appended to ``tasks.yaml`` (the file stays the single source of
  truth) and the running TaskWatcher enqueues it into the pipeline exactly as
  if it had been added by hand (Requirement 5.4).
* ``GET /api/projects/{project_id}/tasks/{task_id}`` — full detail for one
  task: its stored fields plus the per-station outputs (pm/swe/qa) rebuilt from
  its deliverables, a status history derived from its log files, and links to
  its logs and deliverables (task 15).
* ``GET /api/projects/{project_id}/tasks/{task_id}/logs`` — the task's agent
  log files with metadata and contents (Requirements 9.1, 9.2).
* ``GET /api/projects/{project_id}/tasks/{task_id}/logs/{filename}`` — the raw
  contents of a single log file.
* ``GET /api/projects/{project_id}/tasks/{task_id}/deliverables`` — list the
  files produced for a task, each with a download URL.
* ``GET /api/projects/{project_id}/tasks/{task_id}/deliverables/{filename}`` —
  download a single deliverable file.
"""

from __future__ import annotations

import yaml
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from fastapi.responses import PlainTextResponse

from api.projects import get_runtime
from core.deliverables import (
    list_deliverables,
    read_station_outputs,
    resolve_deliverable,
)
from core.runtime import ProjectInitFailedError
from core.task_logs import list_task_logs, resolve_task_log
from core.task_watcher import append_task
from models.task import Approval, Priority

router = APIRouter(prefix="/api", tags=["tasks"])


class NewTaskRequest(BaseModel):
    """Payload for creating a task from the frontend."""

    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    priority: Priority = Priority.MEDIUM
    # When the pipeline must pause for a human: never, after SWE, or after QA passes.
    approval: Approval = Approval.NONE


class RejectRequest(BaseModel):
    """Payload for rejecting a task paused at an approval gate (task 17)."""

    note: str = ""


@router.get("/projects/{project_id}/tasks")
def list_tasks(project_id: str, request: Request) -> dict:
    runtime = get_runtime(request)
    if runtime.ensure_project(project_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown project: {project_id}")
    tasks = (
        runtime.run_on_loop(runtime.list_tasks_db(project_id))
        if runtime.task_store == "db"
        else runtime.load_tasks(project_id)
    )
    return {"tasks": [t.model_dump(mode="json") for t in tasks]}


@router.post("/projects/{project_id}/tasks", status_code=201)
def create_task(project_id: str, payload: NewTaskRequest, request: Request) -> dict:
    runtime = get_runtime(request)
    if runtime.ensure_project(project_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown project: {project_id}")
    try:
        if runtime.task_store == "db":
            # DB mode: straight into the database and the queue; tasks.yaml is never touched.
            task = runtime.run_on_loop(
                runtime.create_task(
                    project_id, payload.title, payload.description, payload.priority, payload.approval
                )
            )
        else:
            task = append_task(
                runtime.project_path(project_id),
                title=payload.title,
                description=payload.description,
                priority=payload.priority,
                approval=payload.approval,
            )
    except ProjectInitFailedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"task": task.model_dump(mode="json")}


@router.get("/projects/{project_id}/tasks/export")
def export_tasks(project_id: str, request: Request) -> Response:
    """DB mode: the project's tasks as a ``tasks.yaml`` document (download).

    The database is the source of truth, so this is how to get a file back out. It must be
    declared before ``/tasks/{task_id}``, which would otherwise swallow ``export``.
    """

    runtime = get_runtime(request)
    if runtime.ensure_project(project_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown project: {project_id}")
    if runtime.task_store != "db":
        raise HTTPException(
            status_code=409,
            detail="Exporting tasks needs TASK_STORE=db; in file mode tasks.yaml is the file itself",
        )
    tasks = runtime.run_on_loop(runtime.list_tasks_db(project_id))
    entries = []
    for task in tasks:
        entry = {
            "id": task.id,
            "title": task.title,
            "description": task.description,
            "priority": task.priority.value,
            "status": task.status.value,
            "created_at": task.created_at.isoformat(),
            "updated_at": task.updated_at.isoformat(),
        }
        if task.approval is not Approval.NONE:
            entry["approval"] = task.approval.value
        if task.max_reworks != 3:
            entry["max_reworks"] = task.max_reworks
        entries.append(entry)
    body = yaml.safe_dump({"tasks": entries}, allow_unicode=True, sort_keys=False)
    return Response(
        content=body,
        media_type="application/yaml",
        headers={"Content-Disposition": 'attachment; filename="tasks.yaml"'},
    )


@router.post("/projects/{project_id}/tasks/{task_id}/rerun", status_code=202)
def rerun_task(project_id: str, task_id: str, request: Request) -> dict:
    """DB mode: run a done or failed task again (it goes back to pending and is enqueued).

    ``409`` unless the task is done or failed, or when not in DB mode.
    """

    runtime = get_runtime(request)
    if runtime.ensure_project(project_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown project: {project_id}")
    if runtime.task_store != "db":
        raise HTTPException(status_code=409, detail="Rerunning a task needs TASK_STORE=db")
    try:
        task = runtime.run_on_loop(runtime.rerun_task(project_id, task_id))
    except ProjectInitFailedError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown task: {task_id}") from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"task": task.model_dump(mode="json")}


@router.post("/projects/{project_id}/tasks/{task_id}/approve")
def approve_task(project_id: str, task_id: str, request: Request) -> dict:
    """Approve a task paused at a human approval gate, resuming it (task 17).

    Returns ``409`` when the task is not currently awaiting approval, so a
    double-click or a stale UI does not silently no-op.
    """

    runtime = get_runtime(request)
    orchestrator = runtime.ensure_project(project_id)
    if orchestrator is None:
        raise HTTPException(status_code=404, detail=f"Unknown project: {project_id}")
    if not orchestrator.approve(task_id):
        raise HTTPException(
            status_code=409,
            detail=f"Task {task_id} is not awaiting approval",
        )
    return {"task_id": task_id, "decision": "approved"}


@router.post("/projects/{project_id}/tasks/{task_id}/reject")
def reject_task(
    project_id: str, task_id: str, payload: RejectRequest, request: Request
) -> dict:
    """Reject a task paused at a gate, routing it back through rework (task 17).

    The ``note`` is fed to SWE_agent as context on the rework pass. Returns
    ``409`` when the task is not currently awaiting approval.
    """

    runtime = get_runtime(request)
    orchestrator = runtime.ensure_project(project_id)
    if orchestrator is None:
        raise HTTPException(status_code=404, detail=f"Unknown project: {project_id}")
    if not orchestrator.reject(task_id, payload.note):
        raise HTTPException(
            status_code=409,
            detail=f"Task {task_id} is not awaiting approval",
        )
    return {"task_id": task_id, "decision": "rejected", "note": payload.note}


@router.get("/projects/{project_id}/tasks/{task_id}")
def get_task_detail(project_id: str, task_id: str, request: Request) -> dict:
    """Return full detail for a single task (task 15).

    Combines three sources so the UI can show everything in one panel:

    * the task's stored fields (status, priority, rework_count, timestamps,
      title, description) from ``tasks.yaml`` / ``TODO.md``;
    * its per-station outputs (``pm_output`` / ``swe_output`` / ``qa_output``)
      rebuilt from the deliverable files the pipeline wrote;
    * a status history and log links derived from its agent log files.
    """

    runtime = get_runtime(request)
    if runtime.ensure_project(project_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown project: {project_id}")

    if runtime.task_store == "db":
        task = runtime.run_on_loop(runtime.get_task_db(project_id, task_id))
    else:
        task = next(
            (t for t in runtime.load_tasks(project_id) if t.id == task_id), None
        )
    if task is None:
        raise HTTPException(status_code=404, detail=f"Unknown task: {task_id}")

    project_path = runtime.project_path(project_id)

    detail = task.model_dump(mode="json")
    # Rebuild the per-station outputs from the persisted deliverables; the
    # in-memory Task the pipeline populated is not itself stored.
    detail.update(read_station_outputs(project_path, task_id))

    logs = list_task_logs(project_path, task_id)
    base = f"/api/projects/{project_id}/tasks/{task_id}"
    log_links = [
        {
            "name": entry["name"],
            "agent": entry["agent"],
            "timestamp": entry["timestamp"],
            "status": entry["status"],
            "execution_time": entry["execution_time"],
            "size": entry["size"],
            "content": entry["content"],
            "content_url": f"{base}/logs/{entry['name']}",
        }
        for entry in logs
    ]
    # A lightweight status history: one entry per agent run, in chronological
    # (log filename) order, plus the task's current status as the latest point.
    status_history = [
        {
            "agent": entry["agent"],
            "status": entry["status"],
            "timestamp": entry["timestamp"],
        }
        for entry in logs
    ]

    deliverables = [
        {
            "name": path.name,
            "size": path.stat().st_size,
            "download_url": f"{base}/deliverables/{path.name}",
        }
        for path in list_deliverables(project_path, task_id)
    ]

    return {
        "task": detail,
        "status_history": status_history,
        "logs": log_links,
        "deliverables": deliverables,
        # Accumulated LLM spend for this task (task 16).
        "usage": runtime.usage_tracker.task_usage(project_id, task_id).to_dict(),
    }


@router.get("/projects/{project_id}/tasks/{task_id}/logs")
def list_task_logs_endpoint(project_id: str, task_id: str, request: Request) -> dict:
    """List a task's agent log files with metadata and contents (Req 9.1, 9.2)."""

    runtime = get_runtime(request)
    if runtime.ensure_project(project_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown project: {project_id}")

    base = f"/api/projects/{project_id}/tasks/{task_id}"
    logs = [
        {**entry, "content_url": f"{base}/logs/{entry['name']}"}
        for entry in list_task_logs(runtime.project_path(project_id), task_id)
    ]
    return {"task_id": task_id, "logs": logs}


@router.get("/projects/{project_id}/tasks/{task_id}/logs/{filename}")
def get_task_log(
    project_id: str, task_id: str, filename: str, request: Request
) -> PlainTextResponse:
    """Return the raw contents of a single log file for a task."""

    runtime = get_runtime(request)
    if runtime.ensure_project(project_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown project: {project_id}")

    path = resolve_task_log(runtime.project_path(project_id), task_id, filename)
    if path is None:
        raise HTTPException(status_code=404, detail=f"Unknown log: {filename}")
    return PlainTextResponse(path.read_text(encoding="utf-8"))


@router.get("/projects/{project_id}/tasks/{task_id}/deliverables")
def list_task_deliverables(project_id: str, task_id: str, request: Request) -> dict:
    """List the deliverable files a task produced, with per-file download URLs."""

    runtime = get_runtime(request)
    if runtime.ensure_project(project_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown project: {project_id}")

    files = list_deliverables(runtime.project_path(project_id), task_id)
    base = f"/api/projects/{project_id}/tasks/{task_id}/deliverables"
    return {
        "task_id": task_id,
        "deliverables": [
            {
                "name": path.name,
                "size": path.stat().st_size,
                "download_url": f"{base}/{path.name}",
            }
            for path in files
        ],
    }


@router.get("/projects/{project_id}/tasks/{task_id}/deliverables/{filename}")
def download_task_deliverable(
    project_id: str, task_id: str, filename: str, request: Request
) -> FileResponse:
    """Download a single deliverable file for a task."""

    runtime = get_runtime(request)
    if runtime.ensure_project(project_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown project: {project_id}")

    path = resolve_deliverable(runtime.project_path(project_id), task_id, filename)
    if path is None:
        raise HTTPException(status_code=404, detail=f"Unknown deliverable: {filename}")
    return FileResponse(path, filename=path.name)
