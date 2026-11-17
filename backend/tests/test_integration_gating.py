"""How integration tests behave without Docker, and that they are marked (Task 6c).

These tests need no Docker themselves, so the gating logic is always checked.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests.integration import conftest as integration_conftest

BACKEND = Path(__file__).resolve().parent.parent


@pytest.fixture
def no_docker(monkeypatch):
    monkeypatch.setattr(
        integration_conftest, "_docker_unavailable_reason", lambda: "Docker not available: no daemon"
    )


def test_require_docker_is_silent_when_docker_answers(monkeypatch):
    monkeypatch.setattr(integration_conftest, "_docker_unavailable_reason", lambda: None)
    monkeypatch.setenv("CI", "true")

    integration_conftest.require_docker()  # neither skips nor fails


def test_without_docker_the_test_is_skipped_with_the_reason(no_docker, monkeypatch):
    monkeypatch.delenv("CI", raising=False)

    with pytest.raises(pytest.skip.Exception) as excinfo:
        integration_conftest.require_docker()

    assert "no daemon" in str(excinfo.value)


@pytest.mark.parametrize("ci", ["", "0", "false", "no"])
def test_a_falsy_ci_value_still_skips(no_docker, monkeypatch, ci):
    monkeypatch.setenv("CI", ci)

    with pytest.raises(pytest.skip.Exception):
        integration_conftest.require_docker()


@pytest.mark.parametrize("ci", ["true", "1", "yes", "TRUE", " true "])
def test_in_ci_missing_docker_is_a_failure_not_a_skip(no_docker, monkeypatch, ci):
    monkeypatch.setenv("CI", ci)

    with pytest.raises(pytest.fail.Exception) as excinfo:
        integration_conftest.require_docker()

    assert "no daemon" in str(excinfo.value) and "CI=true" in str(excinfo.value)


def test_tests_under_integration_are_selected_by_the_integration_marker():
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
         "-m", "integration", "tests/integration"],
        cwd=BACKEND, capture_output=True, text=True,
    )

    collected = [line for line in out.stdout.splitlines() if "::" in line]
    assert len(collected) >= 4
    assert all(line.startswith("tests/integration/") for line in collected)
    assert "PytestUnknownMarkWarning" not in out.stdout + out.stderr  # marker is registered


def test_the_marker_is_registered_in_pytest_ini():
    text = (BACKEND / "pytest.ini").read_text(encoding="utf-8")

    assert "integration:" in text
