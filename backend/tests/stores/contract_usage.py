"""Contract every UsageRepository must satisfy."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from core.stores import UsageTotals
from core.usage_tracker import UsageRecord

T0 = datetime(2026, 3, 1, 9, 0, 0)


def record(project="p", task="t1", minutes=0, input_tokens=100, output_tokens=50, cost=0.0123) -> UsageRecord:
    return UsageRecord(
        project_id=project, task_id=task, provider="anthropic", model="claude-opus-5-5",
        input_tokens=input_tokens, output_tokens=output_tokens, cost=cost,
        timestamp=T0 + timedelta(minutes=minutes),
    )


class UsageRepositoryContract:
    """Subclass (prefixed ``Test``) and provide ``make_repo(tmp_path)``."""

    def make_repo(self, tmp_path):  # pragma: no cover - interface
        raise NotImplementedError

    def run(self, coro):
        return asyncio.run(coro)

    @pytest.fixture
    def repo(self, tmp_path):
        return self.make_repo(tmp_path)

    def test_empty_repository(self, repo):
        async def scenario():
            assert await repo.list_records() == []
            assert await repo.list_records("p") == []
            assert await repo.totals("p", "t1") == UsageTotals(0, 0, 0.0, 0)

        self.run(scenario())

    def test_records_round_trip_oldest_first(self, repo):
        async def scenario():
            first, second, third = record(minutes=0), record(minutes=1, input_tokens=7), record(minutes=2, cost=0.5)
            for r in (first, second, third):
                await repo.add(r)
            assert await repo.list_records("p") == [first, second, third]
            assert await repo.list_records() == [first, second, third]

        self.run(scenario())

    def test_list_records_filters_by_project(self, repo):
        async def scenario():
            a, b = record(project="alpha"), record(project="beta", minutes=1)
            await repo.add(a)
            await repo.add(b)
            assert await repo.list_records("alpha") == [a]
            assert await repo.list_records("beta") == [b]
            assert await repo.list_records("gamma") == []
            assert await repo.list_records() == [a, b]

        self.run(scenario())

    def test_totals_sum_one_task_only(self, repo):
        async def scenario():
            await repo.add(record(task="t1", input_tokens=100, output_tokens=10, cost=0.1))
            await repo.add(record(task="t1", minutes=1, input_tokens=200, output_tokens=20, cost=0.2, ))
            await repo.add(record(task="t2", minutes=2, input_tokens=999, output_tokens=99, cost=9.0))
            await repo.add(record(project="other", task="t1", minutes=3, input_tokens=5, output_tokens=5, cost=1.0))
            totals = await repo.totals("p", "t1")
            assert (totals.input_tokens, totals.output_tokens, totals.call_count) == (300, 30, 2)
            assert totals.cost == pytest.approx(0.3)

        self.run(scenario())

    def test_totals_since_includes_the_boundary(self, repo):
        async def scenario():
            for minutes, tokens in ((0, 1), (10, 2), (20, 4)):
                await repo.add(record(minutes=minutes, input_tokens=tokens, output_tokens=0, cost=0.0))
            since_10 = await repo.totals("p", "t1", since=T0 + timedelta(minutes=10))
            assert (since_10.input_tokens, since_10.call_count) == (6, 2)  # the record AT since counts
            assert (await repo.totals("p", "t1", since=T0 + timedelta(minutes=21))).call_count == 0

        self.run(scenario())

    def test_cost_keeps_six_decimals(self, repo):
        async def scenario():
            await repo.add(record(cost=0.000001))
            await repo.add(record(minutes=1, cost=0.000002))
            assert (await repo.totals("p", "t1")).cost == pytest.approx(0.000003, abs=1e-9)

        self.run(scenario())
