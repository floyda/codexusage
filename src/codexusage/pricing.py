"""Token → USD → credits conversion using bundled pricing.json."""

from __future__ import annotations

import json
import time
import urllib.request
from importlib.resources import files
from pathlib import Path

LITELLM_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
)
_CACHE_TTL = 86_400  # 24 hours


def _cache_path() -> Path:
    return Path.home() / ".config" / "codexusage" / "pricing_cache.json"


def _load_bundled() -> dict:
    data = files("codexusage").joinpath("pricing.json").read_text(encoding="utf-8")
    return json.loads(data)


def _convert_litellm(raw: dict) -> dict[str, dict]:
    """Convert LiteLLM per-token costs to our per-million format."""
    M = 1_000_000
    models: dict[str, dict] = {}
    for name, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        input_rate = entry.get("input_cost_per_token")
        output_rate = entry.get("output_cost_per_token")
        if input_rate is None or output_rate is None:
            continue
        # Fall back to 50 % of input rate when no explicit cached price exists
        cached_rate = entry.get("cache_read_input_token_cost", input_rate * 0.5)
        models[name] = {
            "input": round(float(input_rate) * M, 6),
            "cached_input": round(float(cached_rate) * M, 6),
            "output": round(float(output_rate) * M, 6),
        }
    return models


def _fetch_and_build() -> dict | None:
    """Fetch LiteLLM pricing and merge with bundled fallback. Returns None on failure."""
    try:
        with urllib.request.urlopen(LITELLM_URL, timeout=5) as resp:  # noqa: S310
            raw: dict = json.loads(resp.read().decode())
    except Exception:
        return None

    bundled = _load_bundled()
    live_models = _convert_litellm(raw)
    # Live pricing takes precedence; bundled fills any gaps
    merged_models = {**bundled["models"], **live_models}

    return {
        "models": merged_models,
        "prefix_fallback": bundled["prefix_fallback"],
        "default": bundled["default"],
        "_fetched_at": time.time(),
    }


def load_pricing() -> dict:
    cache = _cache_path()

    if cache.exists():
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
            if time.time() - cached.get("_fetched_at", 0) < _CACHE_TTL:
                return cached
        except Exception:
            pass

    result = _fetch_and_build()
    if result is not None:
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(result), encoding="utf-8")
        except Exception:
            pass
        return result

    return _load_bundled()


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
