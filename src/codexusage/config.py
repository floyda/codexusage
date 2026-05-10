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
    # Synthesize a single default OAuth project from sessions_dir if no projects configured.
    if not cfg["projects"]:
        cfg["projects"] = [
            {
                "name": "default",
                "sessions_dir": cfg["sessions_dir"],
                "auth_type": "oauth",
                "repos": [],
            }
        ]
    else:
        # Ensure every project has a repos field (runtime default, no file rewrite).
        for p in cfg["projects"]:
            if "repos" not in p:
                p["repos"] = []
    return cfg


def save_config(updates: dict[str, Any]) -> None:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    current = _read_raw()
    current.update(updates)
    path.write_text(json.dumps(current, indent=2), encoding="utf-8")


def add_project(
    name: str, sessions_dir: str, auth_type: str, repos: list[str] | None = None
) -> None:
    if auth_type not in {"oauth", "api_token"}:
        raise ValueError(f"auth_type must be 'oauth' or 'api_token', got {auth_type!r}")
    stored = _read_raw()
    projects: list[dict] = stored.get("projects", [])
    if any(p["name"] == name for p in projects):
        raise ValueError(f"Project {name!r} already exists")
    entry: dict[str, Any] = {
        "name": name,
        "sessions_dir": sessions_dir,
        "auth_type": auth_type,
        "repos": repos or [],
    }
    save_config({"projects": [*projects, entry]})


def remove_project(name: str) -> None:
    stored = _read_raw()
    projects: list[dict] = stored.get("projects", [])
    new_projects = [p for p in projects if p["name"] != name]
    if len(new_projects) == len(projects):
        raise ValueError(f"Project {name!r} not found")
    save_config({"projects": new_projects})


def add_repo(project_name: str, repo_path: str) -> None:
    stored = _read_raw()
    projects: list[dict] = stored.get("projects", [])
    for p in projects:
        if p["name"] == project_name:
            repos: list[str] = list(p.get("repos", []))
            if repo_path in repos:
                raise ValueError(f"Repo {repo_path!r} already in project {project_name!r}")
            repos.append(repo_path)
            p["repos"] = repos
            save_config({"projects": projects})
            return
    raise ValueError(f"Project {project_name!r} not found")


def remove_repo(project_name: str, repo_path: str) -> None:
    stored = _read_raw()
    projects: list[dict] = stored.get("projects", [])
    for p in projects:
        if p["name"] == project_name:
            repos = list(p.get("repos", []))
            if repo_path not in repos:
                raise ValueError(f"Repo {repo_path!r} not found in project {project_name!r}")
            repos.remove(repo_path)
            p["repos"] = repos
            save_config({"projects": projects})
            return
    raise ValueError(f"Project {project_name!r} not found")


def list_projects() -> list[dict[str, Any]]:
    return load_config()["projects"]
