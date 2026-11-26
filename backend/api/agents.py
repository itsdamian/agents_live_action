"""Agents and pipelines REST endpoints (plan §7.1, §7.1a, D4, D17; AC16-AC19).

* ``GET /api/projects/{p}/agents`` -- ``{"agents": [AgentView], "pipelines": [PipelineView]}``.
* ``POST /api/projects/{p}/agents`` -- a custom agent, seated behind ``after`` (``null`` = first;
  no ``after`` = last) -> 201 ``{"agent", "effective"}``.
* ``GET`` / ``PUT`` / ``DELETE /api/projects/{p}/agents/{id}``.
* ``GET /api/projects/{p}/agents/export`` (YAML) and ``POST /api/projects/{p}/agents/import``
  (YAML body, replaces the whole roster).
* ``GET /api/projects/{p}/pipelines`` and ``PUT /api/projects/{p}/pipelines/{pid}``.
* ``GET`` / ``PUT /api/projects/{p}/settings`` -- the project's default LLM (plan D21):
  ``{"llm", "force_mock", "anthropic_key_present", "models"}`` (+ ``effective`` on PUT).

Seat changes answer 200 (``effective: "now"``) when the line is idle and 202 (``"next_task"``)
while a task is in flight. Writes need ``PIPELINE_ENGINE=langgraph`` and ``TASK_STORE=db`` (409
otherwise; reads always work). Errors are ``{"detail": <Traditional Chinese>, "errors"?: [...]}``.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from api.projects import get_runtime
from core.runtime import LineupRequestError, ProjectInitFailedError
from models.lineup import InvalidLineup

router = APIRouter(prefix="/api", tags=["agents"])

_STATUS = {"now": 200, "next_task": 202}


def _error(status: int, detail: str, errors: list[dict] | None = None) -> JSONResponse:
    body: dict = {"detail": detail}
    if errors is not None:
        body["errors"] = errors
    return JSONResponse(status_code=status, content=body)


async def _runtime_for(request: Request, project_id: str):
    runtime = get_runtime(request)
    # ensure_project reads the project folder (agent configs): keep that file I/O off the loop.
    if await asyncio.to_thread(runtime.ensure_project, project_id) is None:
        return runtime, _error(404, f"找不到專案：{project_id}")
    return runtime, None


async def _json_body(request: Request):
    try:
        return await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise LineupRequestError(422, "請求內容必須是 JSON") from None


async def _guarded(call):
    """Run a runtime write; turn its refusals into error responses."""

    try:
        return await call(), None
    except LineupRequestError as exc:
        return None, _error(exc.status, exc.detail, exc.errors)
    except InvalidLineup as exc:
        return None, _error(422, "座位安排不合格", exc.errors)
    except ProjectInitFailedError as exc:
        return None, _error(503, str(exc))


@router.get("/projects/{project_id}/agents")
async def list_agents(project_id: str, request: Request):
    runtime, missing = await _runtime_for(request, project_id)
    if missing is not None:
        return missing
    return await runtime.agents_overview(project_id)


@router.post("/projects/{project_id}/agents")
async def create_agent(project_id: str, request: Request):
    runtime, missing = await _runtime_for(request, project_id)
    if missing is not None:
        return missing

    async def call():
        return await runtime.create_agent(project_id, await _json_body(request))

    result, failed = await _guarded(call)
    if failed is not None:
        return failed
    agent, effective = result
    return JSONResponse(status_code=201, content={"agent": agent, "effective": effective})


# Declared before /agents/{agent_id}, which would otherwise take "export" as an id.
@router.get("/projects/{project_id}/agents/export")
async def export_agents(project_id: str, request: Request):
    runtime, missing = await _runtime_for(request, project_id)
    if missing is not None:
        return missing
    text = await runtime.export_lineup(project_id)
    return Response(
        content=text, media_type="application/yaml",
        headers={"Content-Disposition": f'attachment; filename="{project_id}-agents.yaml"'},
    )


@router.post("/projects/{project_id}/agents/import")
async def import_agents(project_id: str, request: Request):
    runtime, missing = await _runtime_for(request, project_id)
    if missing is not None:
        return missing

    async def call():
        try:
            text = (await request.body()).decode("utf-8")
        except UnicodeDecodeError:
            raise LineupRequestError(422, "YAML 必須是 UTF-8 文字") from None
        return await runtime.import_lineup(project_id, text)

    result, failed = await _guarded(call)
    if failed is not None:
        return failed
    body, effective = result
    return JSONResponse(status_code=_STATUS[effective], content={**body, "effective": effective})


@router.get("/projects/{project_id}/agents/{agent_id}")
async def get_agent(project_id: str, agent_id: str, request: Request):
    runtime, missing = await _runtime_for(request, project_id)
    if missing is not None:
        return missing
    detail = await runtime.agent_detail(project_id, agent_id)
    if detail is None:
        return _error(404, f"找不到 agent：{agent_id}")
    return detail


@router.put("/projects/{project_id}/agents/{agent_id}")
async def update_agent(project_id: str, agent_id: str, request: Request):
    runtime, missing = await _runtime_for(request, project_id)
    if missing is not None:
        return missing

    async def call():
        return await runtime.update_agent(project_id, agent_id, await _json_body(request))

    result, failed = await _guarded(call)
    if failed is not None:
        return failed
    agent, effective = result
    return JSONResponse(status_code=_STATUS[effective], content={"agent": agent, "effective": effective})


@router.delete("/projects/{project_id}/agents/{agent_id}")
async def delete_agent(project_id: str, agent_id: str, request: Request):
    runtime, missing = await _runtime_for(request, project_id)
    if missing is not None:
        return missing
    effective, failed = await _guarded(lambda: runtime.delete_agent(project_id, agent_id))
    if failed is not None:
        return failed
    return JSONResponse(status_code=_STATUS[effective], content={"agent_id": agent_id, "effective": effective})


@router.get("/projects/{project_id}/pipelines")
async def list_pipelines(project_id: str, request: Request):
    runtime, missing = await _runtime_for(request, project_id)
    if missing is not None:
        return missing
    return {"pipelines": (await runtime.agents_overview(project_id))["pipelines"]}


@router.put("/projects/{project_id}/pipelines/{pipeline_id}")
async def put_pipeline(project_id: str, pipeline_id: str, request: Request):
    runtime, missing = await _runtime_for(request, project_id)
    if missing is not None:
        return missing

    async def call():
        problem = runtime.lineup_write_problem(project_id)
        if problem is not None:
            raise LineupRequestError(409, problem)
        body = await _json_body(request)
        seats = body.get("seats") if isinstance(body, dict) else None
        if not isinstance(seats, list) or not all(isinstance(s, str) for s in seats):
            raise LineupRequestError(422, "seats 必須是 agent id 的清單",
                                     [{"field": "seats", "message": "seats 必須是 agent id 的清單"}])
        try:
            return await runtime.request_seats(project_id, pipeline_id, seats)
        except KeyError:
            raise LineupRequestError(404, f"找不到這條線：{pipeline_id}") from None

    result, failed = await _guarded(call)
    if failed is not None:
        return failed
    pipeline, effective = result
    return JSONResponse(status_code=_STATUS[effective], content={"pipeline": pipeline, "effective": effective})


@router.get("/projects/{project_id}/settings")
async def get_settings(project_id: str, request: Request):
    runtime, missing = await _runtime_for(request, project_id)
    if missing is not None:
        return missing
    return await runtime.settings_overview(project_id)


@router.put("/projects/{project_id}/settings")
async def put_settings(project_id: str, request: Request):
    runtime, missing = await _runtime_for(request, project_id)
    if missing is not None:
        return missing

    async def call():
        problem = runtime.lineup_write_problem(project_id)
        if problem is not None:
            raise LineupRequestError(409, problem)
        return await runtime.update_settings(project_id, await _json_body(request))

    result, failed = await _guarded(call)
    if failed is not None:
        return failed
    settings, effective = result
    return JSONResponse(status_code=_STATUS[effective], content={**settings, "effective": effective})
