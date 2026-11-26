"""Read side of the sticky-note history (plan D20, §7 ``GET /history``, §7.1a).

:class:`~core.task_history.HistoryRecorder` writes ``task_runs`` / ``task_run_steps``; this module
turns them, together with the tasks themselves, into what the note wall and the task detail show.

* A task's ``status`` is the stored one, overridden by what the orchestrator is doing right now
  (:class:`LiveState`). ``stale`` marks a task stored as ``in_progress`` that nobody runs (in DB
  mode only briefly, before startup recovery; in file mode until the task is run again).
* DB mode (a ``RunRepository``): an item carries its task's newest run. File mode (no
  repository): only tasks.yaml / TODO.md, the log file names (for ``path``) and the deliverable
  files exist, so the run fields are ``null`` and ``runs`` is 0.

Everything here is read-only and works for both engines.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from core.deliverables import list_deliverables
from core.stores import RunRecord, RunRepository, priority_rank
from core.task_logs import list_task_logs
from models.task import Status, Task

# Every task belongs to the default line until tasks carry a pipeline (multi-pipeline, phase 4).
DEFAULT_PIPELINE_ID = "main"

# The five note-wall states, in the order the counts are reported.
HISTORY_STATUSES: tuple[str, ...] = tuple(s.value for s in Status)

DEFAULT_LIMIT = 50
MAX_LIMIT = 200

_FINISHED = frozenset({Status.DONE.value, Status.FAILED.value})
_PATH_STEP_STATUSES = frozenset({"done", "error"})

# Log file agent name -> station id, for the file-mode ``path`` (built-in stations only).
_LOG_AGENT_STATIONS = {"PM_agent": "pm", "SWE_agent": "swe", "QA_agent": "qa"}


@dataclass(frozen=True)
class LiveState:
    """What the project's orchestrator is doing now: tasks it runs and tasks at a gate."""

    running: frozenset[str] = field(default_factory=frozenset)
    awaiting: frozenset[str] = field(default_factory=frozenset)


def effective_status(task: Task, live: LiveState) -> tuple[str, bool]:
    """``(status, stale)``: the stored status overridden by the orchestrator's live state."""

    stored = Status(task.status).value
    if task.id in live.awaiting:
        return Status.AWAITING_APPROVAL.value, False
    if task.id in live.running and stored in (Status.PENDING.value, Status.IN_PROGRESS.value):
        return Status.IN_PROGRESS.value, False
    return stored, stored == Status.IN_PROGRESS.value and task.id not in live.running


def count_statuses(tasks: Sequence[Task], live: LiveState) -> dict[str, int]:
    """Tasks per note-wall state (every state present), as snapshot ``history_counts``."""

    counts = dict.fromkeys(HISTORY_STATUSES, 0)
    for task in tasks:
        counts[effective_status(task, live)[0]] += 1
    return counts


def parse_status_filter(raw: str | None) -> list[str] | None:
    """``"done,failed"`` -> ``["done", "failed"]``; ``None`` for no filter; ``ValueError`` if unknown."""

    if raw is None or not raw.strip():
        return None
    wanted = [part.strip() for part in raw.split(",") if part.strip()]
    unknown = [s for s in wanted if s not in HISTORY_STATUSES]
    if unknown:
        raise ValueError(f"不明的狀態：{', '.join(unknown)}（可用：{', '.join(HISTORY_STATUSES)}）")
    return wanted


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _sort_key(row: tuple[Task, str, bool, RunRecord | None]):
    """Open tasks first (priority, then oldest); then finished ones, newest ``finished_at`` first."""

    task, status, _, run = row
    if status in _FINISHED:
        finished = run.finished_at if run is not None and run.finished_at is not None else task.updated_at
        return (1, 0, -finished.timestamp(), task.id)
    return (0, priority_rank(task.priority), task.created_at.timestamp(), task.id)


async def build_history(
    tasks: Sequence[Task],
    *,
    project_id: str,
    run_repo: RunRepository | None,
    project_path: str | Path,
    live: LiveState,
    current_seats: list[dict],
    statuses: Sequence[str] | None = None,
    pipeline: str | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> dict:
    """``{"items": [HistoryItem], "total", "counts"}`` for ``GET /history``.

    ``counts`` cover the whole wall of the chosen pipeline (the status filter does not narrow
    them: they label the state tabs); ``total`` is the number of items the filters match.
    ``run_repo`` is ``None`` in file mode.
    """

    latest: dict[str, RunRecord] = {}
    run_counts: dict[str, int] = {}
    if run_repo is not None:
        for run in await run_repo.list_project_runs(project_id):  # newest run_no first per task
            latest.setdefault(run.task_id, run)
            run_counts[run.task_id] = run_counts.get(run.task_id, 0) + 1

    rows = []
    for task in tasks:
        run = latest.get(task.id)
        if pipeline is not None and ((run.pipeline_id if run else None) or DEFAULT_PIPELINE_ID) != pipeline:
            continue
        status, stale = effective_status(task, live)
        rows.append((task, status, stale, run))

    counts = dict.fromkeys(HISTORY_STATUSES, 0)
    for _, status, _, _ in rows:
        counts[status] += 1
    if statuses is not None:
        rows = [row for row in rows if row[1] in statuses]
    rows.sort(key=_sort_key)

    items = []
    for task, status, stale, run in rows[offset : offset + limit]:
        items.append(
            await _item(task, status, stale, run, run_counts.get(task.id, 0), run_repo=run_repo,
                        project_path=Path(project_path), current_seats=current_seats)
        )
    return {"items": items, "total": len(rows), "counts": counts}


async def _item(task, status, stale, run, runs, *, run_repo, project_path, current_seats) -> dict:
    seats = [dict(s) for s in (run.seats if run is not None else current_seats)]
    if run_repo is None:
        path = await asyncio.to_thread(_path_from_logs, project_path, task.id)
    elif run is not None:
        path = [s.station for s in await run_repo.list_steps(run.id)
                if s.kind == "station" and s.status in _PATH_STEP_STATUSES]
    else:
        path = []
    seated = {s["id"] for s in seats}
    deliverable_count = len(await asyncio.to_thread(list_deliverables, project_path, task.id))
    # A finished run has the final count; while a run is open the task's own count is current.
    rework_count = run.rework_count if run is not None and run.outcome is not None else task.rework_count
    return {
        "task_id": task.id,
        "title": task.title,
        "priority": task.priority.value,
        "status": status,
        "pipeline_id": run.pipeline_id if run is not None else None,
        "outcome": run.outcome if run is not None else None,
        "reason": run.reason if run is not None else None,
        "rework_count": rework_count,
        "path": [station for station in path if station in seated],
        "cost_usd": run.cost_usd if run is not None else None,
        "started_at": _iso(run.started_at) if run is not None else None,
        "finished_at": _iso(run.finished_at) if run is not None else None,
        "deliverable_count": deliverable_count,
        "runs": runs,
        "stale": stale,
        "seats": seats,
    }


def _path_from_logs(project_path: Path, task_id: str) -> list[str]:
    """File mode: the stations a task passed, from its agent log files (oldest first).

    File names only have whole seconds, so several stations of one task often share a stamp;
    the ``logged_at`` header (microseconds) orders them. The sort is stable: file-name order
    stays for logs without the header.
    """

    entries = sorted(list_task_logs(project_path, task_id), key=lambda e: e["timestamp"] or "")
    return [_LOG_AGENT_STATIONS[e["agent"]] for e in entries if e["agent"] in _LOG_AGENT_STATIONS]


async def task_runs_view(run_repo: RunRepository | None, project_id: str, task_id: str) -> list[dict]:
    """The task detail's ``runs`` (plan §7.1a): newest run first, each with its steps; ``[]`` in file mode."""

    if run_repo is None:
        return []
    view = []
    for run in await run_repo.list_runs(project_id, task_id):
        steps = await run_repo.list_steps(run.id)
        view.append(
            {
                "run_no": run.run_no,
                "pipeline_id": run.pipeline_id,
                "outcome": run.outcome,
                "reason": run.reason,
                "rework_count": run.rework_count,
                "started_at": _iso(run.started_at),
                "finished_at": _iso(run.finished_at),
                "cost_usd": run.cost_usd,
                "input_tokens": run.input_tokens,
                "output_tokens": run.output_tokens,
                "imported": run.imported,
                "seats": [dict(s) for s in run.seats],
                "steps": [
                    {"seq": s.seq, "kind": s.kind, "station": s.station, "status": s.status, "note": s.note,
                     "at": _iso(s.at)}
                    for s in steps
                ],
            }
        )
    return view
