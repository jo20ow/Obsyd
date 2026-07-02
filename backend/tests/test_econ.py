"""Economic release calendar: pure filter + endpoint (no network)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.econ.fred_calendar import parse_calendar
from backend.main import app
from backend.routes import econ as econ_routes


@pytest.fixture
def client():
    return TestClient(app)


def test_parse_calendar_filters_curated_future_deduped():
    sample = [
        {"release_id": 50, "release_name": "Employment Situation", "date": "2026-07-03"},
        {"release_id": 742, "release_name": "Bankrate Monitor National Index", "date": "2026-07-03"},  # not curated
        {"release_id": 10, "release_name": "Consumer Price Index", "date": "2026-07-15"},
        {"release_id": 10, "release_name": "Consumer Price Index", "date": "2026-06-15"},  # past → dropped
        {"release_id": 50, "release_name": "Employment Situation", "date": "2026-07-03"},  # dup → deduped
    ]
    out = parse_calendar(sample, "2026-07-02")
    assert [(x["date"], x["label"]) for x in out] == [
        ("2026-07-03", "Jobs report — payrolls & unemployment"),
        ("2026-07-15", "CPI — consumer inflation"),
    ]


def test_calendar_endpoint_available(client, monkeypatch):
    async def fake_cal(days_ahead=21):
        return [{"date": "2026-07-03", "release": "Employment Situation", "label": "Jobs report — payrolls & unemployment"}]

    monkeypatch.setattr(econ_routes, "get_calendar", fake_cal)
    body = client.get("/api/econ/calendar").json()
    assert body["available"] is True and len(body["data"]) == 1


def test_calendar_endpoint_unavailable(client, monkeypatch):
    async def empty(days_ahead=21):
        return []

    monkeypatch.setattr(econ_routes, "get_calendar", empty)
    assert client.get("/api/econ/calendar").json()["available"] is False
