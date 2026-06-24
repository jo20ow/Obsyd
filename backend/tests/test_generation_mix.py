"""Tests for PowerGenMix model + GET /api/power/generation-mix route."""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.models.energy import PowerGenMix


@pytest.fixture(autouse=True)
def _clear_dependency_overrides():
    """Prevent dependency_overrides leaking into other test files."""
    yield
    from backend.main import app
    app.dependency_overrides.clear()


# ─── helpers ─────────────────────────────────────────────────────────────────

_TODAY = date.today()
_D1 = (_TODAY - timedelta(days=3)).isoformat()
_D2 = (_TODAY - timedelta(days=2)).isoformat()
_D3 = (_TODAY - timedelta(days=1)).isoformat()


def _seed_mix(db: Session, rows: list[dict]) -> None:
    for r in rows:
        db.add(
            PowerGenMix(
                date=r["date"],
                zone=r.get("zone", "DE_LU"),
                psr_type=r["psr_type"],
                gen_mw=r["gen_mw"],
            )
        )
    db.commit()


def _make_client(db: Session) -> TestClient:
    from backend.database import get_db
    from backend.main import app
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app, raise_server_exceptions=True)


# ─── model / upsert helpers ───────────────────────────────────────────────────


def test_model_unique_constraint(db_session):
    """Inserting duplicate (date, zone, psr_type) raises an integrity error."""
    import sqlalchemy.exc

    db_session.add(
        PowerGenMix(date=_D1, zone="DE_LU", psr_type="Solar", gen_mw=5000.0)
    )
    db_session.commit()

    db_session.add(
        PowerGenMix(date=_D1, zone="DE_LU", psr_type="Solar", gen_mw=6000.0)
    )
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        db_session.commit()


# ─── route: available=False when empty ───────────────────────────────────────


def test_route_empty_returns_available_false(db_session):
    client = _make_client(db_session)
    resp = client.get("/api/power/generation-mix?days=30")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False


# ─── route: pivot + types ─────────────────────────────────────────────────────


def test_route_pivot_structure(db_session):
    """Seeded rows are pivoted wide: each data row has per-type MW keys."""
    _seed_mix(db_session, [
        {"date": _D1, "psr_type": "Solar",        "gen_mw": 4000.0},
        {"date": _D1, "psr_type": "Wind Onshore",  "gen_mw": 8000.0},
        {"date": _D1, "psr_type": "Nuclear",        "gen_mw": 6000.0},
        {"date": _D2, "psr_type": "Solar",         "gen_mw": 3500.0},
        {"date": _D2, "psr_type": "Wind Onshore",   "gen_mw": 9000.0},
        {"date": _D2, "psr_type": "Nuclear",         "gen_mw": 6100.0},
    ])
    client = _make_client(db_session)
    resp = client.get("/api/power/generation-mix?days=30")
    assert resp.status_code == 200
    body = resp.json()

    assert body["available"] is True
    assert set(body["types"]) == {"Nuclear", "Solar", "Wind Onshore"}
    assert len(body["data"]) == 2

    row = next(r for r in body["data"] if r["date"] == _D1)
    assert row["Solar"] == pytest.approx(4000.0)
    assert row["Wind Onshore"] == pytest.approx(8000.0)
    assert row["Nuclear"] == pytest.approx(6000.0)


def test_route_latest_total_mw(db_session):
    """latest.total_mw = sum of all types for the most recent date."""
    _seed_mix(db_session, [
        {"date": _D1, "psr_type": "Solar",       "gen_mw": 5000.0},
        {"date": _D1, "psr_type": "Wind Onshore", "gen_mw": 7000.0},
        {"date": _D2, "psr_type": "Solar",        "gen_mw": 3000.0},
        {"date": _D2, "psr_type": "Wind Onshore",  "gen_mw": 6000.0},
        {"date": _D2, "psr_type": "Nuclear",        "gen_mw": 8000.0},
    ])
    client = _make_client(db_session)
    resp = client.get("/api/power/generation-mix?days=30")
    body = resp.json()

    assert body["latest"]["date"] == _D2
    assert body["latest"]["total_mw"] == pytest.approx(17000.0)


def test_route_types_list_sorted(db_session):
    """types list is alphabetically sorted for stable output."""
    _seed_mix(db_session, [
        {"date": _D1, "psr_type": "Wind Onshore", "gen_mw": 5000.0},
        {"date": _D1, "psr_type": "Nuclear",       "gen_mw": 6000.0},
        {"date": _D1, "psr_type": "Solar",         "gen_mw": 4000.0},
    ])
    client = _make_client(db_session)
    resp = client.get("/api/power/generation-mix?days=30")
    body = resp.json()
    assert body["types"] == sorted(body["types"])


def test_route_zone_and_unit(db_session):
    """Response includes zone=DE_LU and unit=MW."""
    _seed_mix(db_session, [
        {"date": _D1, "psr_type": "Solar", "gen_mw": 1000.0},
    ])
    client = _make_client(db_session)
    resp = client.get("/api/power/generation-mix?days=30")
    body = resp.json()
    assert body["zone"] == "DE_LU"
    assert body["unit"] == "MW"


def test_route_data_sorted_ascending(db_session):
    """data rows are sorted by date ascending."""
    _seed_mix(db_session, [
        {"date": _D3, "psr_type": "Solar", "gen_mw": 1000.0},
        {"date": _D1, "psr_type": "Solar", "gen_mw": 2000.0},
        {"date": _D2, "psr_type": "Solar", "gen_mw": 3000.0},
    ])
    client = _make_client(db_session)
    resp = client.get("/api/power/generation-mix?days=30")
    body = resp.json()
    dates = [r["date"] for r in body["data"]]
    assert dates == sorted(dates)


def test_route_days_window_filters(db_session):
    """Only rows within the requested days window are returned."""
    old_date = (_TODAY - timedelta(days=60)).isoformat()
    _seed_mix(db_session, [
        {"date": _D1,     "psr_type": "Solar", "gen_mw": 1000.0},
        {"date": old_date, "psr_type": "Solar", "gen_mw": 2000.0},
    ])
    client = _make_client(db_session)
    resp = client.get("/api/power/generation-mix?days=30")
    body = resp.json()
    assert body["available"] is True
    assert len(body["data"]) == 1
    assert body["data"][0]["date"] == _D1
