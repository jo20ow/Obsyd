"""Tests for the EIA International per-country energy collector + ATLAS endpoint."""

import pytest
from fastapi.testclient import TestClient

from backend.collectors.eia_international import _normalize_row
from backend.main import app
from backend.models.atlas import CountryEnergy


@pytest.fixture
def client(db_session):
    return TestClient(app)


# ─── _normalize_row: keep countries, drop aggregates / invalid ───────────────


def test_normalize_keeps_country():
    row = {"countryRegionId": "SAU", "countryRegionTypeId": "c", "countryRegionName": "Saudi Arabia",
           "value": "9748.4", "unit": "TBPD", "period": "2023"}
    rec = _normalize_row(row, "petroleum", "production", "TBPD")
    assert rec["iso3"] == "SAU" and rec["value"] == pytest.approx(9748.4)
    assert rec["product"] == "petroleum" and rec["activity"] == "production" and rec["unit"] == "TBPD"


def test_normalize_drops_region_aggregate():
    row = {"countryRegionId": "WORL", "countryRegionTypeId": "r", "value": "82060.0", "unit": "TBPD", "period": "2023"}
    assert _normalize_row(row, "petroleum", "production", "TBPD") is None


def test_normalize_drops_missing_or_nonnumeric_value():
    base = {"countryRegionId": "DEU", "countryRegionTypeId": "c", "period": "2023"}
    assert _normalize_row({**base, "value": None}, "petroleum", "production", "TBPD") is None
    assert _normalize_row({**base, "value": "n/a"}, "petroleum", "production", "TBPD") is None


# ─── /api/atlas/energy ───────────────────────────────────────────────────────


def test_atlas_energy_returns_latest_year_per_country(client, db_session):
    db_session.add_all([
        CountryEnergy(iso3="USA", country_name="United States", product="petroleum", activity="production", period="2022", value=18000.0, unit="TBPD"),
        CountryEnergy(iso3="USA", country_name="United States", product="petroleum", activity="production", period="2023", value=19000.0, unit="TBPD"),
        CountryEnergy(iso3="SAU", country_name="Saudi Arabia", product="petroleum", activity="production", period="2023", value=9700.0, unit="TBPD"),
        # different activity must not leak in
        CountryEnergy(iso3="USA", country_name="United States", product="petroleum", activity="consumption", period="2023", value=20000.0, unit="TBPD"),
    ])
    db_session.commit()

    body = client.get("/api/atlas/energy?product=petroleum&activity=production").json()
    assert body["as_of"] == "2023" and body["coverage"] == 2
    assert "public domain" in body["source"]
    usa = next(c for c in body["countries"] if c["iso3"] == "USA")
    assert usa["value"] == 19000.0 and usa["period"] == "2023"  # latest year only
    # sorted by value desc → USA before SAU
    assert [c["iso3"] for c in body["countries"]] == ["USA", "SAU"]
