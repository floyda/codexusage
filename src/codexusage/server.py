"""Local HTTP server: static web UI + JSON API. Stateless — scans fresh per request."""

from __future__ import annotations

import http.server
import json
import mimetypes
import re
from datetime import date, datetime, timedelta
from importlib.resources import files
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from .cache import scan_all_projects_cached
from .pricing import load_pricing, tokens_to_usd, tokens_to_usd_breakdown, usd_to_credits

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2})?$")
_LONDON_TZ = ZoneInfo("Europe/London")


def _week_bounds(now: datetime | None = None) -> tuple[str, str]:
    """Return (since, until) for the current Fri-17:00 → Fri-17:00 billing week (London time)."""
    dt = now if now is not None else datetime.now(_LONDON_TZ)
    # weekday(): Mon=0 … Fri=4 … Sun=6  →  days since last Friday
    days_since_fri = (dt.weekday() - 4) % 7
    # On Friday before 17:00 the new week hasn't started yet — use previous Friday.
    if days_since_fri == 0 and dt.hour < 17:
        days_since_fri = 7
    friday = dt.date() - timedelta(days=days_since_fri)
    return f"{friday}T17:00", f"{friday + timedelta(weeks=1)}T17:00"


def _today_bounds() -> tuple[str, str]:
    d = date.today()
    return d.isoformat(), (d + timedelta(days=1)).isoformat()


def _in_range(timestamp: str, since: str, until: str) -> bool:
    # When boundaries carry a time component, compare the full timestamp so the
    # Fri-17:00 cutoff is respected precisely; otherwise compare date-only.
    ts = timestamp if len(since) > 10 or len(until) > 10 else timestamp[:10]
    return since <= ts < until


_EFFORT_ORDER = {"xhigh": 0, "high": 1, "medium": 2, "low": 3, "none": 4}


def _aggregate(events: list[dict], since: str, until: str, pricing: dict, cfg: dict) -> dict:
    cpd = cfg["credits_per_dollar"]
    pool_limit = cfg["weekly_pool_credits"]

    filtered = [e for e in events if _in_range(e["timestamp"], since, until)]

    # Per-day
    days_map: dict[str, dict] = {}
    for e in filtered:
        day = e["timestamp"][:10]
        if day not in days_map:
            days_map[day] = {
                "date": day,
                "input_tokens": 0,
                "cached_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "usd": 0.0,
                "credits": 0.0,
                "input_usd": 0.0,
                "cached_usd": 0.0,
                "output_usd": 0.0,
            }
        d = days_map[day]
        d["input_tokens"] += e["input_tokens"]
        d["cached_tokens"] += e["cached_input_tokens"]
        d["output_tokens"] += e["output_tokens"]
        d["total_tokens"] += e["total_tokens"]
        usd = tokens_to_usd(e["model"], e, pricing)
        d["usd"] += usd
        parts = tokens_to_usd_breakdown(e["model"], e, pricing)
        d["input_usd"]  += parts["input_usd"]
        d["cached_usd"] += parts["cached_usd"]
        d["output_usd"] += parts["output_usd"]
        if e.get("auth_type", "oauth") == "oauth":
            d["credits"] += usd_to_credits(usd, cpd)

    for d in days_map.values():
        d["usd"] = round(d["usd"], 4)
        d["credits"] = round(d["credits"], 4)
        d["input_usd"]  = round(d["input_usd"],  4)
        d["cached_usd"] = round(d["cached_usd"], 4)
        d["output_usd"] = round(d["output_usd"], 4)

    # Per-day-per-model, per-day-per-project, and per-day-per-cwd breakdowns for chart drill-down
    days_model_map: dict[str, dict[str, dict]] = {}
    days_project_map: dict[str, dict[str, dict]] = {}
    days_cwd_map: dict[str, dict[str, dict]] = {}
    for e in filtered:
        day = e["timestamp"][:10]
        m = e["model"]
        proj = e.get("project", "default")
        cwd = e.get("cwd") or "unknown"
        usd = tokens_to_usd(m, e, pricing)
        credits = usd_to_credits(usd, cpd) if e.get("auth_type", "oauth") == "oauth" else 0.0
        for mapping, key in ((days_model_map, m), (days_project_map, proj), (days_cwd_map, cwd)):
            if key not in mapping:
                mapping[key] = {}
            if day not in mapping[key]:
                mapping[key][day] = {"usd": 0.0, "credits": 0.0}
            mapping[key][day]["usd"] += usd
            mapping[key][day]["credits"] += credits
    for mapping in (days_model_map, days_project_map, days_cwd_map):
        for key_data in mapping.values():
            for v in key_data.values():
                v["usd"] = round(v["usd"], 4)
                v["credits"] = round(v["credits"], 4)

    # Fill in zero-value entries for every calendar day in [since, until) so
    # the chart always spans the full requested range, not just days with events.
    since_date = date.fromisoformat(since[:10])
    until_date = date.fromisoformat(until[:10])
    cursor = since_date
    while cursor < until_date:
        key = cursor.isoformat()
        if key not in days_map:
            days_map[key] = {
                "date": key,
                "input_tokens": 0,
                "cached_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "usd": 0.0,
                "credits": 0.0,
            }
        cursor += timedelta(days=1)

    days = sorted(days_map.values(), key=lambda x: x["date"])

    # Per-model (track which projects each model appears in)
    models_map: dict[str, dict] = {}
    for e in filtered:
        m = e["model"]
        if m not in models_map:
            models_map[m] = {
                "model": m,
                "events": 0,
                "input_tokens": 0,
                "cached_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "usd": 0.0,
                "credits": 0.0,
                "_projects": set(),
            }
        r = models_map[m]
        r["events"] += 1
        r["input_tokens"] += e["input_tokens"]
        r["cached_tokens"] += e["cached_input_tokens"]
        r["output_tokens"] += e["output_tokens"]
        r["total_tokens"] += e["total_tokens"]
        r["_projects"].add(e.get("project", "default"))
        usd = tokens_to_usd(m, e, pricing)
        r["usd"] += usd
        if e.get("auth_type", "oauth") == "oauth":
            r["credits"] += usd_to_credits(usd, cpd)

    for r in models_map.values():
        r["usd"] = round(r["usd"], 4)
        r["credits"] = round(r["credits"], 4)
        r["projects"] = sorted(r.pop("_projects"))

    models = sorted(models_map.values(), key=lambda x: -x["usd"])

    # Per-session
    sessions_map: dict[str, dict] = {}
    for e in filtered:
        sid = e["session_id"]
        if sid not in sessions_map:
            sessions_map[sid] = {
                "session_id": sid,
                "date": e["timestamp"][:10],
                "last_timestamp": e["timestamp"],
                "events": 0,
                "total_tokens": 0,
                "usd": 0.0,
                "credits": 0.0,
                "project": e.get("project", "default"),
                "reasoning_effort": e.get("reasoning_effort"),
                "cwd": e.get("cwd"),
            }
        s = sessions_map[sid]
        s["events"] += 1
        s["total_tokens"] += e["total_tokens"]
        if e["timestamp"] > s["last_timestamp"]:
            s["last_timestamp"] = e["timestamp"]
        usd = tokens_to_usd(e["model"], e, pricing)
        s["usd"] += usd
        if e.get("auth_type", "oauth") == "oauth":
            s["credits"] += usd_to_credits(usd, cpd)
        if e.get("reasoning_effort") and not s["reasoning_effort"]:
            s["reasoning_effort"] = e["reasoning_effort"]

    for s in sessions_map.values():
        s["usd"] = round(s["usd"], 4)
        s["credits"] = round(s["credits"], 4)

    sessions = sorted(sessions_map.values(), key=lambda x: x["last_timestamp"], reverse=True)

    # Per-project
    project_repos: dict[str, list[str]] = {
        p["name"]: p.get("repos", []) for p in cfg.get("projects", [])
    }
    projects_map: dict[str, dict] = {}
    for e in filtered:
        pname = e.get("project", "default")
        auth_type = e.get("auth_type", "oauth")
        if pname not in projects_map:
            projects_map[pname] = {
                "name": pname,
                "auth_type": auth_type,
                "repos": project_repos.get(pname, []),
                "events": 0,
                "total_tokens": 0,
                "usd": 0.0,
                "credits": 0.0,
            }
        r = projects_map[pname]
        r["events"] += 1
        r["total_tokens"] += e["total_tokens"]
        usd = tokens_to_usd(e["model"], e, pricing)
        r["usd"] += usd
        if auth_type == "oauth":
            r["credits"] += usd_to_credits(usd, cpd)

    for r in projects_map.values():
        r["usd"] = round(r["usd"], 4)
        r["credits"] = round(r["credits"], 4)

    projects_list = sorted(projects_map.values(), key=lambda x: x["name"])

    # Per-effort-level
    effort_map: dict[str, dict] = {}
    for e in filtered:
        effort_key = e.get("reasoning_effort") or "none"
        if effort_key not in effort_map:
            effort_map[effort_key] = {
                "effort": effort_key,
                "events": 0,
                "total_tokens": 0,
                "usd": 0.0,
                "credits": 0.0,
            }
        r = effort_map[effort_key]
        r["events"] += 1
        r["total_tokens"] += e["total_tokens"]
        usd = tokens_to_usd(e["model"], e, pricing)
        r["usd"] += usd
        if e.get("auth_type", "oauth") == "oauth":
            r["credits"] += usd_to_credits(usd, cpd)

    for r in effort_map.values():
        r["usd"] = round(r["usd"], 4)
        r["credits"] = round(r["credits"], 4)

    effort_levels = sorted(effort_map.values(), key=lambda x: _EFFORT_ORDER.get(x["effort"], 99))

    # Totals — credits are OAuth-only; USD is all projects
    oauth_credits = sum(r["credits"] for r in projects_map.values() if r["auth_type"] == "oauth")
    api_token_usd = sum(r["usd"] for r in projects_map.values() if r["auth_type"] == "api_token")
    total_usd = sum(r["usd"] for r in projects_map.values())
    total_tokens = sum(r["total_tokens"] for r in projects_map.values())
    pool_pct = round(oauth_credits / pool_limit * 100, 1) if pool_limit > 0 else 0.0

    has_oauth = any(e.get("auth_type", "oauth") == "oauth" for e in filtered)
    has_api_token = any(e.get("auth_type") == "api_token" for e in filtered)
    has_effort_data = any(e.get("reasoning_effort") for e in filtered)
    has_cwd_data = len(days_cwd_map) > 1

    return {
        "days": days,
        "days_by_model": days_model_map,
        "days_by_project": days_project_map,
        "days_by_cwd": days_cwd_map,
        "models": models,
        "sessions": sessions,
        "projects": projects_list,
        "effort_levels": effort_levels,
        "totals": {
            "total_tokens": total_tokens,
            "usd": round(total_usd, 4),
            "credits": round(oauth_credits, 4),
        },
        "pool": {
            "used": round(oauth_credits, 4),
            "limit": pool_limit,
            "pct": pool_pct,
        },
        "api_token_totals": {"usd": round(api_token_usd, 4)},
        "has_oauth": has_oauth,
        "has_api_token": has_api_token,
        "has_effort_data": has_effort_data,
        "has_cwd_data": has_cwd_data,
        "range": {"since": since, "until": until},
    }


def _send_json(handler, obj: object, status: int = 200) -> None:
    body = json.dumps(obj, default=str).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _serve_static(handler, name: str) -> None:
    try:
        data = files("codexusage").joinpath("web").joinpath(name).read_bytes()
    except (FileNotFoundError, TypeError):
        handler.send_response(404)
        handler.end_headers()
        return
    ctype, _ = mimetypes.guess_type(name)
    handler.send_response(200)
    handler.send_header("Content-Type", ctype or "application/octet-stream")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def build_handler(cfg: dict):
    pricing = load_pricing()

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def do_GET(self):
            url = urlparse(self.path)
            path = url.path
            qs = parse_qs(url.query or "")

            if path in ("/", "/index.html"):
                return _serve_static(self, "index.html")
            if path.startswith("/web/"):
                return _serve_static(self, path[5:].lstrip("/"))

            if path in ("/api/week", "/api/today"):
                qs_since = qs.get("since", [None])[0]
                qs_until = qs.get("until", [None])[0]
                if qs_since and not _DATE_RE.match(qs_since):
                    return _send_json(self, {"error": "invalid since — expected YYYY-MM-DD"}, 400)
                if qs_until and not _DATE_RE.match(qs_until):
                    return _send_json(self, {"error": "invalid until — expected YYYY-MM-DD"}, 400)
                events = scan_all_projects_cached(cfg["projects"])
                def_bounds = _week_bounds() if path == "/api/week" else _today_bounds()
                since = qs_since if qs_since else def_bounds[0]
                until = qs_until if qs_until else def_bounds[1]
                result = _aggregate(events, since, until, pricing, cfg)
                result["config"] = {
                    "credits_per_dollar": cfg["credits_per_dollar"],
                    "weekly_pool_credits": cfg["weekly_pool_credits"],
                    "projects": cfg["projects"],
                    "pricing_source": "live" if "_fetched_at" in pricing else "bundled",
                    "pricing_fetched_at": pricing.get("_fetched_at"),
                }
                return _send_json(self, result)

            if path == "/api/sessions":
                since = qs.get("since", [None])[0] or "0000-01-01"
                until = qs.get("until", [None])[0] or "9999-12-31"
                events = scan_all_projects_cached(cfg["projects"])
                result = _aggregate(events, since, until, pricing, cfg)
                return _send_json(self, {"sessions": result["sessions"]})

            handler_instance = self
            handler_instance.send_response(404)
            handler_instance.end_headers()

    return H


def run(host: str, port: int, cfg: dict) -> None:
    H = build_handler(cfg)
    httpd = http.server.ThreadingHTTPServer((host, port), H)
    httpd.serve_forever()
