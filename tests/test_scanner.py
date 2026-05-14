"""Tests for scanner.py session log parsing."""

from __future__ import annotations

import json
from pathlib import Path

from codexusage.scanner import (
    _assign_project,
    _ensure_int,
    _normalize_usage,
    _parse_file,
    _resolve_git_repo_root,
    _subtract_usage,
    scan_all_projects,
    scan_sessions,
)


class TestEnsureInt:
    def test_positive_int(self) -> None:
        assert _ensure_int(42) == 42

    def test_float_truncates(self) -> None:
        assert _ensure_int(3.9) == 3

    def test_negative_clamped_to_zero(self) -> None:
        assert _ensure_int(-5) == 0

    def test_none_returns_zero(self) -> None:
        assert _ensure_int(None) == 0

    def test_string_returns_zero(self) -> None:
        assert _ensure_int("100") == 0

    def test_nan_returns_zero(self) -> None:
        assert _ensure_int(float("nan")) == 0


class TestNormalizeUsage:
    def test_basic_fields(self) -> None:
        result = _normalize_usage({"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})
        assert result == {
            "input_tokens": 10,
            "cached_input_tokens": 0,
            "output_tokens": 5,
            "reasoning_output_tokens": 0,
            "total_tokens": 15,
        }

    def test_cache_read_alias(self) -> None:
        result = _normalize_usage({"cache_read_input_tokens": 7})
        assert result is not None
        assert result["cached_input_tokens"] == 7

    def test_non_dict_returns_none(self) -> None:
        assert _normalize_usage("not a dict") is None
        assert _normalize_usage(None) is None
        assert _normalize_usage([]) is None


class TestSubtractUsage:
    def test_subtraction(self) -> None:
        current = {
            "input_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 150,
            "cached_input_tokens": 0,
            "reasoning_output_tokens": 0,
        }
        prev = {
            "input_tokens": 60,
            "output_tokens": 30,
            "total_tokens": 90,
            "cached_input_tokens": 0,
            "reasoning_output_tokens": 0,
        }
        result = _subtract_usage(current, prev)
        assert result["input_tokens"] == 40
        assert result["output_tokens"] == 20

    def test_no_negative_values(self) -> None:
        current = {
            "input_tokens": 10,
            "output_tokens": 5,
            "total_tokens": 15,
            "cached_input_tokens": 0,
            "reasoning_output_tokens": 0,
        }
        prev = {
            "input_tokens": 20,
            "output_tokens": 5,
            "total_tokens": 25,
            "cached_input_tokens": 0,
            "reasoning_output_tokens": 0,
        }
        result = _subtract_usage(current, prev)
        assert result["input_tokens"] == 0

    def test_none_previous_returns_current(self) -> None:
        current = {"input_tokens": 50}
        assert _subtract_usage(current, None) is current


def _make_jsonl(*records: dict) -> str:
    return "\n".join(json.dumps(r) for r in records)


def _turn_context(model: str, effort: str | None = None) -> dict:
    payload: dict = {"model": model}
    if effort:
        payload["reasoning_effort"] = effort
    return {"type": "turn_context", "payload": payload}


def _token_event(
    timestamp: str,
    input: int,
    output: int,
    cached: int = 0,
    model: str | None = None,
) -> dict:
    last_usage: dict = {
        "input_tokens": input,
        "cached_input_tokens": cached,
        "output_tokens": output,
        "total_tokens": input + output,
    }
    payload: dict = {"type": "token_count", "info": {"last_token_usage": last_usage}}
    if model:
        payload["model"] = model
    return {"type": "event_msg", "timestamp": timestamp, "payload": payload}


class TestParseFile:
    def test_basic_event(self, tmp_path: Path) -> None:
        f = tmp_path / "session.jsonl"
        f.write_text(
            _make_jsonl(
                _turn_context("gpt-4o"),
                _token_event("2024-06-01T10:00:00Z", input=100, output=50),
            )
        )
        events = _parse_file(f, "session")
        assert len(events) == 1
        e = events[0]
        assert e["model"] == "gpt-4o"
        assert e["input_tokens"] == 100
        assert e["output_tokens"] == 50
        assert e["total_tokens"] == 150

    def test_model_from_event_overrides_context(self, tmp_path: Path) -> None:
        f = tmp_path / "session.jsonl"
        f.write_text(
            _make_jsonl(
                _turn_context("gpt-4o"),
                _token_event("2024-06-01T10:00:00Z", input=10, output=5, model="o3"),
            )
        )
        events = _parse_file(f, "session")
        assert events[0]["model"] == "o3"

    def test_skips_zero_total(self, tmp_path: Path) -> None:
        f = tmp_path / "session.jsonl"
        f.write_text(_make_jsonl(_token_event("2024-06-01T10:00:00Z", input=0, output=0)))
        assert _parse_file(f, "session") == []

    def test_skips_bad_json_lines(self, tmp_path: Path) -> None:
        f = tmp_path / "session.jsonl"
        f.write_text(
            "not json\n" + json.dumps(_token_event("2024-06-01T10:00:00Z", input=10, output=5))
        )
        events = _parse_file(f, "session")
        assert len(events) == 1

    def test_reasoning_effort_captured(self, tmp_path: Path) -> None:
        f = tmp_path / "session.jsonl"
        f.write_text(
            _make_jsonl(
                _turn_context("o3", effort="high"),
                _token_event("2024-06-01T10:00:00Z", input=10, output=5),
            )
        )
        events = _parse_file(f, "session")
        assert events[0]["reasoning_effort"] == "high"

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert _parse_file(tmp_path / "nonexistent.jsonl", "x") == []

    def test_session_id_attached(self, tmp_path: Path) -> None:
        f = tmp_path / "mysession.jsonl"
        f.write_text(_make_jsonl(_token_event("2024-06-01T10:00:00Z", input=10, output=5)))
        events = _parse_file(f, "mysession")
        assert events[0]["session_id"] == "mysession"


class TestScanSessions:
    def test_missing_dir_returns_empty(self, tmp_path: Path) -> None:
        assert scan_sessions(str(tmp_path / "no_such_dir")) == []

    def test_scans_nested_jsonl(self, tmp_path: Path) -> None:
        sub = tmp_path / "proj" / "subdir"
        sub.mkdir(parents=True)
        f = sub / "session.jsonl"
        f.write_text(
            _make_jsonl(
                _turn_context("gpt-4o"),
                _token_event("2024-06-01T10:00:00Z", input=50, output=25),
            )
        )
        events = scan_sessions(str(tmp_path))
        assert len(events) == 1

    def test_events_sorted_by_timestamp(self, tmp_path: Path) -> None:
        f = tmp_path / "session.jsonl"
        f.write_text(
            _make_jsonl(
                _token_event("2024-06-03T10:00:00Z", input=1, output=1),
                _token_event("2024-06-01T10:00:00Z", input=2, output=2),
                _token_event("2024-06-02T10:00:00Z", input=3, output=3),
            )
        )
        events = scan_sessions(str(tmp_path))
        timestamps = [e["timestamp"] for e in events]
        assert timestamps == sorted(timestamps)


def _session_with_cwd(
    tmp_path: Path, filename: str, cwd: str, timestamp: str = "2024-06-01T10:00:00Z"
) -> Path:
    f = tmp_path / filename
    meta = {"type": "session_meta", "payload": {"cwd": cwd}}
    f.write_text(_make_jsonl(meta, _token_event(timestamp, input=10, output=5)))
    return f


class TestAssignProject:
    def test_single_project_always_assigned(self) -> None:
        group = [{"name": "only", "auth_type": "oauth", "repos": ["/repo/a"]}]
        assert _assign_project("/unrelated/path", group)["name"] == "only"

    def test_cwd_matches_repo_prefix(self) -> None:
        group = [
            {"name": "proj-a", "auth_type": "oauth", "repos": ["/repo/a"]},
            {"name": "proj-b", "auth_type": "oauth", "repos": ["/repo/b"]},
        ]
        assert _assign_project("/repo/a/src/main.py", group)["name"] == "proj-a"
        assert _assign_project("/repo/b", group)["name"] == "proj-b"

    def test_exact_cwd_match(self) -> None:
        group = [
            {"name": "proj-a", "auth_type": "oauth", "repos": ["/repo/a"]},
            {"name": "proj-b", "auth_type": "oauth", "repos": ["/repo/b"]},
        ]
        assert _assign_project("/repo/a", group)["name"] == "proj-a"

    def test_unmatched_cwd_falls_back_to_first(self) -> None:
        group = [
            {"name": "proj-a", "auth_type": "oauth", "repos": ["/repo/a"]},
            {"name": "proj-b", "auth_type": "oauth", "repos": ["/repo/b"]},
        ]
        assert _assign_project("/unrelated", group)["name"] == "proj-a"

    def test_none_cwd_falls_back_to_first(self) -> None:
        group = [
            {"name": "proj-a", "auth_type": "oauth", "repos": ["/repo/a"]},
            {"name": "proj-b", "auth_type": "oauth", "repos": ["/repo/b"]},
        ]
        assert _assign_project(None, group)["name"] == "proj-a"


class TestScanAllProjects:
    def test_tags_project_and_auth_type(self, tmp_path: Path) -> None:
        f = tmp_path / "session.jsonl"
        f.write_text(_make_jsonl(_token_event("2024-06-01T10:00:00Z", input=10, output=5)))
        projects = [
            {"name": "myproj", "sessions_dir": str(tmp_path), "auth_type": "oauth", "repos": []}
        ]
        events = scan_all_projects(projects)
        assert all(e["project"] == "myproj" for e in events)
        assert all(e["auth_type"] == "oauth" for e in events)

    def test_shared_sessions_dir_assigns_by_cwd(self, tmp_path: Path) -> None:
        _session_with_cwd(tmp_path, "session_a.jsonl", "/repo/a/src")
        _session_with_cwd(tmp_path, "session_b.jsonl", "/repo/b", timestamp="2024-06-01T11:00:00Z")
        projects = [
            {
                "name": "proj-a",
                "sessions_dir": str(tmp_path),
                "auth_type": "oauth",
                "repos": ["/repo/a"],
            },
            {
                "name": "proj-b",
                "sessions_dir": str(tmp_path),
                "auth_type": "api_token",
                "repos": ["/repo/b"],
            },
        ]
        events = scan_all_projects(projects)
        by_project = {e["project"] for e in events}
        assert by_project == {"proj-a", "proj-b"}
        proj_a_events = [e for e in events if e["project"] == "proj-a"]
        proj_b_events = [e for e in events if e["project"] == "proj-b"]
        assert len(proj_a_events) == 1
        assert len(proj_b_events) == 1
        assert proj_b_events[0]["auth_type"] == "api_token"

    def test_shared_sessions_dir_scanned_once(self, tmp_path: Path) -> None:
        f = tmp_path / "session.jsonl"
        f.write_text(_make_jsonl(_token_event("2024-06-01T10:00:00Z", input=10, output=5)))
        projects = [
            {
                "name": "proj-a",
                "sessions_dir": str(tmp_path),
                "auth_type": "oauth",
                "repos": ["/repo/a"],
            },
            {
                "name": "proj-b",
                "sessions_dir": str(tmp_path),
                "auth_type": "oauth",
                "repos": ["/repo/b"],
            },
        ]
        events = scan_all_projects(projects)
        # Should have exactly 1 event, not 2 (no double-scan)
        assert len(events) == 1

    def test_unmatched_cwd_falls_back_to_first_project(self, tmp_path: Path) -> None:
        _session_with_cwd(tmp_path, "session.jsonl", "/unrelated/path")
        projects = [
            {
                "name": "proj-a",
                "sessions_dir": str(tmp_path),
                "auth_type": "oauth",
                "repos": ["/repo/a"],
            },
            {
                "name": "proj-b",
                "sessions_dir": str(tmp_path),
                "auth_type": "oauth",
                "repos": ["/repo/b"],
            },
        ]
        events = scan_all_projects(projects)
        assert events[0]["project"] == "proj-a"


class TestResolveGitRepoRoot:
    def test_normal_repo_returns_root(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        assert _resolve_git_repo_root(str(tmp_path)) == str(tmp_path)

    def test_subdir_of_normal_repo(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        subdir = tmp_path / "src" / "foo"
        subdir.mkdir(parents=True)
        assert _resolve_git_repo_root(str(subdir)) == str(tmp_path)

    def test_worktree_root_resolves_to_main(self, tmp_path: Path) -> None:
        main_repo = tmp_path / "main"
        worktrees_dir = main_repo / ".git" / "worktrees" / "my-branch"
        worktrees_dir.mkdir(parents=True)

        worktree = tmp_path / "my-branch"
        worktree.mkdir()
        (worktree / ".git").write_text(f"gitdir: {worktrees_dir}\n", encoding="utf-8")

        assert _resolve_git_repo_root(str(worktree)) == str(main_repo)

    def test_worktree_subdir_resolves_to_main(self, tmp_path: Path) -> None:
        main_repo = tmp_path / "main"
        worktrees_dir = main_repo / ".git" / "worktrees" / "my-branch"
        worktrees_dir.mkdir(parents=True)

        worktree = tmp_path / "my-branch"
        subdir = worktree / "src" / "pkg"
        subdir.mkdir(parents=True)
        (worktree / ".git").write_text(f"gitdir: {worktrees_dir}\n", encoding="utf-8")

        assert _resolve_git_repo_root(str(subdir)) == str(main_repo)

    def test_non_git_dir_returns_none(self, tmp_path: Path) -> None:
        assert _resolve_git_repo_root(str(tmp_path)) is None


class TestAssignProjectWorktree:
    def _make_worktree(self, tmp_path: Path, name: str = "wt") -> tuple[Path, Path]:
        """Return (main_repo_path, worktree_path) with a valid .git file."""
        main_repo = tmp_path / "main"
        worktrees_dir = main_repo / ".git" / "worktrees" / name
        worktrees_dir.mkdir(parents=True)
        worktree = tmp_path / name
        worktree.mkdir()
        (worktree / ".git").write_text(f"gitdir: {worktrees_dir}\n", encoding="utf-8")
        return main_repo, worktree

    def test_worktree_cwd_matches_main_repo(self, tmp_path: Path) -> None:
        main_repo, worktree = self._make_worktree(tmp_path)
        proj_a = {"name": "a", "repos": [str(main_repo)]}
        proj_b = {"name": "b", "repos": []}
        assert _assign_project(str(worktree), [proj_a, proj_b])["name"] == "a"

    def test_worktree_subdir_cwd_matches_main_repo(self, tmp_path: Path) -> None:
        main_repo, worktree = self._make_worktree(tmp_path)
        subdir = worktree / "src"
        subdir.mkdir()
        proj_a = {"name": "a", "repos": [str(main_repo)]}
        proj_b = {"name": "b", "repos": []}
        assert _assign_project(str(subdir), [proj_a, proj_b])["name"] == "a"
