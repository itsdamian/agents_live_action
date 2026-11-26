"""Unit tests for agent-config loading and bootstrap.

Covers the three scenarios required by task 3.2:
    1. Normal parsing of valid ``agents/*.yaml`` files.
    2. Malformed YAML / invalid fields falling back to defaults (with a
       logged warning, asserted via ``caplog``).
    3. Auto-creation of the four default configs when ``agents/`` is missing.

All tests use ``tmp_path`` so nothing touches the real ``projects/`` tree.
"""

from __future__ import annotations

import logging

import pytest
import yaml

from core.agent_config import (
    DEFAULT_AGENT_CONFIGS,
    DEFAULT_AGENT_NAMES,
    create_default_agents,
    load_agent_configs,
)
from models.agent import AgentConfig


def _write_yaml(directory, name, payload):
    """Helper: write a YAML file and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    with path.open("w", encoding="utf-8") as fh:
        if isinstance(payload, str):
            fh.write(payload)
        else:
            yaml.safe_dump(payload, fh, allow_unicode=True, sort_keys=False)
    return path


def _valid_config_dict(name="PM_agent"):
    return {
        "name": name,
        "role": "Project Manager",
        "description": "分析任務需求",
        "llm_provider": "openai",
        "llm_model": "gpt-4o-mini",
        "api_key_env": "OPENAI_API_KEY",
        "max_retries": 3,
        "system_prompt": "You are a senior project manager.",
    }


# ---------------------------------------------------------------------------
# Scenario 3: auto-creation of defaults
# ---------------------------------------------------------------------------


def test_create_default_agents_creates_four_files(tmp_path):
    created = create_default_agents(tmp_path)

    agents_dir = tmp_path / "agents"
    assert agents_dir.is_dir()

    expected_files = {f"{name}.yaml" for name in DEFAULT_AGENT_NAMES}
    actual_files = {p.name for p in agents_dir.glob("*.yaml")}
    assert actual_files == expected_files

    assert [c.name for c in created] == list(DEFAULT_AGENT_NAMES)


def test_created_default_files_reparse_into_valid_configs(tmp_path):
    create_default_agents(tmp_path)
    agents_dir = tmp_path / "agents"

    for name in DEFAULT_AGENT_NAMES:
        with (agents_dir / f"{name}.yaml").open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        config = AgentConfig(**data)  # must not raise
        assert config == DEFAULT_AGENT_CONFIGS[name]


def test_create_default_agents_does_not_overwrite_existing(tmp_path):
    agents_dir = tmp_path / "agents"
    custom = _valid_config_dict(name="PM_agent")
    custom["role"] = "Custom Role"
    _write_yaml(agents_dir, "PM_agent.yaml", custom)

    create_default_agents(tmp_path)

    with (agents_dir / "PM_agent.yaml").open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    assert data["role"] == "Custom Role"  # untouched

    # The other three defaults were still created.
    assert (agents_dir / "SWE_agent.yaml").is_file()
    assert (agents_dir / "QA_agent.yaml").is_file()
    assert (agents_dir / "DevOps_agent.yaml").is_file()


def test_load_creates_defaults_when_agents_dir_missing(tmp_path, caplog):
    with caplog.at_level(logging.INFO, logger="core.agent_config"):
        configs = load_agent_configs(tmp_path)

    assert (tmp_path / "agents").is_dir()
    assert {c.name for c in configs} == set(DEFAULT_AGENT_NAMES)
    assert any("creating defaults" in r.message.lower() for r in caplog.records)


def test_load_creates_defaults_when_agents_dir_empty(tmp_path):
    (tmp_path / "agents").mkdir()

    configs = load_agent_configs(tmp_path)

    assert {c.name for c in configs} == set(DEFAULT_AGENT_NAMES)
    assert len(list((tmp_path / "agents").glob("*.yaml"))) == 4


# ---------------------------------------------------------------------------
# Scenario 1: normal parsing of valid YAML
# ---------------------------------------------------------------------------


def test_load_parses_valid_config(tmp_path):
    agents_dir = tmp_path / "agents"
    _write_yaml(agents_dir, "PM_agent.yaml", _valid_config_dict("PM_agent"))
    _write_yaml(agents_dir, "SWE_agent.yaml", _valid_config_dict("SWE_agent"))

    configs = load_agent_configs(tmp_path)

    by_name = {c.name: c for c in configs}
    assert set(by_name) == {"PM_agent", "SWE_agent"}
    assert by_name["PM_agent"].llm_provider == "openai"
    assert by_name["PM_agent"].role == "Project Manager"


def test_load_returns_configs_sorted_by_name(tmp_path):
    agents_dir = tmp_path / "agents"
    for name in ("QA_agent", "PM_agent", "SWE_agent"):
        _write_yaml(agents_dir, f"{name}.yaml", _valid_config_dict(name))

    configs = load_agent_configs(tmp_path)

    names = [c.name for c in configs]
    assert names == sorted(names)


def test_load_preserves_custom_field_values(tmp_path):
    agents_dir = tmp_path / "agents"
    custom = _valid_config_dict("PM_agent")
    custom["max_retries"] = 7
    custom["llm_model"] = "claude-3-5-sonnet"
    custom["llm_provider"] = "anthropic"
    _write_yaml(agents_dir, "PM_agent.yaml", custom)

    (config,) = load_agent_configs(tmp_path)

    assert config.max_retries == 7
    assert config.llm_model == "claude-3-5-sonnet"
    assert config.llm_provider == "anthropic"


# ---------------------------------------------------------------------------
# Scenario 2: malformed config falls back to defaults with a warning
# ---------------------------------------------------------------------------


def test_load_falls_back_on_yaml_syntax_error(tmp_path, caplog):
    agents_dir = tmp_path / "agents"
    # Unbalanced brackets -> YAML syntax error.
    _write_yaml(agents_dir, "PM_agent.yaml", "name: [unclosed\n  bad: : :")

    with caplog.at_level(logging.WARNING, logger="core.agent_config"):
        configs = load_agent_configs(tmp_path)

    (config,) = configs
    assert config == DEFAULT_AGENT_CONFIGS["PM_agent"]
    assert any(record.levelno == logging.WARNING for record in caplog.records)
    assert any("PM_agent" in record.message for record in caplog.records)


def test_load_falls_back_on_invalid_fields(tmp_path, caplog):
    agents_dir = tmp_path / "agents"
    # Missing required fields (role, description, etc.) -> ValidationError.
    _write_yaml(agents_dir, "QA_agent.yaml", {"name": "QA_agent"})

    with caplog.at_level(logging.WARNING, logger="core.agent_config"):
        configs = load_agent_configs(tmp_path)

    (config,) = configs
    assert config == DEFAULT_AGENT_CONFIGS["QA_agent"]
    assert any("invalid fields" in record.message for record in caplog.records)


def test_load_skips_unknown_malformed_file(tmp_path, caplog):
    agents_dir = tmp_path / "agents"
    # A valid known agent plus a malformed file with no matching default.
    _write_yaml(agents_dir, "SWE_agent.yaml", _valid_config_dict("SWE_agent"))
    _write_yaml(agents_dir, "random.yaml", {"foo": "bar"})

    with caplog.at_level(logging.WARNING, logger="core.agent_config"):
        configs = load_agent_configs(tmp_path)

    assert [c.name for c in configs] == ["SWE_agent"]
    assert any("skipping" in record.message.lower() for record in caplog.records)


def test_load_mixes_valid_and_fallback_configs(tmp_path, caplog):
    agents_dir = tmp_path / "agents"
    _write_yaml(agents_dir, "PM_agent.yaml", _valid_config_dict("PM_agent"))
    _write_yaml(agents_dir, "SWE_agent.yaml", {"name": "SWE_agent"})  # invalid

    with caplog.at_level(logging.WARNING, logger="core.agent_config"):
        configs = load_agent_configs(tmp_path)

    by_name = {c.name: c for c in configs}
    assert set(by_name) == {"PM_agent", "SWE_agent"}
    # PM parsed from file, SWE fell back to default.
    assert by_name["PM_agent"].role == "Project Manager"
    assert by_name["SWE_agent"] == DEFAULT_AGENT_CONFIGS["SWE_agent"]


def test_load_all_malformed_returns_defaults(tmp_path, caplog):
    agents_dir = tmp_path / "agents"
    # Only unknown malformed files -> no configs survive -> defaults returned.
    _write_yaml(agents_dir, "random.yaml", {"foo": "bar"})

    with caplog.at_level(logging.WARNING, logger="core.agent_config"):
        configs = load_agent_configs(tmp_path)

    assert {c.name for c in configs} == set(DEFAULT_AGENT_NAMES)
