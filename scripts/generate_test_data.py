"""Generate synthetic Codex session JSONL files for testing multi-project setup.

Creates two test projects:
  - oauth-work    → OAuth billing (credits + pool)
  - api-client    → API token billing (USD only)

Usage:
    python3 scripts/generate_test_data.py [--base-dir PATH] [--days N] [--register]

    --register  also adds both projects to ~/.config/codexusage/config.json
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Models and their effort level applicability
OAUTH_MODELS = [
    ("gpt-5",       0.60),
    ("gpt-4.1",     0.25),
    ("gpt-4o",      0.15),
]

API_TOKEN_MODELS = [
    ("o3",          0.50),
    ("o3-mini",     0.30),
    ("gpt-4.1",     0.20),
]

# Reasoning effort levels apply to o-series models
EFFORT_LEVELS = ["low", "medium", "high", "xhigh"]
EFFORT_WEIGHTS = [0.15, 0.35, 0.35, 0.15]

CODEX_PROJECTS = [
    "myapp/backend",
    "myapp/frontend",
    "infra/k8s",
    "data/pipeline",
    "scripts/utils",
    "api/gateway",
    "auth/service",
]

CODEX_CWDS = [
    "/home/user/workspace/myapp",
    "/home/user/workspace/infra",
    "/home/user/workspace/data-pipeline",
    "/home/user/workspace/api-gateway",
]

random.seed(42)


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def is_reasoning_model(model: str) -> bool:
    return model.startswith("o") and model[1:2].isdigit()


def session_meta(dt: datetime, session_id: str, cwd: str) -> dict:
    return {
        "timestamp": iso(dt),
        "type": "session_meta",
        "payload": {
            "id": session_id,
            "timestamp": iso(dt),
            "cwd": cwd,
            "originator": "codex-tui",
            "model_provider": "openai",
        },
    }


def turn_context(dt: datetime, model: str, effort: str | None) -> dict:
    payload: dict = {"model": model}
    if effort:
        payload["reasoning_effort"] = effort
    return {
        "timestamp": iso(dt),
        "type": "turn_context",
        "payload": payload,
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


def make_session(project: str, start: datetime, model: str, effort: str | None, turns: int) -> list[dict]:
    import uuid
    cwd = random.choice(CODEX_CWDS)
    session_id = str(uuid.uuid4())
    lines: list[dict] = [session_meta(start, session_id, cwd), turn_context(start, model, effort)]
    t = start + timedelta(seconds=2)
    for _ in range(turns):
        inp    = random.randint(2_000, 80_000)
        cached = random.randint(0, inp // 2)
        out    = random.randint(200, 4_000)
        # Reasoning models produce more reasoning tokens
        reasoning = random.randint(out // 4, out) if is_reasoning_model(model) else random.randint(0, out // 8)
        lines.append(token_count_event(t, model, {
            "input_tokens":            inp,
            "cached_input_tokens":     cached,
            "output_tokens":           out,
            "reasoning_output_tokens": reasoning,
            "total_tokens":            inp + out,
        }))
        t += timedelta(seconds=random.randint(8, 90))
    return lines


def write_sessions(sessions_dir: Path, days: int, model_weights: list[tuple[str, float]], high_volume: bool) -> int:
    sessions_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    # Start from (days) days ago so data spans the requested window
    start_day = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)

    written = 0
    models  = [m for m, _ in model_weights]
    weights = [w for _, w in model_weights]

    for day_offset in range(days):
        day = start_day + timedelta(days=day_offset)
        if day > now:
            break
        sessions_today = random.randint(4, 10) if high_volume else random.randint(1, 4)
        for _ in range(sessions_today):
            model   = random.choices(models, weights=weights)[0]
            effort  = random.choices(EFFORT_LEVELS, weights=EFFORT_WEIGHTS)[0] if is_reasoning_model(model) else None
            project = random.choice(CODEX_PROJECTS)
            hour    = random.randint(8, 22)
            minute  = random.randint(0, 59)
            start   = day.replace(hour=hour, minute=minute)
            turns   = random.randint(3, 20)
            lines   = make_session(project, start, model, effort, turns)

            slug    = project.replace("/", "-")
            ts_str  = start.strftime("%Y-%m-%dT%H-%M-%S")
            subdir  = sessions_dir / start.strftime("%Y/%m/%d")
            subdir.mkdir(parents=True, exist_ok=True)
            (subdir / f"{slug}-{ts_str}.jsonl").write_text(
                "\n".join(json.dumps(line) for line in lines) + "\n",
                encoding="utf-8",
            )
            written += 1

    return written


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-dir", default=str(Path.home() / ".codex" / "test-data"),
                    help="Parent directory for test project sessions (default: ~/.codex/test-data)")
    ap.add_argument("--days", type=int, default=14, help="Number of days of history to generate (default: 14)")
    ap.add_argument("--register", action="store_true",
                    help="Register projects in ~/.config/codexusage/config.json")
    args = ap.parse_args()

    base = Path(args.base_dir)

    projects = [
        {
            "name":         "oauth-work",
            "sessions_dir": str(base / "oauth-work" / "sessions"),
            "auth_type":    "oauth",
            "model_weights": OAUTH_MODELS,
            "high_volume":  True,
        },
        {
            "name":         "api-client",
            "sessions_dir": str(base / "api-client" / "sessions"),
            "auth_type":    "api_token",
            "model_weights": API_TOKEN_MODELS,
            "high_volume":  False,
        },
    ]

    print(f"Generating {args.days} days of synthetic data under {base}\n")
    for proj in projects:
        n = write_sessions(
            Path(proj["sessions_dir"]),
            args.days,
            proj["model_weights"],
            proj["high_volume"],
        )
        print(f"  [{proj['auth_type']:<9}] {proj['name']:<14}  {n:>3} sessions → {proj['sessions_dir']}")

    if args.register:
        _register(projects)
    else:
        print()
        print("To register these projects, re-run with --register")
        print("or add them manually:")
        for proj in projects:
            print(f"  codexusage config project add --name {proj['name']} "
                  f"--sessions-dir {proj['sessions_dir']} --auth-type {proj['auth_type']}")

    print()
    print("Test CLI:")
    print(f"  codexusage week --since {(datetime.now() - timedelta(days=args.days)).date()}")
    print("Test dashboard:")
    print("  codexusage dashboard")


def _register(projects: list[dict]) -> None:
    # Inline import to keep the script runnable without installing the package
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
        from codexusage.config import _read_raw, _config_path, save_config
    except ImportError:
        print("\nCould not import codexusage — run from the repo root with the venv active.")
        print("Register manually with:  codexusage config project add ...")
        return

    stored   = _read_raw()
    existing = {p["name"]: p for p in stored.get("projects", [])}
    added, skipped = [], []

    for proj in projects:
        entry = {"name": proj["name"], "sessions_dir": proj["sessions_dir"], "auth_type": proj["auth_type"]}
        if proj["name"] in existing:
            existing[proj["name"]] = entry   # update in place (sessions_dir may have changed)
            skipped.append(proj["name"])
        else:
            existing[proj["name"]] = entry
            added.append(proj["name"])

    save_config({"projects": list(existing.values())})
    cfg_path = _config_path()

    print(f"\nConfig updated at {cfg_path}")
    if added:
        print(f"  Added:   {', '.join(added)}")
    if skipped:
        print(f"  Updated: {', '.join(skipped)}")


if __name__ == "__main__":
    main()
