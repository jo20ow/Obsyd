"""Tests for the USGS minerals collector + ATLAS /resources endpoint."""

import pytest
from fastapi.testclient import TestClient

from backend.collectors.usgs_minerals import _records_from_rows
from backend.main import app
from backend.models.atlas import CountryResource


@pytest.fixture
def client(db_session):
    return TestClient(app)


def _row(comm, country, type_, unit="metric tons", p2023=None, p2024=None):
    return {"COMMODITY": comm, "COUNTRY": country, "TYPE": type_, "UNIT_MEAS": unit,
            "PROD_2023": p2023, "PROD_EST_ 2024": p2024}


def test_records_selects_production_and_maps_iso3():
    rows = [_row("Lithium", "Australia", "Mine production, lithium content", p2023="91,700", p2024="88000")]
    unmapped = set()
    recs = _records_from_rows(rows, unmapped)
    assert {r["period"] for r in recs} == {"2023", "2024"}
    r23 = next(r for r in recs if r["period"] == "2023")
    assert r23["iso3"] == "AUS" and r23["commodity"] == "lithium" and r23["value"] == 91700.0
    assert r23["unit"] == "metric tons" and not unmapped


def test_records_exclude_aggregate_wrongtype_nonnumeric():
    rows = [
        _row("Lithium", "Other Countries", "Mine production, lithium content", p2023="5000"),  # aggregate
        _row("Lithium", "Chile", "Reserves, lithium content", p2023="9300000"),  # not mine production
        _row("Lithium", "Argentina", "Mine production, lithium content", p2023="W"),  # withheld
    ]
    unmapped = set()
    assert _records_from_rows(rows, unmapped) == []
    assert not unmapped  # Chile/Argentina ARE mapped — excluded by type/value, not by mapping


def test_records_tracks_unmapped_country():
    rows = [_row("Gold", "Atlantis", "mine production, gold content", p2023="100")]
    unmapped = set()
    assert _records_from_rows(rows, unmapped) == [] and unmapped == {"Atlantis"}


def test_atlas_resources_endpoint(client, db_session):
    db_session.add_all([
        CountryResource(iso3="AUS", country_name="Australia", commodity="lithium", period="2024", value=88000, unit="metric tons"),
        CountryResource(iso3="AUS", country_name="Australia", commodity="lithium", period="2023", value=91700, unit="metric tons"),
        CountryResource(iso3="CHL", country_name="Chile", commodity="lithium", period="2024", value=49000, unit="metric tons"),
    ])
    db_session.commit()
    body = client.get("/api/atlas/resources?commodity=lithium").json()
    assert body["as_of"] == "2024" and body["coverage"] == 2 and "public domain" in body["source"]
    aus = next(c for c in body["countries"] if c["iso3"] == "AUS")
    assert aus["value"] == 88000 and aus["period"] == "2024"
    assert [c["iso3"] for c in body["countries"]] == ["AUS", "CHL"]
