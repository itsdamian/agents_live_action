"""Task logs (and the task detail's ``status_history``) keep the order they were written in.

Log names start with a one-second timestamp (``YYYYMMDD_HHMMSS_<agent>_<task>.log``). A mock
run finishes PM, SWE and QA within the same second, and sorting by name alone then put them in
agent-name order (PM, QA, SWE). Files of the same second are now ordered by when they were
written (their mtime), the name only breaking a full tie.
"""

from __future__ import annotations

import os
from pathlib import Path

from core.task_logs import list_task_logs

TASK = "task-001"
STAMP = "20261123_120000"
BASE_NS = 1_795_000_000_000_000_000


def _write_log(logs: Path, name: str, offset_ns: int) -> None:
    path = logs / name
    path.write_text(f"agent: {name}\nstatus: SUCCESS\n", encoding="utf-8")
    os.utime(path, ns=(BASE_NS + offset_ns, BASE_NS + offset_ns))


def test_logs_of_the_same_second_follow_the_order_they_were_written_in(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    # The real pipeline order, all within one second.
    _write_log(logs, f"{STAMP}_PM_agent_{TASK}.log", 1_000)
    _write_log(logs, f"{STAMP}_SWE_agent_{TASK}.log", 2_000)
    _write_log(logs, f"{STAMP}_QA_agent_{TASK}.log", 3_000)

    names = [e["name"] for e in list_task_logs(tmp_path, TASK)]

    assert names == [f"{STAMP}_PM_agent_{TASK}.log", f"{STAMP}_SWE_agent_{TASK}.log", f"{STAMP}_QA_agent_{TASK}.log"]


def test_the_timestamp_in_the_name_still_comes_first(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    # An older file touched later (e.g. copied) still sorts by the second in its name.
    _write_log(logs, f"20261123_120001_SWE_agent_{TASK}.log", 1_000)
    _write_log(logs, f"20261123_120000_PM_agent_{TASK}.log", 9_000)

    names = [e["name"] for e in list_task_logs(tmp_path, TASK)]

    assert names == [f"20261123_120000_PM_agent_{TASK}.log", f"20261123_120001_SWE_agent_{TASK}.log"]


def test_a_full_tie_falls_back_to_the_name(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    _write_log(logs, f"{STAMP}_SWE_agent_{TASK}.log", 1_000)
    _write_log(logs, f"{STAMP}_PM_agent_{TASK}.log", 1_000)

    names = [e["name"] for e in list_task_logs(tmp_path, TASK)]

    assert names == [f"{STAMP}_PM_agent_{TASK}.log", f"{STAMP}_SWE_agent_{TASK}.log"]
