"""Sticky-note history REST endpoint (plan D20, §7, §7.1a).

* ``GET /api/projects/{project_id}/history`` -- the note wall: ``status`` (comma separated, any of
  the five task states), ``pipeline``, ``limit`` (default 50, at most 200) and ``offset`` ->
  ``{"items": [HistoryItem], "total", "counts"}``. Works in both task stores: DB mode reads the
  tasks and their runs from the database, file mode builds what it can from tasks.yaml / TODO.md,
  the log file names and the deliverable files.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query, Request

from api.projects import get_runtime
from core.history_view import DEFAULT_LIMIT, MAX_LIMIT, parse_status_filter

router = APIRouter(prefix="/api", tags=["history"])


@router.get("/projects/{project_id}/history")
async def get_history(
    project_id: str,
    request: Request,
    status: str | None = None,
    pipeline: str | None = None,
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
) -> dict:
    runtime = get_runtime(request)
    # ensure_project reads the project folder (agent configs): keep that file I/O off the loop.
    if await asyncio.to_thread(runtime.ensure_project, project_id) is None:
        raise HTTPException(status_code=404, detail=f"找不到專案：{project_id}")
    try:
        statuses = parse_status_filter(status)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # The runtime lives on the app's event loop (started in the lifespan), so await it directly.
    return await runtime.list_history(
        project_id, statuses=statuses, pipeline=pipeline, limit=limit, offset=offset
    )
