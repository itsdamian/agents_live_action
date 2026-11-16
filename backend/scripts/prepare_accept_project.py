"""Build the acceptance-test project (Task 14, AC10).

Writes ``<project-dir>/tasks.yaml`` containing every task of the source
``tasks.yaml`` whose id is not ``task-gate`` (id/title/description/priority are
copied, ``status`` comes from ``--status``) plus one gated task, ``task-gate``
(``approval: after_swe``). When the target has no ``agents/`` directory it is
copied from the source.

The source is only ever read. ``--source`` and ``--project-dir`` may point at the
same directory: the source file is read completely before the target is written.

Run it against a throw-away ``--project-dir`` (e.g. ``projects/lg-accept``), never
``projects/example-project``: once the backend sees ``pending`` tasks it runs them
and rewrites that project's tasks.yaml with the resulting statuses.

Standalone on purpose (stdlib + PyYAML only): it also runs inside the backend
container as ``python scripts/prepare_accept_project.py``.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import yaml

GATE_TASK_ID = "task-gate"
COPIED_FIELDS = ("id", "title", "description", "priority")


def build_tasks(source_tasks_file: Path, status: str) -> list[dict]:
    raw = yaml.safe_load(source_tasks_file.read_text(encoding="utf-8"))
    entries = []
    for entry in raw["tasks"]:
        if entry.get("id") == GATE_TASK_ID:
            continue
        item = {field: entry[field] for field in COPIED_FIELDS}
        item["status"] = status
        entries.append(item)
    entries.append(
        {
            "id": GATE_TASK_ID,
            "title": "Acceptance gated task",
            "description": "Exercise the approval gate.",
            "priority": "low",
            "approval": "after_swe",
            "status": status,
        }
    )
    return entries


def prepare(source: Path, project_dir: Path, status: str) -> list[dict]:
    # Read everything first: source and target may be the same directory.
    entries = build_tasks(source / "tasks.yaml", status)

    project_dir.mkdir(parents=True, exist_ok=True)
    if not (project_dir / "agents").exists() and (source / "agents").is_dir():
        shutil.copytree(source / "agents", project_dir / "agents")

    (project_dir / "tasks.yaml").write_text(
        yaml.safe_dump({"tasks": entries}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return entries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--status", required=True, choices=("done", "pending"))
    args = parser.parse_args(argv)

    entries = prepare(args.source, args.project_dir, args.status)
    print(f"wrote {len(entries)} tasks to {args.project_dir / 'tasks.yaml'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
