"""``projects/logs/`` holds agent logs written without a project (see progress 2026-11-21);
it is not a project, so it must not appear in ``/api/projects`` or be started by the runtime."""

from __future__ import annotations

from fastapi.testclient import TestClient

from core.project_scanner import scan_projects, scan_projects_with_structure
from main import create_app


def _make(root, name, *, tasks=True):
    project = root / name
    project.mkdir()
    if tasks:
        (project / "tasks.yaml").write_text("tasks: []\n", encoding="utf-8")
    return project


def test_logs_folder_is_not_a_project(tmp_path):
    _make(tmp_path, "demo")
    (_make(tmp_path, "logs", tasks=False) / "pm-agent.log").write_text("x", encoding="utf-8")

    assert [p.id for p in scan_projects(tmp_path)] == ["demo"]
    assert [p.id for p, _ in scan_projects_with_structure(tmp_path)] == ["demo"]


def test_logs_is_ignored_only_as_the_exact_name(tmp_path):
    _make(tmp_path, "logs-viewer")
    _make(tmp_path, "logs")

    assert [p.id for p in scan_projects(tmp_path)] == ["logs-viewer"]


def test_api_projects_does_not_list_logs(tmp_path, monkeypatch):
    monkeypatch.setenv("FORCE_MOCK", "true")
    monkeypatch.setenv("MOCK_LLM_DELAY", "0")
    for name in ("TASK_STORE", "PIPELINE_CHECKPOINTER", "DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    _make(tmp_path, "demo")
    _make(tmp_path, "logs", tasks=False)

    with TestClient(create_app(projects_dir=str(tmp_path))) as client:
        body = client.get("/api/projects").json()

    ids = [p["id"] for p in (body["projects"] if isinstance(body, dict) else body)]
    assert ids == ["demo"]
