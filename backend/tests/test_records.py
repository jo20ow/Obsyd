"""All-time records per series × zone — descriptive, evidence-linked.

The cheapest "wow" from the gridstatus repertoire: "highest DE-LU day-ahead
hour since 2015". Recomputed nightly by SQL min/max over the canonical store —
always correct, no incremental state. A plausibility guard keeps ENTSO-E
ingest glitches from being celebrated as records.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.power.hourly_store import upsert_hourly
from backend.power.records import PRICE_MAX_PLAUSIBLE, PRICE_MIN_PLAUSIBLE, compute_records


def _ts(days_ago: int, hour: int = 12) -> int:
    d = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return int(d.replace(hour=hour, minute=0, second=0, microsecond=0).timestamp())


def _seed_prices(db, zone="DE_LU", values=None):
    values = values or [(_ts(300), 50.0), (_ts(200), 120.0), (_ts(100), -10.0), (_ts(2), 80.0)]
    upsert_hourly(db, "price.dayahead", zone, values, unit="EUR/MWh")
    db.commit()


def test_compute_finds_max_and_min(db_session):
    _seed_prices(db_session)
    records = compute_records(db_session)

    recs = {(r.series_key, r.kind): r for r in records}
    hi = recs[("price.dayahead", "max")]
    assert hi.zone == "DE_LU"
    assert hi.value == 120.0
    assert hi.ts_utc == _ts(200)
    lo = recs[("price.dayahead", "min")]
    assert lo.value == -10.0


def test_compute_is_idempotent_and_updates_in_place(db_session):
    _seed_prices(db_session)
    compute_records(db_session)
    # a new all-time high arrives
    upsert_hourly(db_session, "price.dayahead", "DE_LU", [(_ts(1), 300.0)], unit="EUR/MWh")
    db_session.commit()
    compute_records(db_session)

    from backend.models.energy import PowerRecord

    rows = db_session.query(PowerRecord).filter_by(series_key="price.dayahead", zone="DE_LU", kind="max").all()
    assert len(rows) == 1, "one row per (series, zone, kind), updated in place"
    assert rows[0].value == 300.0


def test_price_glitches_are_not_records(db_session):
    """ENTSO-E hiccups have produced absurd points; a record must be plausible."""
    _seed_prices(db_session, values=[(_ts(300), 50.0), (_ts(10), 99_999.0), (_ts(5), -20_000.0)])
    compute_records(db_session)

    from backend.models.energy import PowerRecord

    hi = db_session.query(PowerRecord).filter_by(series_key="price.dayahead", kind="max").first()
    lo = db_session.query(PowerRecord).filter_by(series_key="price.dayahead", kind="min").first()
    assert hi.value == 50.0, f"glitch above {PRICE_MAX_PLAUSIBLE} must be ignored"
    assert lo.value == 50.0 or lo.value >= PRICE_MIN_PLAUSIBLE


# ─── route ────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    from backend.main import app

    app.dependency_overrides.clear()


def _client(db) -> TestClient:
    from backend.database import get_db
    from backend.main import app

    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app, raise_server_exceptions=True)


def test_route_returns_records_with_evidence_date(db_session):
    _seed_prices(db_session)
    compute_records(db_session)
    body = _client(db_session).get("/api/power/records?zone=DE_LU").json()

    assert body["available"] is True
    recs = {(r["series"], r["kind"]): r for r in body["records"]}
    hi = recs[("price.dayahead", "max")]
    assert hi["value"] == 120.0
    assert hi["date"] == datetime.fromtimestamp(_ts(200), tz=timezone.utc).strftime("%Y-%m-%d")
    assert hi["unit"] == "EUR/MWh"


def test_route_flags_fresh_records(db_session):
    """A record set in the last 7 days is the story — flag it."""
    _seed_prices(db_session)  # max 200d ago, but the -10 min is 100d ago; freshest point 2d ago is no record
    upsert_hourly(db_session, "price.dayahead", "DE_LU", [(_ts(1), 500.0)], unit="EUR/MWh")
    db_session.commit()
    compute_records(db_session)
    body = _client(db_session).get("/api/power/records?zone=DE_LU").json()

    recs = {(r["series"], r["kind"]): r for r in body["records"]}
    assert recs[("price.dayahead", "max")]["fresh"] is True
    assert recs[("price.dayahead", "min")]["fresh"] is False


def test_route_empty_is_honest(db_session):
    body = _client(db_session).get("/api/power/records?zone=DE_LU").json()
    assert body["available"] is False


def test_zero_load_hour_is_a_gap_artifact_not_a_record(db_session):
    """A '0 MW load' hour is an ENTSO-E data gap; it produced a live bogus
    all-time-min record (SI 2026-07-11). The guard must skip it and record the
    smallest PLAUSIBLE hour instead."""
    import time as _time

    from backend.power.hourly_store import upsert_hourly
    from backend.power.records import compute_records

    now = (int(_time.time()) // 3600) * 3600
    upsert_hourly(db_session, "load.actual", "SI", [
        (now - 3 * 3600, 0.0),       # gap artifact
        (now - 2 * 3600, 640.0),     # real minimum
        (now - 1 * 3600, 1_800.0),
    ], unit="MW")
    rows = compute_records(db_session)
    mins = [r for r in rows if r.series_key == "load.actual" and r.kind == "min"]
    assert len(mins) == 1 and mins[0].value == 640.0


def test_negative_residual_is_a_real_record(db_session):
    """Renewables exceeding load is real — the old 0-floor silently discarded
    the most interesting residual records."""
    import time as _time

    from backend.power.hourly_store import upsert_hourly
    from backend.power.records import compute_records

    now = (int(_time.time()) // 3600) * 3600
    upsert_hourly(db_session, "residual.actual", "DE_LU", [
        (now - 2 * 3600, -3_200.0),
        (now - 1 * 3600, 41_000.0),
    ], unit="MW")
    rows = compute_records(db_session)
    mins = [r for r in rows if r.series_key == "residual.actual" and r.kind == "min"]
    assert len(mins) == 1 and mins[0].value == -3_200.0
