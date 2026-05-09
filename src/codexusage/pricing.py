"""Token → USD → credits conversion using bundled pricing.json."""

from __future__ import annotations

import json
from importlib.resources import files


def load_pricing() -> dict:
    data = files("codexusage").joinpath("pricing.json").read_text(encoding="utf-8")
    return json.loads(data)


def _rates_for(model: str, pricing: dict) -> dict | None:
    m = (model or "").lower().strip()
    # Strip common provider prefixes Codex may include
    for prefix in ("openai/", "azure/openai/", "openrouter/openai/", "openrouter/"):
        if m.startswith(prefix):
            m = m[len(prefix) :]
            break

    exact = pricing["models"].get(m)
    if exact:
        return exact

    for entry in pricing.get("prefix_fallback", []):
        if m.startswith(entry["prefix"].lower()):
            return entry

    return pricing.get("default")


def tokens_to_usd(model: str, event: dict, pricing: dict) -> float:
    rates = _rates_for(model, pricing)
    if rates is None:
        return 0.0
    M = 1_000_000
    input_tokens = event.get("input_tokens", 0)
    cached_tokens = event.get("cached_input_tokens", 0)
    # Codex reports input_tokens as the total (cached is a subset). Bill the
    # non-cached portion at the input rate and the cached portion at the
    # discounted cached rate, mirroring ccusage's calculateCostUSD.
    non_cached = max(input_tokens - cached_tokens, 0)
    cached = min(cached_tokens, input_tokens)
    return (
        non_cached * rates["input"]
        + cached * rates["cached_input"]
        + event.get("output_tokens", 0) * rates["output"]
    ) / M


def usd_to_credits(usd: float, credits_per_dollar: float) -> float:
    return usd * credits_per_dollar
