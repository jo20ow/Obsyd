"""Collector freshness monitoring — must key on the DATA's delivery date.

The product-critical sources (ENTSO-E day-ahead/grid, Energy-Charts flows, gas
balance, yfinance prices) are re-written every night with overwrite=True, so an
ingestion-timestamp probe looks fresh even when the data itself is days stale.
The freshness spec for these sources therefore compares max(date-string) to today,
not the write timestamp.
"""
from __future__ import annotations

from datetime import datetime, timezone

from backend.collectors.freshness import evaluate_freshness
from backend.models.energy import PowerPriceDaily


def _add_dayahead(db, d: str, zone: str = "DE_LU"):
    db.add(PowerPriceDaily(date=d, zone=zone, mean_price=50.0, min_price=10.0, max_price=90.0, negative_hours=0))


def test_dayahead_fresh_when_date_is_recent(db_session):
    now = datetime(2026, 7, 2, 12, 0, tzinfo=timezone.utc)
    _add_dayahead(db_session, "2026-07-01")  # 1 day behind → within 2d window
    db_session.commit()
    result = evaluate_freshness(db_session, now=now)
    assert result["power_dayahead"]["fresh"] is True
    assert result["power_dayahead"]["last_seen"] == "2026-07-01"


def test_dayahead_stale_when_date_is_old(db_session):
    now = datetime(2026, 7, 2, 12, 0, tzinfo=timezone.utc)
    _add_dayahead(db_session, "2026-06-20")  # ~12 days behind → stale
    db_session.commit()
    result = evaluate_freshness(db_session, now=now)
    assert result["power_dayahead"]["fresh"] is False


def test_source_with_no_data_is_not_fresh(db_session):
    now = datetime(2026, 7, 2, 12, 0, tzinfo=timezone.utc)
    result = evaluate_freshness(db_session, now=now)
    # No rows anywhere → every source reports not-fresh with last_seen None.
    assert result["power_dayahead"]["fresh"] is False
    assert result["power_dayahead"]["last_seen"] is None
    assert result["gas_balance"]["fresh"] is False


def test_spec_covers_product_critical_sources(db_session):
    now = datetime(2026, 7, 2, 12, 0, tzinfo=timezone.utc)
    result = evaluate_freshness(db_session, now=now)
    for key in ("power_dayahead", "power_grid", "power_flows", "gas_balance", "ttf", "copper",
                "eia", "fred", "ais", "gdelt"):
        assert key in result
