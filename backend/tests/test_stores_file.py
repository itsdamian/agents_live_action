"""FileTaskRepository against the shared task contract (Task 8).

Operations ``tasks.yaml`` cannot offer are skipped by the contract, and a separate
test checks that they raise ``NotSupportedInFileMode``.
"""

from __future__ import annotations

import asyncio

import pytest
import yaml

from core.stores import NotSupportedInFileMode
from core.stores.file_tasks import FileTaskRepository
from models.task import Approval, Priority, Status
from tests.stores.contract_tasks import TaskRepositoryContract, make


class TestFileTasks(TaskRepositoryContract):
    UNSUPPORTED = frozenset(
        {"insert_if_absent", "get_meta", "set_rework_count", "update_pending_content"}
    )

    def make_repo(self, tmp_path):
        # File mode works on existing project folders (the runtime only knows folders).
        (tmp_path / self.PROJECT).mkdir(exist_ok=True)
        return FileTaskRepository(tmp_path)

    @pytest.fixture(autouse=True)
    def _bind_dir(self, tmp_path):
        self._dir = tmp_path

    async def seed(self, repo, project_id, task):
        """Append the task to <project>/tasks.yaml the way a person would write it."""

        project = self._dir / project_id
        project.mkdir(parents=True, exist_ok=True)
        path = project / "tasks.yaml"
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {"tasks": []}
        raw["tasks"].append(
            {
                "id": task.id, "title": task.title, "description": task.description,
                "priority": task.priority.value, "status": task.status.value,
                "approval": task.approval.value, "max_reworks": task.max_reworks,
                "created_at": task.created_at.isoformat(), "updated_at": task.updated_at.isoformat(),
            }
        )
        path.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")


def test_unsupported_operations_raise_a_clear_error(tmp_path):
    repo = FileTaskRepository(tmp_path)

    async def scenario():
        with pytest.raises(NotSupportedInFileMode):
            await repo.insert_if_absent("p", make("task-001"), source="yaml")
        with pytest.raises(NotSupportedInFileMode):
            await repo.get_meta("p", "task-001")
        with pytest.raises(NotSupportedInFileMode):
            await repo.set_rework_count("p", "task-001", 1)
        with pytest.raises(NotSupportedInFileMode):
            await repo.update_pending_content(
                "p", "task-001", title="t", description="d", priority=Priority.LOW,
                approval=Approval.NONE, max_reworks=3,
            )

    asyncio.run(scenario())
    assert issubclass(NotSupportedInFileMode, NotImplementedError)


def test_it_reads_tasks_yaml_and_todo_md_like_the_runtime(tmp_path):
    project = tmp_path / "p"
    project.mkdir()
    (project / "tasks.yaml").write_text(
        yaml.safe_dump({"tasks": [{"id": "task-001", "title": "from yaml", "description": "d",
                                   "priority": "low", "status": "pending"}]}),
        encoding="utf-8",
    )
    (project / "TODO.md").write_text("- [ ] write docs\n- [x] ship it\n", encoding="utf-8")

    tasks = asyncio.run(FileTaskRepository(tmp_path).list_tasks("p"))

    assert [t.title for t in tasks] == ["from yaml", "write docs", "ship it"]
    assert [t.status for t in tasks] == [Status.PENDING, Status.PENDING, Status.DONE]


def test_set_status_goes_through_the_locked_atomic_writer(tmp_path, monkeypatch):
    """The wrapper adds no write path of its own: it calls write_task_status."""

    import core.stores.file_tasks as module

    calls = []
    monkeypatch.setattr(module, "write_task_status", lambda path, task_id, status: calls.append((path, task_id, status)) or True)

    assert asyncio.run(FileTaskRepository(tmp_path).set_status("p", "task-001", Status.DONE)) is True
    assert calls == [(tmp_path / "p", "task-001", Status.DONE)]
