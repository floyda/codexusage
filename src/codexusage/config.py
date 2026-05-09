"""Load and persist user configuration."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, Any] = {
    "weekly_pool_credits": 2500.0,
    "credits_per_dollar": 25.0,
    "sessions_dir": "",
    "port": 8080,
    "projects": [],
}


def _config_path() -> Path:
    return Path.home() / ".config" / "codexusage" / "config.json"


def _default_sessions_dir() -> str:
    codex_home = os.environ.get("CODEX_HOME", "").strip()
    if codex_home:
        return str(Path(codex_home) / "sessions")
    return str(Path.home() / ".codex" / "sessions")


def _read_raw() -> dict[str, Any]:
    path = _config_path()
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def load_config() -> dict[str, Any]:
    stored = _read_raw()
    cfg = {**DEFAULTS, **stored}
    if not cfg["sessions_dir"]:
        cfg["sessions_dir"] = _default_sessions_dir()
    # Synthesize a single default OAuth project from legacy sessions_dir if no projects configured.
    if not cfg["projects"]:
        cfg["projects"] = [
            {"name": "default", "sessions_dir": cfg["sessions_dir"], "auth_type": "oauth"}
        ]
    return cfg


def save_config(updates: dict[str, Any]) -> None:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    current = _read_raw()
    current.update(updates)
    path.write_text(json.dumps(current, indent=2), encoding="utf-8")


def add_project(name: str, sessions_dir: str, auth_type: str) -> None:
    if auth_type not in {"oauth", "api_token"}:
        raise ValueError(f"auth_type must be 'oauth' or 'api_token', got {auth_type!r}")
    stored = _read_raw()
    projects: list[dict] = stored.get("projects", [])
    if any(p["name"] == name for p in projects):
        raise ValueError(f"Project {name!r} already exists")
    projects = [*projects, {"name": name, "sessions_dir": sessions_dir, "auth_type": auth_type}]
    save_config({"projects": projects})


def remove_project(name: str) -> None:
    stored = _read_raw()
    projects: list[dict] = stored.get("projects", [])
    new_projects = [p for p in projects if p["name"] != name]
    if len(new_projects) == len(projects):
        raise ValueError(f"Project {name!r} not found")
    save_config({"projects": new_projects})


def list_projects() -> list[dict[str, Any]]:
    return load_config()["projects"]
