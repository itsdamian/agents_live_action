"""InMemory LineupRepository against the shared contract (Task 18)."""

from __future__ import annotations

import pytest

from core.stores.memory import (
    InMemoryLineupRepository,
    InMemoryProjectRepository,
    InMemoryRunRepository,
    InMemoryTaskRepository,
)
from tests.stores.contract_lineup import LineupRepositoryContract


class TestInMemoryLineup(LineupRepositoryContract):
    @pytest.fixture
    def repos(self):
        projects = InMemoryProjectRepository(InMemoryTaskRepository(), InMemoryRunRepository())
        return InMemoryLineupRepository(projects), projects
