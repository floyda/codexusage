# codexusage

A web dashboard and CLI tool for tracking OpenAI Codex CLI token usage with enterprise credit tracking and billing.

## Features

- **Token Usage Tracking**: Monitor daily and weekly Codex token consumption
- **Credit Pool Management**: Track usage against configurable weekly credit allocations
- **Interactive Dashboard**: Visualize usage patterns over time with an interactive web interface
- **CLI Commands**: Quick access to usage summaries from the terminal
- **Multi-Project Support**: Group multiple git repos under one project; track OAuth and API-token projects separately with different billing modes

## Screenshots

### CLI Summary

```
codexusage week

Codex Usage — week of 2025-05-02
------------------------------------------------------------
  Model                      Tokens           USD    Credits
  ---------------------- ------------ --------- ----------
  gpt-5                     234,567    $0.7248     18.12 cr
  o4-mini                    98,432    $0.2184      5.46 cr
  ---------------------- ------------ --------- ----------
  Total                     333,099    $0.9432     23.58 cr

  Pool: [██░░░░░░░░░░░░░░░░░░░░░░░░░░░░] 23.58 / 2500 cr (0.9%)
```

### Web Dashboard

<!-- TODO: add screenshot of the web dashboard (codexusage dashboard) -->
> Run `codexusage dashboard` and take a screenshot, then replace this line with `![Dashboard](docs/screenshot-dashboard.png)`.

## Installation

### pipx (recommended for a global CLI tool)

```bash
pipx install git+https://github.com/floyda/codexusage.git
```

### uv

```bash
uv tool install git+https://github.com/floyda/codexusage.git
```

### pip

```bash
pip install git+https://github.com/floyda/codexusage.git
```

## Quick Start

### 1. Initialise config

```bash
codexusage config init
```

This writes `~/.config/codexusage/config.json` with auto-detected defaults. Then
customise as needed:

```bash
codexusage config set --weekly-pool 2500 --credits-per-dollar 25
```

### 2. View today's usage

```bash
codexusage today
```

### 3. View weekly usage

```bash
codexusage week          # current billing week (Fri 17:00 → Fri 17:00)
codexusage week --weeks 4   # last 4 weeks
```

### 4. Start the dashboard

```bash
codexusage dashboard
```

Opens `http://localhost:8080` automatically. Press `Ctrl-C` to stop.

## Commands

### `codexusage today [--since YYYY-MM-DD] [--until YYYY-MM-DD]`

Print today's token and credit summary, or a custom date range.

```bash
codexusage today
codexusage today --since 2025-01-01 --until 2025-02-01
```

### `codexusage week [--weeks N] [--since YYYY-MM-DD] [--until YYYY-MM-DD]`

Print weekly summary. Billing week resets every Friday at 17:00.

```bash
codexusage week
codexusage week --weeks 4
codexusage week --since 2025-01-01 --until 2025-02-01
```

### `codexusage dashboard [--port PORT] [--no-open]`

Start the interactive web dashboard.

| Option | Default | Description |
|--------|---------|-------------|
| `--port` | 8080 | Override the configured port |
| `--no-open` | false | Don't open the browser automatically |

### `codexusage config init [--force]`

Write a default config file to `~/.config/codexusage/config.json`.

### `codexusage config set [OPTIONS]`

| Option | Description |
|--------|-------------|
| `--sessions-dir PATH` | Path to Codex sessions directory |
| `--weekly-pool N` | Weekly credit pool size |
| `--credits-per-dollar N` | Credits per $1 of token spend |
| `--port N` | Dashboard port |

### `codexusage config project add/list/remove/add-repo/remove-repo`

A project covers one CODEX_HOME (sessions directory) and one or more git repo checkouts. Events are assigned to a project by matching the session's working directory against the project's declared repos (prefix match). When multiple projects share the same sessions directory, repos are used to route events to the right project.

```bash
# OAuth project with two repos sharing one CODEX_HOME
codexusage config project add \
  --name work \
  --home ~/.codex \
  --auth-type oauth \
  --repo ~/code/repo_a \
  --repo ~/code/repo_b

# API-token project (billed in USD, shown separately)
codexusage config project add \
  --name personal \
  --home ~/personal/.codex \
  --auth-type api_token

# Add / remove a repo from an existing project
codexusage config project add-repo work ~/code/repo_c
codexusage config project remove-repo work ~/code/repo_b

codexusage config project list
codexusage config project remove --name personal
```

## Configuration File

`~/.config/codexusage/config.json`:

```json
{
  "weekly_pool_credits": 2500,
  "credits_per_dollar": 25,
  "sessions_dir": "~/.codex/sessions",
  "port": 8080,
  "projects": [
    {
      "name": "work",
      "sessions_dir": "~/.codex/sessions",
      "auth_type": "oauth",
      "repos": ["/home/user/code/repo_a", "/home/user/code/repo_b"]
    }
  ]
}
```

## Development

Requires Python 3.10+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/floyda/codexusage.git
cd codexusage

uv sync --group dev        # create .venv and install all deps
uv run codexusage --help   # run from the local checkout

uv run pytest              # tests
uv run ruff check src tests   # lint
uv run ruff format src tests  # format
uv run mypy src/codexusage    # type-check
```

CI runs lint + type-check + tests on every push via GitHub Actions (`.github/workflows/ci.yml`).

## How It Works

1. **Session Scanning**: Parses Codex JSONL session logs in `sessions_dir`
2. **Token Counting**: Aggregates tokens by model, day, and project
3. **USD Conversion**: Uses bundled OpenAI pricing (`pricing.json`) to calculate cost
4. **Credit Conversion**: Converts USD → credits using your configured `credits_per_dollar` rate
5. **Visualization**: Dashboard renders per-day charts, model breakdowns, and pool utilization

## Troubleshooting

**`sessions_dir not found`** — Check the path:
```bash
codexusage config set --sessions-dir ~/.codex/sessions
```

**No usage data showing** — Verify Codex CLI has been used and session files exist:
```bash
ls ~/.codex/sessions/
```

**Dashboard port in use** — Use a different port:
```bash
codexusage dashboard --port 9090
```

## Requirements

- Python 3.10+
- Codex CLI installed and used at least once
- No external Python dependencies (stdlib only)

## Project Structure

```
codexusage/
├── src/codexusage/
│   ├── cli.py          # CLI entry point and argument parsing
│   ├── server.py       # HTTP server for the web dashboard
│   ├── scanner.py      # JSONL session log parser
│   ├── pricing.py      # Token → USD conversion
│   ├── config.py       # Config file management
│   ├── pricing.json    # Bundled OpenAI pricing data
│   └── web/            # Dashboard HTML/CSS/JS (ECharts)
├── tests/              # pytest test suite
├── scripts/            # Dev utilities (test data generation)
├── .github/workflows/  # CI (lint, type-check, test)
└── pyproject.toml      # Build metadata and tool config
```

## License

MIT
