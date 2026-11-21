"""LLM usage & cost tracking with budget enforcement.

Every LLM call the pipeline makes is recorded here: its provider, model, and
input/output token counts, plus an estimated USD cost derived from a static
per-model price table (:data:`MODEL_PRICES`). Costs are accumulated three ways
so the rest of the system can surface them:

* **per task**   — shown in the task-detail panel,
* **per project**— shown in the DevOps panel / project detail,
* **per day**    — used for the daily budget guard.

Budget enforcement (task 16):
    The runtime reads ``DAILY_BUDGET_USD`` and a per-project budget from the
    environment (see :mod:`config`). When either ceiling is crossed,
    :meth:`UsageTracker.budget_status` reports it so the pipeline can pause
    enqueueing new tasks and broadcast a critical ``devops:alert``.

The tracker is provider-agnostic and does no I/O, so it is trivially testable:
prices, budgets, and the clock are all injectable.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, Protocol

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Price table
# ---------------------------------------------------------------------------

# Estimated USD price per 1,000 tokens, keyed by (provider, model) lowercased,
# as ``(input_price_per_1k, output_price_per_1k)``. Values are approximate list
# prices kept intentionally small and centralised so they are easy to update.
MODEL_PRICES: dict[tuple[str, str], tuple[float, float]] = {
    ("openai", "gpt-4o"): (0.0050, 0.0150),
    ("openai", "gpt-4o-mini"): (0.00015, 0.00060),
    ("openai", "gpt-4-turbo"): (0.0100, 0.0300),
    ("openai", "gpt-3.5-turbo"): (0.00050, 0.00150),
    ("anthropic", "claude-3-5-sonnet"): (0.0030, 0.0150),
    ("anthropic", "claude-3-opus"): (0.0150, 0.0750),
    ("anthropic", "claude-3-haiku"): (0.00025, 0.00125),
    # Claude 5 family (2026-11 list prices). When server-side fallback kicks in,
    # a different model actually bills the call, so spend is an estimate.
    ("anthropic", "claude-opus-5-5"): (0.0040, 0.0200),
    ("anthropic", "claude-sonnet-5-5"): (0.0020, 0.0100),
    ("anthropic", "claude-haiku-5-5"): (0.0001, 0.0005),
    ("anthropic", "claude-fable-5-2"): (0.0080, 0.0400),
    ("gemini", "gemini-1.5-pro"): (0.00125, 0.0050),
    ("gemini", "gemini-1.5-flash"): (0.000075, 0.00030),
    ("mock", "mock-model"): (0.0, 0.0),
}

# Fallback price per 1,000 tokens for a known provider whose specific model is
# not in the table. Lets a newly-named model still be costed sensibly.
PROVIDER_DEFAULT_PRICES: dict[str, tuple[float, float]] = {
    "openai": (0.0050, 0.0150),
    "anthropic": (0.0040, 0.0200),  # = claude-opus-5-5, the recommended default
    "gemini": (0.00125, 0.0050),
    "mock": (0.0, 0.0),
}

# Price used when neither the model nor its provider is known. Zero so an
# unrecognised provider never inflates the spend with a made-up number.
UNKNOWN_PRICE: tuple[float, float] = (0.0, 0.0)

# Rough characters-per-token ratio for estimating token counts from text when a
# provider does not report real usage (e.g. the mock client).
_CHARS_PER_TOKEN = 4


def estimate_tokens(text: str | None) -> int:
    """Roughly estimate a token count for ``text`` (>= 1 for non-empty text)."""

    if not text:
        return 0
    return max(1, len(text) // _CHARS_PER_TOKEN)


def price_for(provider: str, model: str) -> tuple[float, float]:
    """Return ``(input_per_1k, output_per_1k)`` for a provider/model pair."""

    key = ((provider or "").lower(), (model or "").lower())
    if key in MODEL_PRICES:
        return MODEL_PRICES[key]
    return PROVIDER_DEFAULT_PRICES.get(key[0], UNKNOWN_PRICE)


def estimate_cost(
    provider: str, model: str, input_tokens: int, output_tokens: int
) -> float:
    """Estimate the USD cost of one call from its token counts and price table."""

    input_price, output_price = price_for(provider, model)
    cost = (input_tokens / 1000.0) * input_price
    cost += (output_tokens / 1000.0) * output_price
    return round(cost, 6)


# ---------------------------------------------------------------------------
# Records & summaries
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UsageRecord:
    """A single recorded LLM call."""

    project_id: str
    task_id: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost: float
    timestamp: datetime

    def to_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "task_id": self.task_id,
            "provider": self.provider,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost": self.cost,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class UsageSummary:
    """Accumulated usage for a task, a project, or a day."""

    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    call_count: int = 0

    def add(self, record: UsageRecord) -> None:
        self.input_tokens += record.input_tokens
        self.output_tokens += record.output_tokens
        self.cost = round(self.cost + record.cost, 6)
        self.call_count += 1

    def to_dict(self) -> dict:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost": self.cost,
            "call_count": self.call_count,
        }


@dataclass(frozen=True)
class BudgetStatus:
    """Result of a budget check for a project.

    ``exceeded`` is True when the daily budget or the project budget has been
    crossed. ``kind`` names which one ("daily" / "project"), ``spend`` is the
    offending accumulated spend, and ``limit`` the budget that was crossed.
    """

    exceeded: bool
    kind: str | None = None
    spend: float = 0.0
    limit: float | None = None
    message: str = ""


# ---------------------------------------------------------------------------
# Tracker
# ---------------------------------------------------------------------------


class UsageStore(Protocol):
    """Where usage records are kept durably (plan D20: ``usage_records`` is the source of truth).

    Synchronous on purpose: :meth:`UsageTracker.record` is a plain method that runs on the event
    loop, and the row must be stored before it returns. Writing one small row per LLM call (a
    call takes seconds) is cheap enough to do inline.
    """

    def add(self, record: "UsageRecord") -> None: ...

    def list_records(self, project_id: str | None = None) -> "list[UsageRecord]":
        """Stored records, oldest first."""


class UsageTracker:
    """Record LLM usage and accumulate cost per task, per project, and per day.

    Args:
        daily_budget_usd: Optional ceiling on total spend for a single day.
            ``None`` disables the daily budget guard.
        project_budget_usd: Optional ceiling on total spend for a single
            project. ``None`` disables the per-project budget guard.
        clock: Callable returning "now"; injectable so tests can control which
            day a record lands in. Defaults to :func:`datetime.now`.
        store: Optional durable :class:`UsageStore`. When given, every record is written to
            it *before* :meth:`record` returns (so a ``usage:update`` event is never sent
            for a row the store does not have yet). It is not read automatically: call
            :meth:`load_from` (or :meth:`attach_store`) to rebuild the totals after a restart.
            Without a store nothing changes.
    """

    def __init__(
        self,
        daily_budget_usd: float | None = None,
        project_budget_usd: float | None = None,
        clock: Callable[[], datetime] | None = None,
        store: UsageStore | None = None,
    ) -> None:
        self.daily_budget_usd = daily_budget_usd
        self.project_budget_usd = project_budget_usd
        self._clock = clock or datetime.now
        self._store: UsageStore | None = store

        self._records: list[UsageRecord] = []
        self._by_project: dict[str, UsageSummary] = defaultdict(UsageSummary)
        self._by_task: dict[tuple[str, str], UsageSummary] = defaultdict(UsageSummary)
        self._by_day: dict[date, UsageSummary] = defaultdict(UsageSummary)

    # -- recording -----------------------------------------------------------

    def record(
        self,
        project_id: str,
        task_id: str,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> UsageRecord:
        """Record one LLM call and roll it into every accumulator."""

        now = self._clock()
        record = UsageRecord(
            project_id=project_id,
            task_id=task_id,
            provider=provider,
            model=model,
            input_tokens=max(0, int(input_tokens)),
            output_tokens=max(0, int(output_tokens)),
            cost=estimate_cost(provider, model, input_tokens, output_tokens),
            timestamp=now,
        )
        if self._store is not None:
            try:
                self._store.add(record)
            except Exception:  # a storage hiccup must not fail the station that made the call
                logger.exception(
                    "Could not store usage record for task %s of project %s; it is kept in memory only",
                    task_id, project_id,
                )
        self._accumulate(record)
        return record

    def _accumulate(self, record: UsageRecord) -> None:
        self._records.append(record)
        self._by_project[record.project_id].add(record)
        self._by_task[(record.project_id, record.task_id)].add(record)
        self._by_day[record.timestamp.date()].add(record)

    # -- persistence ---------------------------------------------------------

    def load_from(self, store: UsageStore | None = None) -> int:
        """Rebuild every total from the stored records; returns how many were loaded.

        Replaces whatever is in memory (call it on a fresh tracker at startup). Per-task,
        per-project and per-day sums, and so the budget status, come out as they were before
        the restart. Nothing is written back to the store.
        """

        source = store if store is not None else self._store
        if source is None:
            raise ValueError("load_from needs a store")
        self._records = []
        self._by_project = defaultdict(UsageSummary)
        self._by_task = defaultdict(UsageSummary)
        self._by_day = defaultdict(UsageSummary)
        loaded = source.list_records()
        for record in loaded:
            self._accumulate(record)
        return len(loaded)

    def attach_store(self, store: UsageStore) -> int:
        """Use ``store`` from now on and load its records (see :meth:`load_from`)."""

        self._store = store
        return self.load_from(store)

    # -- queries -------------------------------------------------------------

    def task_usage(self, project_id: str, task_id: str) -> UsageSummary:
        """Accumulated usage for a single task (a fresh copy)."""

        return _copy(self._by_task.get((project_id, task_id)))

    def project_usage(self, project_id: str) -> UsageSummary:
        """Accumulated usage for a single project (a fresh copy)."""

        return _copy(self._by_project.get(project_id))

    def daily_usage(self, day: date | None = None) -> UsageSummary:
        """Accumulated usage for a given day (defaults to today)."""

        target = day or self._clock().date()
        return _copy(self._by_day.get(target))

    def project_cost(self, project_id: str) -> float:
        return self.project_usage(project_id).cost

    def daily_cost(self, day: date | None = None) -> float:
        return self.daily_usage(day).cost

    def records(self) -> list[UsageRecord]:
        """All recorded calls, oldest first."""

        return list(self._records)

    # -- budget --------------------------------------------------------------

    def budget_status(self, project_id: str) -> BudgetStatus:
        """Report whether the daily or per-project budget has been exceeded.

        The daily budget is checked first (it is the broader, system-wide
        guard). A budget of ``None`` disables that guard. Comparison is ``>=``
        so spend that lands exactly on the ceiling counts as exceeded.
        """

        daily_spend = self.daily_cost()
        if self.daily_budget_usd is not None and daily_spend >= self.daily_budget_usd:
            return BudgetStatus(
                exceeded=True,
                kind="daily",
                spend=daily_spend,
                limit=self.daily_budget_usd,
                message=(
                    f"Daily budget exceeded: ${daily_spend:.4f} of "
                    f"${self.daily_budget_usd:.4f}"
                ),
            )

        project_spend = self.project_cost(project_id)
        if (
            self.project_budget_usd is not None
            and project_spend >= self.project_budget_usd
        ):
            return BudgetStatus(
                exceeded=True,
                kind="project",
                spend=project_spend,
                limit=self.project_budget_usd,
                message=(
                    f"Project '{project_id}' budget exceeded: "
                    f"${project_spend:.4f} of ${self.project_budget_usd:.4f}"
                ),
            )

        return BudgetStatus(exceeded=False, spend=project_spend)

    def is_over_budget(self, project_id: str) -> bool:
        """Convenience boolean wrapper around :meth:`budget_status`."""

        return self.budget_status(project_id).exceeded


def _copy(summary: UsageSummary | None) -> UsageSummary:
    """Return a copy of ``summary`` (or an empty one) so callers cannot mutate
    the tracker's internal accumulators."""

    if summary is None:
        return UsageSummary()
    return UsageSummary(
        input_tokens=summary.input_tokens,
        output_tokens=summary.output_tokens,
        cost=summary.cost,
        call_count=summary.call_count,
    )
