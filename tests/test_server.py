"""Tests for server.py helper functions."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from codexusage.server import _week_bounds

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
