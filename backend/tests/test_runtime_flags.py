"""TASK_STORE / PIPELINE_CHECKPOINTER flags and the supported combinations (Task 6a, D23, AC7c)."""

from __future__ import annotations

import itertools

import pytest
from fastapi.testclient import TestClient

import config
from main import create_app

SECRET = "TopSecretPw-9f3k"
GOOD_URL = f"postgresql+psycopg://factory:{SECRET}@db.internal:5432/factory"
ENGINES, STORES, CHECKPOINTERS = ("legacy", "langgraph"), ("file", "db"), ("memory", "postgres")

# plan D23, one row per combination: (engine, store, checkpointer) -> supported?
D23 = {
    ("legacy", "file", "memory"): True,
    ("legacy", "db", "memory"): True,
    ("legacy", "file", "postgres"): False,
    ("legacy", "db", "postgres"): False,
    ("langgraph", "file", "memory"): True,
    ("langgraph", "file", "postgres"): False,
    ("langgraph", "db", "memory"): True,
    ("langgraph", "db", "postgres"): True,
}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("PIPELINE_ENGINE", "TASK_STORE", "PIPELINE_CHECKPOINTER", "DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)


def _set(monkeypatch, engine, store, checkpointer, with_url=True):
    monkeypatch.setenv("PIPELINE_ENGINE", engine)
    monkeypatch.setenv("TASK_STORE", store)
    monkeypatch.setenv("PIPELINE_CHECKPOINTER", checkpointer)
    if with_url:
        monkeypatch.setenv("DATABASE_URL", GOOD_URL)


# -- defaults and parsing --------------------------------------------------------------------------


def test_defaults_are_file_and_memory():
    assert config.get_task_store() == "file"
    assert config.get_pipeline_checkpointer() == "memory"
    assert config.validate_runtime_flags() == {
        "engine": "langgraph", "task_store": "file", "checkpointer": "memory"}


@pytest.mark.parametrize("value, expected", [("db", "db"), (" DB ", "db"), ("File", "file"), ("", "file"), ("  ", "file")])
def test_task_store_parsing(monkeypatch, value, expected):
    monkeypatch.setenv("TASK_STORE", value)

    assert config.get_task_store() == expected


@pytest.mark.parametrize("value, expected", [("postgres", "postgres"), (" Postgres ", "postgres"), ("MEMORY", "memory"), ("", "memory")])
def test_checkpointer_parsing(monkeypatch, value, expected):
    monkeypatch.setenv("PIPELINE_CHECKPOINTER", value)

    assert config.get_pipeline_checkpointer() == expected


@pytest.mark.parametrize("name, getter, listed", [
    ("TASK_STORE", config.get_task_store, "file, db"),
    ("PIPELINE_CHECKPOINTER", config.get_pipeline_checkpointer, "memory, postgres"),
])
def test_unknown_values_raise_and_list_the_choices(monkeypatch, name, getter, listed):
    monkeypatch.setenv(name, "bogus")

    with pytest.raises(ValueError) as excinfo:
        getter()

    assert name in str(excinfo.value) and listed in str(excinfo.value) and "bogus" in str(excinfo.value)


# -- D23: every row ------------------------------------------------------------------------------------


def test_the_table_in_this_test_covers_every_combination():
    assert set(D23) == set(itertools.product(ENGINES, STORES, CHECKPOINTERS))
    assert {c for c, ok in D23.items() if ok} == set(config.SUPPORTED_RUNTIME_COMBINATIONS)


@pytest.mark.parametrize("combo", [c for c, ok in D23.items() if ok], ids="/".join)
def test_supported_combinations_can_build_and_start_the_app(monkeypatch, tmp_path, combo):
    _set(monkeypatch, *combo)

    assert config.validate_runtime_flags() == dict(zip(("engine", "task_store", "checkpointer"), combo))
    app = create_app(projects_dir=str(tmp_path))
    with TestClient(app) as client:  # startup and shutdown run, nothing touches a database yet
        assert client.get("/api/health").json()["status"] == "ok"


@pytest.mark.parametrize("combo", [c for c, ok in D23.items() if not ok], ids="/".join)
def test_unsupported_combinations_fail_at_create_app_with_the_reason(monkeypatch, tmp_path, combo):
    _set(monkeypatch, *combo)

    with pytest.raises(ValueError) as excinfo:
        create_app(projects_dir=str(tmp_path))

    message = str(excinfo.value)
    engine, store, checkpointer = combo
    assert f"PIPELINE_ENGINE={engine}" in message
    assert f"TASK_STORE={store}" in message and f"PIPELINE_CHECKPOINTER={checkpointer}" in message
    # The supported combinations are listed so the fix is obvious.
    for supported in ("langgraph/db/postgres", "legacy/file/memory"):
        assert supported in message


def test_legacy_with_postgres_names_the_reason(monkeypatch):
    _set(monkeypatch, "legacy", "db", "postgres")

    with pytest.raises(ValueError, match="legacy engine does not use a graph or a checkpointer"):
        config.validate_runtime_flags()


def test_postgres_checkpointer_without_the_db_store_names_the_reason(monkeypatch):
    _set(monkeypatch, "langgraph", "file", "postgres")

    with pytest.raises(ValueError, match="needs TASK_STORE=db"):
        config.validate_runtime_flags()


def test_explicit_arguments_override_the_environment(monkeypatch):
    _set(monkeypatch, "legacy", "file", "postgres")  # unsupported in the environment

    assert config.validate_runtime_flags("langgraph", "db", "postgres") == {
        "engine": "langgraph", "task_store": "db", "checkpointer": "postgres"}


# -- DATABASE_URL ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("combo", [("legacy", "db", "memory"), ("langgraph", "db", "memory"), ("langgraph", "db", "postgres")], ids="/".join)
def test_db_store_without_database_url_fails_clearly(monkeypatch, tmp_path, combo):
    _set(monkeypatch, *combo, with_url=False)

    with pytest.raises(ValueError) as excinfo:
        create_app(projects_dir=str(tmp_path))

    assert "DATABASE_URL" in str(excinfo.value) and "TASK_STORE=db" in str(excinfo.value)
    assert "postgresql+psycopg://" in str(excinfo.value)  # shows the expected shape


def test_blank_database_url_counts_as_missing(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "   ")

    with pytest.raises(ValueError, match="DATABASE_URL is not set"):
        config.get_database_url()


def test_file_store_does_not_need_a_database_url(monkeypatch):
    _set(monkeypatch, "langgraph", "file", "memory", with_url=False)

    assert config.validate_runtime_flags()["task_store"] == "file"


def test_get_database_url_returns_a_valid_url_unchanged(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"  {GOOD_URL}  ")

    assert config.get_database_url() == GOOD_URL


@pytest.mark.parametrize("url", [
    f"postgresql://factory:{SECRET}@db:5432/factory",  # default driver, not psycopg 3
    f"postgresql+psycopg2://factory:{SECRET}@db:5432/factory",
    f"mysql://factory:{SECRET}@db:3306/factory",
    f"mysql+pymysql://factory:{SECRET}@db:3306/factory",
])
def test_wrong_driver_is_rejected_without_leaking_the_password(monkeypatch, url):
    monkeypatch.setenv("DATABASE_URL", url)

    with pytest.raises(ValueError) as excinfo:
        config.get_database_url()

    assert "postgresql+psycopg" in str(excinfo.value)
    assert SECRET not in str(excinfo.value) and SECRET not in repr(excinfo.value)


@pytest.mark.parametrize("garbage", [f"not a url {SECRET}", f"://:{SECRET}@", f"user:{SECRET}@host/db"])
def test_unparseable_url_is_rejected_without_leaking_anything(monkeypatch, garbage):
    monkeypatch.setenv("DATABASE_URL", garbage)

    with pytest.raises(ValueError) as excinfo:
        config.get_database_url()

    assert SECRET not in str(excinfo.value) and SECRET not in repr(excinfo.value)


def test_a_bad_url_through_validate_runtime_flags_does_not_leak_either(monkeypatch):
    _set(monkeypatch, "langgraph", "db", "postgres", with_url=False)
    monkeypatch.setenv("DATABASE_URL", f"postgresql://factory:{SECRET}@db/factory")

    with pytest.raises(ValueError) as excinfo:
        config.validate_runtime_flags()

    assert SECRET not in str(excinfo.value)


@pytest.mark.parametrize("url, masked", [
    ("postgresql+psycopg://u:pw@h:5432/d", "postgresql+psycopg://u:***@h:5432/d"),
    ("postgresql+psycopg://u@h/d", "postgresql+psycopg://u@h/d"),  # no password: unchanged
    ("not a url but://who:hunter2@host", "not a url but://who:***@host"),  # regex fallback
    ("", ""),
])
def test_mask_database_url(url, masked):
    assert config.mask_database_url(url) == masked
