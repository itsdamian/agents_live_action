"""Unit tests for :class:`core.ws_manager.WSManager`.

Uses FastAPI's TestClient against a tiny purpose-built app. Broadcasts are
triggered through an HTTP route so they execute on the app's event loop (the
same way the pipeline's event callback invokes the manager in production).
"""

from __future__ import annotations

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.testclient import TestClient

from core.ws_manager import WSManager, make_event


def make_test_app(manager: WSManager) -> FastAPI:
    app = FastAPI()

    @app.websocket("/ws/{project_id}")
    async def ws_endpoint(websocket: WebSocket, project_id: str) -> None:
        await manager.connect(websocket, project_id)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            manager.disconnect(websocket, project_id)

    @app.post("/emit/{project_id}")
    async def emit(project_id: str) -> dict:
        await manager.broadcast(project_id, "station:update", {"station": "pm"})
        return {"sent": True}

    @app.post("/emit-all")
    async def emit_all() -> dict:
        await manager.broadcast_all("devops:alert", {"level": "warning"})
        return {"sent": True}

    return app


def test_make_event_envelope():
    assert make_event("task:created", "proj", {"id": "task-1"}) == {
        "event": "task:created",
        "project_id": "proj",
        "data": {"id": "task-1"},
    }


def test_broadcast_reaches_only_the_projects_clients():
    manager = WSManager()
    client = TestClient(make_test_app(manager))

    with client.websocket_connect("/ws/proj-a") as ws_a, client.websocket_connect(
        "/ws/proj-b"
    ) as ws_b:
        assert manager.connection_count("proj-a") == 1
        assert manager.connection_count() == 2

        client.post("/emit/proj-a")
        message = ws_a.receive_json()
        assert message["event"] == "station:update"
        assert message["project_id"] == "proj-a"
        assert message["data"] == {"station": "pm"}

        # proj-b must not receive proj-a's event; verify by broadcasting to all
        # and asserting the *first* thing proj-b sees is the broadcast-all.
        client.post("/emit-all")
        assert ws_b.receive_json()["event"] == "devops:alert"


def test_broadcast_to_project_without_clients_is_noop():
    manager = WSManager()
    client = TestClient(make_test_app(manager))
    response = client.post("/emit/ghost-project")
    assert response.status_code == 200


def test_disconnect_removes_connection():
    manager = WSManager()
    client = TestClient(make_test_app(manager))

    with client.websocket_connect("/ws/proj-a"):
        assert manager.connection_count("proj-a") == 1
    assert manager.connection_count("proj-a") == 0
    # Emitting after the client left must not raise.
    assert client.post("/emit/proj-a").status_code == 200


def test_broadcast_prunes_dead_connections():
    manager = WSManager()
    client = TestClient(make_test_app(manager))

    with client.websocket_connect("/ws/proj-a") as ws:
        # Simulate a client that vanished without a proper disconnect: close
        # the underlying socket, then broadcast twice — the first send fails
        # and the connection must be pruned rather than raising.
        ws.close()
        client.post("/emit/proj-a")
        client.post("/emit/proj-a")
    assert manager.connection_count("proj-a") == 0
