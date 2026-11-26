"""Shared test setup.

``FORCE_MOCK`` exported in the developer's shell (as the README suggests for running the app)
must not leak into the tests: several of them check what the factory builds when a key is set,
which ``FORCE_MOCK`` overrides by design (plan D22). Each test starts without it; a test that
wants it sets it with ``monkeypatch.setenv`` (that runs after this fixture, so it still wins).
"""

import pytest


@pytest.fixture(autouse=True)
def _no_force_mock_from_the_shell(monkeypatch):
    monkeypatch.delenv("FORCE_MOCK", raising=False)
