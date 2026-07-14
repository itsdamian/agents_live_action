"""Persist agent output as downloadable deliverable files.

Every time a pipeline station (PM / SWE / QA) finishes a task successfully its
textual output is written to disk so users can retrieve the concrete result of
a run — the value the platform produces only materialises once the deliverables
are obtainable.

Layout (relative to a project root)::

    deliverables/
        {task_id}/
            01_PM_agent.md
            02_SWE_agent.md
            02_SWE_agent_1.py     # code blocks extracted from SWE output
            03_QA_agent.md

* ``{NN}`` is a zero-padded, monotonically increasing sequence number based on
  the order deliverables are written for a given ``task_id`` (01, 02, 03, ...).
  Rework passes therefore accumulate rather than overwrite, keeping the history.
* For **SWE** output the fenced code blocks (```` ```lang ... ``` ````) are
  additionally saved as standalone files with a language-appropriate extension
  (defaulting to ``.txt`` when the language is unknown or absent), so the code
  can be downloaded and used directly.

The ``projects/`` directory is managed by the system; callers always pass an
explicit ``project_path`` and paths are never hardcoded (steering
``structure.md``).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# Subdirectory (relative to a project root) holding all deliverables.
DELIVERABLES_DIRNAME = "deliverables"

# Fenced code block: ```lang\n...\n``` — ``lang`` is optional. DOTALL so the
# body can span multiple lines; non-greedy so consecutive blocks don't merge.
_CODE_BLOCK_RE = re.compile(r"```([\w+#.-]*)\r?\n(.*?)```", re.DOTALL)

# Matches the leading ``NN_`` sequence prefix on a deliverable filename.
_SEQ_PREFIX_RE = re.compile(r"^(\d+)_")

# Common language -> file extension map for extracted SWE code blocks. The
# language tag is lower-cased before lookup; anything unknown falls back to
# ``.txt`` via :data:`_DEFAULT_CODE_EXTENSION`.
_LANGUAGE_EXTENSIONS: dict[str, str] = {
    "python": ".py",
    "py": ".py",
    "javascript": ".js",
    "js": ".js",
    "jsx": ".jsx",
    "typescript": ".ts",
    "ts": ".ts",
    "tsx": ".tsx",
    "java": ".java",
    "kotlin": ".kt",
    "go": ".go",
    "golang": ".go",
    "rust": ".rs",
    "rs": ".rs",
    "c": ".c",
    "cpp": ".cpp",
    "c++": ".cpp",
    "csharp": ".cs",
    "cs": ".cs",
    "ruby": ".rb",
    "rb": ".rb",
    "php": ".php",
    "swift": ".swift",
    "scala": ".scala",
    "sh": ".sh",
    "bash": ".sh",
    "shell": ".sh",
    "zsh": ".sh",
    "sql": ".sql",
    "html": ".html",
    "css": ".css",
    "scss": ".scss",
    "json": ".json",
    "yaml": ".yaml",
    "yml": ".yaml",
    "xml": ".xml",
    "toml": ".toml",
    "markdown": ".md",
    "md": ".md",
    "dockerfile": ".dockerfile",
}

_DEFAULT_CODE_EXTENSION = ".txt"


def extension_for_language(language: str | None) -> str:
    """Return the file extension for a fenced-block ``language`` tag.

    Unknown or empty languages map to :data:`_DEFAULT_CODE_EXTENSION` (``.txt``).
    """

    if not language:
        return _DEFAULT_CODE_EXTENSION
    return _LANGUAGE_EXTENSIONS.get(language.strip().lower(), _DEFAULT_CODE_EXTENSION)


def extract_code_blocks(content: str) -> list[tuple[str, str]]:
    """Return the fenced code blocks in ``content`` as ``(language, code)``.

    ``language`` is the (possibly empty) tag following the opening fence; the
    returned code has a single trailing newline trimmed so files don't gain a
    spurious blank line, but internal formatting is preserved.
    """

    blocks: list[tuple[str, str]] = []
    for match in _CODE_BLOCK_RE.finditer(content or ""):
        language = match.group(1).strip()
        code = match.group(2)
        # Drop exactly one trailing newline (the one before the closing fence).
        if code.endswith("\r\n"):
            code = code[:-2]
        elif code.endswith("\n"):
            code = code[:-1]
        blocks.append((language, code))
    return blocks


def _is_swe_agent(agent_name: str) -> bool:
    """Whether ``agent_name`` denotes the SWE station (code-producing agent)."""

    return "swe" in (agent_name or "").lower()


def _deliverables_dir(project_path: str | Path, task_id: str) -> Path:
    """Return the deliverables directory for ``task_id`` under a project."""

    return Path(project_path) / DELIVERABLES_DIRNAME / task_id


def _next_sequence(task_dir: Path) -> int:
    """Next 1-based sequence number for a task's deliverables directory.

    Derived from the highest ``NN_`` prefix already present (across markdown and
    extracted code files, which share their parent deliverable's prefix), so
    numbering keeps increasing across successive writes and process restarts.
    """

    highest = 0
    if task_dir.is_dir():
        for entry in task_dir.iterdir():
            match = _SEQ_PREFIX_RE.match(entry.name)
            if match:
                highest = max(highest, int(match.group(1)))
    return highest + 1


def write_deliverable(
    project_path: str | Path,
    task_id: str,
    agent_name: str,
    content: str,
) -> list[Path]:
    """Persist one agent's output as a deliverable and return the files written.

    Writes ``{NN}_{agent_name}.md`` (holding the full output) into
    ``{project_path}/deliverables/{task_id}/``. For SWE output every fenced code
    block is additionally saved as ``{NN}_{agent_name}_{index}{ext}`` alongside
    the markdown file.

    Args:
        project_path: Project root directory (never hardcoded by callers).
        task_id: The task the output belongs to.
        agent_name: The producing agent (e.g. ``"PM_agent"``, ``"SWE_agent"``).
        content: The agent's textual output.

    Returns:
        The paths written, markdown file first, then any extracted code files.
    """

    task_dir = _deliverables_dir(project_path, task_id)
    task_dir.mkdir(parents=True, exist_ok=True)

    sequence = _next_sequence(task_dir)
    prefix = f"{sequence:02d}_{agent_name}"

    written: list[Path] = []
    md_path = task_dir / f"{prefix}.md"
    md_path.write_text(content or "", encoding="utf-8")
    written.append(md_path)

    if _is_swe_agent(agent_name):
        for index, (language, code) in enumerate(extract_code_blocks(content), start=1):
            code_path = task_dir / f"{prefix}_{index}{extension_for_language(language)}"
            code_path.write_text(code, encoding="utf-8")
            written.append(code_path)

    logger.info(
        "Wrote %d deliverable file(s) for task %s (%s) under %s",
        len(written),
        task_id,
        agent_name,
        task_dir,
    )
    return written


def list_deliverables(project_path: str | Path, task_id: str) -> list[Path]:
    """Return the deliverable files for a task, sorted by name (sequence order).

    Returns an empty list when the task has no deliverables directory yet.
    """

    task_dir = _deliverables_dir(project_path, task_id)
    if not task_dir.is_dir():
        return []
    return sorted((p for p in task_dir.iterdir() if p.is_file()), key=lambda p: p.name)


# Deliverable markdown filename -> the task field its content populates. The
# leading ``NN_`` sequence prefix is stripped before matching, so
# ``02_SWE_agent.md`` maps via ``SWE_agent`` to ``swe_output``.
_AGENT_OUTPUT_FIELDS: dict[str, str] = {
    "PM_agent": "pm_output",
    "SWE_agent": "swe_output",
    "QA_agent": "qa_output",
}


def read_station_outputs(project_path: str | Path, task_id: str) -> dict[str, str]:
    """Reconstruct a task's per-station outputs from its deliverable files.

    The pipeline persists each station's textual output as
    ``deliverables/{task_id}/{NN}_{agent}.md`` (see :func:`write_deliverable`);
    the ``Task`` object that carried ``pm_output`` / ``swe_output`` /
    ``qa_output`` in memory is not itself persisted, so the API rebuilds those
    fields from disk here.

    When a station ran more than once (rework), the highest-sequence markdown
    file for that agent wins, so the returned value is the latest output.

    Returns a mapping with any of ``pm_output`` / ``swe_output`` /
    ``qa_output`` that have a corresponding deliverable; stations that never
    produced output are omitted.
    """

    task_dir = _deliverables_dir(project_path, task_id)
    if not task_dir.is_dir():
        return {}

    # field -> (sequence, path); keep the highest sequence seen per field.
    latest: dict[str, tuple[int, Path]] = {}
    for path in task_dir.iterdir():
        if not path.is_file() or path.suffix != ".md":
            continue
        match = _SEQ_PREFIX_RE.match(path.name)
        if not match:
            continue
        sequence = int(match.group(1))
        agent = path.stem[match.end() :]  # strip the ``NN_`` prefix
        field = _AGENT_OUTPUT_FIELDS.get(agent)
        if field is None:
            continue
        if field not in latest or sequence > latest[field][0]:
            latest[field] = (sequence, path)

    outputs: dict[str, str] = {}
    for field, (_, path) in latest.items():
        outputs[field] = path.read_text(encoding="utf-8")
    return outputs


def resolve_deliverable(
    project_path: str | Path, task_id: str, filename: str
) -> Path | None:
    """Resolve a single deliverable file for download, guarding traversal.

    Returns the file path when ``filename`` names an existing file directly
    inside the task's deliverables directory, or ``None`` otherwise (including
    any attempt to escape the directory with ``..`` or absolute paths).
    """

    task_dir = _deliverables_dir(project_path, task_id).resolve()
    candidate = (task_dir / filename).resolve()
    if candidate.parent != task_dir or not candidate.is_file():
        return None
    return candidate
