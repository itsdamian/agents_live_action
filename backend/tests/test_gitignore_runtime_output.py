"""Files the running factory writes into ``projects/`` must stay out of version control."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _rules() -> list[str]:
    return [line.strip() for line in (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()]


def test_deliverables_written_by_a_run_are_ignored():
    # A Mock run under compose writes projects/<p>/deliverables/ on the host (bind mount).
    assert "projects/*/deliverables/" in _rules()


def test_agent_logs_stay_ignored():
    rules = _rules()
    assert "projects/*/logs/" in rules and "projects/logs/" in rules
