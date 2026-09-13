"""The premium preview gate (backend/premium.py): the analytics products are
gated, not advertised-but-locked — hidden means the free tier never sees them
in the catalog, gets 403/401 at every data exit, and receives capture
responses without the floor0 variant. The comp subscription (grant_pro) is
today's only key, which makes hidden testing and premium gating one code path."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.power.hourly_store import upsert_hourly
from backend.premium import is_premium_series


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    from backend.main import app

    app.dependency_overrides.clear()


def _client(db, *, pro: bool = False) -> TestClient:
    from backend.auth.dependencies import optional_pro, require_pro
    from backend.database import get_db
    from backend.main import app

    app.dependency_overrides[get_db] = lambda: db
    if pro:
        app.dependency_overrides[optional_pro] = lambda: True
        app.dependency_overrides[require_pro] = lambda: {"email": "owner@test"}
    return TestClient(app, raise_server_exceptions=True)


def test_premium_series_classification():
    assert is_premium_series("conv.full.FR")
    assert is_premium_series("conv.spread.DK1")
    assert is_premium_series("spread.tb2")
    assert is_premium_series("capture.B16.price_floor0")
    # The free tier keeps everything that was free before the analytics phase.
    assert not is_premium_series("price.dayahead")
    assert not is_premium_series("spread.da_imbalance")
    assert not is_premium_series("capture.B16.price")
    assert not is_premium_series("capture.B16.factor")
    assert not is_premium_series("co2.intensity.lifecycle")


def _seed_tb(db):
    ts = int(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp())
    upsert_hourly(db, "spread.tb2", "DE_LU", [(ts, 225.0)], unit="EUR/MW-day")
    upsert_hourly(db, "conv.full.FR", "DE_LU", [(ts, 20.0)], unit="h")


def test_series_and_snapshot_refuse_premium_without_pro(db_session):
    _seed_tb(db_session)
    c = _client(db_session)
    assert c.get("/api/v1/series?series=spread.tb2&zone=DE_LU").status_code == 403
    assert c.get("/api/v1/series?series=conv.full.FR&zone=DE_LU").status_code == 403
    assert c.get("/api/v1/snapshot?series=spread.tb2").status_code == 403
    # CSV and parquet ride the same endpoint — the gate fires before format.
    assert c.get("/api/v1/series?series=spread.tb2&zone=DE_LU&format=csv").status_code == 403


def test_series_serves_premium_with_pro(db_session):
    _seed_tb(db_session)
    c = _client(db_session, pro=True)
    body = c.get("/api/v1/series?series=spread.tb2&zone=DE_LU&start=2026-08-01").json()
    assert body["available"] is True
    assert body["data"][0]["value"] == 225.0


def test_catalog_hides_premium_for_free_and_lists_it_for_pro(db_session):
    _seed_tb(db_session)
    free_keys = {s["key"] for s in _client(db_session).get("/api/v1/series/catalog").json()["series"]}
    assert "spread.tb2" not in free_keys and "conv.full.FR" not in free_keys
    pro_keys = {s["key"] for s in
                _client(db_session, pro=True).get("/api/v1/series/catalog").json()["series"]}
    assert "spread.tb2" in pro_keys and "conv.full.FR" in pro_keys


def test_convergence_endpoint_requires_pro(db_session):
    assert _client(db_session).get("/api/power/convergence").status_code == 401
    body = _client(db_session, pro=True).get("/api/power/convergence").json()
    assert body["available"] is False  # empty test DB answers honestly, not 4xx


def test_capture_route_omits_floor0_for_free_sessions(db_session):
    # One full month of prices+solar (the derived-stats seed, inlined).
    t0 = int(datetime(2026, 4, 1, tzinfo=timezone.utc).timestamp())
    prices, solar = [], []
    for day in range(30):
        for h in range(24):
            ts = t0 + day * 86400 + h * 3600
            prices.append((ts, 40.0 if 10 <= h <= 14 else 100.0))
            if 10 <= h <= 14:
                solar.append((ts, 1000.0))
    upsert_hourly(db_session, "price.dayahead", "DE_LU", prices, unit="EUR/MWh")
    upsert_hourly(db_session, "gen.B16", "DE_LU", solar, unit="MW")

    free = _client(db_session).get("/api/power/capture?zone=DE_LU&months=120").json()
    assert free["available"] is True
    row = free["fuels"][0]["latest"]
    assert "capture_price" in row and "capture_price_floor0" not in row
    assert "floor" not in free["note"]

    pro = _client(db_session, pro=True).get("/api/power/capture?zone=DE_LU&months=120").json()
    assert pro["fuels"][0]["latest"]["capture_price_floor0"] == pro["fuels"][0]["latest"]["capture_price"]
    assert "floor" in pro["note"]
