"""codexusage CLI entry point."""

from __future__ import annotations

import argparse
import sys
import webbrowser
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .cache import scan_all_projects_cached
from .config import (
    add_project,
    add_repo,
    list_projects,
    load_config,
    remove_project,
    remove_repo,
    save_config,
)
from .pricing import load_pricing, tokens_to_usd, usd_to_credits


def _merge_cfg(cfg: dict, args) -> dict:
    merged = dict(cfg)
    if getattr(args, "sessions_dir", None):
        merged["sessions_dir"] = args.sessions_dir
        # Backwards compat: propagate override into the synthesized default project.
        if len(merged["projects"]) == 1 and merged["projects"][0]["name"] == "default":
            merged["projects"] = [{**merged["projects"][0], "sessions_dir": args.sessions_dir}]
    if getattr(args, "weekly_pool", None) is not None:
        merged["weekly_pool_credits"] = args.weekly_pool
    if getattr(args, "credits_per_dollar", None) is not None:
        merged["credits_per_dollar"] = args.credits_per_dollar
    return merged


def _today_range():
    d = date.today()
    return d.isoformat(), (d + timedelta(days=1)).isoformat()


def _user_range(args) -> tuple[str, str] | None:
    """Return (since, until) if user specified explicit dates, else None."""
    since = getattr(args, "since", None)
    until = getattr(args, "until", None)
    if since:
        if not until:
            until = (date.today() + timedelta(days=1)).isoformat()
        return since, until
    return None


def _scoped_events(events: list[dict], since: str, until: str) -> list[dict]:
    has_time = len(since) > 10 or len(until) > 10
    return [
        e for e in events if since <= (e["timestamp"] if has_time else e["timestamp"][:10]) < until
    ]


def _print_model_table(
    events: list[dict], pricing: dict, cpd: float, show_credits: bool
) -> tuple[float, float, float]:
    """Print per-model breakdown. Returns (total_tokens, total_usd, total_credits)."""
    models: dict[str, dict] = {}
    for e in events:
        m = e["model"]
        if m not in models:
            models[m] = {"tokens": 0, "usd": 0.0, "credits": 0.0}
        models[m]["tokens"] += e["total_tokens"]
        usd = tokens_to_usd(m, e, pricing)
        models[m]["usd"] += usd
        models[m]["credits"] += usd_to_credits(usd, cpd)

    total_tokens = sum(v["tokens"] for v in models.values())
    total_usd = sum(v["usd"] for v in models.values())
    total_credits = sum(v["credits"] for v in models.values())

    if show_credits:
        print(f"  {'Model':<22} {'Tokens':>12} {'USD':>9} {'Credits':>10}")
        print(f"  {'-' * 22} {'-' * 12} {'-' * 9} {'-' * 10}")
        for m, v in sorted(models.items(), key=lambda x: -x[1]["credits"]):
            print(f"  {m:<22} {v['tokens']:>12,} ${v['usd']:>8.4f} {v['credits']:>9.2f} cr")
        print(f"  {'-' * 22} {'-' * 12} {'-' * 9} {'-' * 10}")
        print(f"  {'Total':<22} {total_tokens:>12,} ${total_usd:>8.4f} {total_credits:>9.2f} cr")
    else:
        print(f"  {'Model':<22} {'Tokens':>12} {'USD':>9}")
        print(f"  {'-' * 22} {'-' * 12} {'-' * 9}")
        for m, v in sorted(models.items(), key=lambda x: -x[1]["usd"]):
            print(f"  {m:<22} {v['tokens']:>12,} ${v['usd']:>8.4f}")
        print(f"  {'-' * 22} {'-' * 12} {'-' * 9}")
        print(f"  {'Total':<22} {total_tokens:>12,} ${total_usd:>8.4f}")

    return total_tokens, total_usd, total_credits


def _print_summary(label: str, events: list[dict], cfg: dict) -> None:
    pricing = load_pricing()
    cpd = cfg["credits_per_dollar"]
    pool = cfg["weekly_pool_credits"]

    oauth_events = [e for e in events if e.get("auth_type", "oauth") == "oauth"]
    api_token_events = [e for e in events if e.get("auth_type") == "api_token"]

    print(f"\nCodex Usage — {label}")
    print("-" * 60)

    if oauth_events:
        if api_token_events:
            print("\n  OAuth projects\n")

        _, _, total_credits = _print_model_table(oauth_events, pricing, cpd, show_credits=True)
        print()

        pct = total_credits / pool * 100 if pool > 0 else 0.0
        bar_width = 30
        filled = int(bar_width * min(pct / 100, 1.0))
        bar = "█" * filled + "░" * (bar_width - filled)
        print(f"  Pool: [{bar}] {total_credits:.2f} / {pool:.0f} cr ({pct:.1f}%)")
        print()

    if api_token_events:
        if oauth_events:
            print("  API token projects\n")

        # Group by project then model
        proj_model: dict[tuple[str, str], dict] = {}
        for e in api_token_events:
            key = (e.get("project", "default"), e["model"])
            if key not in proj_model:
                proj_model[key] = {"project": key[0], "model": key[1], "tokens": 0, "usd": 0.0}
            proj_model[key]["tokens"] += e["total_tokens"]
            proj_model[key]["usd"] += tokens_to_usd(key[1], e, pricing)

        total_tokens = sum(v["tokens"] for v in proj_model.values())
        total_usd = sum(v["usd"] for v in proj_model.values())

        print(f"  {'Project':<16} {'Model':<22} {'Tokens':>12} {'USD':>9}")
        print(f"  {'-' * 16} {'-' * 22} {'-' * 12} {'-' * 9}")
        for (p, m), v in sorted(proj_model.items(), key=lambda x: -x[1]["usd"]):
            print(f"  {p:<16} {m:<22} {v['tokens']:>12,} ${v['usd']:>8.4f}")
        print(f"  {'-' * 16} {'-' * 22} {'-' * 12} {'-' * 9}")
        print(f"  {'Total':<16} {'':22} {total_tokens:>12,} ${total_usd:>8.4f}")
        print()


def cmd_today(args):
    cfg = _merge_cfg(load_config(), args)
    events = scan_all_projects_cached(cfg["projects"])
    user = _user_range(args)
    since, until = user if user else _today_range()
    label = f"{since} .. {until}" if user else "today"
    _print_summary(label, _scoped_events(events, since, until), cfg)


def cmd_week(args):
    cfg = _merge_cfg(load_config(), args)
    events = scan_all_projects_cached(cfg["projects"])
    user = _user_range(args)
    if user:
        since, until = user
        label = f"{since} .. {until}"
    else:
        now = datetime.now(timezone.utc)
        since = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M")
        until = now.strftime("%Y-%m-%dT%H:%M")
        label = "rolling 7 days"
    _print_summary(label, _scoped_events(events, since, until), cfg)


def cmd_dashboard(args):
    cfg = _merge_cfg(load_config(), args)
    cfg.pop("since", None)
    cfg.pop("until", None)
    port = getattr(args, "port", None) or cfg["port"]
    host = "127.0.0.1"
    url = f"http://{host}:{port}/"

    from .server import run

    print(f"codexusage dashboard → {url}")
    projects = cfg["projects"]
    print(f"  projects ({len(projects)}):")
    for p in projects:
        print(f"    [{p['auth_type']}] {p['name']} → {p['sessions_dir']}")
        for r in p.get("repos", []):
            print(f"      repo: {r}")
    print(f"  pool: {cfg['weekly_pool_credits']} cr @ {cfg['credits_per_dollar']} cr/$")
    print("  Press Ctrl+C to stop.\n")

    if not getattr(args, "no_open", False):
        webbrowser.open(url)

    try:
        run(host, port, cfg)
    except KeyboardInterrupt:
        print("\nStopped.")


def cmd_config_set(args):
    updates: dict = {}
    if args.weekly_pool is not None:
        updates["weekly_pool_credits"] = args.weekly_pool
    if args.credits_per_dollar is not None:
        updates["credits_per_dollar"] = args.credits_per_dollar
    if args.sessions_dir:
        updates["sessions_dir"] = args.sessions_dir
    if args.port is not None:
        updates["port"] = args.port
    if not updates:
        print("Nothing to set. Use --weekly-pool, --credits-per-dollar, --sessions-dir, or --port.")
        sys.exit(1)
    save_config(updates)
    cfg = load_config()
    print("Config saved:")
    print(f"  weekly_pool_credits : {cfg['weekly_pool_credits']}")
    print(f"  credits_per_dollar  : {cfg['credits_per_dollar']}")
    print(f"  sessions_dir        : {cfg['sessions_dir']}")
    print(f"  port                : {cfg['port']}")


def cmd_config_init(args):
    from .config import DEFAULTS, _config_path, _default_sessions_dir

    path = _config_path()
    if path.is_file() and not getattr(args, "force", False):
        print(f"Config already exists at {path}")
        print("Use --force to overwrite, or edit it directly.")
        return
    sessions_dir = _default_sessions_dir()
    save_config(
        {
            "weekly_pool_credits": DEFAULTS["weekly_pool_credits"],
            "credits_per_dollar": DEFAULTS["credits_per_dollar"],
            "sessions_dir": sessions_dir,
            "port": DEFAULTS["port"],
            "projects": [
                {"name": "default", "sessions_dir": sessions_dir, "auth_type": "oauth", "repos": []}
            ],
        }
    )
    print(f"Config written to {path}")
    print(f"  sessions_dir        : {sessions_dir}")
    print(f"  weekly_pool_credits : {DEFAULTS['weekly_pool_credits']}")
    print(f"  credits_per_dollar  : {DEFAULTS['credits_per_dollar']}")
    print(f"  port                : {DEFAULTS['port']}")
    print()
    print("Edit with:  codexusage config set --weekly-pool N --credits-per-dollar N")
    print(
        "Projects:   codexusage config project add --name <name> --home <dir> --auth-type oauth|api_token"
    )


def _print_projects(projects: list[dict]) -> None:
    for p in projects:
        print(f"  {p['name']:<20} [{p['auth_type']}]  {p['sessions_dir']}")
        for r in p.get("repos", []):
            print(f"    {'':20}   repo: {r}")


def cmd_project_add(args):
    sessions_dir = args.sessions_dir
    if getattr(args, "home", None):
        sessions_dir = str(Path(args.home) / "sessions")
    repos: list[str] = getattr(args, "repo", None) or []
    try:
        add_project(args.name, sessions_dir, args.auth_type, repos)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Added project '{args.name}' [{args.auth_type}] → {sessions_dir}")
    _print_projects(list_projects())


def cmd_project_list(args):
    projects = list_projects()
    if not projects:
        print("No projects configured.")
        return
    _print_projects(projects)


def cmd_project_remove(args):
    try:
        remove_project(args.name)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Removed project '{args.name}'.")
    _print_projects(list_projects())


def cmd_project_add_repo(args):
    try:
        add_repo(args.project, args.path)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Added repo '{args.path}' to project '{args.project}'.")
    _print_projects(list_projects())


def cmd_project_remove_repo(args):
    try:
        remove_repo(args.project, args.path)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Removed repo '{args.path}' from project '{args.project}'.")
    _print_projects(list_projects())


def _date_arg(s: str) -> str:
    try:
        date.fromisoformat(s)
        return s
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid date '{s}' — expected YYYY-MM-DD") from None


def main():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--sessions-dir", help="Path to Codex sessions directory")
    common.add_argument("--weekly-pool", type=float, metavar="N", help="Weekly credit pool size")
    common.add_argument("--credits-per-dollar", type=float, metavar="N", help="Credits per USD")
    common.add_argument(
        "--since", type=_date_arg, metavar="YYYY-MM-DD", help="Start date — overrides default range"
    )
    common.add_argument(
        "--until", type=_date_arg, metavar="YYYY-MM-DD", help="End date (exclusive)"
    )

    p = argparse.ArgumentParser(
        prog="codexusage",
        description="Codex CLI token usage dashboard with enterprise credit tracking",
        parents=[common],
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser(
        "today",
        parents=[common],
        add_help=True,
        help="Print today's token/credit summary (or custom range with --since/--until)",
    ).set_defaults(func=cmd_today)

    w = sub.add_parser(
        "week",
        parents=[common],
        add_help=True,
        help="Print rolling 7-day token/credit summary (or custom range with --since/--until)",
    )
    w.set_defaults(func=cmd_week)

    d = sub.add_parser("dashboard", parents=[common], add_help=True, help="Start the web dashboard")
    d.add_argument("--port", type=int, help="Port (default from config, fallback 8080)")
    d.add_argument("--no-open", action="store_true", help="Don't open browser automatically")
    d.set_defaults(func=cmd_dashboard)

    cs = sub.add_parser("config", add_help=True, help="Configure codexusage")
    css = cs.add_subparsers(dest="config_cmd", required=True)

    cinit = css.add_parser("init", help="Write a default config file")
    cinit.add_argument("--force", action="store_true", help="Overwrite existing config")
    cinit.set_defaults(func=cmd_config_init)

    cset = css.add_parser("set", help="Set configuration values")
    cset.add_argument("--weekly-pool", type=float, metavar="N")
    cset.add_argument("--credits-per-dollar", type=float, metavar="N")
    cset.add_argument("--sessions-dir")
    cset.add_argument("--port", type=int)
    cset.set_defaults(func=cmd_config_set)

    cproj = css.add_parser("project", help="Manage projects")
    cproj_sub = cproj.add_subparsers(dest="project_cmd", required=True)

    cadd = cproj_sub.add_parser("add", help="Add a project")
    cadd.add_argument("--name", required=True, help="Project name")
    cadd_dir = cadd.add_mutually_exclusive_group(required=True)
    cadd_dir.add_argument(
        "--home", dest="home", help="Codex home directory (sessions/ appended automatically)"
    )
    cadd_dir.add_argument(
        "--sessions-dir", dest="sessions_dir", help="Path directly to the sessions directory"
    )
    cadd.add_argument(
        "--auth-type",
        required=True,
        choices=["oauth", "api_token"],
        dest="auth_type",
        help="oauth (counts toward credit pool) or api_token (USD billing)",
    )
    cadd.add_argument(
        "--repo",
        action="append",
        metavar="PATH",
        help="Repo path belonging to this project (repeatable)",
    )
    cadd.set_defaults(func=cmd_project_add)

    clist = cproj_sub.add_parser("list", help="List configured projects")
    clist.set_defaults(func=cmd_project_list)

    cremove = cproj_sub.add_parser("remove", help="Remove a project")
    cremove.add_argument("--name", required=True)
    cremove.set_defaults(func=cmd_project_remove)

    cadd_repo = cproj_sub.add_parser("add-repo", help="Add a repo path to a project")
    cadd_repo.add_argument("project", help="Project name")
    cadd_repo.add_argument("path", help="Absolute path to the repo")
    cadd_repo.set_defaults(func=cmd_project_add_repo)

    cremove_repo = cproj_sub.add_parser("remove-repo", help="Remove a repo path from a project")
    cremove_repo.add_argument("project", help="Project name")
    cremove_repo.add_argument("path", help="Repo path to remove")
    cremove_repo.set_defaults(func=cmd_project_remove_repo)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
