"""Forecast-vs-actual error: how good was the published TSO forecast, in numbers.

Posture B: this describes the accuracy of ENTSO-E's OWN published forecast —
no model of ours, no forecast claim. Bias answers "does it lean high or low",
MAE answers "how far off is a typical hour". Only hours where both forecast
and actual exist count.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.power.hourly_store import upsert_hourly


def _hour(days_ago: int, hour: int) -> int:
    d = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return int(d.replace(hour=hour, minute=0, second=0, microsecond=0).timestamp())


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


def _seed(db, zone="DE_LU"):
    # Forecast always 1000 MW under actual for 2 days × 3 hours → bias +1000, mae 1000
    fc, ac = [], []
    for d in (1, 2):
        for h in (6, 12, 18):
            fc.append((_hour(d, h), 50_000.0))
            ac.append((_hour(d, h), 51_000.0))
    # one forecast hour without an actual — must not count
    fc.append((_hour(0, 23), 48_000.0))
    upsert_hourly(db, "load.forecast", zone, fc, unit="MW")
    upsert_hourly(db, "load.actual", zone, ac, unit="MW")
    db.commit()


def test_forecast_error_bias_and_mae(db_session):
    _seed(db_session)
    body = _client(db_session).get("/api/power/forecast-error?zone=DE_LU&series=load").json()

    assert body["available"] is True
    assert body["series"] == "load"
    assert body["n_hours"] == 6
    # bias = mean(actual − forecast): the TSO forecast leaned LOW by 1 GW
    assert body["bias_mw"] == pytest.approx(1000.0)
    assert body["mae_mw"] == pytest.approx(1000.0)


def test_forecast_error_wind_actual_is_the_genmix_sum(db_session):
    """There is no wind.actual series — realised wind is gen.B18 + gen.B19."""
    upsert_hourly(db_session, "wind.forecast", "DE_LU", [(_hour(1, 12), 10_000.0)], unit="MW")
    upsert_hourly(db_session, "gen.B18", "DE_LU", [(_hour(1, 12), 3_000.0)], unit="MW")
    upsert_hourly(db_session, "gen.B19", "DE_LU", [(_hour(1, 12), 5_000.0)], unit="MW")
    db_session.commit()
    body = _client(db_session).get("/api/power/forecast-error?zone=DE_LU&series=wind").json()
    assert body["available"] is True
    assert body["bias_mw"] == pytest.approx(-2000.0)  # wind UNDER-delivered vs forecast


def test_forecast_error_solar_maps_to_gen_b16(db_session):
    upsert_hourly(db_session, "solar.forecast", "DE_LU", [(_hour(1, 12), 6_000.0)], unit="MW")
    upsert_hourly(db_session, "gen.B16", "DE_LU", [(_hour(1, 12), 7_500.0)], unit="MW")
    db_session.commit()
    body = _client(db_session).get("/api/power/forecast-error?zone=DE_LU&series=solar").json()
    assert body["bias_mw"] == pytest.approx(1500.0)  # solar OVER-delivered


def test_forecast_error_rejects_unknown_series(db_session):
    r = _client(db_session).get("/api/power/forecast-error?zone=DE_LU&series=hack")
    assert r.status_code == 422


def test_forecast_error_empty_is_honest(db_session):
    body = _client(db_session).get("/api/power/forecast-error?zone=DE_LU&series=load").json()
    assert body["available"] is False
    assert "reason" in body
