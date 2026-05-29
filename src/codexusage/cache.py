"""SQLite-backed incremental cache for session scanning."""

from __future__ import annotations

import logging
import sqlite3
from collections import defaultdict
from pathlib import Path

from .config import _config_path
from .scanner import _annotate_events, _parse_file, scan_all_projects

_LOG = logging.getLogger(__name__)

# Bump when columns are added/removed so existing caches rebuild automatically.
_SCHEMA_VERSION = 3

_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    mtime REAL NOT NULL,
    size INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    rowid INTEGER PRIMARY KEY,
    path TEXT NOT NULL REFERENCES files(path) ON DELETE CASCADE,
    session_id TEXT NOT NULL,
    timestamp TEXT,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    cached_input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    reasoning_output_tokens INTEGER NOT NULL,
    total_tokens INTEGER NOT NULL,
    reasoning_effort TEXT,
    cwd TEXT,
    thread_source TEXT,
    parent_session_uuid TEXT,
    agent_nickname TEXT,
    git_branch TEXT,
    git_repo TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_path ON events(path);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
"""

_INSERT_FILE = "INSERT OR REPLACE INTO files (path, mtime, size) VALUES (?, ?, ?)"
_INSERT_EVENT = (
    "INSERT INTO events (path, session_id, timestamp, model, input_tokens, "
    "cached_input_tokens, output_tokens, reasoning_output_tokens, total_tokens, "
    "reasoning_effort, cwd, thread_source, parent_session_uuid, agent_nickname, "
    "git_branch, git_repo) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def cache_path() -> Path:
    """Return the path to the SQLite cache database."""
    return _config_path().parent / "cache.db"


def _open_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        _maybe_wipe(path)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_DDL)
    conn.execute("INSERT OR IGNORE INTO schema_version (version) VALUES (?)", (_SCHEMA_VERSION,))
    conn.commit()
    return conn


def _maybe_wipe(path: Path) -> None:
    """Delete the DB file if its schema version doesn't match."""
    try:
        tmp = sqlite3.connect(str(path))
        try:
            row = tmp.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
            stale = row is None or row[0] != _SCHEMA_VERSION
        except sqlite3.OperationalError:
            stale = True
        finally:
            tmp.close()
    except Exception:
        stale = True
    if stale:
        path.unlink(missing_ok=True)


def _scan_sessions_cached(sessions_dir: str, conn: sqlite3.Connection) -> list[dict]:
    root = Path(sessions_dir)
    if not root.is_dir():
        return []

    # Current .jsonl files on disk: path_str -> (mtime, size)
    current: dict[str, tuple[float, int]] = {}
    for path in root.rglob("*.jsonl"):
        try:
            st = path.stat()
        except OSError:
            continue
        current[str(path)] = (st.st_mtime, st.st_size)

    # DB manifest filtered to this sessions_dir
    db_for_dir: dict[str, tuple[float, int]] = {
        row[0]: (row[1], row[2])
        for row in conn.execute("SELECT path, mtime, size FROM files").fetchall()
        if Path(row[0]).is_relative_to(root)
    }

    with conn:
        # Re-parse new or changed files
        for path_str, (mtime, size) in current.items():
            if db_for_dir.get(path_str) == (mtime, size):
                continue
            path = Path(path_str)
            rel = path.relative_to(root)
            session_id = str(rel).replace("\\", "/").removesuffix(".jsonl")
            conn.execute("DELETE FROM files WHERE path = ?", (path_str,))
            events = _parse_file(path, session_id)
            conn.execute(_INSERT_FILE, (path_str, mtime, size))
            if events:
                conn.executemany(
                    _INSERT_EVENT,
                    [
                        (
                            path_str,
                            e["session_id"],
                            e["timestamp"],
                            e["model"],
                            e["input_tokens"],
                            e["cached_input_tokens"],
                            e["output_tokens"],
                            e["reasoning_output_tokens"],
                            e["total_tokens"],
                            e["reasoning_effort"],
                            e["cwd"],
                            e.get("thread_source"),
                            e.get("parent_session_uuid"),
                            e.get("agent_nickname"),
                            e.get("git_branch"),
                            e.get("git_repo"),
                        )
                        for e in events
                    ],
                )

        # Remove entries for deleted files
        for path_str in set(db_for_dir) - set(current):
            conn.execute("DELETE FROM files WHERE path = ?", (path_str,))

    if not current:
        return []

    placeholders = ",".join("?" * len(current))
    rows = conn.execute(
        f"SELECT session_id, timestamp, model, input_tokens, cached_input_tokens, "
        f"output_tokens, reasoning_output_tokens, total_tokens, reasoning_effort, cwd, "
        f"thread_source, parent_session_uuid, agent_nickname, git_branch, git_repo "
        f"FROM events WHERE path IN ({placeholders})",
        list(current),
    ).fetchall()

    return [
        {
            "session_id": row[0],
            "timestamp": row[1],
            "model": row[2],
            "input_tokens": row[3],
            "cached_input_tokens": row[4],
            "output_tokens": row[5],
            "reasoning_output_tokens": row[6],
            "total_tokens": row[7],
            "reasoning_effort": row[8],
            "cwd": row[9],
            "thread_source": row[10],
            "parent_session_uuid": row[11],
            "agent_nickname": row[12],
            "git_branch": row[13],
            "git_repo": row[14],
        }
        for row in rows
    ]


def scan_all_projects_cached(
    projects: list[dict], overrides: dict[str, str] | None = None
) -> list[dict]:
    """Drop-in replacement for scan_all_projects() with SQLite incremental caching."""
    try:
        conn = _open_db(cache_path())
    except Exception:
        _LOG.warning("Cache DB unavailable, falling back to full scan", exc_info=True)
        return scan_all_projects(projects, overrides)

    try:
        groups: dict[str, list[dict]] = defaultdict(list)
        for proj in projects:
            groups[proj["sessions_dir"]].append(proj)

        all_events: list[dict] = []
        root_cache: dict[str, str | None] = {}

        for sessions_dir, group in groups.items():
            events = _scan_sessions_cached(sessions_dir, conn)
            _annotate_events(events, group, root_cache, overrides)
            all_events.extend(events)

        all_events.sort(key=lambda e: e["timestamp"])
        return all_events
    except Exception:
        _LOG.warning("Cache scan failed, falling back to full scan", exc_info=True)
        return scan_all_projects(projects, overrides)
    finally:
        conn.close()
