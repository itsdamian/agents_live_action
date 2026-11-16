"""Claude 5 prices in the usage tracker (A5, D11). Prices are USD per 1k tokens."""

from __future__ import annotations

import pytest

from core.usage_tracker import (
    MODEL_PRICES,
    PROVIDER_DEFAULT_PRICES,
    UsageTracker,
    estimate_cost,
    price_for,
)

NEW_PRICES = {
    "claude-opus-5-5": (0.004, 0.020),
    "claude-sonnet-5-5": (0.002, 0.010),
    "claude-haiku-5-5": (0.0001, 0.0005),
    "claude-fable-5-2": (0.008, 0.040),
}


@pytest.mark.parametrize("model, expected", sorted(NEW_PRICES.items()))
def test_new_model_prices(model, expected):
    assert price_for("anthropic", model) == expected
    assert MODEL_PRICES[("anthropic", model)] == expected


def test_lookup_is_case_insensitive():
    assert price_for("Anthropic", "Claude-Opus-5-5") == (0.004, 0.020)


def test_anthropic_provider_default_is_the_opus_price():
    assert PROVIDER_DEFAULT_PRICES["anthropic"] == NEW_PRICES["claude-opus-5-5"]
    assert price_for("anthropic", "claude-some-future-model") == NEW_PRICES["claude-opus-5-5"]


def test_existing_rows_are_kept():
    assert MODEL_PRICES[("anthropic", "claude-3-5-sonnet")] == (0.0030, 0.0150)
    assert MODEL_PRICES[("anthropic", "claude-3-opus")] == (0.0150, 0.0750)
    assert MODEL_PRICES[("anthropic", "claude-3-haiku")] == (0.00025, 0.00125)
    assert MODEL_PRICES[("openai", "gpt-4o")] == (0.0050, 0.0150)
    assert MODEL_PRICES[("mock", "mock-model")] == (0.0, 0.0)


@pytest.mark.parametrize(
    "model, input_tokens, output_tokens, expected",
    [
        ("claude-opus-5-5", 1000, 1000, 0.024),
        ("claude-opus-5-5", 2500, 400, 0.018),
        ("claude-sonnet-5-5", 1000, 1000, 0.012),
        ("claude-haiku-5-5", 10_000, 2_000, 0.002),
        ("claude-fable-5-2", 500, 500, 0.024),
    ],
)
def test_estimated_cost(model, input_tokens, output_tokens, expected):
    assert estimate_cost("anthropic", model, input_tokens, output_tokens) == pytest.approx(expected)


def test_budget_guard_sees_the_new_prices():
    tracker = UsageTracker(project_budget_usd=0.02)

    tracker.record("proj", "task-1", "anthropic", "claude-opus-5-5", 1000, 1000)  # $0.024

    assert tracker.budget_status("proj").exceeded is True
    assert tracker.project_usage("proj").cost == pytest.approx(0.024)
