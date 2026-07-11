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
    assert result["power_dayahead:DE_LU"]["fresh"] is True
    assert result["power_dayahead:DE_LU"]["last_seen"] == "2026-07-01"


def test_dayahead_stale_when_date_is_old(db_session):
    now = datetime(2026, 7, 2, 12, 0, tzinfo=timezone.utc)
    _add_dayahead(db_session, "2026-06-20")  # ~12 days behind → stale
    db_session.commit()
    result = evaluate_freshness(db_session, now=now)
    assert result["power_dayahead:DE_LU"]["fresh"] is False


def test_source_with_no_data_is_not_fresh(db_session):
    now = datetime(2026, 7, 2, 12, 0, tzinfo=timezone.utc)
    result = evaluate_freshness(db_session, now=now)
    # No rows anywhere → every source reports not-fresh with last_seen None.
    assert result["power_dayahead:DE_LU"]["fresh"] is False
    assert result["power_dayahead:DE_LU"]["last_seen"] is None
    assert result["gas_balance"]["fresh"] is False


def test_spec_covers_product_critical_sources(db_session):
    now = datetime(2026, 7, 2, 12, 0, tzinfo=timezone.utc)
    result = evaluate_freshness(db_session, now=now)
    for key in ("power_dayahead:DE_LU", "power_grid:DE_LU", "power_flows", "gas_balance", "ttf", "copper",
                "eia", "fred", "ais", "gdelt"):
        assert key in result


# ─── the new verticals must be watched too (the 2026-07 outage lesson) ────────
#
# hydro.reservoir, the QH series, generation.forecast and the outage collector
# were added without freshness specs — a stalled collector would have gone
# unnoticed exactly like the original incident.


def _add_hourly(db, series: str, days_ago: int, value: float = 1.0, zone: str = "DE_LU"):
    from datetime import timedelta

    from backend.power.hourly_store import upsert_hourly

    ts = int((datetime.now(timezone.utc) - timedelta(days=days_ago)).timestamp())
    upsert_hourly(db, series, zone, [(ts, value)], unit="X")


def test_hourly_series_specs_exist_and_track_data_age(db_session):
    _add_hourly(db_session, "price.dayahead.qh", days_ago=1)
    _add_hourly(db_session, "hydro.reservoir", days_ago=30)  # beyond its 16d window
    db_session.commit()
    result = evaluate_freshness(db_session)

    assert result["price_qh"]["fresh"] is True
    assert result["hydro_reservoir"]["fresh"] is False


def test_hourly_series_without_data_is_not_fresh(db_session):
    result = evaluate_freshness(db_session)
    assert result["generation_forecast"]["fresh"] is False
    assert result["imbalance_qh"]["fresh"] is False


def test_outage_collector_is_watched(db_session):
    from backend.models.energy import PowerOutage

    db_session.add(PowerOutage(
        mrid="m", revision=1, doc_type="A77", zone="DE_LU", business_type="A53",
        start_utc="2026-07-01T00:00Z", end_utc="2026-08-01T00:00Z", status="active",
    ))
    db_session.commit()
    result = evaluate_freshness(db_session)
    assert "power_outages" in result
    assert result["power_outages"]["fresh"] is True  # created_at is now
