"""Stateless Codex JSONL session reader.

Ports the parsing logic from ccusage/apps/codex/src/data-loader.ts.
No database — scans files fresh on every call.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def _ensure_int(value: object) -> int:
    if isinstance(value, (int, float)) and value == value:  # excludes NaN
        return max(0, int(value))
    return 0


def _normalize_usage(obj: object) -> dict | None:
    if not isinstance(obj, dict):
        return None
    return {
        "input_tokens": _ensure_int(obj.get("input_tokens")),
        "cached_input_tokens": _ensure_int(
            obj.get("cached_input_tokens") or obj.get("cache_read_input_tokens")
        ),
        "output_tokens": _ensure_int(obj.get("output_tokens")),
        "reasoning_output_tokens": _ensure_int(obj.get("reasoning_output_tokens")),
        "total_tokens": _ensure_int(obj.get("total_tokens")),
    }


def _subtract_usage(current: dict, previous: dict | None) -> dict:
    if previous is None:
        return current
    return {k: max(current[k] - previous.get(k, 0), 0) for k in current}


def _extract_model(payload: object) -> str | None:
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


def _extract_effort(payload: object) -> str | None:
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
    current_model: str | None = None
    current_effort: str | None = None
    previous_totals: dict | None = None
    session_cwd: str | None = None
    session_thread_source: str | None = None
    session_parent_uuid: str | None = None
    session_agent_nickname: str | None = None
    session_git_branch: str | None = None
    session_git_repo: str | None = None

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

        if entry_type == "session_meta" and isinstance(payload, dict):
            cwd = payload.get("cwd")
            if isinstance(cwd, str) and cwd.strip():
                session_cwd = cwd.strip()
            ts = payload.get("thread_source")
            if isinstance(ts, str) and ts.strip():
                session_thread_source = ts.strip()
            source = payload.get("source")
            if isinstance(source, dict):
                subagent = source.get("subagent")
                if isinstance(subagent, dict):
                    spawn = subagent.get("thread_spawn")
                    if isinstance(spawn, dict):
                        pid = spawn.get("parent_thread_id")
                        if isinstance(pid, str) and pid.strip():
                            session_parent_uuid = pid.strip()
                        nick = spawn.get("agent_nickname")
                        if isinstance(nick, str) and nick.strip():
                            session_agent_nickname = nick.strip()
            git = payload.get("git")
            if isinstance(git, dict):
                branch = git.get("branch")
                if isinstance(branch, str) and branch.strip():
                    session_git_branch = branch.strip()
                repo_url = git.get("repository_url") or git.get("remote_url")
                if isinstance(repo_url, str) and repo_url.strip():
                    repo_name = repo_url.rstrip("/").split("/")[-1]
                    if repo_name.endswith(".git"):
                        repo_name = repo_name[:-4]
                    session_git_repo = repo_name
            continue

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

        raw: dict | None = None
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
        event_model = _extract_model(payload) or current_model or _LEGACY_FALLBACK
        event_effort = _extract_effort(payload) or current_effort

        events.append(
            {
                "session_id": session_id,
                "timestamp": timestamp,
                "model": event_model,
                "input_tokens": raw["input_tokens"],
                "cached_input_tokens": raw["cached_input_tokens"],
                "output_tokens": raw["output_tokens"],
                "reasoning_output_tokens": raw["reasoning_output_tokens"],
                "total_tokens": raw["total_tokens"],
                "reasoning_effort": event_effort,
                "cwd": session_cwd,
                "thread_source": session_thread_source,
                "parent_session_uuid": session_parent_uuid,
                "agent_nickname": session_agent_nickname,
                "git_branch": session_git_branch,
                "git_repo": session_git_repo,
            }
        )

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


def _resolve_git_repo_root(path: str) -> str | None:
    """Return the main repo root for path, resolving git worktrees transparently.

    Walks up from path looking for .git. If .git is a file (worktree), reads
    the gitdir pointer and resolves it back to the main repo root. If .git is
    a directory (normal repo), returns that directory's parent. Returns None
    if path is not inside any git repo.
    """
    p = Path(path)
    for candidate in [p, *p.parents]:
        git_entry = candidate / ".git"
        try:
            if git_entry.is_file():
                content = git_entry.read_text(encoding="utf-8").strip()
                if content.startswith("gitdir:"):
                    gitdir = Path(content[len("gitdir:") :].strip())
                    if not gitdir.is_absolute():
                        gitdir = Path(os.path.normpath(candidate / gitdir))
                    # gitdir is .git/worktrees/<name> — two levels up is the main .git
                    main_git = gitdir.parent.parent
                    if main_git.name == ".git":
                        return str(main_git.parent)
            elif git_entry.is_dir():
                return str(candidate)
        except OSError:
            pass
    return None


def _assign_project(cwd: str | None, group: list[dict]) -> dict:
    """Return the project from group that best matches cwd via repos prefix."""
    if len(group) == 1:
        return group[0]
    if cwd:
        # Direct prefix match (fast path — no filesystem I/O)
        for proj in group:
            for repo in proj.get("repos", []):
                prefix = repo.rstrip("/")
                if cwd == prefix or cwd.startswith(prefix + "/"):
                    return proj
        # Fallback: resolve worktrees to their main repo root and retry
        resolved = _resolve_git_repo_root(cwd)
        if resolved and resolved != cwd:
            for proj in group:
                for repo in proj.get("repos", []):
                    prefix = repo.rstrip("/")
                    if resolved == prefix or resolved.startswith(prefix + "/"):
                        return proj
    return group[0]


def _annotate_events(
    events: list[dict],
    group: list[dict],
    root_cache: dict[str, str | None],
    overrides: dict[str, str] | None = None,
) -> None:
    """Tag each event with project, auth_type, and cwd_root (in-place).

    root_cache is shared across calls within a scan run to avoid redundant
    filesystem reads for the same cwd.
    """
    for e in events:
        proj = _assign_project(e.get("cwd"), group)
        e["project"] = proj["name"]
        if overrides and e.get("session_id") in overrides:
            e["auth_type"] = overrides[e["session_id"]]
        else:
            e["auth_type"] = proj.get("auth_type", "oauth")
        cwd = e.get("cwd")
        if cwd:
            if cwd not in root_cache:
                root_cache[cwd] = _resolve_git_repo_root(cwd)
            e["cwd_root"] = root_cache[cwd] or cwd


def scan_all_projects(
    projects: list[dict], overrides: dict[str, str] | None = None
) -> list[dict]:
    """Scan all projects and tag each event with project name and auth_type.

    Groups projects by sessions_dir so each directory is scanned exactly once.
    When multiple projects share a sessions_dir, events are assigned by cwd
    prefix-matching against each project's repos list; unmatched events fall
    back to the first project in the group.
    """
    from collections import defaultdict

    groups: dict[str, list[dict]] = defaultdict(list)
    for proj in projects:
        groups[proj["sessions_dir"]].append(proj)

    all_events: list[dict] = []
    root_cache: dict[str, str | None] = {}

    for sessions_dir, group in groups.items():
        events = scan_sessions(sessions_dir)
        _annotate_events(events, group, root_cache, overrides)
        all_events.extend(events)

    all_events.sort(key=lambda e: e["timestamp"])
    return all_events
