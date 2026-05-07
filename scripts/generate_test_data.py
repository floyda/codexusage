"""Generate synthetic Codex session JSONL files for testing.

Usage:
    python3 scripts/generate_test_data.py [--sessions-dir PATH]

Writes sessions for the current week into the target directory.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

MODELS = [
    ("gpt-5",       0.70),
    ("gpt-4o",      0.20),
    ("gpt-4o-mini", 0.10),
]

PROJECTS = [
    "myapp/backend",
    "myapp/frontend",
    "infra/k8s",
    "data/pipeline",
    "scripts/utils",
]

random.seed(42)


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def turn_context(dt: datetime, model: str) -> dict:
    return {
        "timestamp": iso(dt),
        "type": "turn_context",
        "payload": {"model": model},
    }


def token_count_event(dt: datetime, model: str, last: dict) -> dict:
    return {
        "timestamp": iso(dt),
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {
                "model": model,
                "last_token_usage": last,
            },
        },
    }


def make_session(project: str, start: datetime, model: str, turns: int) -> list[dict]:
    lines: list[dict] = [turn_context(start, model)]
    t = start + timedelta(seconds=2)
    for _ in range(turns):
        inp = random.randint(2_000, 80_000)
        cached = random.randint(0, inp // 2)
        out = random.randint(200, 4_000)
        reasoning = random.randint(0, out // 4)
        lines.append(token_count_event(t, model, {
            "input_tokens": inp,
            "cached_input_tokens": cached,
            "output_tokens": out,
            "reasoning_output_tokens": reasoning,
            "total_tokens": inp + out,
        }))
        t += timedelta(seconds=random.randint(8, 90))
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions-dir", default=str(Path.home() / ".codex" / "test-sessions"))
    ap.add_argument("--days", type=int, default=7)
    args = ap.parse_args()

    root = Path(args.sessions_dir)
    root.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today - timedelta(days=today.weekday())

    written = 0
    for day_offset in range(args.days):
        day = week_start + timedelta(days=day_offset)
        if day > now:
            break
        sessions_today = random.randint(2, 6)
        for _ in range(sessions_today):
            model, _ = random.choices(MODELS, weights=[w for _, w in MODELS])[0], None
            model = random.choices([m for m, _ in MODELS], weights=[w for _, w in MODELS])[0]
            project = random.choice(PROJECTS)
            hour = random.randint(8, 22)
            minute = random.randint(0, 59)
            start = day.replace(hour=hour, minute=minute)
            turns = random.randint(3, 20)
            lines = make_session(project, start, model, turns)

            # Filename mirrors Codex CLI naming pattern
            slug = project.replace("/", "-")
            ts_str = start.strftime("%Y-%m-%dT%H-%M-%S")
            fname = f"{slug}-{ts_str}.jsonl"
            subdir = root / start.strftime("%Y/%m/%d")
            subdir.mkdir(parents=True, exist_ok=True)
            path = subdir / fname
            path.write_text(
                "\n".join(json.dumps(l) for l in lines) + "\n",
                encoding="utf-8",
            )
            written += 1

    print(f"Wrote {written} synthetic session files to {root}")
    print(f"Test with:  codexusage week --sessions-dir {root}")
    print(f"Dashboard:  codexusage dashboard --sessions-dir {root}")


if __name__ == "__main__":
    main()
