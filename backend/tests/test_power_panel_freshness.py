"""Every power detail endpoint must state how old its data is.

The panels used to show a neutral "latest {date}" caption — a hung feed looked
identical to a healthy one. Each endpoint now returns `as_of`/`age_days`/`stale`,
with thresholds mirroring backend/collectors/freshness.py::SPECS so the panel
captions and /api/health/collectors can never disagree.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.models.energy import (
    EnergyPrice,
    PowerFlow,
    PowerGenMix,
    PowerGrid,
    PowerLoadForecast,
    PowerPriceDaily,
)

# UTC, not local: the routes bucket on datetime.utcnow().date(). With a local
# date.today() these tests fail for the two hours between local and UTC midnight
# (same fix as test_power_situation.py).
_TODAY = datetime.now(timezone.utc).date()


@pytest.fixture(autouse=True)
def _clear_dependency_overrides():
    yield
    from backend.main import app

    app.dependency_overrides.clear()


def _client(db: Session) -> TestClient:
    from backend.database import get_db
    from backend.main import app

    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app, raise_server_exceptions=True)


def _seed_all(db: Session, end: date, n: int = 3) -> None:
    for i in range(n):
        d = (end - timedelta(days=n - 1 - i)).isoformat()
        db.add(PowerPriceDaily(date=d, zone="DE_LU", mean_price=60.0, min_price=20.0,
                               max_price=90.0, negative_hours=0))
        db.add(PowerGrid(date=d, zone="DE_LU", load_mw=50_000.0, wind_mw=10_000.0,
                         solar_mw=5_000.0, residual_mw=35_000.0))
        db.add(PowerGenMix(date=d, zone="DE_LU", psr_type="Solar", gen_mw=5_000.0))
        db.add(PowerFlow(date=d, from_zone="DE_LU", to_zone="FR", net_mw=1_000.0))
        # /spark-spread computes live from the POWER_DE + TTF price series
        db.add(EnergyPrice(date=d, symbol="POWER_DE", close=60.0))
        db.add(EnergyPrice(date=d, symbol="TTF", close=30.0))
        db.add(PowerLoadForecast(date=d, zone="DE_LU", forecast_mw=50_000.0,
                                 wind_forecast_mw=10_000.0, solar_forecast_mw=5_000.0))
    db.commit()


ENDPOINTS = [
    "/api/power/day-ahead?zone=DE_LU",
    "/api/power/grid?zone=DE_LU",
    "/api/power/generation-mix?zone=DE_LU",
    "/api/power/flows",
    "/api/power/spark-spread",
    "/api/power/load-forecast?zone=DE_LU",
]


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_stale_data_is_declared(db_session, endpoint):
    old_end = _TODAY - timedelta(days=10)
    _seed_all(db_session, old_end)
    body = _client(db_session).get(endpoint).json()

    assert body["available"] is True
    assert body["as_of"] == old_end.isoformat(), endpoint
    assert body["age_days"] == 10, endpoint
    assert body["stale"] is True, endpoint


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_fresh_data_is_not_stale(db_session, endpoint):
    _seed_all(db_session, _TODAY)
    body = _client(db_session).get(endpoint).json()

    assert body["available"] is True
    assert body["as_of"] == _TODAY.isoformat(), endpoint
    assert body["age_days"] == 0, endpoint
    assert body["stale"] is False, endpoint


def test_forecast_frontier_in_the_future_is_not_stale(db_session):
    """The load forecast legitimately extends into tomorrow (D+1)."""
    _seed_all(db_session, _TODAY + timedelta(days=1))
    body = _client(db_session).get("/api/power/load-forecast?zone=DE_LU").json()
    assert body["stale"] is False
    assert body["age_days"] == -1


def test_panel_thresholds_match_health_specs():
    """UI captions and /api/health/collectors must share one truth."""
    from backend.collectors.freshness import SPECS
    from backend.routes.power import PANEL_MAX_AGE_DAYS

    by_key = {s.key.split(":")[0]: s.max_age.days for s in SPECS}
    assert PANEL_MAX_AGE_DAYS["day_ahead"] == by_key["power_dayahead"]
    assert PANEL_MAX_AGE_DAYS["grid"] == by_key["power_grid"]
    assert PANEL_MAX_AGE_DAYS["flows"] == by_key["power_flows"]
    assert PANEL_MAX_AGE_DAYS["flows_hourly"] == by_key["flows_hourly"]
    assert PANEL_MAX_AGE_DAYS["imbalance"] == by_key["imbalance_qh"]
    assert PANEL_MAX_AGE_DAYS["spark"] == by_key["ttf"]
