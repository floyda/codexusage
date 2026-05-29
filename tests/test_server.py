"""Tests for server.py helper functions."""

from __future__ import annotations

from datetime import datetime, timezone

from codexusage.server import _aggregate, _release_schedule, _rolling_bounds

_UTC = timezone.utc


def utc(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=_UTC)


class TestRollingBounds:
    def test_returns_7_day_window(self):
        now = utc(2026, 5, 29, 12, 0)
        since, until = _rolling_bounds(now)
        assert since == "2026-05-22T12:00"
        assert until == "2026-05-29T12:00"

    def test_spans_midnight(self):
        now = utc(2026, 5, 29, 3, 30)
        since, until = _rolling_bounds(now)
        assert since == "2026-05-22T03:30"
        assert until == "2026-05-29T03:30"

    def test_spans_month_boundary(self):
        now = utc(2026, 6, 3, 10, 15)
        since, until = _rolling_bounds(now)
        assert since == "2026-05-27T10:15"
        assert until == "2026-06-03T10:15"


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
    auth_type: str = "oauth",
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
        "auth_type": auth_type,
        "thread_source": thread_source,
        "parent_session_uuid": parent_session_uuid,
        "agent_nickname": agent_nickname,
    }


class TestReleaseSchedule:
    def test_events_in_window_produce_schedule(self):
        now = utc(2026, 5, 29, 12, 0)
        events = [_evt("s1", "2026-05-25T10:30:00Z")]
        schedule = _release_schedule(events, now, _PRICING, 100)
        assert len(schedule) == 1
        assert schedule[0]["at"] == "2026-06-01T10:00"
        assert schedule[0]["credits_releasing"] > 0

    def test_events_outside_window_excluded(self):
        now = utc(2026, 5, 29, 12, 0)
        # Event older than 7 days
        events = [_evt("s1", "2026-05-21T10:00:00Z")]
        schedule = _release_schedule(events, now, _PRICING, 100)
        assert schedule == []

    def test_non_oauth_events_excluded(self):
        now = utc(2026, 5, 29, 12, 0)
        events = [_evt("s1", "2026-05-25T10:30:00Z", auth_type="api_token")]
        schedule = _release_schedule(events, now, _PRICING, 100)
        assert schedule == []

    def test_multiple_events_same_hour_grouped(self):
        now = utc(2026, 5, 29, 12, 0)
        # Both events expire in the same hour bucket
        events = [
            _evt("s1", "2026-05-25T10:10:00Z"),
            _evt("s2", "2026-05-25T10:45:00Z"),
        ]
        schedule = _release_schedule(events, now, _PRICING, 100)
        assert len(schedule) == 1
        assert schedule[0]["at"] == "2026-06-01T10:00"

    def test_schedule_sorted_ascending(self):
        now = utc(2026, 5, 29, 12, 0)
        events = [
            _evt("s2", "2026-05-27T15:00:00Z"),
            _evt("s1", "2026-05-23T09:00:00Z"),
        ]
        schedule = _release_schedule(events, now, _PRICING, 100)
        assert len(schedule) == 2
        assert schedule[0]["at"] < schedule[1]["at"]


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


class TestAggregateDayFill:
    """Zero-fill covers the correct number of calendar days for different bound types."""

    def _agg(self, events: list[dict], since: str, until: str) -> dict:
        return _aggregate(events, since, until, _PRICING, _CFG)

    def _dates(self, since: str, until: str) -> list[str]:
        return [d["date"] for d in self._agg([], since, until)["days"]]

    def test_rolling_window_shows_8_days(self) -> None:
        # Rolling 7-day window with time components: both boundary dates must appear.
        dates = self._dates("2026-05-22T12:00", "2026-05-29T12:00")
        assert dates == [
            "2026-05-22",
            "2026-05-23",
            "2026-05-24",
            "2026-05-25",
            "2026-05-26",
            "2026-05-27",
            "2026-05-28",
            "2026-05-29",
        ]

    def test_date_only_range_is_exclusive_of_until(self) -> None:
        # Date-only until is fully out-of-range — 7 days, not 8.
        dates = self._dates("2026-05-16", "2026-05-23")
        assert dates == [
            "2026-05-16",
            "2026-05-17",
            "2026-05-18",
            "2026-05-19",
            "2026-05-20",
            "2026-05-21",
            "2026-05-22",
        ]

    def test_event_within_rolling_window_appears_with_data(self) -> None:
        # An event at 09:00 on the last day is within the rolling window.
        event = _evt("s1", "2026-05-29T09:00:00Z")
        result = self._agg([event], "2026-05-22T12:00", "2026-05-29T12:00")
        day_map = {d["date"]: d for d in result["days"]}
        assert "2026-05-29" in day_map
        assert day_map["2026-05-29"]["total_tokens"] > 0

    def test_pool_always_reflects_rolling_window(self) -> None:
        # Even when querying a historical range, pool stats come from the rolling window.
        # Event is in rolling window (recent) but outside the historical query range.
        now = utc(2026, 5, 29, 12, 0)
        recent_event = _evt("s1", "2026-05-28T10:00:00Z")  # in rolling window
        result = _aggregate([recent_event], "2026-05-01", "2026-05-15", _PRICING, _CFG, now=now)
        # Pool reflects rolling window (includes the recent event)
        assert result["pool"]["used"] > 0
        # But the queried range has no events
        assert result["totals"]["total_tokens"] == 0
