"""Single-glance power overview: /api/power/overview (all zones at once)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from backend.main import app
from backend.models.energy import (
    EnergyPrice,
    InstalledCapacity,
    PowerGenMix,
    PowerGrid,
    PowerOutage,
    PowerPriceDaily,
)
from backend.routes.power import load_power_situation, load_power_situations_bulk


def test_overview_empty_is_unavailable(db_session):
    body = TestClient(app).get("/api/power/overview").json()
    assert body["available"] is False
    assert body["zones"] == []


def test_overview_returns_seeded_zone_with_state(db_session):
    # One day of price + grid for DE_LU is enough for load_power_situation to be "available".
    db_session.add(PowerPriceDaily(date="2026-07-02", zone="DE_LU", mean_price=71.0,
                                   min_price=40.0, max_price=95.0, negative_hours=0))
    db_session.add(PowerGrid(date="2026-07-02", zone="DE_LU", load_mw=55000.0,
                             wind_mw=8000.0, solar_mw=6000.0, residual_mw=41000.0))
    db_session.commit()

    body = TestClient(app).get("/api/power/overview").json()
    assert body["available"] is True
    de = next((z for z in body["zones"] if z["zone"] == "DE_LU"), None)
    assert de is not None
    assert de["state"] in ("CALM", "ELEVATED", "STRESSED")
    assert de["price_close"] == 71.0
    assert de["residual_gw"] is not None


# ─── batching (bulk loader) ───────────────────────────────────────────────────
# The bulk loader must agree with the per-zone path bit for bit (it feeds the
# same pure build_power_situation), and do so in a FIXED number of queries —
# the endpoint is the default EUROPE tab and the old loop was a 37×N+1.


def _seed_zone(db, zone, *, base_price, load, wind, solar, days=40, gen_coverage=0.8):
    today = date.today()
    for i in range(days):
        d = (today - timedelta(days=days - 1 - i)).isoformat()
        db.add(PowerPriceDaily(date=d, zone=zone, mean_price=base_price + (i % 7),
                               min_price=base_price - 30, max_price=base_price + 40,
                               negative_hours=0))
        db.add(PowerGrid(date=d, zone=zone, load_mw=load, wind_mw=wind, solar_mw=solar))
        db.add(PowerGenMix(date=d, zone=zone, psr_type="Fossil Gas",
                           gen_mw=load * gen_coverage))


def _seed_multi(db):
    today = date.today()
    _seed_zone(db, "DE_LU", base_price=80.0, load=55_000.0, wind=9_000.0, solar=5_000.0)
    _seed_zone(db, "FR", base_price=60.0, load=48_000.0, wind=3_000.0, solar=4_000.0)
    # NL: incomplete A75 coverage (~33% of load) → renewable share untrusted
    _seed_zone(db, "NL", base_price=70.0, load=12_000.0, wind=100.0, solar=80.0,
               gen_coverage=0.33)

    for i in range(10):  # spark legs
        d = (today - timedelta(days=9 - i)).isoformat()
        db.add(EnergyPrice(date=d, symbol="TTF", close=35.0))
        db.add(EnergyPrice(date=d, symbol="POWER_DE", close=82.0))

    # Outages: superseding revision counts, withdrawn vanishes; plus A68 capacity.
    now = datetime.now(timezone.utc)
    fmt = "%Y-%m-%dT%H:%MZ"
    common = dict(doc_type="A77", zone="DE_LU", business_type="A54", psr_type="B14",
                  unit_name="Block A", unit_eic="11W", location="DE",
                  start_utc=(now - timedelta(days=1)).strftime(fmt),
                  end_utc=(now + timedelta(days=3)).strftime(fmt))
    db.add(PowerOutage(mrid="m1", revision=1, nominal_mw=900.0, available_mw=0.0,
                       status="active", **common))
    db.add(PowerOutage(mrid="m1", revision=2, nominal_mw=700.0, available_mw=0.0,
                       status="active", **common))
    db.add(PowerOutage(mrid="wd", revision=1, nominal_mw=5_000.0, available_mw=0.0,
                       status="active", **common))
    db.add(PowerOutage(mrid="wd", revision=2, nominal_mw=5_000.0, available_mw=0.0,
                       status="withdrawn", **common))
    db.add(InstalledCapacity(zone="DE_LU", year=2026, psr_type="Solar", capacity_mw=200_000.0))
    db.commit()


def test_bulk_matches_per_zone_path(db_session):
    _seed_multi(db_session)
    bulk = load_power_situations_bulk(db_session)
    for zone in ("DE_LU", "FR", "NL"):
        assert bulk[zone] == load_power_situation(db_session, zone), zone


def test_bulk_matches_per_zone_on_empty_zones(db_session):
    _seed_multi(db_session)
    bulk = load_power_situations_bulk(db_session)
    for zone in ("AT", "SE3"):
        if zone in bulk:  # only enabled zones are built
            assert bulk[zone] == load_power_situation(db_session, zone), zone


def test_bulk_uses_fixed_query_count(db_session):
    """Budget leaves headroom but catches any reintroduced N+1."""
    _seed_multi(db_session)
    statements = []

    def _count(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", _count)
    try:
        load_power_situations_bulk(db_session)
    finally:
        event.remove(engine, "before_cursor_execute", _count)
    assert len(statements) <= 12, f"{len(statements)} SELECTs — bulk loader regressed to N+1"


def test_forced_outage_totals_now_matches_per_zone(db_session):
    from backend.signals.detectors.power import (
        forced_outage_mw_now,
        forced_outage_totals_now,
    )

    _seed_multi(db_session)
    totals = forced_outage_totals_now(db_session)
    per_zone, _ = forced_outage_mw_now(db_session, "DE_LU")
    # Highest revision of m1 (700 MW) counts; withdrawn wd vanishes entirely.
    assert per_zone == pytest.approx(700.0)
    assert totals == {"DE_LU": pytest.approx(700.0)}
