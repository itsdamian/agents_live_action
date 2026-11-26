"""agents/*.yaml -> DB on first start, YAML export / import (Task 18, plan D4, D21, section 4.3)."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
import yaml

from core.agent_config import create_default_agents
from core.lineup_yaml import (
    LineupImportError,
    export_project_yaml,
    export_yaml,
    import_from_agent_files,
    import_yaml,
    lineup_from_agent_files,
    llm_from_agent_config,
    parse_import,
)
from core.stores import AgentRecord, PipelineRecord
from core.stores.memory import (
    InMemoryLineupRepository,
    InMemoryProjectRepository,
    InMemoryRunRepository,
    InMemoryTaskRepository,
)


def repos():
    projects = InMemoryProjectRepository(InMemoryTaskRepository(), InMemoryRunRepository())
    return InMemoryLineupRepository(projects), projects


def run(coro):
    return asyncio.run(coro)


def write_agent(project: Path, name: str, **overrides) -> Path:
    data = dict(name=name, role="role", description="d", llm_provider="mock", llm_model="gpt-4o-mini",
                api_key_env="OPENAI_API_KEY", max_retries=3, system_prompt=f"You are {name}.")
    data.update(overrides)
    path = project / "agents" / f"{name}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def files_state(project: Path) -> dict:
    agents = project / "agents"
    if not agents.exists():
        return {}
    return {p.name: (p.read_bytes(), os.stat(p).st_mtime_ns) for p in sorted(agents.iterdir())}


# -- the LLM mapping table (plan D21, one case per row) -------------------------------------------


@pytest.mark.parametrize(("provider", "model", "expected", "noted"), [
    ("mock", "gpt-4o-mini", None, False),
    ("mock", "mock-model", None, False),
    ("anthropic", "claude-opus-5-5", {"provider": "anthropic", "model": "claude-opus-5-5"}, False),
    ("anthropic", "claude-haiku-5-5", {"provider": "anthropic", "model": "claude-haiku-5-5"}, False),
    ("anthropic", "claude-3-opus", None, True),  # anthropic, not on the whitelist
    ("openai", "gpt-4o", None, True),
    ("gemini", "gemini-1.5-pro", None, True),
    ("ANTHROPIC", "claude-opus-5-5", {"provider": "anthropic", "model": "claude-opus-5-5"}, False),
])
def test_llm_mapping(provider, model, expected, noted):
    llm, note = llm_from_agent_config(provider, model)
    assert llm == expected
    assert (note is not None) is noted
    if noted:
        assert provider.lower() in note and model in note


# -- first start: agents/*.yaml -> DB --------------------------------------------------------------


def test_the_default_files_become_the_three_builtins_and_the_main_pipeline(tmp_path):
    create_default_agents(tmp_path)
    agents, pipelines, notes = lineup_from_agent_files(tmp_path)

    assert [(a.id, a.builtin, a.kind, a.display_name, a.look, a.llm) for a in agents] == [
        ("pm", True, "make", "PM", "glasses", None),
        ("swe", True, "make", "SWE", "headset", None),
        ("qa", True, "check", "QA", "cap", None),
    ]
    assert agents[1].system_prompt.startswith("You are a senior software engineer.")
    assert pipelines == [PipelineRecord("main", "主線", True, ("pm", "swe", "qa"))]
    assert notes == []  # DevOps_agent is the monitor, not a seat: left out without a note


def test_prompts_and_retries_come_from_the_project_files(tmp_path):
    write_agent(tmp_path, "PM_agent", system_prompt="Custom PM prompt", max_retries=5)
    agents, _, _ = lineup_from_agent_files(tmp_path)

    pm = agents[0]
    assert (pm.id, pm.system_prompt, pm.max_retries) == ("pm", "Custom PM prompt", 5)
    # Missing built-in files fall back to the defaults, like load_agent_configs does.
    assert agents[1].system_prompt.startswith("You are a senior software engineer.")


def test_a_malformed_builtin_file_falls_back_to_its_default(tmp_path):
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "QA_agent.yaml").write_text("name: [unclosed", encoding="utf-8")
    agents, _, _ = lineup_from_agent_files(tmp_path)
    assert agents[2].system_prompt.startswith("You are a senior QA engineer.")


def test_unsupported_llms_are_mapped_and_noted(tmp_path):
    write_agent(tmp_path, "PM_agent", llm_provider="anthropic", llm_model="claude-sonnet-5-5", api_key_env="ANTHROPIC_API_KEY")
    write_agent(tmp_path, "SWE_agent", llm_provider="openai", llm_model="gpt-4o")
    write_agent(tmp_path, "QA_agent", llm_provider="anthropic", llm_model="claude-3-opus")
    agents, _, notes = lineup_from_agent_files(tmp_path)

    assert [a.llm for a in agents] == [{"provider": "anthropic", "model": "claude-sonnet-5-5"}, None, None]
    assert sorted((n["agent"], n["file"]) for n in notes) == [("qa", "agents/QA_agent.yaml"), ("swe", "agents/SWE_agent.yaml")]


def test_custom_format_files_join_the_roster_without_a_seat(tmp_path):
    create_default_agents(tmp_path)
    (tmp_path / "agents" / "designer.yaml").write_text(yaml.safe_dump({
        "id": "designer", "display_name": "設計師", "duty": "畫介面草圖", "kind": "make", "look": "beret",
        "system_prompt": "你是介面設計師", "llm": {"provider": "anthropic", "model": "claude-opus-5-5"},
    }, allow_unicode=True), encoding="utf-8")
    (tmp_path / "agents" / "broken.yaml").write_text(yaml.safe_dump({
        "id": "broken", "display_name": "", "duty": "x", "kind": "review", "look": "beret", "system_prompt": "p",
    }), encoding="utf-8")
    (tmp_path / "agents" / "Writer_agent.yaml").write_text(yaml.safe_dump({
        "name": "Writer_agent", "role": "Writer", "description": "d", "llm_provider": "mock",
        "llm_model": "m", "api_key_env": "X", "system_prompt": "p",
    }), encoding="utf-8")

    agents, pipelines, notes = lineup_from_agent_files(tmp_path)

    assert [a.id for a in agents] == ["pm", "swe", "qa", "designer"]
    assert agents[3].llm == {"provider": "anthropic", "model": "claude-opus-5-5"} and not agents[3].builtin
    assert pipelines[0].seats == ("pm", "swe", "qa")
    assert sorted(n["file"] for n in notes) == ["agents/Writer_agent.yaml", "agents/broken.yaml"]


def test_import_from_files_writes_once_and_never_touches_the_files(tmp_path):
    create_default_agents(tmp_path)
    write_agent(tmp_path, "SWE_agent", llm_provider="openai", llm_model="gpt-4o")
    before = files_state(tmp_path)
    lineup, projects = repos()

    async def scenario():
        await projects.create_from_migration("p", settings={"llm": "mock"}, tasks=[])
        assert await import_from_agent_files("p", tmp_path, lineup) is True
        assert await import_from_agent_files("p", tmp_path, lineup) is False  # the table has rows now
        settings = await projects.get_settings("p")
        assert settings["llm"] == "mock"
        assert [n["agent"] for n in settings["migration_notes"]] == ["swe"]
        assert [a.id for a in await lineup.list_agents("p")] == ["pm", "swe", "qa"]

    run(scenario())
    assert files_state(tmp_path) == before


def test_import_from_a_project_without_agent_files_creates_no_files(tmp_path):
    lineup, _ = repos()
    assert run(import_from_agent_files("p", tmp_path, lineup)) is True
    assert not (tmp_path / "agents").exists()
    assert [a.id for a in run(lineup.list_agents("p"))] == ["pm", "swe", "qa"]


# -- export / import --------------------------------------------------------------------------------

DESIGNER = AgentRecord("designer", False, "make", "設計師", "畫介面草圖", "beret", "你是介面設計師……",
                       llm={"provider": "anthropic", "model": "claude-opus-5-5"})
SECURITY = AgentRecord("security", False, "check", "資安", "找漏洞", "antenna", "你是資安審查員", llm="mock")


def seeded(tmp_path):
    create_default_agents(tmp_path)
    lineup, projects = repos()

    async def scenario():
        await projects.create_from_migration("a", settings={"llm": {"provider": "anthropic", "model": "claude-haiku-5-5"}}, tasks=[])
        await import_from_agent_files("a", tmp_path, lineup)
        await lineup.insert_agent("a", DESIGNER)
        await lineup.insert_agent("a", SECURITY)
        await lineup.update_agent("a", "qa", llm={"provider": "anthropic", "model": "claude-fable-5-2"})
        await lineup.save_pipeline("a", PipelineRecord("main", "主線", True, ("pm", "designer", "swe", "qa", "security")))

    run(scenario())
    return lineup, projects


def test_export_follows_the_section_4_3_format(tmp_path):
    lineup, projects = seeded(tmp_path)
    data = yaml.safe_load(run(export_project_yaml("a", lineup, projects)))

    assert data["schema_version"] == 1
    assert data["settings"] == {"llm": {"provider": "anthropic", "model": "claude-haiku-5-5"}}
    assert data["pipelines"] == [{"id": "main", "name": "主線", "default": True,
                                  "seats": ["pm", "designer", "swe", "qa", "security"]}]
    pm, swe, qa, designer, security = data["agents"]
    assert set(pm) == {"id", "builtin", "system_prompt", "llm"} and pm["builtin"] is True and pm["llm"] is None
    assert qa["llm"] == {"provider": "anthropic", "model": "claude-fable-5-2"}
    assert designer == {"id": "designer", "display_name": "設計師", "duty": "畫介面草圖", "kind": "make",
                        "look": "beret", "system_prompt": "你是介面設計師……",
                        "llm": {"provider": "anthropic", "model": "claude-opus-5-5"}}
    assert security["llm"] == "mock"
    assert "api_key_env" not in run(export_project_yaml("a", lineup, projects))


def test_export_then_import_into_an_empty_project_gives_the_same_roster_and_seats(tmp_path):
    lineup, projects = seeded(tmp_path)
    text = run(export_project_yaml("a", lineup, projects))

    async def scenario():
        await projects.create_from_migration("b", settings={"llm": "mock"}, tasks=[])
        await import_yaml("b", text, lineup)
        strip = lambda records: [r.without_times() for r in records]  # noqa: E731
        assert strip(await lineup.list_agents("b")) == strip(await lineup.list_agents("a"))
        assert [(p.id, p.name, p.is_default, p.seats) for p in await lineup.list_pipelines("b")] == [
            (p.id, p.name, p.is_default, p.seats) for p in await lineup.list_pipelines("a")]
        assert (await projects.get_settings("b"))["llm"] == {"provider": "anthropic", "model": "claude-haiku-5-5"}
        # A second round trip changes nothing.
        assert await export_project_yaml("b", lineup, projects) == text

    run(scenario())


def test_import_replaces_the_whole_lineup(tmp_path):
    lineup, projects = seeded(tmp_path)
    text = yaml.safe_dump({
        "schema_version": 1,
        "agents": [{"id": "writer", "display_name": "寫手", "duty": "寫文件", "kind": "make", "look": "bow",
                    "system_prompt": "你是寫手"}],
        "pipelines": [{"id": "main", "name": "主線", "default": True, "seats": ["pm", "writer"]}],
    }, allow_unicode=True)

    run(import_yaml("a", text, lineup))
    agents = run(lineup.list_agents("a"))
    # Built-ins always exist; the ones the YAML leaves out keep their stored prompt and llm.
    assert [a.id for a in agents] == ["pm", "swe", "qa", "writer"]
    assert agents[2].llm == {"provider": "anthropic", "model": "claude-fable-5-2"}
    assert agents[3].llm is None
    assert run(lineup.get_pipeline("a", "main")).seats == ("pm", "writer")
    # No settings in the YAML: the project LLM is left alone.
    assert run(projects.get_settings("a"))["llm"] == {"provider": "anthropic", "model": "claude-haiku-5-5"}


def test_export_yaml_is_pure():
    agents = [AgentRecord("pm", True, "make", "PM", "d", "glasses", "p")]
    text = export_yaml(agents, [PipelineRecord("main", "主線", True, ("pm",))], "mock")
    assert yaml.safe_load(text) == {
        "schema_version": 1,
        "agents": [{"id": "pm", "builtin": True, "system_prompt": "p", "llm": None}],
        "settings": {"llm": "mock"},
        "pipelines": [{"id": "main", "name": "主線", "default": True, "seats": ["pm"]}],
    }


GOOD_IMPORT = {
    "schema_version": 1,
    "agents": [
        {"id": "pm", "builtin": True},
        {"id": "designer", "display_name": "設計師", "duty": "畫介面草圖", "kind": "make", "look": "beret",
         "system_prompt": "p"},
    ],
    "settings": {"llm": "mock"},
    "pipelines": [{"id": "main", "name": "主線", "default": True, "seats": ["pm", "designer", "swe", "qa"]}],
}


def _with(path: str, value):
    data = yaml.safe_load(yaml.safe_dump(GOOD_IMPORT, allow_unicode=True))
    target = data
    *parents, last = path.split(".")
    for key in parents:
        target = target[int(key)] if key.isdigit() else target[key]
    if value is _DELETE:
        del target[last]
    elif last.isdigit():
        target[int(last)] = value
    else:
        target[last] = value
    return yaml.safe_dump(data, allow_unicode=True)


_DELETE = object()


def test_the_good_import_is_good():
    plan = parse_import(yaml.safe_dump(GOOD_IMPORT, allow_unicode=True), [])
    assert [a.id for a in plan.agents] == ["pm", "swe", "qa", "designer"]
    assert plan.project_llm == "mock"


@pytest.mark.parametrize(("text", "fragment"), [
    ("{not yaml", "YAML"),
    ("- a list", "schema_version"),
    (_with("schema_version", 2), "schema_version"),
    (_with("agents", _DELETE), "agents"),
    (_with("agents.1.api_key_env", "ANTHROPIC_API_KEY"), "api_key_env"),
    (_with("agents.1.llm", {"provider": "anthropic", "model": "claude-opus-5-5", "api_key_env": "X"}), "api_key_env"),
    (_with("agents.1.llm", {"provider": "anthropic", "model": "claude-3-opus"}), "model"),
    (_with("agents.1.llm", {"provider": "openai", "model": "gpt-4o"}), "provider"),
    (_with("agents.1.id", "Designer"), "id"),
    (_with("agents.1.id", "qa"), "保留"),
    (_with("agents.1.display_name", "設計​師"), "控制字元"),
    (_with("agents.1.kind", "review"), "kind"),
    (_with("agents.0.id", "boss"), "內建"),
    (_with("agents.0.display_name", "Boss"), "display_name"),
    (_with("agents.0.api_key_env", "X"), "api_key_env"),
    (_with("settings.llm", {"provider": "anthropic", "model": "nope"}), "model"),
    (_with("settings.llm", None), "llm"),
    (_with("pipelines.0.seats", ["pm", "ghost"]), "ghost"),
    (_with("pipelines.0.seats", ["qa", "pm"]), "第一席"),
    (_with("pipelines.0.default", False), "預設"),
    (_with("pipelines", []), "pipelines"),
])
def test_bad_imports_are_refused_with_errors(text, fragment):
    with pytest.raises(LineupImportError) as excinfo:
        parse_import(text, [])
    assert excinfo.value.errors
    assert any(fragment in e["message"] or fragment in e["field"] for e in excinfo.value.errors), excinfo.value.errors


def test_duplicate_ids_and_too_many_agents_are_refused():
    data = yaml.safe_load(yaml.safe_dump(GOOD_IMPORT, allow_unicode=True))
    data["agents"].append(dict(data["agents"][1]))
    with pytest.raises(LineupImportError, match="重複"):
        parse_import(yaml.safe_dump(data, allow_unicode=True), [])
    data["agents"] = [{**GOOD_IMPORT["agents"][1], "id": f"agent-{i:02d}"} for i in range(17)]
    data["pipelines"][0]["seats"] = ["pm"]
    with pytest.raises(LineupImportError, match="16"):
        parse_import(yaml.safe_dump(data, allow_unicode=True), [])


def test_a_refused_import_writes_nothing(tmp_path):
    lineup, projects = seeded(tmp_path)
    before = [a.without_times() for a in run(lineup.list_agents("a"))]
    settings_before = run(projects.get_settings("a"))
    with pytest.raises(LineupImportError):
        run(import_yaml("a", _with("pipelines.0.seats", ["pm", "ghost"]), lineup))
    assert [a.without_times() for a in run(lineup.list_agents("a"))] == before
    assert run(projects.get_settings("a")) == settings_before
