# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Common Commands

```bash
uv sync --group dev          # install all deps including dev tools into .venv
uv run codexusage --help     # run CLI from the local checkout

uv run pytest                # run all tests
uv run pytest tests/test_pricing.py::TestTokensToUsd  # run a single class
uv run pytest -k "test_cached"  # run tests matching a keyword

uv run ruff check src tests  # lint
uv run ruff format src tests # format
uv run mypy src/codexusage   # type-check
```

All four gates must pass — the CI workflow (`.github/workflows/ci.yml`) runs them on every push.

## Architecture

The tool is **stateless**: every CLI invocation and every HTTP request re-scans the session files from disk. There is no database or daemon.

### Data flow

```
~/.codex/sessions/**/*.jsonl
        │
        ▼
scanner.py  →  list[event dict]  →  server.py (_aggregate)  →  JSON API
                                 →  cli.py (_print_summary)  →  terminal
```

### Key modules

**`scanner.py`** — pure parsing, no I/O side effects beyond reading files. `scan_sessions(dir)` walks `*.jsonl` files and emits one dict per `token_count` event. `scan_all_projects(projects)` wraps it for multi-project configs, tagging each event with `project` and `auth_type`. Events carry: `timestamp`, `model`, `input_tokens`, `cached_input_tokens`, `output_tokens`, `reasoning_output_tokens`, `total_tokens`, `reasoning_effort`.

**`pricing.py`** — converts a single event dict to USD via `tokens_to_usd(model, event, pricing)`. Cached tokens are billed as a subset of `input_tokens` (not additive). `_rates_for` strips provider prefixes (`openai/`, `azure/openai/`, etc.) then tries exact match → prefix fallback → default.

**`server.py`** — `build_handler(cfg)` returns a `BaseHTTPRequestHandler` subclass closed over the config and a loaded pricing table. `_aggregate(events, since, until, pricing, cfg)` does all the grouping (per-day, per-model, per-session, per-project, per-effort-level) in one pass and is the single source of truth for the JSON API. Billing week boundaries use Friday 17:00 as the reset point.

**`config.py`** — reads/writes `~/.config/codexusage/config.json`. `load_config()` always returns a fully-merged dict with defaults; it synthesises a single `"default"` OAuth project from `sessions_dir` if no `projects` list is stored.

**`cli.py`** — thin argparse wrapper. All `cmd_*` functions call `load_config()`, scan, then delegate to `_print_summary` or `_aggregate` helpers.

### Auth types

Projects are tagged `auth_type: oauth | api_token`. OAuth events are converted to credits and tracked against the weekly pool; API-token events are shown in USD only. The distinction flows through from scanner → aggregate → both display paths.

### Billing week

The week resets on **Friday at 17:00** (local time). `_week_bounds()` in `server.py` and the equivalent logic in `cli.py::cmd_week` both implement this. On Friday before 17:00, the previous Friday is used as the start.

### Pricing data

`pricing.json` is a package resource (loaded via `importlib.resources.files`). It contains per-model rates (USD per million tokens) with `input`, `cached_input`, and `output` keys, plus a `prefix_fallback` list and a `default` entry.

### Web frontend

Static files in `src/codexusage/web/` (`index.html`, `style.css`, `app.js`, bundled `echarts.min.js`) are served directly from the package via `importlib.resources`. The frontend calls `/api/week` and `/api/today` with optional `?since=` and `?until=` query params.
