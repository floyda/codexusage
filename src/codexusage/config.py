"""Load and persist user configuration."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, Any] = {
    "weekly_pool_credits": 2500.0,
    "credits_per_dollar": 65.0,
    "sessions_dir": "",
    "port": 8080,
}


def _config_path() -> Path:
    return Path.home() / ".config" / "codexusage" / "config.json"


def _default_sessions_dir() -> str:
    codex_home = os.environ.get("CODEX_HOME", "").strip()
    if codex_home:
        return str(Path(codex_home) / "sessions")
    return str(Path.home() / ".codex" / "sessions")


def load_config() -> dict[str, Any]:
    path = _config_path()
    stored: dict[str, Any] = {}
    if path.is_file():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    cfg = {**DEFAULTS, **stored}
    if not cfg["sessions_dir"]:
        cfg["sessions_dir"] = _default_sessions_dir()
    return cfg


def save_config(updates: dict[str, Any]) -> None:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    current: dict[str, Any] = {}
    if path.is_file():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    current.update(updates)
    path.write_text(json.dumps(current, indent=2), encoding="utf-8")
