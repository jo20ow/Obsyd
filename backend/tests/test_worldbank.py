"""Tests for the World Bank macro collector + ATLAS /macro endpoint."""

import pytest
from fastapi.testclient import TestClient

from backend.collectors.worldbank import _normalize_row
from backend.main import app
from backend.models.atlas import CountryMacro

_COUNTRIES = {"USA": "United States", "DEU": "Germany"}  # real-country ISO-3 set


@pytest.fixture
def client(db_session):
    return TestClient(app)


def test_normalize_keeps_real_country():
    row = {"countryiso3code": "USA", "country": {"value": "United States"}, "date": "2023", "value": 27360000000000.0}
    rec = _normalize_row(row, "gdp_usd", "NY.GDP.MKTP.CD", _COUNTRIES)
    assert rec["iso3"] == "USA" and rec["metric"] == "gdp_usd" and rec["period"] == "2023"
    assert rec["value"] == pytest.approx(27360000000000.0) and rec["country_name"] == "United States"


def test_normalize_drops_aggregate():
    # WLD (World) is not in the real-country set → dropped.
    row = {"countryiso3code": "WLD", "date": "2023", "value": 100000000000000.0}
    assert _normalize_row(row, "gdp_usd", "NY.GDP.MKTP.CD", _COUNTRIES) is None


def test_normalize_drops_null_value():
    row = {"countryiso3code": "DEU", "date": "2023", "value": None}
    assert _normalize_row(row, "gdp_usd", "NY.GDP.MKTP.CD", _COUNTRIES) is None


def test_atlas_macro_latest_year_per_country(client, db_session):
    db_session.add_all([
        CountryMacro(iso3="USA", country_name="United States", metric="gdp_usd", indicator_code="NY.GDP.MKTP.CD", period="2022", value=25.0e12),
        CountryMacro(iso3="USA", country_name="United States", metric="gdp_usd", indicator_code="NY.GDP.MKTP.CD", period="2023", value=27.0e12),
        CountryMacro(iso3="CHN", country_name="China", metric="gdp_usd", indicator_code="NY.GDP.MKTP.CD", period="2023", value=17.0e12),
        CountryMacro(iso3="USA", country_name="United States", metric="population", indicator_code="SP.POP.TOTL", period="2023", value=3.3e8),
    ])
    db_session.commit()

    body = client.get("/api/atlas/macro?metric=gdp_usd").json()
    assert body["as_of"] == "2023" and body["coverage"] == 2 and "CC BY 4.0" in body["source"]
    usa = next(c for c in body["countries"] if c["iso3"] == "USA")
    assert usa["value"] == 27.0e12 and usa["period"] == "2023"  # latest year only
    assert [c["iso3"] for c in body["countries"]] == ["USA", "CHN"]  # sorted by value desc
