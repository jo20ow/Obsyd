"""Gas detail endpoints must state how old their data is — same contract as the
power panels (as_of/age_days/stale), same freshness derivation."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.models.gas import GasBalance, GasDemandModel, GasLng, GasPowerBurn, GasStorage

# UTC, not local: the routes bucket on datetime.utcnow().date(). With a local
# date.today() these tests fail for the two hours between local and UTC midnight
# (same fix as test_power_situation.py).
_TODAY = datetime.now(timezone.utc).date()


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    from backend.main import app

    app.dependency_overrides.clear()


def _client(db: Session) -> TestClient:
    from backend.database import get_db
    from backend.main import app

    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app, raise_server_exceptions=True)


def _seed(db: Session, end: date) -> None:
    for i in range(3):
        d = (end - timedelta(days=2 - i)).isoformat()
        db.add(GasStorage(date=d, stock_twh=800.0, injection_gwh=100.0, withdrawal_gwh=50.0, fill_pct=70.0))
        db.add(GasLng(date=d, send_out_gwh=3000.0, inventory_twh=5.0))
        db.add(GasBalance(date=d, residual_7d=-10.0, z_score=0.1, flag=None))
        db.add(GasPowerBurn(date=d, gen_gwh_el=1000.0, implied_gas_gwh=2000.0))
        db.add(GasDemandModel(date=d, heat_gwh=1000.0, industrial_gwh=2000.0, model_version="t"))
    db.commit()


ENDPOINTS = ["/api/gas/storage", "/api/gas/lng", "/api/gas/balance",
             "/api/gas/power-burn", "/api/gas/demand"]


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_stale_gas_data_is_declared(db_session, endpoint):
    old_end = _TODAY - timedelta(days=10)
    _seed(db_session, old_end)
    body = _client(db_session).get(endpoint).json()

    assert body["available"] is True
    assert body["as_of"] == old_end.isoformat(), endpoint
    assert body["age_days"] == 10, endpoint
    assert body["stale"] is True, endpoint


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_fresh_gas_data_is_not_stale(db_session, endpoint):
    _seed(db_session, _TODAY)
    body = _client(db_session).get(endpoint).json()
    assert body["stale"] is False, endpoint
    assert body["age_days"] == 0, endpoint
