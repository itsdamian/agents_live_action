"""Tests for the acceptance helper scripts (compare_ws_events, prepare_accept_project)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
EXAMPLE_PROJECT = Path(__file__).resolve().parents[2] / "projects" / "example-project"

EVENTS = [
    {"event": "snapshot", "data": {"anything": 1}},
    {"event": "task:created", "data": {"task": {"id": "t", "status": "pending", "rework_count": 0}, "queue_size": 2}},
    {"event": "station:update", "data": {"station": "pm", "status": "working",
                                         "task": {"id": "t", "status": "in_progress", "rework_count": 0}}},
]


def _write(path: Path, events: list[dict]) -> Path:
    path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    return path


def _compare(a: Path, b: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "compare_ws_events.py"), str(a), str(b)],
        capture_output=True, text=True,
    )


def test_identical_files_exit_zero(tmp_path):
    a = _write(tmp_path / "a.jsonl", EVENTS)
    b = _write(tmp_path / "b.jsonl", EVENTS)

    assert _compare(a, b).returncode == 0


def test_snapshot_events_are_ignored(tmp_path):
    other = [{"event": "snapshot", "data": {"different": True}}] + EVENTS[1:]
    a = _write(tmp_path / "a.jsonl", EVENTS)
    b = _write(tmp_path / "b.jsonl", other)

    assert _compare(a, b).returncode == 0


def test_changed_status_exits_one_and_reports_first_difference(tmp_path):
    changed = json.loads(json.dumps(EVENTS))
    changed[2]["data"]["status"] = "done"
    a = _write(tmp_path / "a.jsonl", EVENTS)
    b = _write(tmp_path / "b.jsonl", changed)

    result = _compare(a, b)

    assert result.returncode == 1
    assert "index 1" in result.stdout  # snapshot skipped: created=0, station:update=1
    assert "working" in result.stdout and "done" in result.stdout


def test_prepare_accept_project_builds_four_tasks_without_touching_source(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "agents").mkdir()
    (source / "agents" / "PM_agent.yaml").write_text("name: PM_agent\n", encoding="utf-8")
    original = (EXAMPLE_PROJECT / "tasks.yaml").read_bytes()
    (source / "tasks.yaml").write_bytes(original)
    target = tmp_path / "target"

    subprocess.run(
        [sys.executable, str(SCRIPTS / "prepare_accept_project.py"),
         "--source", str(source), "--project-dir", str(target), "--status", "pending"],
        check=True, capture_output=True,
    )

    tasks = yaml.safe_load((target / "tasks.yaml").read_text(encoding="utf-8"))["tasks"]
    assert [t["id"] for t in tasks] == ["task-001", "task-002", "task-003", "task-gate"]
    assert {t["status"] for t in tasks} == {"pending"}
    assert tasks[-1]["approval"] == "after_swe"
    assert tasks[-1]["priority"] == "low"
    assert (target / "agents" / "PM_agent.yaml").is_file()  # copied from source
    assert (source / "tasks.yaml").read_bytes() == original  # source untouched


def test_prepare_in_place_rewrites_status_and_is_repeatable(tmp_path):
    project = tmp_path / "p"
    project.mkdir()
    (project / "tasks.yaml").write_bytes((EXAMPLE_PROJECT / "tasks.yaml").read_bytes())

    for status in ("done", "pending"):
        subprocess.run(
            [sys.executable, str(SCRIPTS / "prepare_accept_project.py"),
             "--source", str(project), "--project-dir", str(project), "--status", status],
            check=True, capture_output=True,
        )
        tasks = yaml.safe_load((project / "tasks.yaml").read_text(encoding="utf-8"))["tasks"]
        assert len(tasks) == 4  # the gate task is replaced, never duplicated
        assert {t["status"] for t in tasks} == {status}
