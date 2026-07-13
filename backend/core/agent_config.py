"""Agent configuration loading and bootstrap.

Reads per-project agent configuration from ``<project>/agents/*.yaml`` and
parses each file into an :class:`~models.agent.AgentConfig` with Pydantic. When
a project has no ``agents/`` directory (or the directory holds no config
files), the four built-in default agents are written to disk automatically so
every project starts with a working PM -> SWE -> QA pipeline plus the
independent DevOps monitor.

Malformed configuration is tolerated: on a YAML syntax error or a Pydantic
validation error the offending file is logged as a warning and the matching
hardcoded default is used instead (matched by agent name / filename). This
keeps a single broken file from taking down a whole project.

Requirements:
    2.1 (four default agent roles: PM_agent, SWE_agent, QA_agent, DevOps_agent),
    2.2 (roles used by the pipeline / DevOps monitor),
    3.1 (look for agent YAML under the agents/ subdirectory),
    3.2 (auto-create four default YAML files when agents/ is missing or empty),
    3.3 (parse config files and initialize the matching agent),
    3.4 (support fields: name / role / description / LLM model /
         api-key env-var reference / system prompt / max concurrency),
    3.5 (on malformed config, log the error and fall back to the default).
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from pydantic import ValidationError

from models.agent import AgentConfig

logger = logging.getLogger(__name__)

# Subdirectory (relative to a project root) that holds agent YAML files.
AGENTS_DIRNAME = "agents"


# ---------------------------------------------------------------------------
# Hardcoded default agent configurations
# ---------------------------------------------------------------------------
#
# These serve two purposes:
#   1. They are written to disk by ``create_default_agents`` when a project has
#      no agent config yet (Requirement 3.2).
#   2. They are the fallback used when a config file fails to parse
#      (Requirement 3.5), matched by agent name.
#
# ``llm_provider`` defaults to "mock" so a fresh project runs end-to-end with
# no API key configured; ``api_key_env`` still records the env var to use once
# a real provider key is available (matches config.PROVIDER_ENV_VARS).
#
# NOTE (Requirement 3.4 "最大任務並發數" / max concurrent tasks): the existing
# ``AgentConfig`` model exposes ``max_retries`` but has no dedicated
# max-concurrency field. To avoid breaking other tasks/tests that depend on the
# model, this module maps the documented YAML fields onto the existing
# ``AgentConfig`` fields and does not add a new field. See the module docstring
# / task report for the follow-up note.

DEFAULT_AGENT_CONFIGS: dict[str, AgentConfig] = {
    "PM_agent": AgentConfig(
        name="PM_agent",
        role="Project Manager",
        description="分析任務需求、拆解工作項目、追蹤執行進度，並協調 SWE 與 QA agents 的工作順序。",
        llm_provider="mock",
        llm_model="gpt-4o-mini",
        api_key_env="OPENAI_API_KEY",
        max_retries=3,
        system_prompt=(
            "You are a senior project manager. Analyze the incoming task, break "
            "it down into clear work items, and produce a structured execution "
            "plan with acceptance criteria that the engineering and QA agents "
            "can act on."
        ),
    ),
    "SWE_agent": AgentConfig(
        name="SWE_agent",
        role="Senior Software Engineer",
        description="根據 PM 的規劃撰寫程式碼、解決技術問題，重工時接收 QA 的失敗報告。",
        llm_provider="mock",
        llm_model="gpt-4o-mini",
        api_key_env="OPENAI_API_KEY",
        max_retries=3,
        system_prompt=(
            "You are a senior software engineer. Implement the task according to "
            "the project manager's plan. When you receive a QA failure report, "
            "use it to fix the reported defects and produce corrected code."
        ),
    ),
    "QA_agent": AgentConfig(
        name="QA_agent",
        role="Senior QA Engineer",
        description="設計測試案例、執行測試、回報缺陷並驗證修復結果，失敗時輸出結構化報告。",
        llm_provider="mock",
        llm_model="gpt-4o-mini",
        api_key_env="OPENAI_API_KEY",
        max_retries=3,
        system_prompt=(
            "You are a senior QA engineer. Design and run tests against the "
            "engineer's output, then decide pass or fail. On failure, produce a "
            "structured defect report the software engineer can act on."
        ),
    ),
    "DevOps_agent": AgentConfig(
        name="DevOps_agent",
        role="DevOps Engineer",
        description="獨立於主流水線之外，持續監控系統健康（CPU/記憶體/磁碟/服務），異常時記錄並警示。",
        llm_provider="mock",
        llm_model="gpt-4o-mini",
        api_key_env="OPENAI_API_KEY",
        max_retries=3,
        system_prompt=(
            "You are a DevOps engineer. Monitor overall system health — CPU, "
            "memory, disk, and service availability — and raise a clear alert "
            "whenever a metric crosses a safe threshold."
        ),
    ),
}

# Deterministic ordering for created files / returned lists.
DEFAULT_AGENT_NAMES: tuple[str, ...] = (
    "PM_agent",
    "SWE_agent",
    "QA_agent",
    "DevOps_agent",
)


def _agents_dir(project_path: str | Path) -> Path:
    """Return the ``agents/`` directory for a project (not necessarily existing)."""

    return Path(project_path) / AGENTS_DIRNAME


def _config_to_yaml_dict(config: AgentConfig) -> dict:
    """Serialize an :class:`AgentConfig` to a plain dict for ``yaml.safe_dump``."""

    # model_dump keeps field order and gives JSON-safe primitives, which
    # yaml.safe_dump can serialize directly.
    return config.model_dump()


def create_default_agents(project_path: str | Path) -> list[AgentConfig]:
    """Create the four default agent YAML files under ``<project>/agents/``.

    The ``agents/`` directory is created if missing. Each default agent is
    written to ``agents/<name>.yaml`` (e.g. ``agents/PM_agent.yaml``) using
    ``yaml.safe_dump``. Existing files are left untouched so user
    customizations are never overwritten.

    Args:
        project_path: Path to the project root directory.

    Returns:
        The list of default :class:`AgentConfig` objects (in canonical
        PM/SWE/QA/DevOps order), regardless of whether each file was newly
        written or already present.
    """

    agents_dir = _agents_dir(project_path)
    agents_dir.mkdir(parents=True, exist_ok=True)

    created: list[AgentConfig] = []
    for name in DEFAULT_AGENT_NAMES:
        config = DEFAULT_AGENT_CONFIGS[name]
        target = agents_dir / f"{name}.yaml"
        if target.exists():
            logger.debug("Default agent config already exists, skipping: %s", target)
        else:
            with target.open("w", encoding="utf-8") as fh:
                yaml.safe_dump(
                    _config_to_yaml_dict(config),
                    fh,
                    allow_unicode=True,
                    sort_keys=False,
                    default_flow_style=False,
                )
            logger.info("Created default agent config: %s", target)
        created.append(config)

    return created


def _default_for_file(path: Path) -> AgentConfig | None:
    """Return the default config matching a YAML file's stem, if any.

    Matches ``PM_agent.yaml`` -> the ``PM_agent`` default. Returns ``None`` for
    files that do not correspond to a known default agent (so an unrelated
    malformed file is skipped rather than misattributed).
    """

    return DEFAULT_AGENT_CONFIGS.get(path.stem)


def load_agent_configs(project_path: str | Path) -> list[AgentConfig]:
    """Load all agent configs for a project, creating defaults when needed.

    Behavior:
        * If the ``agents/`` directory is missing or contains no ``*.yaml``
          files, the four defaults are written via
          :func:`create_default_agents` and returned (Requirement 3.2).
        * Otherwise every ``agents/*.yaml`` file is parsed with Pydantic
          (Requirement 3.3). On a YAML syntax error or validation error a
          warning is logged and, if the filename matches a known default agent,
          that default is substituted; unknown malformed files are skipped
          (Requirement 3.5).

    Args:
        project_path: Path to the project root directory.

    Returns:
        A list of :class:`AgentConfig` objects sorted by agent name for
        deterministic output.
    """

    agents_dir = _agents_dir(project_path)

    yaml_files = (
        sorted(agents_dir.glob("*.yaml")) if agents_dir.is_dir() else []
    )

    if not yaml_files:
        logger.info(
            "No agent configs found for project %s; creating defaults.",
            project_path,
        )
        return create_default_agents(project_path)

    configs: list[AgentConfig] = []
    for path in yaml_files:
        config = _load_single_config(path)
        if config is not None:
            configs.append(config)

    # If every file failed and none matched a default, fall back to defaults so
    # the project is still usable.
    if not configs:
        logger.warning(
            "All agent configs for project %s were unusable; using defaults.",
            project_path,
        )
        return list(DEFAULT_AGENT_CONFIGS.values())

    return sorted(configs, key=lambda c: c.name)


def _load_single_config(path: Path) -> AgentConfig | None:
    """Parse one agent YAML file, falling back to a default on error.

    Returns the parsed config, the matching default when the file is malformed,
    or ``None`` when a malformed file has no corresponding default.
    """

    try:
        with path.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        return _fallback(path, f"YAML syntax error: {exc}")

    if not isinstance(raw, dict):
        return _fallback(
            path, f"expected a mapping at the top level, got {type(raw).__name__}"
        )

    try:
        return AgentConfig(**raw)
    except ValidationError as exc:
        return _fallback(path, f"invalid fields: {exc}")


def _fallback(path: Path, reason: str) -> AgentConfig | None:
    """Log a warning for a malformed file and return its default (or None)."""

    default = _default_for_file(path)
    if default is not None:
        logger.warning(
            "Malformed agent config %s (%s); falling back to default '%s'.",
            path,
            reason,
            default.name,
        )
        return default

    logger.warning(
        "Malformed agent config %s (%s); no matching default, skipping.",
        path,
        reason,
    )
    return None
