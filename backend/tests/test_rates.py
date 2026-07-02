"""Rates vertical: US Treasury yield curve assembled from FREDSeries."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.models.prices import FREDSeries


@pytest.fixture
def client(db_session):
    from backend.database import get_db
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_curve_ordered_latest_per_tenor_with_spread(client, db_session):
    # DGS2 has two dates → the latest wins.
    db_session.add(FREDSeries(series_id="DGS2", date="2026-07-01", value=4.5))
    db_session.add(FREDSeries(series_id="DGS2", date="2026-07-02", value=4.6))
    db_session.add(FREDSeries(series_id="DGS10", date="2026-07-02", value=4.2))
    db_session.add(FREDSeries(series_id="DGS3MO", date="2026-07-02", value=5.1))
    db_session.commit()

    body = client.get("/api/rates/curve").json()
    assert body["available"] is True
    assert [p["tenor"] for p in body["data"]] == ["3M", "2Y", "10Y"]  # ascending maturity
    dgs2 = next(p for p in body["data"] if p["series_id"] == "DGS2")
    assert dgs2["yield"] == 4.6  # latest
    assert body["spread_10y2y"] == -0.4  # 4.2 - 4.6
    assert body["inverted"] is True
    assert body["as_of"] == "2026-07-02"


def test_curve_empty(client, db_session):
    assert client.get("/api/rates/curve").json()["available"] is False


def test_history_ascending(client, db_session):
    for i, d in enumerate(["2026-06-30", "2026-07-01", "2026-07-02"]):
        db_session.add(FREDSeries(series_id="DGS10", date=d, value=4.0 + i * 0.1))
    db_session.commit()
    body = client.get("/api/rates/history?series=dgs10&days=90").json()
    assert body["available"] is True and body["series"] == "DGS10"
    assert len(body["data"]) == 3
    assert body["data"] == sorted(body["data"], key=lambda x: x["date"])


def test_history_unknown_series(client, db_session):
    assert client.get("/api/rates/history?series=NOPE").json()["available"] is False
