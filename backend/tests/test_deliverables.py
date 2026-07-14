"""Unit tests for :mod:`core.deliverables`.

Covered behaviour:

    * path/naming — deliverables land under ``deliverables/{task_id}/`` with a
      ``{NN}_{agent}.md`` name,
    * sequence numbering — successive writes increment the zero-padded prefix,
    * SWE code extraction — fenced blocks become standalone files with the
      right extension (and ``.txt`` for unknown/absent languages),
    * listing / download resolution — including path-traversal guarding.
"""

from __future__ import annotations

from pathlib import Path

from core.deliverables import (
    extension_for_language,
    extract_code_blocks,
    list_deliverables,
    resolve_deliverable,
    write_deliverable,
)


# ---------------------------------------------------------------------------
# path / naming
# ---------------------------------------------------------------------------


def test_write_deliverable_path_and_naming(tmp_path):
    written = write_deliverable(tmp_path, "task-001", "PM_agent", "the plan")

    md = tmp_path / "deliverables" / "task-001" / "01_PM_agent.md"
    assert written == [md]
    assert md.is_file()
    assert md.read_text(encoding="utf-8") == "the plan"


def test_write_deliverable_creates_task_directory(tmp_path):
    write_deliverable(tmp_path, "task-xyz", "QA_agent", "report")
    assert (tmp_path / "deliverables" / "task-xyz").is_dir()


# ---------------------------------------------------------------------------
# sequence numbering
# ---------------------------------------------------------------------------


def test_sequence_number_increments_per_task(tmp_path):
    write_deliverable(tmp_path, "task-1", "PM_agent", "a")
    write_deliverable(tmp_path, "task-1", "SWE_agent", "b")
    write_deliverable(tmp_path, "task-1", "QA_agent", "c")

    names = sorted(p.name for p in (tmp_path / "deliverables" / "task-1").iterdir())
    assert names == ["01_PM_agent.md", "02_SWE_agent.md", "03_QA_agent.md"]


def test_sequence_numbering_is_independent_between_tasks(tmp_path):
    write_deliverable(tmp_path, "task-a", "PM_agent", "a")
    written = write_deliverable(tmp_path, "task-b", "PM_agent", "b")
    assert written[0].name == "01_PM_agent.md"


def test_sequence_counts_extracted_code_files(tmp_path):
    """Code files share their deliverable's prefix; the next write skips past it."""

    swe_output = "impl\n```python\nprint('hi')\n```"
    write_deliverable(tmp_path, "task-1", "SWE_agent", swe_output)
    written = write_deliverable(tmp_path, "task-1", "QA_agent", "report")
    assert written[0].name == "02_QA_agent.md"


# ---------------------------------------------------------------------------
# SWE code block extraction
# ---------------------------------------------------------------------------


def test_swe_code_blocks_saved_as_separate_files(tmp_path):
    content = (
        "Here is the implementation:\n"
        "```python\n"
        "def add(a, b):\n"
        "    return a + b\n"
        "```\n"
        "and a helper script:\n"
        "```bash\n"
        "echo hello\n"
        "```"
    )
    written = write_deliverable(tmp_path, "task-1", "SWE_agent", content)

    names = [p.name for p in written]
    assert names == [
        "01_SWE_agent.md",
        "01_SWE_agent_1.py",
        "01_SWE_agent_2.sh",
    ]
    py = tmp_path / "deliverables" / "task-1" / "01_SWE_agent_1.py"
    assert py.read_text(encoding="utf-8") == "def add(a, b):\n    return a + b"
    sh = tmp_path / "deliverables" / "task-1" / "01_SWE_agent_2.sh"
    assert sh.read_text(encoding="utf-8") == "echo hello"


def test_unknown_language_defaults_to_txt(tmp_path):
    content = "```\nplain code\n```"
    written = write_deliverable(tmp_path, "task-1", "SWE_agent", content)
    assert [p.name for p in written] == ["01_SWE_agent.md", "01_SWE_agent_1.txt"]


def test_non_swe_agents_do_not_extract_code(tmp_path):
    content = "```python\nprint('x')\n```"
    written = write_deliverable(tmp_path, "task-1", "PM_agent", content)
    assert [p.name for p in written] == ["01_PM_agent.md"]


def test_extract_code_blocks_returns_language_and_body():
    blocks = extract_code_blocks("```ts\nconst x = 1;\n```")
    assert blocks == [("ts", "const x = 1;")]


def test_extension_for_language_mapping():
    assert extension_for_language("python") == ".py"
    assert extension_for_language("JavaScript") == ".js"
    assert extension_for_language("ts") == ".ts"
    assert extension_for_language("") == ".txt"
    assert extension_for_language(None) == ".txt"
    assert extension_for_language("brainfuck") == ".txt"


# ---------------------------------------------------------------------------
# listing / download resolution
# ---------------------------------------------------------------------------


def test_list_deliverables_sorted_and_empty_default(tmp_path):
    assert list_deliverables(tmp_path, "missing") == []

    write_deliverable(tmp_path, "task-1", "PM_agent", "a")
    write_deliverable(tmp_path, "task-1", "SWE_agent", "```python\nx=1\n```")
    names = [p.name for p in list_deliverables(tmp_path, "task-1")]
    assert names == ["01_PM_agent.md", "02_SWE_agent.md", "02_SWE_agent_1.py"]


def test_resolve_deliverable_returns_existing_file(tmp_path):
    write_deliverable(tmp_path, "task-1", "PM_agent", "a")
    resolved = resolve_deliverable(tmp_path, "task-1", "01_PM_agent.md")
    assert resolved is not None and resolved.name == "01_PM_agent.md"


def test_resolve_deliverable_missing_returns_none(tmp_path):
    write_deliverable(tmp_path, "task-1", "PM_agent", "a")
    assert resolve_deliverable(tmp_path, "task-1", "nope.md") is None


def test_resolve_deliverable_blocks_path_traversal(tmp_path):
    write_deliverable(tmp_path, "task-1", "PM_agent", "a")
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    assert resolve_deliverable(tmp_path, "task-1", "../../secret.txt") is None
