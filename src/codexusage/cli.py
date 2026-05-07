"""codexusage CLI entry point."""
from __future__ import annotations

import argparse
import sys
import webbrowser
from datetime import date, timedelta

from .config import load_config, save_config
from .pricing import load_pricing, tokens_to_usd, usd_to_credits
from .scanner import scan_sessions


def _merge_cfg(cfg: dict, args) -> dict:
    merged = dict(cfg)
    if getattr(args, "sessions_dir", None):
        merged["sessions_dir"] = args.sessions_dir
    if getattr(args, "weekly_pool", None) is not None:
        merged["weekly_pool_credits"] = args.weekly_pool
    if getattr(args, "credits_per_dollar", None) is not None:
        merged["credits_per_dollar"] = args.credits_per_dollar
    return merged


def _today_range():
    d = date.today()
    return d.isoformat(), (d + timedelta(days=1)).isoformat()


def _week_range():
    d = date.today()
    monday = d - timedelta(days=d.weekday())
    return monday.isoformat(), (monday + timedelta(days=7)).isoformat()


def _print_summary(label: str, events: list[dict], since: str, until: str, cfg: dict) -> None:
    pricing = load_pricing()
    cpd = cfg["credits_per_dollar"]
    pool = cfg["weekly_pool_credits"]

    filtered = [e for e in events if since <= e["timestamp"][:10] < until]

    models: dict[str, dict] = {}
    for e in filtered:
        m = e["model"]
        if m not in models:
            models[m] = {"tokens": 0, "usd": 0.0, "credits": 0.0}
        models[m]["tokens"] += e["total_tokens"]
        usd = tokens_to_usd(m, e, pricing)
        models[m]["usd"]     += usd
        models[m]["credits"] += usd_to_credits(usd, cpd)

    total_tokens  = sum(v["tokens"]  for v in models.values())
    total_usd     = sum(v["usd"]     for v in models.values())
    total_credits = sum(v["credits"] for v in models.values())
    pct = total_credits / pool * 100 if pool > 0 else 0.0

    print(f"\nCodex Usage — {label}")
    print("-" * 60)
    if models:
        print(f"  {'Model':<22} {'Tokens':>12} {'USD':>9} {'Credits':>10}")
        print(f"  {'-'*22} {'-'*12} {'-'*9} {'-'*10}")
        for m, v in sorted(models.items(), key=lambda x: -x[1]["credits"]):
            print(f"  {m:<22} {v['tokens']:>12,} ${v['usd']:>8.4f} {v['credits']:>9.2f} cr")
        print(f"  {'-'*22} {'-'*12} {'-'*9} {'-'*10}")
    print(f"  {'Total':<22} {total_tokens:>12,} ${total_usd:>8.4f} {total_credits:>9.2f} cr")
    print()

    bar_width = 30
    filled = int(bar_width * min(pct / 100, 1.0))
    bar = "█" * filled + "░" * (bar_width - filled)
    print(f"  Pool: [{bar}] {total_credits:.2f} / {pool:.0f} cr ({pct:.1f}%)")
    print()


def cmd_today(args):
    cfg = _merge_cfg(load_config(), args)
    events = scan_sessions(cfg["sessions_dir"])
    since, until = _today_range()
    _print_summary("today", events, since, until, cfg)


def cmd_week(args):
    cfg = _merge_cfg(load_config(), args)
    events = scan_sessions(cfg["sessions_dir"])
    since, until = _week_range()
    _print_summary(f"week of {since}", events, since, until, cfg)


def cmd_dashboard(args):
    cfg = _merge_cfg(load_config(), args)
    port = getattr(args, "port", None) or cfg["port"]
    host = "127.0.0.1"
    url = f"http://{host}:{port}/"

    from .server import run

    print(f"codexusage dashboard → {url}")
    print(f"  sessions: {cfg['sessions_dir']}")
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


def main():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--sessions-dir", help="Path to Codex sessions directory")
    common.add_argument("--weekly-pool",  type=float, metavar="N", help="Weekly credit pool size")
    common.add_argument("--credits-per-dollar", type=float, metavar="N", help="Credits per USD")

    p = argparse.ArgumentParser(
        prog="codexusage",
        description="Codex CLI token usage dashboard with enterprise credit tracking",
        parents=[common],
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("today",     parents=[common], add_help=True,
                   help="Print today's token/credit summary").set_defaults(func=cmd_today)
    sub.add_parser("week",      parents=[common], add_help=True,
                   help="Print this week's token/credit summary").set_defaults(func=cmd_week)

    d = sub.add_parser("dashboard", parents=[common], add_help=True,
                       help="Start the web dashboard")
    d.add_argument("--port", type=int, help="Port (default from config, fallback 8080)")
    d.add_argument("--no-open", action="store_true", help="Don't open browser automatically")
    d.set_defaults(func=cmd_dashboard)

    cs = sub.add_parser("config", add_help=True, help="Configure codexusage")
    css = cs.add_subparsers(dest="config_cmd", required=True)
    cset = css.add_parser("set", help="Set configuration values")
    cset.add_argument("--weekly-pool",        type=float, metavar="N")
    cset.add_argument("--credits-per-dollar", type=float, metavar="N")
    cset.add_argument("--sessions-dir")
    cset.add_argument("--port",               type=int)
    cset.set_defaults(func=cmd_config_set)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
