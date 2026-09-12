"""Derived-statistics series (backend/power/derived_stats.py): both are
MIRRORS of an existing single source of truth — every test here guards the
mirror property (never a recomputation that could drift) and the promotion
wiring (records band, ledger exclusion, catalog, freshness)."""
from __future__ import annotations

from datetime import datetime, timezone

from backend.models.energy import PowerPriceDaily, PowerRevision, SeriesDim
from backend.power.derived_stats import (
    NEGATIVE_HOURS_SERIES,
    store_capture,
    store_negative_hours,
)
from backend.power.hourly_store import (
    REVISION_EXCLUDED_PREFIXES,
    day_hour_ts,
    read_hourly,
    upsert_hourly,
)


def _seed_daily(db, zone="DE_LU", rows=(("2026-05-01", 5), ("2026-05-02", 0))):
    for d, n in rows:
        db.add(PowerPriceDaily(date=d, zone=zone, mean_price=50.0,
                               min_price=-10.0, max_price=120.0, negative_hours=n))
    db.commit()


# ─── the negative-hours mirror ───────────────────────────────────────────────


def test_negative_hours_mirror_matches_the_daily_table(db_session):
    _seed_daily(db_session)
    written = store_negative_hours(db_session, "DE_LU")
    assert written == 2
    rows = read_hourly(db_session, NEGATIVE_HOURS_SERIES, "DE_LU")
    assert rows == [(day_hour_ts("2026-05-01", 0), 5.0), (day_hour_ts("2026-05-02", 0), 0.0)]


def test_zone_without_dayahead_gets_no_series(db_session):
    assert store_negative_hours(db_session, "GB") == 0
    assert read_hourly(db_session, NEGATIVE_HOURS_SERIES, "GB") == []


def test_restated_day_overwrites_without_ledgering(db_session):
    _seed_daily(db_session)
    store_negative_hours(db_session, "DE_LU")
    row = db_session.query(PowerPriceDaily).filter_by(date="2026-05-01", zone="DE_LU").one()
    row.negative_hours = 7
    db_session.commit()
    store_negative_hours(db_session, "DE_LU")
    assert read_hourly(db_session, NEGATIVE_HOURS_SERIES, "DE_LU")[0][1] == 7.0

    assert any(NEGATIVE_HOURS_SERIES.startswith(p) for p in REVISION_EXCLUDED_PREFIXES)
    sids = [i for (i,) in db_session.query(SeriesDim.id)
            .filter(SeriesDim.key == NEGATIVE_HOURS_SERIES).all()]
    assert db_session.query(PowerRevision).filter(PowerRevision.series_id.in_(sids)).count() == 0


def test_records_band_caps_at_a_real_day():
    from backend.power.records import RECORD_SERIES, _bounds

    assert NEGATIVE_HOURS_SERIES in RECORD_SERIES
    assert _bounds(NEGATIVE_HOURS_SERIES) == (0.0, 24.0)  # not the ±4000 price band


# ─── the capture mirror ──────────────────────────────────────────────────────


def _seed_capture_month(db, zone="DE_LU"):
    """One full month of hourly prices + solar generation: price 100 all hours,
    solar 1000 MW in hours 10-14 at price 40 — capture 40, baseload computed."""
    month_hours = []
    t0 = int(datetime(2026, 4, 1, tzinfo=timezone.utc).timestamp())
    prices, solar = [], []
    for day in range(30):
        for h in range(24):
            ts = t0 + day * 86400 + h * 3600
            prices.append((ts, 40.0 if 10 <= h <= 14 else 100.0))
            if 10 <= h <= 14:
                solar.append((ts, 1000.0))
    upsert_hourly(db, "price.dayahead", zone, prices, unit="EUR/MWh")
    upsert_hourly(db, "gen.B16", zone, solar, unit="MW")


def test_capture_series_equal_the_engine_exactly(db_session):
    from backend.power.capture import compute_capture

    _seed_capture_month(db_session)
    today = datetime(2026, 5, 10, tzinfo=timezone.utc).date()
    written = store_capture(db_session, "DE_LU", months=2, today=today)
    assert written >= 2  # price + factor for at least solar

    engine = compute_capture(db_session, "DE_LU", months=2, today=today)
    solar = next(f for f in engine["fuels"] if f["psr"] == "B16")["data"][-1]
    ts = int(datetime(2026, 4, 1, tzinfo=timezone.utc).timestamp())
    (got_ts, got_price), = read_hourly(db_session, "capture.B16.price", "DE_LU")
    (_, got_factor), = read_hourly(db_session, "capture.B16.factor", "DE_LU")
    assert (got_ts, got_price) == (ts, solar["capture_price"])
    assert got_factor == solar["value_factor"]
    # and the numbers themselves are the seeded arithmetic:
    assert got_price == 40.0
    assert round(got_factor, 3) == round(40.0 / (100 * 19 / 24 + 40 * 5 / 24), 3)


def test_capture_withheld_month_stays_absent(db_session):
    """A zone with no complete month writes nothing — absence, never a zero."""
    assert store_capture(db_session, "FR", months=3) == 0
    assert read_hourly(db_session, "capture.B16.factor", "FR") == []


# ─── wiring ──────────────────────────────────────────────────────────────────


def test_catalog_and_freshness_wiring():
    from backend.collectors.freshness import SPECS
    from backend.power.series_catalog import GROUP_LABELS, series_label

    assert series_label(NEGATIVE_HOURS_SERIES) != NEGATIVE_HOURS_SERIES
    assert series_label("capture.B16.factor") == "Capture · Solar · value factor"
    assert series_label("capture.B04.price") == "Capture · Fossil Gas · capture price"
    assert "capture" in GROUP_LABELS
    keys = {s.key for s in SPECS}
    assert {"negative_hours_series", "capture_series"} <= keys
