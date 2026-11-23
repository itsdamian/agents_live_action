"""Contract every LineupRepository must satisfy (Task 18, plan D4).

Not collected by pytest. A backend's test module subclasses :class:`LineupRepositoryContract`
(prefixed ``Test``) and provides a ``repos`` fixture returning ``(lineup_repo, project_repo)``;
the project repository is used to create the project row whose settings the lineup repository
patches.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from core.stores import AgentRecord, PipelineRecord

T = datetime(2026, 3, 1, 9, 0, 0)


def builtin(agent_id: str, kind: str = "make", **overrides) -> AgentRecord:
    base = dict(id=agent_id, builtin=True, kind=kind, display_name=agent_id.upper(), duty="職責",
                look="cap", system_prompt=f"You are {agent_id}.", llm=None, max_retries=3)
    base.update(overrides)
    return AgentRecord(**base)


def custom(agent_id: str, kind: str = "make", **overrides) -> AgentRecord:
    base = dict(id=agent_id, builtin=False, kind=kind, display_name=agent_id, duty="職責",
                look="beret", system_prompt=f"prompt of {agent_id}", llm=None, max_retries=3)
    base.update(overrides)
    return AgentRecord(**base)


BUILTINS = [builtin("pm"), builtin("swe"), builtin("qa", "check")]
MAIN = PipelineRecord(id="main", name="主線", is_default=True, seats=("pm", "swe", "qa"))


class LineupRepositoryContract:
    PROJECT = "proj"

    def run(self, coro):
        return asyncio.run(coro)

    async def make_project(self, projects, project_id: str, settings: dict | None = None) -> None:
        await projects.create_from_migration(project_id, settings=settings or {"llm": "mock"}, tasks=[])

    # -- initial import ------------------------------------------------------------------------

    def test_a_new_project_has_no_agents_and_no_pipelines(self, repos):
        lineup, _ = repos

        async def scenario():
            assert await lineup.has_agents(self.PROJECT) is False
            assert await lineup.list_agents(self.PROJECT) == []
            assert await lineup.list_pipelines(self.PROJECT) == []
            assert await lineup.get_agent(self.PROJECT, "pm") is None
            assert await lineup.get_pipeline(self.PROJECT, "main") is None

        self.run(scenario())

    def test_import_initial_writes_agents_and_pipelines_once(self, repos):
        lineup, projects = repos

        async def scenario():
            await self.make_project(projects, self.PROJECT)
            assert await lineup.import_initial(self.PROJECT, [*BUILTINS, custom("designer")], [MAIN]) is True
            assert await lineup.has_agents(self.PROJECT) is True
            again = await lineup.import_initial(self.PROJECT, [builtin("pm", system_prompt="other")], [])
            assert again is False
            assert (await lineup.get_agent(self.PROJECT, "pm")).system_prompt == "You are pm."
            assert [a.id for a in await lineup.list_agents(self.PROJECT)] == ["pm", "swe", "qa", "designer"]

        self.run(scenario())

    def test_import_initial_merges_migration_notes_into_the_project_settings(self, repos):
        lineup, projects = repos
        note = {"agent": "SWE_agent", "message": "openai 不支援，改為跟隨專案預設"}

        async def scenario():
            await self.make_project(projects, self.PROJECT, {"llm": "mock"})
            await lineup.import_initial(self.PROJECT, BUILTINS, [MAIN], migration_notes=[note])
            assert await projects.get_settings(self.PROJECT) == {"llm": "mock", "migration_notes": [note]}

        self.run(scenario())

    def test_import_initial_without_a_project_row_still_writes_the_lineup(self, repos):
        lineup, projects = repos

        async def scenario():
            assert await lineup.import_initial(self.PROJECT, BUILTINS, [MAIN], migration_notes=[{"m": 1}]) is True
            assert await projects.exists(self.PROJECT) is False  # never invents a project row
            assert len(await lineup.list_agents(self.PROJECT)) == 3

        self.run(scenario())

    def test_projects_are_independent(self, repos):
        lineup, _ = repos

        async def scenario():
            await lineup.import_initial("a", [*BUILTINS, custom("designer")], [MAIN])
            await lineup.import_initial("b", BUILTINS, [MAIN])
            assert [x.id for x in await lineup.list_agents("b")] == ["pm", "swe", "qa"]
            assert await lineup.get_agent("b", "designer") is None

        self.run(scenario())

    # -- reading -------------------------------------------------------------------------------

    def test_agents_round_trip_every_field(self, repos):
        lineup, _ = repos
        designer = custom("designer", llm={"provider": "anthropic", "model": "claude-opus-5-5"},
                          max_retries=2, display_name="設計師", duty="畫介面草圖")
        writer = custom("writer", llm="mock")

        async def scenario():
            await lineup.import_initial(self.PROJECT, [*BUILTINS, designer, writer], [MAIN])
            got = await lineup.get_agent(self.PROJECT, "designer")
            assert got.without_times() == designer.without_times()
            assert isinstance(got.created_at, datetime) and isinstance(got.updated_at, datetime)
            assert (await lineup.get_agent(self.PROJECT, "writer")).llm == "mock"
            assert (await lineup.get_agent(self.PROJECT, "pm")).llm is None

        self.run(scenario())

    def test_builtins_come_first_in_pm_swe_qa_order_then_customs_by_creation(self, repos):
        lineup, _ = repos

        async def scenario():
            await lineup.import_initial(
                self.PROJECT, [custom("zeta"), builtin("qa", "check"), custom("alpha"), builtin("swe"), builtin("pm")], [MAIN])
            assert [a.id for a in await lineup.list_agents(self.PROJECT)] == ["pm", "swe", "qa", "zeta", "alpha"]
            await lineup.insert_agent(self.PROJECT, custom("beta"))
            assert [a.id for a in await lineup.list_agents(self.PROJECT)][-1] == "beta"

        self.run(scenario())

    def test_pipelines_default_first(self, repos):
        lineup, _ = repos
        side = PipelineRecord(id="aside", name="支線", is_default=False, seats=("pm",))

        async def scenario():
            await lineup.import_initial(self.PROJECT, BUILTINS, [side, MAIN])
            pipelines = await lineup.list_pipelines(self.PROJECT)
            assert [p.id for p in pipelines] == ["main", "aside"]
            main = await lineup.get_pipeline(self.PROJECT, "main")
            assert (main.name, main.is_default, main.seats, main.pending_seats) == ("主線", True, ("pm", "swe", "qa"), None)
            assert isinstance(main.updated_at, datetime)

        self.run(scenario())

    def test_returned_records_are_copies(self, repos):
        lineup, _ = repos

        async def scenario():
            await lineup.import_initial(self.PROJECT, [*BUILTINS, custom("d", llm={"provider": "anthropic", "model": "claude-opus-5-5"})], [MAIN])
            got = await lineup.get_agent(self.PROJECT, "d")
            got.llm["model"] = "changed"
            assert (await lineup.get_agent(self.PROJECT, "d")).llm["model"] == "claude-opus-5-5"

        self.run(scenario())

    # -- writing -------------------------------------------------------------------------------

    def test_insert_agent_refuses_an_existing_id(self, repos):
        lineup, _ = repos

        async def scenario():
            await lineup.import_initial(self.PROJECT, BUILTINS, [MAIN])
            assert await lineup.insert_agent(self.PROJECT, custom("designer")) is True
            assert await lineup.insert_agent(self.PROJECT, custom("designer", duty="另一個")) is False
            assert (await lineup.get_agent(self.PROJECT, "designer")).duty == "職責"

        self.run(scenario())

    def test_update_agent_changes_only_the_given_fields(self, repos):
        lineup, _ = repos

        async def scenario():
            await lineup.import_initial(self.PROJECT, [*BUILTINS, custom("designer")], [MAIN])
            before = await lineup.get_agent(self.PROJECT, "designer")
            assert await lineup.update_agent(
                self.PROJECT, "designer", display_name="設計", llm={"provider": "anthropic", "model": "claude-haiku-5-5"}) is True
            after = await lineup.get_agent(self.PROJECT, "designer")
            assert (after.display_name, after.llm, after.duty) == (
                "設計", {"provider": "anthropic", "model": "claude-haiku-5-5"}, "職責")
            assert after.created_at == before.created_at
            assert await lineup.update_agent(self.PROJECT, "designer", llm=None) is True
            assert (await lineup.get_agent(self.PROJECT, "designer")).llm is None
            assert await lineup.update_agent(self.PROJECT, "ghost", duty="x") is False

        self.run(scenario())

    def test_update_agent_refuses_unknown_fields(self, repos):
        lineup, _ = repos

        async def scenario():
            await lineup.import_initial(self.PROJECT, BUILTINS, [MAIN])
            with pytest.raises(ValueError):
                await lineup.update_agent(self.PROJECT, "pm", api_key_env="X")
            with pytest.raises(ValueError):
                await lineup.update_agent(self.PROJECT, "pm", id="other")

        self.run(scenario())

    def test_delete_agent(self, repos):
        lineup, _ = repos

        async def scenario():
            await lineup.import_initial(self.PROJECT, [*BUILTINS, custom("designer")], [MAIN])
            assert await lineup.delete_agent(self.PROJECT, "designer") is True
            assert await lineup.delete_agent(self.PROJECT, "designer") is False
            assert await lineup.get_agent(self.PROJECT, "designer") is None

        self.run(scenario())

    def test_save_pipeline_inserts_or_replaces(self, repos):
        lineup, _ = repos

        async def scenario():
            await lineup.import_initial(self.PROJECT, [*BUILTINS, custom("designer")], [MAIN])
            await lineup.save_pipeline(self.PROJECT, PipelineRecord("main", "主線", True, ("pm", "designer", "swe", "qa")))
            assert (await lineup.get_pipeline(self.PROJECT, "main")).seats == ("pm", "designer", "swe", "qa")
            await lineup.save_pipeline(self.PROJECT, PipelineRecord("second", "二線", False, ("designer",)))
            assert [p.id for p in await lineup.list_pipelines(self.PROJECT)] == ["main", "second"]

        self.run(scenario())

    def test_pending_seats_are_set_and_cleared(self, repos):
        lineup, _ = repos

        async def scenario():
            await lineup.import_initial(self.PROJECT, [*BUILTINS, custom("designer")], [MAIN])
            assert await lineup.set_pending_seats(self.PROJECT, "main", ["pm", "designer", "swe", "qa"]) is True
            main = await lineup.get_pipeline(self.PROJECT, "main")
            assert (main.seats, main.pending_seats) == (("pm", "swe", "qa"), ("pm", "designer", "swe", "qa"))
            assert await lineup.set_pending_seats(self.PROJECT, "main", None) is True
            assert (await lineup.get_pipeline(self.PROJECT, "main")).pending_seats is None
            assert await lineup.set_pending_seats(self.PROJECT, "ghost", ["pm"]) is False

        self.run(scenario())

    # -- whole replacement (YAML import) --------------------------------------------------------

    def test_replace_all_swaps_agents_pipelines_and_the_project_llm(self, repos):
        lineup, projects = repos

        async def scenario():
            await self.make_project(projects, self.PROJECT, {"llm": "mock", "migration_notes": ["kept"]})
            await lineup.import_initial(self.PROJECT, [*BUILTINS, custom("designer"), custom("old")], [MAIN])
            await lineup.replace_all(
                self.PROJECT, [*BUILTINS, custom("security", "check")],
                [PipelineRecord("main", "主線", True, ("pm", "swe", "qa", "security"))],
                project_llm={"provider": "anthropic", "model": "claude-sonnet-5-5"},
            )
            assert [a.id for a in await lineup.list_agents(self.PROJECT)] == ["pm", "swe", "qa", "security"]
            assert (await lineup.get_pipeline(self.PROJECT, "main")).seats == ("pm", "swe", "qa", "security")
            assert await projects.get_settings(self.PROJECT) == {
                "llm": {"provider": "anthropic", "model": "claude-sonnet-5-5"}, "migration_notes": ["kept"]}

        self.run(scenario())

    def test_replace_all_is_all_or_nothing(self, repos):
        lineup, _ = repos

        async def scenario():
            await lineup.import_initial(self.PROJECT, [*BUILTINS, custom("designer")], [MAIN])
            bad = custom("broken", kind="review")  # refused by the kind CHECK / validation
            with pytest.raises(Exception):
                await lineup.replace_all(self.PROJECT, [*BUILTINS, custom("security", "check"), bad], [MAIN])
            assert [a.id for a in await lineup.list_agents(self.PROJECT)] == ["pm", "swe", "qa", "designer"]

        self.run(scenario())
