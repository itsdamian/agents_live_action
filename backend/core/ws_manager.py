"""WebSocket connection manager.

Tracks active WebSocket connections grouped by ``project_id`` and broadcasts
pipeline / DevOps events to every client watching a given project
(Requirements 7.1, 7.2). All backend-pushed messages share one envelope
(see design "WebSocket 事件格式")::

    {"event": "station:update", "project_id": "my-project", "data": {...}}

Sending to a dead connection never raises out of :meth:`WSManager.broadcast`;
the connection is dropped from the group instead, so one disconnected browser
cannot stall the pipeline's event emission.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from fastapi import WebSocket

logger = logging.getLogger(__name__)


def make_event(event: str, project_id: str, data: dict) -> dict:
    """Build the uniform event envelope pushed to clients."""

    return {"event": event, "project_id": project_id, "data": data}


class WSManager:
    """Manage WebSocket connections grouped per project."""

    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)

    def connection_count(self, project_id: str | None = None) -> int:
        """Number of live connections, for one project or in total."""

        if project_id is not None:
            return len(self._connections.get(project_id, ()))
        return sum(len(group) for group in self._connections.values())

    async def connect(self, websocket: WebSocket, project_id: str) -> None:
        """Accept a client connection and register it under ``project_id``."""

        await websocket.accept()
        self._connections[project_id].add(websocket)
        logger.info(
            "WS connected to project %s (%d client(s))",
            project_id,
            self.connection_count(project_id),
        )

    def disconnect(self, websocket: WebSocket, project_id: str) -> None:
        """Deregister a connection. Safe to call for unknown sockets."""

        group = self._connections.get(project_id)
        if group is None:
            return
        group.discard(websocket)
        if not group:
            del self._connections[project_id]

    async def send_to(
        self, websocket: WebSocket, event: str, project_id: str, data: dict
    ) -> None:
        """Send one event to a single client (e.g. the initial snapshot)."""

        await websocket.send_json(make_event(event, project_id, data))

    async def broadcast(self, project_id: str, event: str, data: dict) -> None:
        """Push an event to every client watching ``project_id``.

        Dead connections are pruned instead of raising (a client that vanished
        mid-send must not break the pipeline's event callback).
        """

        group = self._connections.get(project_id)
        if not group:
            return
        message = make_event(event, project_id, data)
        dead: list[WebSocket] = []
        for websocket in list(group):
            try:
                await websocket.send_json(message)
            except Exception:
                dead.append(websocket)
        for websocket in dead:
            self.disconnect(websocket, project_id)
        if dead:
            logger.info(
                "Pruned %d dead WS connection(s) from project %s",
                len(dead),
                project_id,
            )

    async def broadcast_all(self, event: str, data: dict) -> None:
        """Push an event to every connected client regardless of project.

        Used for system-wide events such as DevOps alerts (Requirement 7.8).
        """

        for project_id in list(self._connections):
            await self.broadcast(project_id, event, data)
