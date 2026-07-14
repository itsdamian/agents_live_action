"""List and read the per-task agent log files for the task-detail API.

Every agent run writes one log file via :class:`~agents.base_agent.BaseAgent`
to ``projects/{name}/logs/{timestamp}_{agent_name}_{task_id}.log`` (Requirements
9.1, 9.2). This module reads those files back so the UI can show a task's
execution history and log contents without anyone having to open the
``projects/`` folder (task 15).

The ``projects/`` directory is system-managed; callers always pass an explicit
``project_path`` and paths are never hardcoded (steering ``structure.md``).
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from agents.base_agent import LOGS_DIRNAME

logger = logging.getLogger(__name__)

# Must match the timestamp format base_agent uses in log filenames
# (``%Y%m%d_%H%M%S`` -> ``20260613_120000``). Kept in sync deliberately; the
# format is part of the on-disk log-file contract.
_LOG_TIMESTAMP_FMT = "%Y%m%d_%H%M%S"

# Log filenames end with ``_{task_id}.log``.
_LOG_SUFFIX = ".log"


def _logs_dir(project_path: str | Path) -> Path:
    return Path(project_path) / LOGS_DIRNAME


def _matches_task(name: str, task_id: str) -> bool:
    """Whether a log filename belongs to ``task_id``.

    Filenames are ``{timestamp}_{agent_name}_{task_id}.log``; matching on the
    full ``_{task_id}.log`` suffix avoids one task id being treated as a prefix
    of another (e.g. ``task-1`` vs ``task-10``).
    """

    return name.endswith(f"_{task_id}{_LOG_SUFFIX}")


def _parse_log_name(name: str, task_id: str) -> tuple[str | None, str]:
    """Return ``(iso_timestamp, agent_name)`` parsed from a log filename.

    ``iso_timestamp`` is ``None`` when the leading token pair is not a valid
    timestamp, in which case the agent portion still best-effort parses.
    """

    stem = name[: -len(_LOG_SUFFIX)]  # drop ".log"
    core = stem[: -(len(task_id) + 1)]  # drop "_{task_id}"
    parts = core.split("_")
    timestamp_raw = "_".join(parts[:2]) if len(parts) >= 2 else ""
    agent = "_".join(parts[2:]) if len(parts) > 2 else ""
    iso: str | None = None
    try:
        iso = datetime.strptime(timestamp_raw, _LOG_TIMESTAMP_FMT).isoformat()
    except ValueError:
        iso = None
    return iso, agent


def _read_header(text: str) -> dict[str, str]:
    """Parse the ``key: value`` header block at the top of a log file.

    The header is the run metadata block base_agent writes before the first
    blank line (agent, role, status, execution_time_seconds, logged_at, ...).
    """

    header: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            break
        key, sep, value = line.partition(":")
        if sep:
            header[key.strip()] = value.strip()
    return header


def list_task_logs(project_path: str | Path, task_id: str) -> list[dict]:
    """Return metadata + contents for every log file belonging to a task.

    Logs are sorted by filename, which — because of the leading timestamp — is
    chronological order. Each entry carries::

        {
            "name": "20260613_120000_PM_agent_task-001.log",
            "agent": "PM_agent",
            "timestamp": "2026-06-13T12:00:00",   # or None if unparseable
            "status": "SUCCESS",                  # from the log header
            "execution_time": 1.2345,             # seconds, or None
            "size": 512,
            "content": "agent: PM_agent\\n...",
        }

    Returns an empty list when the project has no ``logs/`` directory or no log
    matches ``task_id``.
    """

    logs_dir = _logs_dir(project_path)
    if not logs_dir.is_dir():
        return []

    entries: list[dict] = []
    for path in sorted(logs_dir.iterdir(), key=lambda p: p.name):
        if not path.is_file() or not _matches_task(path.name, task_id):
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:  # pragma: no cover - defensive I/O guard
            logger.warning("Failed to read log %s: %s", path, exc)
            continue
        header = _read_header(content)
        timestamp, agent = _parse_log_name(path.name, task_id)
        execution_time: float | None = None
        raw_time = header.get("execution_time_seconds")
        if raw_time is not None:
            try:
                execution_time = float(raw_time)
            except ValueError:
                execution_time = None
        entries.append(
            {
                "name": path.name,
                "agent": header.get("agent") or agent,
                "timestamp": header.get("logged_at") or timestamp,
                "status": header.get("status"),
                "execution_time": execution_time,
                "size": path.stat().st_size,
                "content": content,
            }
        )
    return entries


def resolve_task_log(
    project_path: str | Path, task_id: str, filename: str
) -> Path | None:
    """Resolve a single log file for a task, guarding path traversal.

    Returns the file path when ``filename`` names an existing log directly
    inside the project's ``logs/`` directory *and* belongs to ``task_id``;
    otherwise ``None`` (including any ``..`` / absolute-path escape attempt).
    """

    logs_dir = _logs_dir(project_path).resolve()
    candidate = (logs_dir / filename).resolve()
    if candidate.parent != logs_dir or not candidate.is_file():
        return None
    if not _matches_task(candidate.name, task_id):
        return None
    return candidate
