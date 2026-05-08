"""Local HTTP server: static web UI + JSON API. Stateless — scans fresh per request."""
from __future__ import annotations

import http.server
import json
import mimetypes
import re
from datetime import date, datetime, timedelta, timezone
from importlib.resources import files
from typing import Optional
from urllib.parse import parse_qs, urlparse

_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')

from .config import load_config
from .pricing import load_pricing, tokens_to_usd, usd_to_credits
from .scanner import scan_sessions


def _week_bounds(today: Optional[date] = None) -> tuple[str, str]:
    """Return (since, until) ISO strings for the current Mon–Sun week in local time."""
    d = today or date.today()
    monday = d - timedelta(days=d.weekday())
    sunday = monday + timedelta(days=7)
    return monday.isoformat(), sunday.isoformat()


def _today_bounds() -> tuple[str, str]:
    d = date.today()
    return d.isoformat(), (d + timedelta(days=1)).isoformat()


def _in_range(timestamp: str, since: str, until: str) -> bool:
    ts = timestamp[:10]
    return since <= ts < until


def _aggregate(events: list[dict], since: str, until: str, pricing: dict, cfg: dict) -> dict:
    cpd = cfg["credits_per_dollar"]
    pool_limit = cfg["weekly_pool_credits"]

    filtered = [e for e in events if _in_range(e["timestamp"], since, until)]

    # Per-day
    days_map: dict[str, dict] = {}
    for e in filtered:
        day = e["timestamp"][:10]
        if day not in days_map:
            days_map[day] = {"date": day, "input_tokens": 0, "cached_tokens": 0,
                             "output_tokens": 0, "total_tokens": 0, "usd": 0.0, "credits": 0.0}
        d = days_map[day]
        d["input_tokens"]  += e["input_tokens"]
        d["cached_tokens"] += e["cached_input_tokens"]
        d["output_tokens"] += e["output_tokens"]
        d["total_tokens"]  += e["total_tokens"]
        usd = tokens_to_usd(e["model"], e, pricing)
        d["usd"]     += usd
        d["credits"] += usd_to_credits(usd, cpd)

    for d in days_map.values():
        d["usd"]     = round(d["usd"], 4)
        d["credits"] = round(d["credits"], 4)

    days = sorted(days_map.values(), key=lambda x: x["date"])

    # Per-model
    models_map: dict[str, dict] = {}
    for e in filtered:
        m = e["model"]
        if m not in models_map:
            models_map[m] = {"model": m, "events": 0, "input_tokens": 0, "cached_tokens": 0,
                             "output_tokens": 0, "total_tokens": 0, "usd": 0.0, "credits": 0.0}
        r = models_map[m]
        r["events"]        += 1
        r["input_tokens"]  += e["input_tokens"]
        r["cached_tokens"] += e["cached_input_tokens"]
        r["output_tokens"] += e["output_tokens"]
        r["total_tokens"]  += e["total_tokens"]
        usd = tokens_to_usd(m, e, pricing)
        r["usd"]     += usd
        r["credits"] += usd_to_credits(usd, cpd)

    for r in models_map.values():
        r["usd"]     = round(r["usd"], 4)
        r["credits"] = round(r["credits"], 4)

    models = sorted(models_map.values(), key=lambda x: -x["credits"])

    # Per-session
    sessions_map: dict[str, dict] = {}
    for e in filtered:
        sid = e["session_id"]
        if sid not in sessions_map:
            sessions_map[sid] = {"session_id": sid, "date": e["timestamp"][:10],
                                 "last_timestamp": e["timestamp"],
                                 "events": 0, "total_tokens": 0, "usd": 0.0, "credits": 0.0}
        s = sessions_map[sid]
        s["events"]       += 1
        s["total_tokens"] += e["total_tokens"]
        if e["timestamp"] > s["last_timestamp"]:
            s["last_timestamp"] = e["timestamp"]
        usd = tokens_to_usd(e["model"], e, pricing)
        s["usd"]     += usd
        s["credits"] += usd_to_credits(usd, cpd)

    for s in sessions_map.values():
        s["usd"]     = round(s["usd"], 4)
        s["credits"] = round(s["credits"], 4)

    sessions = sorted(sessions_map.values(), key=lambda x: x["last_timestamp"], reverse=True)

    # Totals
    total_usd = sum(r["usd"] for r in models)
    total_credits = sum(r["credits"] for r in models)
    total_tokens = sum(r["total_tokens"] for r in models)
    pool_pct = round(total_credits / pool_limit * 100, 1) if pool_limit > 0 else 0.0

    return {
        "days": days,
        "models": models,
        "sessions": sessions,
        "totals": {
            "total_tokens": total_tokens,
            "usd": round(total_usd, 4),
            "credits": round(total_credits, 4),
        },
        "pool": {
            "used": round(total_credits, 4),
            "limit": pool_limit,
            "pct": pool_pct,
        },
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
                events = scan_sessions(cfg["sessions_dir"])
                def_bounds = _week_bounds() if path == "/api/week" else _today_bounds()
                # Priority: query string > hardcoded default (config since/until removed at dashboard startup)
                since = qs_since if qs_since else def_bounds[0]
                until = qs_until if qs_until else def_bounds[1]
                result = _aggregate(events, since, until, pricing, cfg)
                result["config"] = {
                    "credits_per_dollar": cfg["credits_per_dollar"],
                    "weekly_pool_credits": cfg["weekly_pool_credits"],
                }
                return _send_json(self, result)

            if path == "/api/sessions":
                since = qs.get("since", [None])[0]
                until = qs.get("until", [None])[0]
                events = scan_sessions(cfg["sessions_dir"])
                if not since:
                    since = "0000-01-01"
                if not until:
                    until = "9999-12-31"
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
