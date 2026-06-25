"""Tests for the /api/atlas/criticality endpoint (supply concentration — the product wedge)."""

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.models.atlas import CountryEnergy, CountryResource


@pytest.fixture
def client(db_session):
    return TestClient(app)


def test_criticality_concentration(client, db_session):
    db_session.add_all([
        CountryResource(iso3="CHN", country_name="China", commodity="rare_earths", period="2024", value=270000, unit="metric tons"),
        CountryResource(iso3="USA", country_name="United States", commodity="rare_earths", period="2024", value=45000, unit="metric tons"),
        CountryResource(iso3="AUS", country_name="Australia", commodity="rare_earths", period="2024", value=13000, unit="metric tons"),
        # oil: more spread out → lower HHI than rare earths
        CountryEnergy(iso3="USA", country_name="United States", product="petroleum", activity="production", period="2024", value=20000, unit="TBPD"),
        CountryEnergy(iso3="SAU", country_name="Saudi Arabia", product="petroleum", activity="production", period="2024", value=11000, unit="TBPD"),
        CountryEnergy(iso3="RUS", country_name="Russia", product="petroleum", activity="production", period="2024", value=10000, unit="TBPD"),
    ])
    db_session.commit()

    body = client.get("/api/atlas/criticality").json()
    mats = {m["key"]: m for m in body["materials"]}

    re = mats["rare_earths"]
    assert re["top_country"] == "CHN" and re["top_share"] > 0.8 and re["hhi"] > 0.6
    assert re["top3"][0]["iso3"] == "CHN" and re["producers"] == 3 and re["as_of"] == "2024"

    oil = mats["oil"]
    assert oil["top_country"] == "USA" and oil["hhi"] < re["hhi"]

    # most-concentrated first → rare earths ahead of oil
    assert body["materials"][0]["key"] == "rare_earths"
    assert "public domain" in body["source"]


def test_criticality_skips_material_with_no_data(client, db_session):
    # No rows at all → no materials, but the endpoint still responds cleanly.
    body = client.get("/api/atlas/criticality").json()
    assert body["materials"] == []
