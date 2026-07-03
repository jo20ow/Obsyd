"""Canonical hourly store: idempotent upsert + range read over power_hourly."""
from __future__ import annotations

# Import models early so Base.metadata knows the new tables before create_all.
from backend.models.energy import PowerHourly, SeriesDim, ZoneDim
from backend.power.hourly_store import (
    read_hourly,
    resolve_series_id,
    resolve_zone_id,
    upsert_hourly,
)

H = 3600
BASE = 1_700_000_000  # arbitrary fixed epoch (top-of-hour not required for the test)


def test_upsert_then_read_roundtrip(db_session):
    pts = [(BASE + i * H, 100.0 + i) for i in range(24)]
    n = upsert_hourly(db_session, "load.actual", "DE_LU", pts, unit="MW")
    assert n == 24
    got = read_hourly(db_session, "load.actual", "DE_LU")
    assert got == pts  # ordered by ts, values intact


def test_upsert_is_idempotent_and_overwrites(db_session):
    upsert_hourly(db_session, "price.dayahead", "FR", [(BASE, 50.0), (BASE + H, 60.0)])
    # Re-run with a changed value on an existing hour + one new hour.
    upsert_hourly(db_session, "price.dayahead", "FR", [(BASE, 55.0), (BASE + 2 * H, 70.0)])
    got = dict(read_hourly(db_session, "price.dayahead", "FR"))
    assert got == {BASE: 55.0, BASE + H: 60.0, BASE + 2 * H: 70.0}
    # No duplicate rows: exactly 3 for this series+zone.
    sid = resolve_series_id(db_session, "price.dayahead")
    zid = resolve_zone_id(db_session, "FR")
    cnt = (
        db_session.query(PowerHourly)
        .filter(PowerHourly.series_id == sid, PowerHourly.zone_id == zid)
        .count()
    )
    assert cnt == 3


def test_dims_created_once(db_session):
    upsert_hourly(db_session, "load.actual", "DE_LU", [(BASE, 1.0)])
    upsert_hourly(db_session, "load.actual", "DE_LU", [(BASE + H, 2.0)])
    assert db_session.query(ZoneDim).filter(ZoneDim.key == "DE_LU").count() == 1
    assert db_session.query(SeriesDim).filter(SeriesDim.key == "load.actual").count() == 1


def test_read_range_and_zone_isolation(db_session):
    upsert_hourly(db_session, "load.actual", "DE_LU", [(BASE + i * H, float(i)) for i in range(5)])
    upsert_hourly(db_session, "load.actual", "NL", [(BASE, 999.0)])
    window = read_hourly(db_session, "load.actual", "DE_LU", start_ts=BASE + H, end_ts=BASE + 3 * H)
    assert [ts for ts, _ in window] == [BASE + H, BASE + 2 * H]  # end exclusive
    # NL data must not leak into DE_LU.
    assert read_hourly(db_session, "load.actual", "DE_LU", start_ts=BASE, end_ts=BASE + H) == [(BASE, 0.0)]


def test_none_values_skipped(db_session):
    n = upsert_hourly(db_session, "solar.forecast", "DE_LU", [(BASE, 10.0), (BASE + H, None)])
    assert n == 1
    assert read_hourly(db_session, "solar.forecast", "DE_LU") == [(BASE, 10.0)]


def test_read_unknown_series_or_zone_returns_empty(db_session):
    assert read_hourly(db_session, "nope.series", "DE_LU") == []
    assert read_hourly(db_session, "load.actual", "ZZ") == []
