"""Day-ahead load forecast vs actual: the /api/power/load-forecast join + error."""
from __future__ import annotations

from datetime import date, timedelta

from fastapi.testclient import TestClient

from backend.main import app
from backend.models.energy import PowerGrid, PowerLoadForecast


def test_load_forecast_joins_actual_error_and_forward(db_session):
    today = date.today()
    d2 = (today - timedelta(days=2)).isoformat()
    d1 = (today - timedelta(days=1)).isoformat()
    dtom = (today + timedelta(days=1)).isoformat()

    db_session.add_all([
        PowerLoadForecast(date=d2, zone="DE_LU", forecast_mw=50000.0),
        PowerLoadForecast(date=d1, zone="DE_LU", forecast_mw=60000.0),
        PowerLoadForecast(date=dtom, zone="DE_LU", forecast_mw=55000.0),  # forward (no actual yet)
        PowerGrid(date=d2, zone="DE_LU", load_mw=55000.0),  # actual +10% vs forecast
        PowerGrid(date=d1, zone="DE_LU", load_mw=57000.0),  # actual -5% vs forecast
    ])
    db_session.commit()

    body = TestClient(app).get("/api/power/load-forecast?zone=DE_LU").json()
    assert body["available"] is True
    rows = {d["date"]: d for d in body["data"]}
    assert rows[d2]["error_pct"] == 10.0
    assert rows[d1]["error_pct"] == -5.0
    assert rows[dtom]["actual_mw"] is None
    assert [f["date"] for f in body["forward"]] == [dtom]
    assert body["mape_pct"] == 7.5  # mean(|+10|, |-5|)


def test_load_forecast_unavailable_when_empty(db_session):
    body = TestClient(app).get("/api/power/load-forecast?zone=DE_LU").json()
    assert body["available"] is False
