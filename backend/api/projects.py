"""Project REST endpoints.

* ``GET /api/projects`` — every project folder under ``projects/``, freshly
  scanned on each call (Requirements 1.2, 1.4), plus the global mock-mode flag
  the frontend shows in its header (Requirement 4.4).
* ``GET /api/projects/{project_id}`` — one project's detail: agent configs and
  a live pipeline state snapshot (Requirement 1.3).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

import config
from core.agent_config import load_agent_configs
from core.runtime import FactoryRuntime

router = APIRouter(prefix="/api", tags=["projects"])


def get_runtime(request: Request) -> FactoryRuntime:
    return request.app.state.runtime


@router.get("/projects")
def list_projects(request: Request) -> dict:
    runtime = get_runtime(request)
    return {
        "projects": [p.model_dump(mode="json") for p in runtime.list_projects()],
        "mock_mode": config.is_mock_mode(),
        "providers": [p.name for p in config.get_configured_providers()],
    }


@router.get("/projects/{project_id}")
def get_project(project_id: str, request: Request) -> dict:
    runtime = get_runtime(request)
    orchestrator = runtime.ensure_project(project_id)
    if orchestrator is None:
        raise HTTPException(status_code=404, detail=f"Unknown project: {project_id}")

    agents = load_agent_configs(runtime.project_path(project_id))
    tasks = runtime.load_tasks(project_id)
    return {
        "id": project_id,
        "name": project_id,
        "agents": [
            {
                "name": a.name,
                "role": a.role,
                "description": a.description,
                "llm_provider": a.llm_provider,
                "llm_model": a.llm_model,
            }
            for a in agents
        ],
        "pipeline": orchestrator.snapshot(),
        "task_count": len(tasks),
        "mock_mode": config.is_mock_mode(),
        "usage": runtime.usage_summary(project_id),
    }
