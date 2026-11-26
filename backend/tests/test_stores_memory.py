"""InMemory repositories against the shared contracts (Task 8)."""

from __future__ import annotations

import pytest

from core.stores.memory import (
    InMemoryProjectRepository,
    InMemoryRunRepository,
    InMemoryTaskRepository,
    InMemoryUsageRepository,
)
from tests.stores.contract_projects import ProjectRepositoryContract
from tests.stores.contract_runs import RunRepositoryContract
from tests.stores.contract_tasks import TaskRepositoryContract, test_priority_rank_matches_the_pipeline_queue_order  # noqa: F401
from tests.stores.contract_usage import UsageRepositoryContract


class TestInMemoryTasks(TaskRepositoryContract):
    def make_repo(self, tmp_path):
        return InMemoryTaskRepository()

    async def seed(self, repo, project_id, task):
        await repo.insert_if_absent(project_id, task, source="yaml")


class TestInMemoryUsage(UsageRepositoryContract):
    def make_repo(self, tmp_path):
        return InMemoryUsageRepository()


class TestInMemoryRuns(RunRepositoryContract):
    def make_repo(self, tmp_path):
        return InMemoryRunRepository()


class TestInMemoryProjects(ProjectRepositoryContract):
    @pytest.fixture
    def repos(self):
        tasks, runs = InMemoryTaskRepository(), InMemoryRunRepository()
        return InMemoryProjectRepository(tasks, runs), tasks, runs
