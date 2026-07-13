"""Project data model.

Represents a project folder discovered under projects/.
"""

from __future__ import annotations

from pydantic import BaseModel

from models.agent import AgentConfig


class Project(BaseModel):
    """A project discovered by the project scanner."""

    id: str  # folder name
    name: str  # display name
    path: str  # absolute path
    agents: list[AgentConfig] = []
    task_count: int = 0
    active_task_count: int = 0
    status: str = "idle"  # idle | running | error
