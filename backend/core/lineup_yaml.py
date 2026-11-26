"""The lineup as YAML: first-start import of ``agents/*.yaml``, export and import (plan D4, 4.3).

* :func:`import_from_agent_files` -- in DB mode, the first time a project is seen (its ``agents``
  table has no rows) the project's ``agents/*.yaml`` become its roster: the three built-in
  stations (prompt and LLM from ``PM_agent.yaml`` / ``SWE_agent.yaml`` / ``QA_agent.yaml``) plus
  any file in the custom-agent format. The LLM of each file is mapped by the D21 table; a choice
  that can not be kept is recorded in ``projects.settings.migration_notes``. The files are only
  read, never written or created.
* :func:`export_yaml` / :func:`export_project_yaml` -- the section 4.3 document.
* :func:`parse_import` / :func:`import_yaml` -- "replace everything": every entry is validated
  first and nothing is written unless all of it is fine (then in one transaction).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError

from core.agent_config import AGENTS_DIRNAME, DEFAULT_AGENT_CONFIGS
from core.llm_client import ANTHROPIC_MODELS
from core.stores import AgentRecord, LineupRepository, PipelineRecord, ProjectRepository
from models.agent import AgentConfig
from models.lineup import (
    AGENT_ID_RE,
    BUILTIN_SEATS,
    DEFAULT_LINEUP,
    DEFAULT_PIPELINE_NAME,
    MAX_CUSTOM_AGENTS,
    MAX_PIPELINES,
    derive_agent_id,
    validate_agent_fields,
    validate_lineup,
    validate_llm_choice,
)


logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
# The built-in agents' files; DevOps_agent.yaml is the system monitor, not a seat.
BUILTIN_FILES = {"PM_agent": "pm", "SWE_agent": "swe", "QA_agent": "qa"}
MONITOR_FILES = {"DevOps_agent"}
_BUILTIN_SEAT = {seat.id: seat for seat in BUILTIN_SEATS}
_BUILTIN_KEYS = {"id", "builtin", "system_prompt", "llm"}
_CUSTOM_KEYS = {"id", "builtin", "display_name", "duty", "kind", "look", "system_prompt", "llm"}


# -- the D21 mapping ----------------------------------------------------------------------------


def llm_from_agent_config(provider: str, model: str) -> tuple[dict | None, str | None]:
    """``(llm, note)`` for an agent YAML's ``llm_provider`` / ``llm_model`` (plan D21).

    mock -> ``None`` (follow the project default, which is mock); anthropic with a whitelisted
    model -> that model; anything else (anthropic off the whitelist, openai, gemini ...; all of
    which already ran as Mock) -> ``None`` plus a note.
    """

    provider = (provider or "").lower()
    if provider == "mock":
        return None, None
    if provider == "anthropic" and model in ANTHROPIC_MODELS:
        return {"provider": "anthropic", "model": model}, None
    return None, f"{provider}/{model} 不在可選清單（DB 模式只支援 mock 與 Claude 白名單），改為跟隨專案預設"


# -- first start ---------------------------------------------------------------------------------


def _note(agent: str, path: Path, message: str) -> dict:
    return {"agent": agent, "file": f"{AGENTS_DIRNAME}/{path.name}", "message": message}


def _read_yaml(path: Path):
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("Can not read agent file %s: %s", path, exc)
        return None


def _builtin_record(seat_id: str, config: AgentConfig, path: Path | None, notes: list) -> AgentRecord:
    seat = _BUILTIN_SEAT[seat_id]
    llm, message = llm_from_agent_config(config.llm_provider, config.llm_model)
    if message is not None and path is not None:
        logger.warning("Agent %s (%s): %s", seat_id, path, message)
        notes.append(_note(seat_id, path, message))
    return AgentRecord(seat.id, True, seat.kind, seat.display_name, seat.duty, seat.look,
                       config.system_prompt, llm=llm, max_retries=config.max_retries)


def _custom_record(raw: dict, path: Path, taken: set[str], notes: list) -> AgentRecord | None:
    prompt = raw.get("system_prompt", raw.get("prompt"))
    agent_id = raw.get("id") or derive_agent_id(str(raw.get("display_name") or ""), taken)
    errors = validate_agent_fields(display_name=raw.get("display_name"), duty=raw.get("duty"), prompt=prompt,
                                   kind=raw.get("kind"), look=raw.get("look"), agent_id=agent_id)
    if agent_id in taken:
        errors.append({"field": "id", "message": f"id「{agent_id}」重複"})
    if errors:
        message = "不是合格的自訂 agent，略過：" + "；".join(e["message"] for e in errors)
        logger.warning("Agent file %s: %s", path, message)
        notes.append(_note(str(agent_id), path, message))
        return None
    llm = raw.get("llm")
    if validate_llm_choice(llm):
        message = f"llm 設定 {llm!r} 不合格，改為跟隨專案預設"
        logger.warning("Agent file %s: %s", path, message)
        notes.append(_note(agent_id, path, message))
        llm = None
    return AgentRecord(agent_id, False, raw["kind"], raw["display_name"], raw["duty"], raw["look"],
                       prompt, llm=llm)


def lineup_from_agent_files(project_path: Path) -> tuple[list[AgentRecord], list[PipelineRecord], list[dict]]:
    """``(agents, pipelines, migration notes)`` read from ``<project>/agents/*.yaml`` (blocking)."""

    agents_dir = Path(project_path) / AGENTS_DIRNAME
    files = sorted(agents_dir.glob("*.yaml")) if agents_dir.is_dir() else []
    notes: list[dict] = []
    builtins: dict[str, AgentRecord] = {}
    customs: list[AgentRecord] = []
    taken = set(_BUILTIN_SEAT)
    for path in files:
        if path.stem in MONITOR_FILES:
            continue
        raw = _read_yaml(path)
        if path.stem in BUILTIN_FILES:
            seat_id = BUILTIN_FILES[path.stem]
            try:
                config = AgentConfig(**raw) if isinstance(raw, dict) else None
            except ValidationError:
                config = None
            if config is None:  # malformed: the default, as load_agent_configs does
                logger.warning("Malformed agent file %s; using the default %s", path, path.stem)
                config = DEFAULT_AGENT_CONFIGS[path.stem]
            builtins[seat_id] = _builtin_record(seat_id, config, path, notes)
        elif isinstance(raw, dict) and "kind" in raw:
            record = _custom_record(raw, path, taken, notes)
            if record is not None and len(customs) < MAX_CUSTOM_AGENTS:
                customs.append(record)
                taken.add(record.id)
        else:
            message = "不是自訂 agent 的格式（需要 id、display_name、duty、kind、look、system_prompt），略過"
            logger.warning("Agent file %s: %s", path, message)
            notes.append(_note(path.stem, path, message))
    for name, seat_id in BUILTIN_FILES.items():
        if seat_id not in builtins:
            builtins[seat_id] = _builtin_record(seat_id, DEFAULT_AGENT_CONFIGS[name], None, notes)
    agents = [builtins[seat.id] for seat in BUILTIN_SEATS] + customs
    pipelines = [PipelineRecord(DEFAULT_LINEUP.pipeline_id, DEFAULT_PIPELINE_NAME, True, DEFAULT_LINEUP.ids)]
    return agents, pipelines, notes


async def import_from_agent_files(project_id: str, project_path: Path, repo: LineupRepository) -> bool:
    """First start in DB mode: copy ``agents/*.yaml`` into the database unless it has agents.

    Returns ``True`` when the lineup was written. The YAML files are never modified.
    """

    import asyncio

    if await repo.has_agents(project_id):
        return False
    agents, pipelines, notes = await asyncio.to_thread(lineup_from_agent_files, Path(project_path))
    written = await repo.import_initial(project_id, agents, pipelines, migration_notes=notes)
    if written:
        logger.info("Imported the lineup of project %s from agents/*.yaml: %s", project_id,
                    ", ".join(a.id for a in agents))
    return written


# -- export ----------------------------------------------------------------------------------------


def export_yaml(agents: Sequence[AgentRecord], pipelines: Sequence[PipelineRecord], project_llm) -> str:
    """The section 4.3 document. Built-ins carry only their prompt (as a backup) and LLM."""

    entries = []
    for agent in agents:
        if agent.builtin:
            entries.append({"id": agent.id, "builtin": True, "system_prompt": agent.system_prompt,
                            "llm": agent.llm})
        else:
            entries.append({"id": agent.id, "display_name": agent.display_name, "duty": agent.duty,
                            "kind": agent.kind, "look": agent.look, "system_prompt": agent.system_prompt,
                            "llm": agent.llm})
    document = {
        "schema_version": SCHEMA_VERSION,
        "agents": entries,
        "settings": {"llm": project_llm},
        "pipelines": [{"id": p.id, "name": p.name, "default": p.is_default, "seats": list(p.seats)}
                      for p in pipelines],
    }
    return yaml.safe_dump(document, allow_unicode=True, sort_keys=False, default_flow_style=False)


async def export_project_yaml(project_id: str, repo: LineupRepository, projects: ProjectRepository) -> str:
    settings = await projects.get_settings(project_id) or {}
    return export_yaml(await repo.list_agents(project_id), await repo.list_pipelines(project_id),
                       settings.get("llm", "mock"))


# -- import ----------------------------------------------------------------------------------------


class LineupImportError(ValueError):
    """The YAML can not be imported; ``errors`` lists every problem (nothing was written)."""

    def __init__(self, errors: list[dict]) -> None:
        self.errors = errors
        super().__init__("編制 YAML 不合格：" + "；".join(f"{e['field']}: {e['message']}" for e in errors))


@dataclass(frozen=True)
class ImportPlan:
    agents: list[AgentRecord]
    pipelines: list[PipelineRecord]
    project_llm: object  # None = leave the project setting alone


def _err(field: str, message: str) -> dict:
    return {"field": field, "message": message}


def _prefixed(prefix: str, errors: list[dict]) -> list[dict]:
    return [_err(f"{prefix}.{e['field']}", e["message"]) for e in errors]


def _parse_agents(entries, current: dict[str, AgentRecord], errors: list[dict]) -> list[AgentRecord]:
    if not isinstance(entries, list):
        errors.append(_err("agents", "agents 必須是清單"))
        return []
    builtins: dict[str, AgentRecord] = {}
    customs: list[AgentRecord] = []
    seen: set[str] = set()
    for i, entry in enumerate(entries):
        where = f"agents[{i}]"
        if not isinstance(entry, dict):
            errors.append(_err(where, "每個 agent 必須是 mapping"))
            continue
        if "api_key_env" in entry:
            errors.append(_err(f"{where}.api_key_env", "不接受 api_key_env：金鑰的環境變數由 provider 決定"))
            continue
        agent_id = entry.get("id")
        if agent_id in seen:
            errors.append(_err(f"{where}.id", f"id「{agent_id}」重複"))
            continue
        seen.add(agent_id)
        llm_errors = _prefixed(where, validate_llm_choice(entry.get("llm")))
        if entry.get("builtin") is True:
            if agent_id not in _BUILTIN_SEAT:
                errors.append(_err(f"{where}.id", f"「{agent_id}」不是內建 agent（只有 pm、swe、qa）"))
                continue
            extra = sorted(set(entry) - _BUILTIN_KEYS)
            if extra:
                errors.append(_err(f"{where}.{extra[0]}", f"內建 agent 只能設定 system_prompt 與 llm：{'、'.join(extra)}"))
                continue
            prompt = entry.get("system_prompt")
            if prompt is not None and (not isinstance(prompt, str) or not prompt.strip()):
                errors.append(_err(f"{where}.system_prompt", "system_prompt 不能是空的"))
                continue
            if llm_errors:
                errors += llm_errors
                continue
            seat = _BUILTIN_SEAT[agent_id]
            base = current.get(agent_id)
            builtins[agent_id] = AgentRecord(
                seat.id, True, seat.kind, seat.display_name, seat.duty, seat.look,
                prompt if prompt is not None else (base.system_prompt if base else
                                                   DEFAULT_AGENT_CONFIGS[_builtin_file(seat.id)].system_prompt),
                llm=entry.get("llm"), max_retries=base.max_retries if base else 3,
            )
            continue
        extra = sorted(set(entry) - _CUSTOM_KEYS)
        field_errors = validate_agent_fields(
            display_name=entry.get("display_name"), duty=entry.get("duty"), prompt=entry.get("system_prompt"),
            kind=entry.get("kind"), look=entry.get("look"), agent_id=agent_id if isinstance(agent_id, str) else "",
        )
        problems = _prefixed(where, field_errors) + llm_errors
        if extra:
            problems.append(_err(f"{where}.{extra[0]}", f"不認得的欄位：{'、'.join(extra)}"))
        if entry.get("builtin") not in (None, False):
            problems.append(_err(f"{where}.builtin", "builtin 只能是 true 或 false"))
        if problems:
            errors += problems
            continue
        customs.append(AgentRecord(agent_id, False, entry["kind"], entry["display_name"], entry["duty"],
                                   entry["look"], entry["system_prompt"], llm=entry.get("llm")))
    if len(customs) > MAX_CUSTOM_AGENTS:
        errors.append(_err("agents", f"自訂 agent 最多 {MAX_CUSTOM_AGENTS} 個"))
    for seat in BUILTIN_SEATS:  # built-ins always exist; ones left out keep what is stored
        if seat.id not in builtins:
            base = current.get(seat.id)
            builtins[seat.id] = base if base is not None else AgentRecord(
                seat.id, True, seat.kind, seat.display_name, seat.duty, seat.look,
                DEFAULT_AGENT_CONFIGS[_builtin_file(seat.id)].system_prompt)
    return [builtins[seat.id] for seat in BUILTIN_SEATS] + customs


def _builtin_file(seat_id: str) -> str:
    return next(name for name, sid in BUILTIN_FILES.items() if sid == seat_id)


def _parse_pipelines(entries, agents: list[AgentRecord], errors: list[dict]) -> list[PipelineRecord]:
    if not isinstance(entries, list) or not entries:
        errors.append(_err("pipelines", "pipelines 必須是至少一條線的清單"))
        return []
    if len(entries) > MAX_PIPELINES:
        errors.append(_err("pipelines", f"最多 {MAX_PIPELINES} 條線"))
    roster = {a.id: a.kind for a in agents}
    pipelines: list[PipelineRecord] = []
    seen: set[str] = set()
    for i, entry in enumerate(entries):
        where = f"pipelines[{i}]"
        if not isinstance(entry, dict):
            errors.append(_err(where, "每條線必須是 mapping"))
            continue
        pid, name, seats = entry.get("id"), entry.get("name"), entry.get("seats")
        problems = []
        if not isinstance(pid, str) or not AGENT_ID_RE.match(pid):
            problems.append(_err(f"{where}.id", "線的 id 必須是 2–24 個字元，小寫英文字母開頭"))
        elif pid in seen:
            problems.append(_err(f"{where}.id", f"線的 id「{pid}」重複"))
        if not isinstance(name, str) or not name.strip():
            problems.append(_err(f"{where}.name", "線的名稱不能是空的"))
        if not isinstance(seats, list) or not all(isinstance(s, str) for s in seats):
            problems.append(_err(f"{where}.seats", "seats 必須是 agent id 的清單"))
        else:
            problems += _prefixed(where, validate_lineup(seats, roster))
        if problems:
            errors += problems
            continue
        seen.add(pid)
        pipelines.append(PipelineRecord(pid, name, entry.get("default") is True, tuple(seats)))
    if pipelines and sum(p.is_default for p in pipelines) != 1:
        errors.append(_err("pipelines", "必須剛好有一條預設線（default: true）"))
    return pipelines


def parse_import(text: str, current_agents: Sequence[AgentRecord]) -> ImportPlan:
    """Validate a YAML lineup; raise :class:`LineupImportError` listing every problem."""

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise LineupImportError([_err("yaml", f"不是合格的 YAML：{exc}")]) from None
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        raise LineupImportError([_err("schema_version", f"schema_version 必須是 {SCHEMA_VERSION}")])
    errors: list[dict] = []
    if "agents" not in data:
        errors.append(_err("agents", "缺少 agents"))
    agents = _parse_agents(data.get("agents", []), {a.id: a for a in current_agents}, errors)
    pipelines = _parse_pipelines(data.get("pipelines"), agents, errors)
    project_llm = None
    settings = data.get("settings")
    if settings is not None:
        if not isinstance(settings, dict):
            errors.append(_err("settings", "settings 必須是 mapping"))
        elif "llm" in settings:
            llm_errors = validate_llm_choice(settings["llm"], allow_null=False)
            errors += _prefixed("settings", llm_errors)
            project_llm = settings["llm"]
    if errors:
        raise LineupImportError(errors)
    return ImportPlan(agents, pipelines, project_llm)


async def import_yaml(project_id: str, text: str, repo: LineupRepository) -> ImportPlan:
    """Validate ``text`` and replace the project's whole lineup with it (one transaction)."""

    plan = parse_import(text, await repo.list_agents(project_id))
    await repo.replace_all(project_id, plan.agents, plan.pipelines, project_llm=plan.project_llm)
    return plan
