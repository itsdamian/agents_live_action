"""A lineup that can not run is refused before it changes anything (QA gate C, item 1).

``request_lineup`` validates first (plan 4.2 rules plus the seat ids), so an invalid lineup
neither replaces the current one nor waits as a pending change. ``_set_lineup`` and
``use_lineup`` compile the new graph before they touch any state.
"""

from __future__ import annotations

import asyncio

import pytest

from core.stores import PipelineRecord
from core.stores.memory import InMemoryLineupRepository
from models.lineup import DEFAULT_LINEUP, InvalidLineup
from models.task import Approval
from tests.hud_harness import make_lineup, make_orchestrator_with_repo
from tests.pipeline_harness import make_project, make_task

INVALID = {
    "two_checks": (("c1", "check"), ("c2", "check")),
    "id_intake": ("pm", ("intake", "make")),
    "id_done": ("pm", ("done", "make")),
    "id_with_colon": ("pm", ("a:b", "make")),
}


def _orchestrator(tmp_path, tasks):
    live: list[tuple[str, dict]] = []
    orch, events = make_orchestrator_with_repo(
        make_project(tmp_path, tasks), engine="langgraph", on_live_event=lambda e, d: live.append((e, d)))
    return orch, events, live


def _state(orch):
    return (orch.lineup, orch._graph_runner.lineup, orch._graph_runner._graph, dict(orch.extra_agents),
            dict(orch._agents), list(orch.station_states), orch.pending_lineup)


@pytest.mark.parametrize("name", sorted(INVALID))
def test_an_invalid_lineup_is_refused_when_idle_and_nothing_changes(tmp_path, name):
    orch, events, live = _orchestrator(tmp_path, [make_task()])
    before = _state(orch)
    lineup, agents = make_lineup(*INVALID[name])

    with pytest.raises(InvalidLineup) as caught:
        asyncio.run(orch.request_lineup(lineup, agents))
    assert caught.value.errors and isinstance(caught.value, ValueError)
    assert _state(orch) == before
    assert events == [] and live == []


@pytest.mark.parametrize("name", sorted(INVALID))
def test_an_invalid_lineup_is_refused_while_a_task_runs_and_is_not_kept_pending(tmp_path, name):
    first, second = make_task("task-001", approval=Approval.AFTER_SWE), make_task("task-002")
    orch, events, live = _orchestrator(tmp_path, [first, second])
    repo = InMemoryLineupRepository()
    asyncio.run(repo.import_initial(orch.project_id, [], [PipelineRecord("main", "主線", True, ("pm", "swe", "qa"))]))
    orch._lineup_repo = repo
    lineup, agents = make_lineup(*INVALID[name])

    async def scenario():
        running = asyncio.create_task(orch.process_task(first))
        for _ in range(500):
            if orch.is_awaiting_approval(first.id):
                break
            await asyncio.sleep(0.005)
        assert orch.in_flight_task_id == first.id
        with pytest.raises(InvalidLineup):
            await orch.request_lineup(lineup, agents)
        assert orch.pending_lineup is None
        assert (await repo.get_pipeline(orch.project_id, "main")).pending_seats is None
        orch.approve(first.id)
        await running
        return await orch.process_task(second)

    result = asyncio.run(scenario())
    assert "pipeline:pending" not in [e for e, _ in live]
    assert (first.status.value, result.status.value) == ("done", "done")
    assert orch.lineup == DEFAULT_LINEUP


def test_a_builtin_seat_must_be_the_real_builtin_seat(tmp_path):
    from models.lineup import Lineup, Seat

    orch, _, _ = _orchestrator(tmp_path, [make_task()])
    fake_qa = Seat("qa", "make", True, "QA", "測試", "cap")
    with pytest.raises(InvalidLineup):
        asyncio.run(orch.request_lineup(Lineup("main", (fake_qa,)), {}))
    assert orch.lineup == DEFAULT_LINEUP


def test_legacy_refuses_an_invalid_lineup_too(tmp_path):
    orch, _ = make_orchestrator_with_repo(make_project(tmp_path, [make_task()]), engine="legacy")
    lineup, agents = make_lineup(*INVALID["two_checks"])
    with pytest.raises(InvalidLineup):
        asyncio.run(orch.request_lineup(lineup, agents))
    assert orch.lineup == DEFAULT_LINEUP


def test_set_lineup_keeps_the_old_state_when_the_graph_does_not_compile(tmp_path):
    orch, _, _ = _orchestrator(tmp_path, [make_task()])
    before = _state(orch)
    lineup, agents = make_lineup(*INVALID["id_intake"])  # bypasses request_lineup's validation

    with pytest.raises(ValueError):
        orch._set_lineup(lineup, agents)
    assert _state(orch) == before


def test_use_lineup_keeps_the_old_graph_when_the_new_one_does_not_compile(tmp_path):
    orch, _, _ = _orchestrator(tmp_path, [make_task()])
    runner = orch._graph_runner
    graph = runner._graph
    lineup, _ = make_lineup(*INVALID["id_with_colon"])

    with pytest.raises(ValueError):
        runner.use_lineup(lineup)
    assert (runner.lineup, runner._graph) == (DEFAULT_LINEUP, graph)


def test_a_valid_custom_lineup_is_still_accepted(tmp_path):
    orch, _, _ = _orchestrator(tmp_path, [make_task()])
    lineup, agents = make_lineup("pm", ("designer", "make"), "swe", "qa", ("security", "check"))
    assert asyncio.run(orch.request_lineup(lineup, agents)) == "now"
    assert orch.lineup == lineup
