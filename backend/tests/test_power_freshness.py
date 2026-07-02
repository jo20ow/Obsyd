"""Day-ahead ingest window: the scheduler must request the published price frontier.

ENTSO-E publishes day-ahead prices for delivery day D (and D+1) the afternoon
before. The old `_power_recent_days` ended at *yesterday*, so the newest stored
delivery day was always behind the frontier — the desk showed a 1-2 day lag even
when the collector ran fine. The window must reach *tomorrow*.
"""
from __future__ import annotations

from datetime import date

from backend.collectors.scheduler import _power_recent_days


def test_power_recent_days_reaches_tomorrow():
    today = date(2026, 7, 2)
    days = _power_recent_days(7, today=today)
    assert days[-1] == "2026-07-03"      # tomorrow = published day-ahead frontier
    assert "2026-07-02" in days          # today included
    assert days == sorted(days)          # ascending
    assert len(days) == 7


def test_power_recent_days_defaults_to_wallclock():
    # Called without `today` (production path), it must still return n ascending days.
    days = _power_recent_days(5)
    assert len(days) == 5
    assert days == sorted(days)
