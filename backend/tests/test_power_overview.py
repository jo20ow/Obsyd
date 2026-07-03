"""Single-glance power overview: /api/power/overview (all zones at once)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from backend.main import app
from backend.models.energy import PowerGrid, PowerPriceDaily


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
