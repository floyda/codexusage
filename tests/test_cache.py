"""Tests for the SQLite incremental session cache."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import codexusage.cache as cache_module
from codexusage.cache import scan_all_projects_cached
from codexusage.scanner import scan_all_projects


def _make_jsonl(*records: dict) -> str:
    return "\n".join(json.dumps(r) for r in records)


def _turn_context(model: str) -> dict:
    return {"type": "turn_context", "payload": {"model": model}}


def _token_event(timestamp: str, input: int, output: int) -> dict:
    last_usage = {
        "input_tokens": input,
        "cached_input_tokens": 0,
        "output_tokens": output,
        "total_tokens": input + output,
    }
    return {
        "type": "event_msg",
        "timestamp": timestamp,
        "payload": {"type": "token_count", "info": {"last_token_usage": last_usage}},
    }


def _make_projects(sessions_dir: Path, auth_type: str = "oauth") -> list[dict]:
    return [
        {
            "name": "proj",
            "sessions_dir": str(sessions_dir),
            "auth_type": auth_type,
            "repos": [],
        }
    ]


@pytest.fixture
def sessions_dir(tmp_path: Path) -> Path:
    d = tmp_path / "sessions"
    d.mkdir()
    return d


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "cache.db"


class TestSchemaCreation:
    def test_db_file_created_on_first_call(
        self, sessions_dir: Path, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cache_module, "cache_path", lambda: db_path)
        scan_all_projects_cached(_make_projects(sessions_dir))
        assert db_path.exists()

    def test_tables_exist_after_first_call(
        self, sessions_dir: Path, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cache_module, "cache_path", lambda: db_path)
        scan_all_projects_cached(_make_projects(sessions_dir))
        conn = sqlite3.connect(str(db_path))
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()
        assert {"files", "events"}.issubset(tables)


class TestColdScanCorrectness:
    def test_events_match_uncached_scan(
        self, sessions_dir: Path, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cache_module, "cache_path", lambda: db_path)
        f = sessions_dir / "session.jsonl"
        f.write_text(
            _make_jsonl(
                _turn_context("gpt-4o"),
                _token_event("2024-06-01T10:00:00Z", 100, 50),
            )
        )
        projects = _make_projects(sessions_dir)
        cached = scan_all_projects_cached(projects)
        real = scan_all_projects(projects)

        assert len(cached) == len(real) == 1
        assert cached[0]["input_tokens"] == real[0]["input_tokens"]
        assert cached[0]["output_tokens"] == real[0]["output_tokens"]
        assert cached[0]["model"] == real[0]["model"]
        assert cached[0]["project"] == "proj"
        assert cached[0]["auth_type"] == "oauth"

    def test_empty_dir_returns_empty(
        self, sessions_dir: Path, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cache_module, "cache_path", lambda: db_path)
        assert scan_all_projects_cached(_make_projects(sessions_dir)) == []

    def test_missing_sessions_dir_returns_empty(
        self, tmp_path: Path, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cache_module, "cache_path", lambda: db_path)
        projects = _make_projects(tmp_path / "no_such_dir")
        assert scan_all_projects_cached(projects) == []


class TestIncrementalBehavior:
    def test_unchanged_files_not_reparsed(
        self, sessions_dir: Path, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cache_module, "cache_path", lambda: db_path)
        f = sessions_dir / "session.jsonl"
        f.write_text(_make_jsonl(_token_event("2024-06-01T10:00:00Z", 100, 50)))
        projects = _make_projects(sessions_dir)
        scan_all_projects_cached(projects)  # populate cache

        call_count = [0]
        real_parse = cache_module._parse_file

        def spy(path: Path, sid: str) -> list[dict]:
            call_count[0] += 1
            return real_parse(path, sid)

        monkeypatch.setattr(cache_module, "_parse_file", spy)
        scan_all_projects_cached(projects)
        assert call_count[0] == 0

    def test_changed_file_reparsed(
        self, sessions_dir: Path, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cache_module, "cache_path", lambda: db_path)
        f = sessions_dir / "session.jsonl"
        # Write small values first (small file size)
        f.write_text(_make_jsonl(_token_event("2024-06-01T10:00:00Z", 1, 1)))
        projects = _make_projects(sessions_dir)
        scan_all_projects_cached(projects)

        # Overwrite with clearly different size (large token numbers → longer JSON)
        f.write_text(_make_jsonl(_token_event("2024-06-01T10:00:00Z", 99999, 55555)))
        events = scan_all_projects_cached(projects)
        assert events[0]["input_tokens"] == 99999

    def test_new_file_picked_up(
        self, sessions_dir: Path, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cache_module, "cache_path", lambda: db_path)
        projects = _make_projects(sessions_dir)
        scan_all_projects_cached(projects)  # empty first scan

        f = sessions_dir / "new_session.jsonl"
        f.write_text(_make_jsonl(_token_event("2024-06-01T10:00:00Z", 100, 50)))
        events = scan_all_projects_cached(projects)
        assert len(events) == 1
        assert events[0]["input_tokens"] == 100

    def test_deleted_file_removes_events(
        self, sessions_dir: Path, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cache_module, "cache_path", lambda: db_path)
        f = sessions_dir / "session.jsonl"
        f.write_text(_make_jsonl(_token_event("2024-06-01T10:00:00Z", 100, 50)))
        projects = _make_projects(sessions_dir)
        scan_all_projects_cached(projects)

        f.unlink()
        events = scan_all_projects_cached(projects)
        assert events == []

    def test_events_sorted_by_timestamp(
        self, sessions_dir: Path, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cache_module, "cache_path", lambda: db_path)
        f = sessions_dir / "session.jsonl"
        f.write_text(
            _make_jsonl(
                _token_event("2024-06-03T10:00:00Z", 10, 5),
                _token_event("2024-06-01T10:00:00Z", 20, 10),
                _token_event("2024-06-02T10:00:00Z", 30, 15),
            )
        )
        events = scan_all_projects_cached(_make_projects(sessions_dir))
        timestamps = [e["timestamp"] for e in events]
        assert timestamps == sorted(timestamps)


class TestFallback:
    def test_corrupt_db_falls_back_to_full_scan(
        self, sessions_dir: Path, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cache_module, "cache_path", lambda: db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db_path.write_bytes(b"not a sqlite database at all")

        f = sessions_dir / "session.jsonl"
        f.write_text(_make_jsonl(_token_event("2024-06-01T10:00:00Z", 100, 50)))
        projects = _make_projects(sessions_dir)

        # Must not raise; falls back to scan_all_projects()
        events = scan_all_projects_cached(projects)
        assert len(events) == 1
        assert events[0]["input_tokens"] == 100
