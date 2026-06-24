"""Multi-zone route tests for the ENERGY vertical.

Tests:
  - /api/power/grid returns correct zone, zones list, and available=True
  - /api/power/day-ahead returns correct zone + zones list
  - /api/power/generation-mix returns correct zone + zones list
  - Unknown zone falls back to DE_LU
  - Default zone (no param) is DE_LU
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

# Import models early so Base.metadata knows about these tables
# before the db_session fixture calls create_all.
from backend.models.energy import PowerGenMix, PowerGrid, PowerPriceDaily


@pytest.fixture(autouse=True)
def _clear_dependency_overrides():
    """Clear app.dependency_overrides after every test."""
    yield
    from backend.main import app
    app.dependency_overrides.clear()


_TODAY = date.today()
_D1 = (_TODAY - timedelta(days=3)).isoformat()
_D2 = (_TODAY - timedelta(days=2)).isoformat()


def _make_client(db):
    from backend.database import get_db
    from backend.main import app
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app, raise_server_exceptions=True)


def _seed_grid(db, zone: str):
    db.add(PowerGrid(
        date=_D1, zone=zone, load_mw=50_000.0, wind_mw=8_000.0, solar_mw=4_000.0
    ))
    db.add(PowerGrid(
        date=_D2, zone=zone, load_mw=45_000.0, wind_mw=2_000.0, solar_mw=1_000.0
    ))
    db.commit()


def _seed_price_daily(db, zone: str, symbol: str):
    db.add(PowerPriceDaily(
        date=_D1, zone=zone, mean_price=75.0, min_price=40.0, max_price=110.0, negative_hours=0.0
    ))
    db.add(PowerPriceDaily(
        date=_D2, zone=zone, mean_price=-5.0, min_price=-20.0, max_price=30.0, negative_hours=3.0
    ))
    db.commit()


def _seed_genmix(db, zone: str):
    for date_str in (_D1, _D2):
        db.add(PowerGenMix(date=date_str, zone=zone, psr_type="Solar", gen_mw=3_000.0))
        db.add(PowerGenMix(date=date_str, zone=zone, psr_type="Wind Onshore", gen_mw=5_000.0))
    db.commit()


# ─── /api/power/grid ──────────────────────────────────────────────────────────

def test_grid_de_lu_default(db_session):
    """No zone param → default DE_LU."""
    _seed_grid(db_session, "DE_LU")
    client = _make_client(db_session)
    resp = client.get("/api/power/grid?days=120")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["zone"] == "DE_LU"
    assert "FR" in body["zones"]
    assert "NL" in body["zones"]
    assert "DE_LU" in body["zones"]
    assert len(body["data"]) == 2


def test_grid_fr_zone(db_session):
    """?zone=FR returns FR zone data."""
    _seed_grid(db_session, "FR")
    client = _make_client(db_session)
    resp = client.get("/api/power/grid?days=120&zone=FR")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["zone"] == "FR"
    assert set(body["zones"]) == {"DE_LU", "FR", "NL"}
    assert len(body["data"]) == 2


def test_grid_nl_zone(db_session):
    """?zone=NL returns NL zone data."""
    _seed_grid(db_session, "NL")
    client = _make_client(db_session)
    resp = client.get("/api/power/grid?days=120&zone=NL")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["zone"] == "NL"


def test_grid_unknown_zone_falls_back_to_de_lu(db_session):
    """Unknown zone falls back to DE_LU silently."""
    _seed_grid(db_session, "DE_LU")
    client = _make_client(db_session)
    resp = client.get("/api/power/grid?days=120&zone=XX_INVALID")
    assert resp.status_code == 200
    body = resp.json()
    # Falls back to DE_LU
    assert body["zone"] == "DE_LU"
    assert body["available"] is True


def test_grid_zone_isolation(db_session):
    """FR zone data does not appear when querying DE_LU."""
    _seed_grid(db_session, "FR")
    client = _make_client(db_session)
    resp = client.get("/api/power/grid?days=120&zone=DE_LU")
    assert resp.status_code == 200
    body = resp.json()
    # DE_LU has no data → unavailable
    assert body["available"] is False
    assert body["zone"] == "DE_LU"
    assert "zones" in body


# ─── /api/power/day-ahead ────────────────────────────────────────────────────

def test_day_ahead_de_lu_default(db_session):
    """No zone param → default DE_LU."""
    _seed_price_daily(db_session, "DE_LU", "POWER_DE")
    client = _make_client(db_session)
    resp = client.get("/api/power/day-ahead?days=120")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["zone"] == "DE_LU"
    assert set(body["zones"]) == {"DE_LU", "FR", "NL"}


def test_day_ahead_fr_zone(db_session):
    """?zone=FR returns FR zone price data."""
    _seed_price_daily(db_session, "FR", "POWER_FR")
    client = _make_client(db_session)
    resp = client.get("/api/power/day-ahead?days=120&zone=FR")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["zone"] == "FR"
    assert body["symbol"] == "POWER_FR"
    # One negative day (D2 has negative_hours=3)
    assert body["negative_days"] == 1
    assert len(body["data"]) == 2


def test_day_ahead_unknown_zone_falls_back(db_session):
    """Unknown zone falls back to DE_LU."""
    _seed_price_daily(db_session, "DE_LU", "POWER_DE")
    client = _make_client(db_session)
    resp = client.get("/api/power/day-ahead?days=120&zone=BOGUS")
    assert resp.status_code == 200
    body = resp.json()
    assert body["zone"] == "DE_LU"


# ─── /api/power/generation-mix ───────────────────────────────────────────────

def test_generation_mix_fr_zone(db_session):
    """?zone=FR returns FR generation mix."""
    _seed_genmix(db_session, "FR")
    client = _make_client(db_session)
    resp = client.get("/api/power/generation-mix?days=120&zone=FR")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["zone"] == "FR"
    assert set(body["zones"]) == {"DE_LU", "FR", "NL"}
    assert "Solar" in body["types"]
    assert "Wind Onshore" in body["types"]


def test_generation_mix_zone_isolation(db_session):
    """NL mix data does not bleed into FR query."""
    _seed_genmix(db_session, "NL")
    client = _make_client(db_session)
    resp = client.get("/api/power/generation-mix?days=120&zone=FR")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["zone"] == "FR"


def test_generation_mix_default_de_lu(db_session):
    """No zone param → default DE_LU."""
    _seed_genmix(db_session, "DE_LU")
    client = _make_client(db_session)
    resp = client.get("/api/power/generation-mix?days=120")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["zone"] == "DE_LU"
