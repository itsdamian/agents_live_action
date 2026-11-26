"""Unit tests for the project scanner.

Uses pytest's ``tmp_path`` fixture to build fake ``projects/`` directories with
various layouts, so the tests never touch the real repo-root projects folder.

Requirements: 1.1 (auto-scan projects/ to identify projects),
1.4 (next query reflects added/removed folders).
"""

from __future__ import annotations

import shutil
from pathlib import Path

from core.project_scanner import (
    ProjectStructure,
    detect_structure,
    scan_projects,
    scan_projects_with_structure,
)
from models.project import Project


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_project(
    projects_dir: Path,
    name: str,
    *,
    agents: bool = False,
    tasks_yaml: bool = False,
    todo_md: bool = False,
) -> Path:
    """Create a fake project folder with the requested structure pieces."""
    project = projects_dir / name
    project.mkdir(parents=True)
    if agents:
        (project / "agents").mkdir()
    if tasks_yaml:
        (project / "tasks.yaml").write_text("tasks: []\n", encoding="utf-8")
    if todo_md:
        (project / "TODO.md").write_text("- [ ] do things\n", encoding="utf-8")
    return project


# ---------------------------------------------------------------------------
# Basic scanning (Requirement 1.1)
# ---------------------------------------------------------------------------


def test_empty_projects_dir_returns_empty_list(tmp_path):
    assert scan_projects(tmp_path) == []


def test_nonexistent_projects_dir_returns_empty_list(tmp_path):
    missing = tmp_path / "does_not_exist"
    assert scan_projects(missing) == []


def test_single_project_detected(tmp_path):
    _make_project(tmp_path, "alpha")

    projects = scan_projects(tmp_path)

    assert len(projects) == 1
    project = projects[0]
    assert isinstance(project, Project)
    assert project.id == "alpha"
    assert project.name == "alpha"
    assert Path(project.path) == (tmp_path / "alpha").resolve()
    # Default fields untouched by the scanner.
    assert project.agents == []
    assert project.task_count == 0
    assert project.active_task_count == 0
    assert project.status == "idle"


def test_multiple_projects_sorted_by_id(tmp_path):
    _make_project(tmp_path, "charlie")
    _make_project(tmp_path, "alpha")
    _make_project(tmp_path, "bravo")

    ids = [p.id for p in scan_projects(tmp_path)]

    assert ids == ["alpha", "bravo", "charlie"]


# ---------------------------------------------------------------------------
# Ignoring non-project entries
# ---------------------------------------------------------------------------


def test_files_are_not_treated_as_projects(tmp_path):
    _make_project(tmp_path, "realproject")
    (tmp_path / "loose_file.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "tasks.yaml").write_text("x", encoding="utf-8")

    ids = [p.id for p in scan_projects(tmp_path)]

    assert ids == ["realproject"]


def test_hidden_folders_ignored(tmp_path):
    _make_project(tmp_path, "visible")
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".git").mkdir()

    ids = [p.id for p in scan_projects(tmp_path)]

    assert ids == ["visible"]


def test_gitkeep_file_ignored(tmp_path):
    # Mirrors the real projects/ dir which ships a .gitkeep placeholder.
    (tmp_path / ".gitkeep").write_text("", encoding="utf-8")
    _make_project(tmp_path, "proj")

    ids = [p.id for p in scan_projects(tmp_path)]

    assert ids == ["proj"]


# ---------------------------------------------------------------------------
# Structure detection
# ---------------------------------------------------------------------------


def test_detect_structure_all_present(tmp_path):
    project = _make_project(
        tmp_path, "full", agents=True, tasks_yaml=True, todo_md=True
    )

    structure = detect_structure(project)

    assert structure == ProjectStructure(
        has_agents_dir=True, has_tasks_yaml=True, has_todo_md=True
    )


def test_detect_structure_none_present(tmp_path):
    project = _make_project(tmp_path, "bare")

    structure = detect_structure(project)

    assert structure == ProjectStructure(
        has_agents_dir=False, has_tasks_yaml=False, has_todo_md=False
    )


def test_detect_structure_partial(tmp_path):
    project = _make_project(tmp_path, "partial", agents=True, todo_md=True)

    structure = detect_structure(project)

    assert structure.has_agents_dir is True
    assert structure.has_tasks_yaml is False
    assert structure.has_todo_md is True


def test_tasks_yaml_as_directory_not_detected_as_file(tmp_path):
    # A directory named tasks.yaml should not count as the tasks.yaml file.
    project = _make_project(tmp_path, "weird")
    (project / "tasks.yaml").mkdir()

    structure = detect_structure(project)

    assert structure.has_tasks_yaml is False


def test_scan_with_structure_pairs_projects_and_structure(tmp_path):
    _make_project(tmp_path, "one", agents=True)
    _make_project(tmp_path, "two", tasks_yaml=True, todo_md=True)

    results = scan_projects_with_structure(tmp_path)

    assert [p.id for p, _ in results] == ["one", "two"]
    by_id = {p.id: s for p, s in results}
    assert by_id["one"].has_agents_dir is True
    assert by_id["one"].has_tasks_yaml is False
    assert by_id["two"].has_tasks_yaml is True
    assert by_id["two"].has_todo_md is True


# ---------------------------------------------------------------------------
# Fresh scan on every call (Requirement 1.4)
# ---------------------------------------------------------------------------


def test_added_project_reflected_on_next_scan(tmp_path):
    _make_project(tmp_path, "first")
    assert [p.id for p in scan_projects(tmp_path)] == ["first"]

    _make_project(tmp_path, "second")
    assert [p.id for p in scan_projects(tmp_path)] == ["first", "second"]


def test_removed_project_reflected_on_next_scan(tmp_path):
    _make_project(tmp_path, "keep")
    removed = _make_project(tmp_path, "remove")
    assert [p.id for p in scan_projects(tmp_path)] == ["keep", "remove"]

    shutil.rmtree(removed)
    assert [p.id for p in scan_projects(tmp_path)] == ["keep"]
