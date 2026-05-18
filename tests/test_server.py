"""Tests for server.py helper functions."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from codexusage.server import _aggregate, _week_bounds

_LON = ZoneInfo("Europe/London")


def lon(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=_LON)


class TestWeekBounds:
    def test_mid_week_returns_prior_friday(self):
        # Wednesday — should anchor to the previous Friday
        since, until = _week_bounds(lon(2026, 5, 6, 12))  # Wednesday noon
        assert since == "2026-05-01T17:00"
        assert until == "2026-05-08T17:00"

    def test_friday_after_reset_returns_current_friday(self):
        # Friday 17:01 — new week has just opened
        since, until = _week_bounds(lon(2026, 5, 8, 17, 1))
        assert since == "2026-05-08T17:00"
        assert until == "2026-05-15T17:00"

    def test_friday_before_reset_returns_previous_friday(self):
        # Friday 16:59 — new week hasn't started yet
        since, until = _week_bounds(lon(2026, 5, 8, 16, 59))
        assert since == "2026-05-01T17:00"
        assert until == "2026-05-08T17:00"

    def test_friday_exactly_at_reset(self):
        # Friday exactly 17:00 — new week starts
        since, until = _week_bounds(lon(2026, 5, 8, 17, 0))
        assert since == "2026-05-08T17:00"
        assert until == "2026-05-15T17:00"

    def test_sunday_mid_week(self):
        since, until = _week_bounds(lon(2026, 5, 10, 10))  # Sunday
        assert since == "2026-05-08T17:00"
        assert until == "2026-05-15T17:00"

    def test_bst_summer_time(self):
        # During BST (UTC+1): Friday 17:00 BST = 16:00 UTC.
        # datetime.hour reflects London clock (BST), so 17 == reset boundary.
        since, until = _week_bounds(lon(2026, 7, 10, 17, 1))  # Friday in BST
        assert since == "2026-07-10T17:00"
        assert until == "2026-07-17T17:00"

    def test_bst_friday_before_reset(self):
        since, until = _week_bounds(lon(2026, 7, 10, 16, 59))  # Friday in BST, before 17:00
        assert since == "2026-07-03T17:00"
        assert until == "2026-07-10T17:00"


# ── Session grouping helpers ──────────────────────────────────────────────────

_PRICING: dict = {
    "models": {"gpt-4o": {"input": 2.5, "cached_input": 1.25, "output": 10.0}},
    "prefix_fallback": [],
    "default": {"input": 2.5, "cached_input": 1.25, "output": 10.0},
}
_CFG: dict = {
    "credits_per_dollar": 100,
    "weekly_pool_credits": 1000,
    "projects": [],
}


def _evt(
    session_id: str,
    ts: str,
    *,
    thread_source: str | None = None,
    parent_session_uuid: str | None = None,
    agent_nickname: str | None = None,
) -> dict:
    return {
        "session_id": session_id,
        "timestamp": ts,
        "model": "gpt-4o",
        "input_tokens": 100,
        "cached_input_tokens": 0,
        "output_tokens": 50,
        "reasoning_output_tokens": 0,
        "total_tokens": 150,
        "reasoning_effort": None,
        "cwd": None,
        "project": "default",
        "auth_type": "oauth",
        "thread_source": thread_source,
        "parent_session_uuid": parent_session_uuid,
        "agent_nickname": agent_nickname,
    }


class TestSessionGrouping:
    def _agg(self, events: list[dict]) -> dict:
        return _aggregate(events, "2024-01-01", "2024-12-31", _PRICING, _CFG)

    def test_standalone_session_not_grouped(self) -> None:
        events = [_evt("2024/01/01/rollout-abc", "2024-06-01T10:00:00Z", thread_source="user")]
        result = self._agg(events)
        assert len(result["sessions"]) == 1
        s = result["sessions"][0]
        assert s["session_id"] == "2024/01/01/rollout-abc"
        assert s.get("subagents", []) == []
        assert s["total_usd"] == s["own_usd"]

    def test_subagent_removed_from_top_level(self) -> None:
        parent_uuid = "019e2723-6036-7b81-936e-c99ebb64ca09"
        parent_sid = f"2024/06/01/rollout-{parent_uuid}"
        child_sid = "2024/06/01/rollout-child-aaa"
        events = [
            _evt(parent_sid, "2024-06-01T10:00:00Z", thread_source="user"),
            _evt(
                child_sid,
                "2024-06-01T10:05:00Z",
                thread_source="subagent",
                parent_session_uuid=parent_uuid,
                agent_nickname="Confucius",
            ),
        ]
        result = self._agg(events)
        top_ids = [s["session_id"] for s in result["sessions"]]
        assert parent_sid in top_ids
        assert child_sid not in top_ids

    def test_subagent_appears_in_parent_subagents_list(self) -> None:
        parent_uuid = "019e2723-6036-7b81-936e-c99ebb64ca09"
        parent_sid = f"2024/06/01/rollout-{parent_uuid}"
        child_sid = "2024/06/01/rollout-child-aaa"
        events = [
            _evt(parent_sid, "2024-06-01T10:00:00Z", thread_source="user"),
            _evt(
                child_sid,
                "2024-06-01T10:05:00Z",
                thread_source="subagent",
                parent_session_uuid=parent_uuid,
                agent_nickname="Confucius",
            ),
        ]
        result = self._agg(events)
        parent = next(s for s in result["sessions"] if s["session_id"] == parent_sid)
        assert len(parent["subagents"]) == 1
        child = parent["subagents"][0]
        assert child["session_id"] == child_sid
        assert child["agent_nickname"] == "Confucius"

    def test_total_usd_includes_subagent_costs(self) -> None:
        parent_uuid = "019e2723-6036-7b81-936e-c99ebb64ca09"
        parent_sid = f"2024/06/01/rollout-{parent_uuid}"
        child_sid = "2024/06/01/rollout-child-aaa"
        events = [
            _evt(parent_sid, "2024-06-01T10:00:00Z", thread_source="user"),
            _evt(
                child_sid,
                "2024-06-01T10:05:00Z",
                thread_source="subagent",
                parent_session_uuid=parent_uuid,
            ),
        ]
        result = self._agg(events)
        parent = next(s for s in result["sessions"] if s["session_id"] == parent_sid)
        assert parent["total_usd"] == round(
            parent["own_usd"] + parent["subagents"][0]["own_usd"], 4
        )
        assert parent["total_usd"] > parent["own_usd"]

    def test_orphan_subagent_stays_top_level(self) -> None:
        # Subagent whose parent is not in the event window → remains a top-level entry.
        child_sid = "2024/06/01/rollout-child-aaa"
        events = [
            _evt(
                child_sid,
                "2024-06-01T10:05:00Z",
                thread_source="subagent",
                parent_session_uuid="no-parent-in-window",
            ),
        ]
        result = self._agg(events)
        assert len(result["sessions"]) == 1
        assert result["sessions"][0]["session_id"] == child_sid
