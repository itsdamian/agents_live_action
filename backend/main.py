"""FastAPI entry point for the Agent Factory Platform backend.

Startup (Requirements 1.1, 2.2, 4.1, 4.3, 4.4):
    1. scan ``projects/`` and bootstrap every project found — missing agent
       YAML configs are created with defaults,
    2. start one pipeline orchestrator + task watcher per project (existing
       ``status: pending`` tasks are enqueued immediately),
    3. start the global DevOps monitor,
    4. log the detected LLM providers and whether the system runs in
       [MOCK MODE].

Shutdown stops watchers, the monitor, and pipeline workers gracefully
(Requirement 8.6).
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import config
from api import projects, tasks, websocket
from core.runtime import FactoryRuntime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    runtime: FactoryRuntime = app.state.runtime
    await runtime.startup()
    yield
    await runtime.shutdown()


def create_app(projects_dir: str | None = None) -> FastAPI:
    """Build the FastAPI app.

    ``projects_dir`` is overridable for tests. When not given explicitly it
    falls back to the ``PROJECTS_DIR`` environment variable (set to the mounted
    volume path ``/app/projects`` in the Docker image, see docker-compose.yml)
    and finally to the packaged default. This keeps the container's scan path
    aligned with the ``./projects:/app/projects`` volume mount regardless of
    where the source lives inside the image (Requirements 8.1, 8.2, 8.5).
    """

    resolved_projects_dir = projects_dir or os.environ.get("PROJECTS_DIR") or None

    app = FastAPI(title="Agent Factory Platform", lifespan=lifespan)
    app.state.runtime = FactoryRuntime(projects_dir=resolved_projects_dir)

    # The frontend is normally served by nginx on the same origin; CORS is for
    # the Vite dev server / direct-port access during development.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(projects.router)
    app.include_router(tasks.router)
    app.include_router(websocket.router)

    @app.get("/api/health")
    async def health() -> dict:
        """Basic liveness probe."""

        return {
            "status": "ok",
            "mock_mode": config.is_mock_mode(),
            "providers": [p.name for p in config.get_configured_providers()],
        }

    return app


app = create_app()
