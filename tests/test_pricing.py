"""Tests for pricing.py token → USD → credits conversion."""

from __future__ import annotations

import pytest

from codexusage.pricing import (
    _load_bundled,
    _rates_for,
    tokens_to_usd,
    tokens_to_usd_breakdown,
    usd_to_credits,
)


@pytest.fixture(scope="module")
def pricing() -> dict:
    # Use the bundled snapshot so tests are deterministic regardless of network
    return _load_bundled()


def _event(input: int = 0, cached: int = 0, output: int = 0) -> dict:
    return {
        "input_tokens": input,
        "cached_input_tokens": cached,
        "output_tokens": output,
        "total_tokens": input + output,
    }


class TestRatesFor:
    def test_exact_match(self, pricing: dict) -> None:
        rates = _rates_for("gpt-4o", pricing)
        assert rates is not None
        assert rates["input"] == 2.50

    def test_case_insensitive(self, pricing: dict) -> None:
        assert _rates_for("GPT-4O", pricing) == _rates_for("gpt-4o", pricing)

    def test_provider_prefix_stripped_openai(self, pricing: dict) -> None:
        assert _rates_for("openai/gpt-4o", pricing) == _rates_for("gpt-4o", pricing)

    def test_provider_prefix_stripped_azure(self, pricing: dict) -> None:
        assert _rates_for("azure/openai/gpt-4o", pricing) == _rates_for("gpt-4o", pricing)

    def test_prefix_fallback_versioned_model(self, pricing: dict) -> None:
        # "gpt-4o-2024-08-06" should hit the gpt-4o prefix fallback
        rates = _rates_for("gpt-4o-2024-08-06", pricing)
        assert rates is not None
        assert rates["input"] == 2.50

    def test_unknown_model_uses_default(self, pricing: dict) -> None:
        rates = _rates_for("completely-unknown-model-xyz", pricing)
        assert rates is not None
        assert rates == pricing["default"]

    def test_empty_string_uses_default(self, pricing: dict) -> None:
        rates = _rates_for("", pricing)
        assert rates is not None


class TestTokensToUsd:
    def test_output_only(self, pricing: dict) -> None:
        # gpt-5: output = $10/M  →  1_000_000 tokens = $10.00
        usd = tokens_to_usd("gpt-5", _event(output=1_000_000), pricing)
        assert usd == pytest.approx(10.00)

    def test_input_only(self, pricing: dict) -> None:
        # gpt-5: input = $2.50/M  →  1_000_000 non-cached = $2.50
        usd = tokens_to_usd("gpt-5", _event(input=1_000_000), pricing)
        assert usd == pytest.approx(2.50)

    def test_cached_tokens_cheaper(self, pricing: dict) -> None:
        # gpt-5: input $2.50/M, cached_input $0.625/M
        # 500k non-cached + 500k cached  = 0.5*2.50 + 0.5*0.625 = 1.25 + 0.3125 = 1.5625
        usd = tokens_to_usd("gpt-5", _event(input=1_000_000, cached=500_000), pricing)
        assert usd == pytest.approx(1.5625)

    def test_cached_cannot_exceed_input(self, pricing: dict) -> None:
        # If cached > input, clamp cached to input (no negative non-cached)
        usd_normal = tokens_to_usd("gpt-5", _event(input=100, cached=100), pricing)
        usd_over = tokens_to_usd("gpt-5", _event(input=100, cached=200), pricing)
        assert usd_over == pytest.approx(usd_normal)

    def test_zero_tokens_is_zero(self, pricing: dict) -> None:
        assert tokens_to_usd("gpt-5", _event(), pricing) == 0.0

    def test_o3_is_more_expensive_than_gpt4o(self, pricing: dict) -> None:
        ev = _event(input=100_000, output=100_000)
        assert tokens_to_usd("o3", ev, pricing) > tokens_to_usd("gpt-4o", ev, pricing)


class TestTokensToUsdBreakdown:
    def test_output_only(self, pricing: dict) -> None:
        b = tokens_to_usd_breakdown("gpt-5", _event(output=1_000_000), pricing)
        assert b["input_usd"] == 0.0
        assert b["cached_usd"] == 0.0
        assert b["output_usd"] == pytest.approx(tokens_to_usd("gpt-5", _event(output=1_000_000), pricing))

    def test_input_only(self, pricing: dict) -> None:
        b = tokens_to_usd_breakdown("gpt-5", _event(input=1_000_000), pricing)
        assert b["cached_usd"] == 0.0
        assert b["output_usd"] == 0.0
        assert b["input_usd"] == pytest.approx(tokens_to_usd("gpt-5", _event(input=1_000_000), pricing))

    def test_cached_subset_of_input(self, pricing: dict) -> None:
        b = tokens_to_usd_breakdown("gpt-5", _event(input=1_000_000, cached=500_000), pricing)
        assert b["input_usd"] > 0
        assert b["cached_usd"] > 0
        assert b["cached_usd"] < b["input_usd"]  # cached rate is cheaper
        total = b["input_usd"] + b["cached_usd"] + b["output_usd"]
        assert total == pytest.approx(tokens_to_usd("gpt-5", _event(input=1_000_000, cached=500_000), pricing))

    def test_parts_sum_to_total(self, pricing: dict) -> None:
        ev = _event(input=100_000, cached=30_000, output=20_000)
        b = tokens_to_usd_breakdown("gpt-5", ev, pricing)
        total = b["input_usd"] + b["cached_usd"] + b["output_usd"]
        assert total == pytest.approx(tokens_to_usd("gpt-5", ev, pricing))

    def test_zero_event(self, pricing: dict) -> None:
        b = tokens_to_usd_breakdown("gpt-5", _event(), pricing)
        assert b == {"input_usd": 0.0, "cached_usd": 0.0, "output_usd": 0.0}


class TestUsdToCredits:
    def test_basic_conversion(self) -> None:
        assert usd_to_credits(1.0, 25.0) == pytest.approx(25.0)

    def test_zero_usd(self) -> None:
        assert usd_to_credits(0.0, 25.0) == 0.0

    def test_fractional(self) -> None:
        assert usd_to_credits(0.5, 10.0) == pytest.approx(5.0)
