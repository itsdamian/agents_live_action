"""WebSocket endpoint.

``WS /ws/{project_id}`` — on connect the client immediately receives a
``snapshot`` event carrying the current pipeline state, task list, mock-mode
flag, and recent DevOps alerts; afterwards it receives live pipeline and
DevOps events pushed by the runtime (Requirements 7.1, 7.6, 7.8).

Incoming client messages are ignored (read only to detect disconnects); the
protocol is push-only.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from core.runtime import FactoryRuntime

logger = logging.getLogger(__name__)

router = APIRouter()

# Close code for "no such project" (application-defined 4xxx range).
WS_CLOSE_UNKNOWN_PROJECT = 4004

EVENT_SNAPSHOT = "snapshot"


@router.websocket("/ws/{project_id}")
async def project_websocket(websocket: WebSocket, project_id: str) -> None:
    runtime: FactoryRuntime = websocket.app.state.runtime
    orchestrator = runtime.ensure_project(project_id)
    if orchestrator is None:
        # Accept then close with an application code so browsers can read it.
        await websocket.accept()
        await websocket.close(
            code=WS_CLOSE_UNKNOWN_PROJECT, reason=f"Unknown project: {project_id}"
        )
        return

    manager = runtime.ws_manager
    await manager.connect(websocket, project_id)
    try:
        await manager.send_to(
            websocket, EVENT_SNAPSHOT, project_id, runtime.snapshot(project_id)
        )
        while True:
            # Push-only protocol: drain (and ignore) client messages so the
            # disconnect surfaces here.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(websocket, project_id)
