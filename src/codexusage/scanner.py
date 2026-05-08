"""Stateless Codex JSONL session reader.

Ports the parsing logic from ccusage/apps/codex/src/data-loader.ts.
No database — scans files fresh on every call.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


def _ensure_int(value: object) -> int:
    if isinstance(value, (int, float)) and value == value:  # excludes NaN
        return max(0, int(value))
    return 0


def _normalize_usage(obj: object) -> Optional[dict]:
    if not isinstance(obj, dict):
        return None
    return {
        "input_tokens":             _ensure_int(obj.get("input_tokens")),
        "cached_input_tokens":      _ensure_int(obj.get("cached_input_tokens") or obj.get("cache_read_input_tokens")),
        "output_tokens":            _ensure_int(obj.get("output_tokens")),
        "reasoning_output_tokens":  _ensure_int(obj.get("reasoning_output_tokens")),
        "total_tokens":             _ensure_int(obj.get("total_tokens")),
    }


def _subtract_usage(current: dict, previous: Optional[dict]) -> dict:
    if previous is None:
        return current
    return {k: max(current[k] - previous.get(k, 0), 0) for k in current}


def _extract_model(payload: object) -> Optional[str]:
    if not isinstance(payload, dict):
        return None
    for key in ("model", "model_name"):
        v = payload.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    info = payload.get("info")
    if isinstance(info, dict):
        for key in ("model", "model_name"):
            v = info.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
        meta = info.get("metadata")
        if isinstance(meta, dict):
            v = meta.get("model")
            if isinstance(v, str) and v.strip():
                return v.strip()
    meta = payload.get("metadata")
    if isinstance(meta, dict):
        v = meta.get("model")
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _extract_effort(payload: object) -> Optional[str]:
    """Extract reasoning effort level (low/medium/high/xhigh) from a payload dict."""
    if not isinstance(payload, dict):
        return None
    for key in ("reasoning_effort", "effort", "effort_level"):
        v = payload.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    info = payload.get("info")
    if isinstance(info, dict):
        for key in ("reasoning_effort", "effort", "effort_level"):
            v = info.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


_LEGACY_FALLBACK = "gpt-5"


def _parse_file(path: Path, session_id: str) -> list[dict]:
    events: list[dict] = []
    current_model: Optional[str] = None
    current_effort: Optional[str] = None
    previous_totals: Optional[dict] = None

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return events

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue

        entry_type = rec.get("type")
        payload = rec.get("payload")
        timestamp = rec.get("timestamp")

        if entry_type == "turn_context":
            model = _extract_model(payload)
            if model:
                current_model = model
            effort = _extract_effort(payload)
            if effort:
                current_effort = effort
            continue

        if entry_type != "event_msg" or not isinstance(payload, dict):
            continue
        if payload.get("type") != "token_count":
            continue
        if not timestamp:
            continue

        info = payload.get("info") or {}
        last_usage = _normalize_usage(info.get("last_token_usage"))
        total_usage = _normalize_usage(info.get("total_token_usage"))

        raw: Optional[dict] = None
        if last_usage is not None:
            raw = last_usage
        elif total_usage is not None:
            raw = _subtract_usage(total_usage, previous_totals)

        if total_usage is not None:
            previous_totals = total_usage

        if raw is None:
            continue

        # Synthesize total if missing
        if raw["total_tokens"] == 0:
            raw["total_tokens"] = raw["input_tokens"] + raw["output_tokens"]

        if raw["total_tokens"] == 0:
            continue

        # Model and effort from event payload override session-level context
        event_model  = _extract_model(payload) or current_model or _LEGACY_FALLBACK
        event_effort = _extract_effort(payload) or current_effort

        events.append({
            "session_id":              session_id,
            "timestamp":               timestamp,
            "model":                   event_model,
            "input_tokens":            raw["input_tokens"],
            "cached_input_tokens":     raw["cached_input_tokens"],
            "output_tokens":           raw["output_tokens"],
            "reasoning_output_tokens": raw["reasoning_output_tokens"],
            "total_tokens":            raw["total_tokens"],
            "reasoning_effort":        event_effort,
        })

    return events


def scan_sessions(sessions_dir: str) -> list[dict]:
    """Walk sessions_dir/**/*.jsonl and return all token usage events, sorted by timestamp."""
    root = Path(sessions_dir)
    if not root.is_dir():
        return []

    all_events: list[dict] = []
    for path in root.rglob("*.jsonl"):
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        session_id = str(rel).replace("\\", "/").removesuffix(".jsonl")
        all_events.extend(_parse_file(path, session_id))

    all_events.sort(key=lambda e: e["timestamp"])
    return all_events


def scan_all_projects(projects: list[dict]) -> list[dict]:
    """Scan all projects and tag each event with project name and auth_type."""
    all_events: list[dict] = []
    for proj in projects:
        events = scan_sessions(proj["sessions_dir"])
        for e in events:
            e["project"]   = proj["name"]
            e["auth_type"] = proj.get("auth_type", "oauth")
        all_events.extend(events)
    all_events.sort(key=lambda e: e["timestamp"])
    return all_events
