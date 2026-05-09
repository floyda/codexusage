"""Tests for config.py load/save/project management."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import codexusage.config as config_mod
from codexusage.config import DEFAULTS


@pytest.fixture(autouse=True)
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(config_mod, "_config_path", lambda: cfg_file)
    return cfg_file


class TestLoadConfig:
    def test_defaults_when_no_file(self) -> None:
        cfg = config_mod.load_config()
        assert cfg["weekly_pool_credits"] == DEFAULTS["weekly_pool_credits"]
        assert cfg["credits_per_dollar"] == DEFAULTS["credits_per_dollar"]
        assert cfg["port"] == DEFAULTS["port"]

    def test_synthesizes_default_project(self) -> None:
        cfg = config_mod.load_config()
        assert len(cfg["projects"]) == 1
        assert cfg["projects"][0]["name"] == "default"
        assert cfg["projects"][0]["auth_type"] == "oauth"

    def test_env_codex_home(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CODEX_HOME", "/custom/codex")
        cfg = config_mod.load_config()
        assert cfg["sessions_dir"] == "/custom/codex/sessions"

    def test_stored_values_override_defaults(self, isolated_config: Path) -> None:
        isolated_config.write_text(json.dumps({"weekly_pool_credits": 9999.0}))
        cfg = config_mod.load_config()
        assert cfg["weekly_pool_credits"] == 9999.0
        assert cfg["port"] == DEFAULTS["port"]  # still default


class TestSaveConfig:
    def test_round_trip(self, isolated_config: Path) -> None:
        config_mod.save_config({"weekly_pool_credits": 5000.0, "port": 9090})
        cfg = config_mod.load_config()
        assert cfg["weekly_pool_credits"] == 5000.0
        assert cfg["port"] == 9090

    def test_partial_update_preserves_other_keys(self, isolated_config: Path) -> None:
        config_mod.save_config({"weekly_pool_credits": 1000.0})
        config_mod.save_config({"port": 9999})
        cfg = config_mod.load_config()
        assert cfg["weekly_pool_credits"] == 1000.0
        assert cfg["port"] == 9999


class TestProjectManagement:
    def test_add_and_list(self) -> None:
        config_mod.add_project("proj1", "/some/path/sessions", "oauth")
        projects = config_mod.list_projects()
        names = [p["name"] for p in projects]
        assert "proj1" in names

    def test_add_duplicate_raises(self) -> None:
        config_mod.add_project("proj1", "/path/sessions", "oauth")
        with pytest.raises(ValueError, match="already exists"):
            config_mod.add_project("proj1", "/other/sessions", "oauth")

    def test_add_invalid_auth_type_raises(self) -> None:
        with pytest.raises(ValueError, match="auth_type"):
            config_mod.add_project("proj1", "/path/sessions", "invalid")

    def test_remove_existing(self) -> None:
        config_mod.add_project("proj1", "/path/sessions", "api_token")
        config_mod.remove_project("proj1")
        names = [p["name"] for p in config_mod.list_projects()]
        assert "proj1" not in names

    def test_remove_nonexistent_raises(self) -> None:
        with pytest.raises(ValueError, match="not found"):
            config_mod.remove_project("ghost")

    def test_add_api_token_project(self) -> None:
        config_mod.add_project("work", "/work/sessions", "api_token")
        projects = config_mod.list_projects()
        work = next(p for p in projects if p["name"] == "work")
        assert work["auth_type"] == "api_token"
