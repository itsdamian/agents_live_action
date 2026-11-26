"""Project scanner.

Scans the ``projects/`` directory and turns each subdirectory into a
:class:`~models.project.Project`. The scan runs against the live filesystem on
every call (no caching), so adding or removing a project folder is reflected on
the next query.

For each project the scanner also detects the presence of the expected
structure pieces: the ``agents/`` subdirectory, a ``tasks.yaml`` file, and a
``TODO.md`` file. This structure information is returned alongside the project
so callers (e.g. agent-config bootstrap) can decide what to create.

Requirements: 1.1 (auto-scan projects/ to identify projects),
1.4 (next query reflects added/removed project folders).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from models.project import Project

# Default projects directory: the repo-root ``projects/`` folder, resolved
# relative to this file (backend/core/project_scanner.py -> repo root). Kept as
# a module-level default so callers can override it (e.g. in tests) without
# hardcoding an absolute path anywhere.
DEFAULT_PROJECTS_DIR: Path = Path(__file__).resolve().parents[2] / "projects"

# Directory entries that are never treated as projects even though they are
# directories (or otherwise look like folder noise). ``logs`` is where an agent
# without a project writes its log files (BaseAgent falls back to ``projects/``).
_IGNORED_NAMES: frozenset[str] = frozenset({".gitkeep", "logs"})


@dataclass(frozen=True)
class ProjectStructure:
    """Result of inspecting a single project directory's layout.

    Attributes:
        has_agents_dir: True if an ``agents/`` subdirectory exists.
        has_tasks_yaml: True if a ``tasks.yaml`` file exists.
        has_todo_md: True if a ``TODO.md`` file exists.
    """

    has_agents_dir: bool
    has_tasks_yaml: bool
    has_todo_md: bool


def _is_project_dir(entry: Path) -> bool:
    """Return True if ``entry`` should be treated as a project folder.

    Non-directories, hidden folders (names starting with ``.``) and known
    noise entries (e.g. ``.gitkeep``) are excluded.
    """

    if not entry.is_dir():
        return False
    if entry.name.startswith("."):
        return False
    if entry.name in _IGNORED_NAMES:
        return False
    return True


def detect_structure(project_path: Path) -> ProjectStructure:
    """Inspect a project directory and report which structure pieces exist."""

    project_path = Path(project_path)
    return ProjectStructure(
        has_agents_dir=(project_path / "agents").is_dir(),
        has_tasks_yaml=(project_path / "tasks.yaml").is_file(),
        has_todo_md=(project_path / "TODO.md").is_file(),
    )


def scan_projects(projects_dir: str | Path | None = None) -> list[Project]:
    """Scan ``projects_dir`` and return a :class:`Project` for each subfolder.

    The scan reads the filesystem fresh on every call, so newly added or
    removed folders are reflected on the next invocation (Requirement 1.4).

    Args:
        projects_dir: Directory to scan. Defaults to the repo-root
            ``projects/`` folder when not provided.

    Returns:
        A list of :class:`Project` objects, sorted by id (folder name) for
        deterministic output. Returns an empty list when the directory does
        not exist.
    """

    base = Path(projects_dir) if projects_dir is not None else DEFAULT_PROJECTS_DIR

    if not base.is_dir():
        return []

    projects: list[Project] = []
    for entry in sorted(base.iterdir(), key=lambda p: p.name):
        if not _is_project_dir(entry):
            continue
        projects.append(
            Project(
                id=entry.name,
                name=entry.name,
                path=str(entry.resolve()),
            )
        )

    return projects


def scan_projects_with_structure(
    projects_dir: str | Path | None = None,
) -> list[tuple[Project, ProjectStructure]]:
    """Like :func:`scan_projects` but also return each project's structure.

    Useful for the agent-config bootstrap step, which needs to know whether
    the ``agents/`` directory already exists.
    """

    projects = scan_projects(projects_dir)
    return [(project, detect_structure(Path(project.path))) for project in projects]
