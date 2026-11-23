"""agents/*.yaml import and YAML export / import on PostgreSQL (Task 18, AC16)."""

from __future__ import annotations

import asyncio

import pytest
import yaml

from core.agent_config import create_default_agents
from core.lineup_yaml import LineupImportError, export_project_yaml, import_from_agent_files, import_yaml
from core.stores import AgentRecord, PipelineRecord
from core.stores.pg import PgLineupRepository, PgProjectRepository
from db.session import create_engine_for, session_factory
from tests.test_lineup_yaml import files_state, write_agent

pytestmark = pytest.mark.integration


@pytest.fixture
def sessions(migrated_db_sqlalchemy_url):
    engine = create_engine_for(migrated_db_sqlalchemy_url, null_pool=True)
    yield session_factory(engine)
    asyncio.run(engine.dispose())


def test_first_start_import_export_and_import_into_an_empty_project(sessions, tmp_path):
    create_default_agents(tmp_path)
    write_agent(tmp_path, "SWE_agent", llm_provider="gemini", llm_model="gemini-1.5-pro")
    before = files_state(tmp_path)
    lineup, projects = PgLineupRepository(sessions), PgProjectRepository(sessions)

    async def scenario():
        await projects.create_from_migration("a", settings={"llm": "mock"}, tasks=[])
        await projects.create_from_migration("b", settings={"llm": "mock"}, tasks=[])
        assert await import_from_agent_files("a", tmp_path, lineup) is True
        assert await import_from_agent_files("a", tmp_path, lineup) is False
        notes = (await projects.get_settings("a"))["migration_notes"]
        assert [n["agent"] for n in notes] == ["swe"]

        await lineup.insert_agent("a", AgentRecord("designer", False, "make", "設計師", "畫介面草圖", "beret", "p",
                                                   llm={"provider": "anthropic", "model": "claude-opus-5-5"}))
        await lineup.save_pipeline("a", PipelineRecord("main", "主線", True, ("pm", "designer", "swe", "qa")))
        text = await export_project_yaml("a", lineup, projects)

        await import_yaml("b", text, lineup)
        strip = lambda records: [r.without_times() for r in records]  # noqa: E731
        assert strip(await lineup.list_agents("b")) == strip(await lineup.list_agents("a"))
        assert (await lineup.get_pipeline("b", "main")).seats == ("pm", "designer", "swe", "qa")
        assert await export_project_yaml("b", lineup, projects) == text

        bad = yaml.safe_load(text)
        bad["pipelines"][0]["seats"] = ["pm", "ghost"]
        with pytest.raises(LineupImportError):
            await import_yaml("b", yaml.safe_dump(bad, allow_unicode=True), lineup)
        assert strip(await lineup.list_agents("b")) == strip(await lineup.list_agents("a"))

    asyncio.run(scenario())
    assert files_state(tmp_path) == before
