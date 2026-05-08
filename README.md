# codexusage

A web dashboard and CLI tool for tracking OpenAI Codex CLI token usage with enterprise credit tracking and billing.

## Features

- **Token Usage Tracking**: Monitor daily and weekly Codex token consumption
- **Credit Pool Management**: Track usage against configurable weekly credit allocations
- **Interactive Dashboard**: Visualize usage patterns over time with an interactive web interface
- **CLI Commands**: Quick access to usage summaries from the terminal
- **Flexible Billing**: Configure custom credit pools and dollar-to-credit conversion rates

## Installation

```bash
pip install .
```

This installs the `codexusage` command globally.

## Quick Start

### 1. Configure the tool

```bash
codexusage config set \
  --sessions-dir ~/.codex/sessions \
  --weekly-pool 2500 \
  --credits-per-dollar 25
```

**Configuration options:**
- `--sessions-dir`: Path to your Codex sessions directory (auto-detected from `~/.codex/sessions` or `$CODEX_HOME/sessions`)
- `--weekly-pool`: Weekly credit pool (default: 2500)
- `--credits-per-dollar`: How many credits per $1 of usage (default: 25)
- `--port`: Dashboard port (default: 8080)

### 2. View today's usage

```bash
codexusage today
```

Shows a summary table with token counts, USD cost, and credit usage for each model.

### 3. View weekly usage

```bash
codexusage week
```

Shows usage for the current billing week (Friday 17:00 reset).

```bash
codexusage week --weeks 4
```

Show the last 4 weeks of usage.

### 4. Start the dashboard

```bash
codexusage dashboard
```

Starts a local web server and opens the interactive dashboard in your browser. The dashboard shows:
- Real-time token and credit usage charts
- Weekly and daily breakdowns
- Model-specific usage patterns
- Credit pool utilization

## Commands

### `codexusage today [--since YYYY-MM-DD] [--until YYYY-MM-DD]`

Print today's token and credit summary, or a custom date range.

**Examples:**
```bash
codexusage today                              # Today's usage
codexusage today --since 2025-01-01           # From Jan 1 to today
codexusage today --since 2025-01-01 --until 2025-02-01  # Custom range
```

### `codexusage week [--weeks N] [--since YYYY-MM-DD] [--until YYYY-MM-DD]`

Print weekly summary (billing week: Friday 17:00 to next Friday 17:00).

**Examples:**
```bash
codexusage week              # Current week
codexusage week --weeks 4    # Last 4 weeks
codexusage week --since 2025-01-01 --until 2025-02-01  # Custom range
```

### `codexusage dashboard [--port PORT] [--no-open]`

Start the interactive dashboard.

**Options:**
- `--port`: Override configured port
- `--no-open`: Don't automatically open in browser

### `codexusage config set [OPTIONS]`

Update configuration.

**Options:**
- `--sessions-dir PATH`: Path to Codex sessions directory
- `--weekly-pool AMOUNT`: Weekly credit pool
- `--credits-per-dollar RATE`: Credit conversion rate
- `--port PORT`: Dashboard port

## Configuration File

Configuration is stored in `~/.config/codexusage/config.json`:

```json
{
  "sessions_dir": "~/.codex/sessions",
  "weekly_pool_credits": 2500,
  "credits_per_dollar": 25,
  "port": 8080
}
```

You can edit this file directly or use `codexusage config set`.

## How It Works

1. **Session Scanning**: The tool scans your Codex sessions directory for usage logs
2. **Token Counting**: Aggregates tokens by model and date
3. **USD Conversion**: Uses current OpenAI pricing to calculate costs
4. **Credit Conversion**: Converts USD cost to credits using your configured rate
5. **Visualization**: Displays usage against your weekly credit pool

## Output Examples

### CLI Summary

```
Codex Usage — today
────────────────────────────────────────────────────────
  Model                Tokens       USD     Credits
  ────────────────────────────────────────────────────────
  gpt-4               234,567    $12.34      308.50 cr
  gpt-3.5-turbo       123,456     $1.23       30.75 cr
  ────────────────────────────────────────────────────────
  Total               357,023    $13.57      339.25 cr

  Pool: [████████░░░░░░░░░░░░░░░░░░] 339.25 / 2500.00 cr (13.6%)
```

### Dashboard

The web dashboard provides:
- Line charts showing token/credit usage over time
- Bar charts comparing models
- Pool utilization gauge
- Filterable date ranges
- Hourly granularity for detailed analysis

## Requirements

- Python 3.9+
- Codex CLI installed with session history
- No external dependencies (pure Python)

## Troubleshooting

### "sessions_dir not found"
Ensure your Codex sessions directory exists and is correctly configured. Default paths are:
- `~/.codex/sessions` (if `CODEX_HOME` not set)
- `$CODEX_HOME/sessions` (if `CODEX_HOME` is set)

### No usage data showing
1. Verify Codex CLI has been used (sessions exist)
2. Check `--sessions-dir` points to the correct directory
3. Try `codexusage config set --sessions-dir /path/to/sessions`

### Dashboard won't open
- Port may be in use. Try: `codexusage dashboard --port 9000`
- Check firewall allows localhost connections

## Project Structure

```
codexusage/
├── src/codexusage/
│   ├── cli.py          # CLI entry point and commands
│   ├── server.py       # Web dashboard server
│   ├── scanner.py      # Session log scanner
│   ├── pricing.py      # Token-to-USD conversion
│   ├── config.py       # Configuration management
│   ├── pricing.json    # OpenAI pricing data
│   └── web/            # Dashboard HTML/CSS/JS
├── scripts/            # Utilities (test data generation)
└── pyproject.toml      # Project metadata
```

## License

Internal tool.
