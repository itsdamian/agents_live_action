"""Contract every RunRepository must satisfy."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

T0 = datetime(2026, 4, 1, 8, 0, 0)
SEATS = [
    {"id": "pm", "display_name": "PM", "look": "glasses", "kind": "make"},
    {"id": "qa", "display_name": "QA", "look": "cap", "kind": "check"},
]


def at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


class RunRepositoryContract:
    """Subclass (prefixed ``Test``) and provide ``make_repo(tmp_path)``."""

    def make_repo(self, tmp_path):  # pragma: no cover - interface
        raise NotImplementedError

    def run(self, coro):
        return asyncio.run(coro)

    @pytest.fixture
    def repo(self, tmp_path):
        return self.make_repo(tmp_path)

    def test_open_run_numbers_runs_per_task_from_one(self, repo):
        async def scenario():
            a1 = await repo.open_run("p", "t1", started_at=at(0))
            a2 = await repo.open_run("p", "t1", started_at=at(1))
            b1 = await repo.open_run("p", "t2", started_at=at(2))
            other = await repo.open_run("other", "t1", started_at=at(3))
            assert [a1.run_no, a2.run_no, b1.run_no, other.run_no] == [1, 2, 1, 1]
            assert len({a1.id, a2.id, b1.id, other.id}) == 4

        self.run(scenario())

    def test_open_run_defaults_and_round_trip(self, repo):
        async def scenario():
            run = await repo.open_run("p", "t1", started_at=at(0), pipeline_id="main", seats=SEATS)
            fetched = await repo.get_run(run.id)
            assert fetched == run
            assert (fetched.outcome, fetched.reason, fetched.finished_at) == (None, None, None)
            assert (fetched.rework_count, fetched.cost_usd, fetched.input_tokens, fetched.output_tokens) == (0, 0.0, 0, 0)
            assert (fetched.imported, fetched.pipeline_id, fetched.started_at) == (False, "main", at(0))
            assert fetched.seats == SEATS  # JSON-like values survive unchanged
            plain = await repo.open_run("p", "t9", started_at=at(1), imported=True)
            assert (plain.pipeline_id, plain.seats, plain.imported) == (None, [], True)
            assert await repo.get_run(10**9) is None

        self.run(scenario())

    def test_returned_runs_are_copies(self, repo):
        async def scenario():
            run = await repo.open_run("p", "t1", started_at=at(0), seats=SEATS)
            run.seats.append({"id": "x"})
            run.outcome = "done"
            fetched = await repo.get_run(run.id)
            assert fetched.seats == SEATS and fetched.outcome is None

        self.run(scenario())

    def test_find_open_run_returns_the_newest_unfinished_one(self, repo):
        async def scenario():
            assert await repo.find_open_run("p", "t1") is None
            first = await repo.open_run("p", "t1", started_at=at(0))
            assert (await repo.find_open_run("p", "t1")).id == first.id
            await repo.finish_run(first.id, outcome="done", finished_at=at(5))
            assert await repo.find_open_run("p", "t1") is None
            second = await repo.open_run("p", "t1", started_at=at(6))
            assert (await repo.find_open_run("p", "t1")).id == second.id
            assert await repo.find_open_run("p", "t2") is None

        self.run(scenario())

    def test_list_runs_newest_first_and_scoped_to_the_task(self, repo):
        async def scenario():
            r1 = await repo.open_run("p", "t1", started_at=at(0))
            r2 = await repo.open_run("p", "t1", started_at=at(1))
            await repo.open_run("p", "t2", started_at=at(2))
            assert [r.id for r in await repo.list_runs("p", "t1")] == [r2.id, r1.id]
            assert [r.run_no for r in await repo.list_runs("p", "t1")] == [2, 1]
            assert await repo.list_runs("p", "none") == []

        self.run(scenario())

    def test_steps_are_numbered_without_gaps_per_run(self, repo):
        async def scenario():
            r1 = await repo.open_run("p", "t1", started_at=at(0))
            r2 = await repo.open_run("p", "t2", started_at=at(0))
            s1 = await repo.add_step(r1.id, kind="station", station="pm", status="working", at=at(1))
            s2 = await repo.add_step(r1.id, kind="station", station="pm", status="done", at=at(2))
            other = await repo.add_step(r2.id, kind="gate", station="swe", note="please add tests", at=at(3))
            s3 = await repo.add_step(r1.id, kind="rework", station="swe", at=at(4))
            assert [s.seq for s in (s1, s2, s3)] == [1, 2, 3]
            assert other.seq == 1  # another run counts on its own
            steps = await repo.list_steps(r1.id)
            assert [(s.seq, s.kind, s.station, s.status) for s in steps] == [
                (1, "station", "pm", "working"), (2, "station", "pm", "done"), (3, "rework", "swe", None)]
            assert steps[0].run_id == r1.id and steps[0].at == at(1) and steps[0].note is None
            gate = (await repo.list_steps(r2.id))[0]
            assert (gate.kind, gate.note) == ("gate", "please add tests")
            assert await repo.list_steps(10**9) == []

        self.run(scenario())

    def test_update_totals_and_finish_run(self, repo):
        async def scenario():
            run = await repo.open_run("p", "t1", started_at=at(0))
            assert await repo.update_totals(run.id, cost_usd=0.123456, input_tokens=300, output_tokens=40) is True
            assert await repo.finish_run(run.id, outcome="failed", reason="QA still failing after 3 reworks",
                                         rework_count=4, finished_at=at(9)) is True
            done = await repo.get_run(run.id)
            assert (done.outcome, done.reason, done.rework_count, done.finished_at) == (
                "failed", "QA still failing after 3 reworks", 4, at(9))
            assert (done.input_tokens, done.output_tokens) == (300, 40)
            assert done.cost_usd == pytest.approx(0.123456)
            assert await repo.update_totals(10**9, cost_usd=0, input_tokens=0, output_tokens=0) is False
            assert await repo.finish_run(10**9, outcome="done", finished_at=at(1)) is False

        self.run(scenario())

    def test_supersede_closes_only_the_unfinished_runs_of_that_task(self, repo):
        async def scenario():
            finished = await repo.open_run("p", "t1", started_at=at(0))
            await repo.finish_run(finished.id, outcome="done", finished_at=at(1))
            stale_a = await repo.open_run("p", "t1", started_at=at(2))
            stale_b = await repo.open_run("p", "t1", started_at=at(3))
            elsewhere = await repo.open_run("p", "t2", started_at=at(4))
            count = await repo.supersede_open_runs("p", "t1", reason="superseded", finished_at=at(10))
            assert count == 2
            for run_id in (stale_a.id, stale_b.id):
                run = await repo.get_run(run_id)
                assert (run.outcome, run.reason, run.finished_at) == ("failed", "superseded", at(10))
            assert (await repo.get_run(finished.id)).outcome == "done"  # untouched
            assert (await repo.get_run(elsewhere.id)).outcome is None  # other task untouched
            assert await repo.supersede_open_runs("p", "t1", reason="superseded", finished_at=at(11)) == 0

        self.run(scenario())
