"""Testcontainers fixtures: one Postgres container per session, a fresh database per test.

Everything under tests/integration is marked ``integration`` automatically. Without
Docker those tests are skipped with the reason printed; with ``CI=true`` they fail
instead, so they can never silently disappear from a CI run.

The container image is the one in docker-compose.yml (``images.POSTGRES_IMAGE``).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from sqlalchemy.engine import make_url

from tests.integration.images import POSTGRES_IMAGE

INTEGRATION_DIR = Path(__file__).resolve().parent


def pytest_collection_modifyitems(config, items):
    """Mark every test in this directory ``integration`` so ``-m integration`` selects it."""

    for item in items:
        if INTEGRATION_DIR in Path(str(item.fspath)).resolve().parents:
            item.add_marker(pytest.mark.integration)


def _docker_unavailable_reason() -> str | None:
    try:
        import docker

        docker.from_env().ping()
    except Exception as exc:  # any failure means containers cannot be started
        return f"Docker not available for Testcontainers tests: {exc}"
    return None


def _running_in_ci() -> bool:
    return os.environ.get("CI", "").strip().lower() in {"1", "true", "yes"}


def require_docker() -> None:
    """Skip locally without Docker, but fail in CI so integration tests never silently vanish."""

    reason = _docker_unavailable_reason()
    if reason is None:
        return
    if _running_in_ci():
        pytest.fail(f"{reason} (CI=true: integration tests must run)", pytrace=False)
    pytest.skip(reason)


@pytest.fixture(scope="session")
def postgres_admin_url() -> Iterator[str]:
    """Plain ``postgresql://`` URL of the container's superuser and its default database."""

    require_docker()

    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer(POSTGRES_IMAGE, driver=None) as container:
        yield container.get_connection_url()


def create_database(admin_url: str) -> tuple[str, str]:
    """Create an empty database; return ``(plain URL, database name)``."""

    name = f"t_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    url = make_url(admin_url).set(database=name).render_as_string(hide_password=False)
    return url, name


def drop_database(admin_url: str, name: str) -> None:
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
def empty_db_url(postgres_admin_url: str) -> Iterator[str]:
    """Plain ``postgresql://`` URL of a brand-new empty database (dropped after the test)."""

    url, name = create_database(postgres_admin_url)
    try:
        yield url
    finally:
        drop_database(postgres_admin_url, name)


@pytest.fixture
def empty_db_sqlalchemy_url(empty_db_url: str) -> str:
    """The same database as a SQLAlchemy URL for the psycopg 3 driver (what DATABASE_URL holds)."""

    return make_url(empty_db_url).set(drivername="postgresql+psycopg").render_as_string(
        hide_password=False
    )
