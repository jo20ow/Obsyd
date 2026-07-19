"""Tests for GET /api/power/grid (residual load + Dunkelflaute)."""
from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.models.energy import PowerGrid
from backend.routes.power import _compute_grid_row


@pytest.fixture(autouse=True)
def _clear_dependency_overrides():
    """_make_client installs an app.dependency_overrides[get_db]; clear it after
    every test so the override never leaks into other test files' TestClients."""
    yield
    from backend.main import app

    app.dependency_overrides.clear()


# ─── helpers ─────────────────────────────────────────────────────────────────

# Dates well within the last 120 days so route window tests find them.
_TODAY = date.today()
_D1 = (_TODAY - timedelta(days=3)).isoformat()
_D2 = (_TODAY - timedelta(days=2)).isoformat()
_D3 = (_TODAY - timedelta(days=1)).isoformat()


def _make_row(**kwargs) -> SimpleNamespace:
    """Return a plain object that mimics a PowerGrid row (no SQLAlchemy state)."""
    return SimpleNamespace(
        date=kwargs.get("date", "2026-01-01"),
        zone="DE_LU",
        load_mw=kwargs.get("load_mw", None),
        wind_mw=kwargs.get("wind_mw", None),
        solar_mw=kwargs.get("solar_mw", None),
        # A settled day, unless a test says otherwise: 24 hours of load, generation in each of them.
        load_hours=kwargs.get("load_hours", 24),
        gen_hours=kwargs.get("gen_hours", 24),
    )


# ─── unit tests for _compute_grid_row ────────────────────────────────────────


def test_compute_residual_basic():
    """residual_mw = load − wind − solar."""
    row = _make_row(load_mw=50_000.0, wind_mw=10_000.0, solar_mw=5_000.0)
    d = _compute_grid_row(row)
    assert d["residual_mw"] == pytest.approx(35_000.0)


def test_compute_renewable_share():
    """renewable_share = (wind + solar) / load."""
    row = _make_row(load_mw=40_000.0, wind_mw=4_000.0, solar_mw=4_000.0)
    d = _compute_grid_row(row)
    assert d["renewable_share"] == pytest.approx(0.2)


def test_whole_day_generation_blackout_gives_no_residual_not_full_load():
    """gen_hours=0 (A75 feed down all day) leaves wind/solar None because UNKNOWN.
    Reading them as 0 would invent residual = full load and a 0% renewable share.
    Load is present, generation is not → residual/share must be None."""
    row = _make_row(load_mw=45_000.0, wind_mw=None, solar_mw=None, gen_hours=0)
    d = _compute_grid_row(row)
    assert d["residual_mw"] is None
    assert d["renewable_share"] is None


def test_zone_with_no_wind_or_solar_but_generation_present_still_computes():
    """gen_hours>0 with no wind/solar (e.g. an all-thermal day) is a REAL 0% share
    and residual == load — must not be suppressed."""
    row = _make_row(load_mw=30_000.0, wind_mw=None, solar_mw=None, gen_hours=24)
    d = _compute_grid_row(row)
    assert d["residual_mw"] == pytest.approx(30_000.0)
    assert d["renewable_share"] == pytest.approx(0.0)


def test_a_single_row_makes_no_dunkelflaute_claim():
    """A Dunkelflaute is a judgment against the zone's own history — a lone row cannot make it.

    This function used to answer it with a flat `share < 15%`, and /grid, /overview and the hero
    published that answer while the radar (cured of exactly that predicate in #100) published a
    different one. The verdict now comes from power/dunkelflaute.py, filled in by the route;
    the row's own default is False, never a claim. See test_dunkelflaute_parity.py.
    """
    dark = _compute_grid_row(_make_row(load_mw=50_000.0, wind_mw=3_000.0, solar_mw=2_000.0))
    assert dark["renewable_share"] == pytest.approx(0.1)
    assert dark["dunkelflaute"] is False, "no history in scope → no claim"


def test_null_wind_treated_as_zero():
    """wind_mw=None → treated as 0 in all calculations."""
    row = _make_row(load_mw=50_000.0, wind_mw=None, solar_mw=5_000.0)
    d = _compute_grid_row(row)
    assert d["residual_mw"] == pytest.approx(45_000.0)
    assert d["renewable_share"] == pytest.approx(0.1)  # 5k / 50k


def test_null_solar_treated_as_zero():
    """solar_mw=None → treated as 0 in all calculations."""
    row = _make_row(load_mw=50_000.0, wind_mw=15_000.0, solar_mw=None)
    d = _compute_grid_row(row)
    assert d["residual_mw"] == pytest.approx(35_000.0)
    assert d["renewable_share"] == pytest.approx(0.3)  # 15k / 50k


def test_null_wind_and_solar_treated_as_zero():
    """Both wind_mw=None and solar_mw=None → residual = load, share = 0."""
    row = _make_row(load_mw=50_000.0, wind_mw=None, solar_mw=None)
    d = _compute_grid_row(row)
    assert d["residual_mw"] == pytest.approx(50_000.0)
    assert d["renewable_share"] == pytest.approx(0.0)


def test_no_load_means_no_residual_and_no_share():
    """Residual load is demand minus renewables. With no demand there is no
    residual — it is None, not zero, and certainly not negative.

    This is live on prod: IE-SEM stopped publishing A65 load on 2025-10-23, and
    coercing the missing load to 0.0 made the desk render residual = −(wind+solar)
    — a NEGATIVE residual load — plus a 0% renewable share, out of nothing."""
    row = _make_row(load_mw=None, wind_mw=1_090.0, solar_mw=0.0)
    d = _compute_grid_row(row)
    assert d["residual_mw"] is None, "no load → no residual (it read −1090 MW)"
    assert d["renewable_share"] is None
    assert d["dunkelflaute"] is False, "cannot claim a Dunkelflaute without a load"

    # A stored zero load is an ENTSO-E gap, not a grid with no demand.
    zero = _compute_grid_row(_make_row(load_mw=0.0, wind_mw=0.0, solar_mw=0.0))
    assert zero["residual_mw"] is None and zero["renewable_share"] is None


# ─── route integration tests ──────────────────────────────────────────────────


def _seed_grid(db: Session, rows: list[dict]) -> None:
    for r in rows:
        db.add(
            PowerGrid(
                date=r["date"],
                zone="DE_LU",
                load_mw=r.get("load_mw"),
                wind_mw=r.get("wind_mw"),
                solar_mw=r.get("solar_mw"),
                load_hours=r.get("load_hours", 24),
                gen_hours=r.get("gen_hours", 24),
            )
        )
    db.commit()


def _make_client(db: Session) -> TestClient:
    """Build a TestClient with the test DB injected via dependency_overrides."""
    from backend.database import get_db
    from backend.main import app

    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app, raise_server_exceptions=True)
    return client


def test_route_available_true(db_session):
    """Seeded rows within window → available=True, data non-empty, fields present."""
    _seed_grid(db_session, [
        {"date": _D1, "load_mw": 50_000.0, "wind_mw": 10_000.0, "solar_mw": 5_000.0},
        {"date": _D2, "load_mw": 45_000.0, "wind_mw": 2_000.0, "solar_mw": 1_000.0},
    ])
    client = _make_client(db_session)
    resp = client.get("/api/power/grid?days=120")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert len(body["data"]) == 2
    # Check derived fields on the earlier row
    row = next(r for r in body["data"] if r["date"] == _D1)
    assert row["residual_mw"] == pytest.approx(35_000.0)
    assert row["renewable_share"] == pytest.approx(0.3)
    assert row["dunkelflaute"] is False


def test_route_available_false_when_empty(db_session):
    """No rows → available=False."""
    client = _make_client(db_session)
    resp = client.get("/api/power/grid?days=120")
    assert resp.status_code == 200
    assert resp.json()["available"] is False


def test_route_latest_is_the_most_recent_row(db_session):
    """latest reflects the most recent row. (What makes a day a Dunkelflaute — and what makes
    dunkelflaute_days count it — is pinned in test_dunkelflaute_parity.py, which needs the zone's
    own history to say so; three days of it cannot support the claim, and no longer pretend to.)"""
    _seed_grid(db_session, [
        {"date": _D1, "load_mw": 50_000.0, "wind_mw": 3_000.0, "solar_mw": 2_000.0},
        {"date": _D2, "load_mw": 50_000.0, "wind_mw": 8_000.0, "solar_mw": 2_000.0},
        {"date": _D3, "load_mw": 50_000.0, "wind_mw": 3_000.0, "solar_mw": 1_000.0},
    ])
    client = _make_client(db_session)
    resp = client.get("/api/power/grid?days=120")
    body = resp.json()
    assert body["latest"]["date"] == _D3
    assert body["latest"]["residual_mw"] == pytest.approx(46_000.0)
    assert body["dunkelflaute_days"] == 0, "no history, no coverage → no claim"


def test_route_null_wind_solar_treated_as_zero(db_session):
    """Rows with null wind/solar are handled gracefully."""
    _seed_grid(db_session, [
        {"date": _D1, "load_mw": 50_000.0, "wind_mw": None, "solar_mw": None},
    ])
    client = _make_client(db_session)
    resp = client.get("/api/power/grid?days=120")
    body = resp.json()
    assert body["available"] is True
    row = body["data"][0]
    assert row["residual_mw"] == pytest.approx(50_000.0)
    assert row["renewable_share"] == pytest.approx(0.0)
