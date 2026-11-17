"""docker-compose.yml, compose.expose-backend.yml and the container entrypoint (Task 6b, D10, AC7).

These read the files as YAML/text, so they run without Docker. The rendered result
(``docker compose config``) was checked by hand and again by QA with Docker.
"""

from __future__ import annotations

import re
import stat
from pathlib import Path

import yaml

from tests.integration.images import POSTGRES_IMAGE

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"


def _compose(name="docker-compose.yml") -> dict:
    return yaml.safe_load((REPO / name).read_text(encoding="utf-8"))


def _env(service: dict) -> dict[str, str]:
    return dict(item.split("=", 1) for item in service["environment"])


def test_postgres_image_is_pinned_by_tag_and_digest_and_matches_the_test_constant():
    image = _compose()["services"]["postgres"]["image"]

    assert image == POSTGRES_IMAGE
    assert re.fullmatch(r"postgres:17\.\d+-bookworm@sha256:[0-9a-f]{64}", image)


def test_postgres_service_is_loopback_only_healthchecked_and_persistent():
    postgres = _compose()["services"]["postgres"]

    assert postgres["ports"] == ["${HUD_BIND_ADDR:-127.0.0.1}:${POSTGRES_HOST_PORT:-5433}:5432"]
    assert "pg_isready" in " ".join(postgres["healthcheck"]["test"])
    assert postgres["volumes"] == ["pgdata:/var/lib/postgresql/data"]
    assert "pgdata" in _compose()["volumes"]
    assert postgres["environment"]["POSTGRES_USER"] == "${POSTGRES_USER:-factory}"  # overridable


def test_backend_publishes_no_ports_and_waits_for_a_healthy_database():
    backend = _compose()["services"]["backend"]

    assert "ports" not in backend
    assert backend["depends_on"] == {"postgres": {"condition": "service_healthy"}}
    assert "urlopen('http://127.0.0.1:8000/api/health'" in " ".join(backend["healthcheck"]["test"])


def test_backend_environment():
    env = _env(_compose()["services"]["backend"])

    assert env["TASK_STORE"] == "db"
    assert env["PIPELINE_CHECKPOINTER"] == "${PIPELINE_CHECKPOINTER:-postgres}"
    assert env["PIPELINE_ENGINE"] == "${PIPELINE_ENGINE:-langgraph}"
    assert env["FORCE_MOCK"] == "${FORCE_MOCK:-false}"
    assert env["PROJECTS_DIR"] == "/app/projects"
    # The URL uses the psycopg 3 driver (plan D6) and the service name as host.
    assert env["DATABASE_URL"].startswith("postgresql+psycopg://")
    assert "@postgres:5432/" in env["DATABASE_URL"]


def test_frontend_port_is_a_variable_bound_to_loopback_and_waits_for_the_backend():
    frontend = _compose()["services"]["frontend"]

    assert frontend["ports"] == ["${HUD_BIND_ADDR:-127.0.0.1}:${FRONTEND_HOST_PORT:-8080}:80"]
    assert frontend["depends_on"] == {"backend": {"condition": "service_healthy"}}


def test_expose_backend_file_only_adds_the_backend_port():
    override = _compose("compose.expose-backend.yml")

    assert override == {
        "services": {"backend": {"ports": ["${HUD_BIND_ADDR:-127.0.0.1}:${BACKEND_HOST_PORT:-8001}:8000"]}}
    }
    # The auto-loaded name would open the port by default, so it must not exist.
    assert not (REPO / "docker-compose.override.yml").exists()
    assert not (REPO / "docker-compose.override.yaml").exists()


def test_entrypoint_migrates_in_db_mode_then_execs_uvicorn():
    script = BACKEND / "scripts" / "start.sh"
    text = script.read_text(encoding="utf-8")

    assert script.stat().st_mode & stat.S_IXUSR
    assert 'if [ "${TASK_STORE:-file}" = "db" ]' in text
    assert "alembic upgrade head" in text
    assert text.index("alembic upgrade head") < text.index("exec uvicorn main:app --host 0.0.0.0 --port 8000")
    assert text.startswith("#!/bin/sh") and "set -e" in text
    assert 'CMD ["sh", "scripts/start.sh"]' in (BACKEND / "Dockerfile").read_text(encoding="utf-8")


def test_dockerignore_excludes_caches_at_every_depth_but_not_tests():
    lines = (BACKEND / ".dockerignore").read_text(encoding="utf-8").splitlines()

    assert {"**/__pycache__/", "**/*.pyc", ".venv/", ".pytest_cache/"} <= set(lines)
    assert not any(line.strip().rstrip("/") == "tests" for line in lines)


def test_acceptance_script_defaults_to_the_frontend_port():
    source = (BACKEND / "scripts" / "acceptance_run.py").read_text(encoding="utf-8")

    assert '"--base-url", default="http://localhost:8080"' in source
