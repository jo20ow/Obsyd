"""A71/A33 is not the installed fleet, and the whole risk of this table is that it looks like it.

    DE-LU   A71/A33:  133 units,  65,193 MW      FR   A71/A33:  174 units,  93,903 MW
            A68    :             294,941 MW           A68    :             163,611 MW
                                 ──────────                                ──────────
                                  factor 4.5                                factor 1.7

It lists only units above ENTSO-E's ~100 MW publication threshold: a different population, not a
smaller sample of the same one — and the ratio is not even constant (NL: 2.7), so no correction
factor could turn one into the other. Wiring it in as a denominator for the A68-calibrated outage
thresholds would fire far more often, with the 19 A68 zones and the 18 A71 zones measuring
different populations under one threshold.

What it IS good for is the reason it was ingested: names for the EICs, and a denominator for the
18 zones that have none.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from backend.models.energy import InstalledCapacity, PowerOutage, ProductionUnit
from backend.power.entsoe_units import parse_production_units
from backend.signals.detectors.power import (
    forced_outage_severity,
    installed_capacity_mw,
    published_unit_capacity_mw,
)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    from backend.main import app

    app.dependency_overrides.clear()


def _client(db):
    from backend.database import get_db
    from backend.main import app

    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def _unit(eic: str, name: str, psr: str, mw: float, *, bare_nominal: bool = False) -> str:
    """One TimeSeries in the REAL document shape.

    `bare_nominal` switches to the OTHER tag name the same document uses. Both are real: NL
    2026 carries 51 `nominalIP_PowerSystemResources.nominalP` and 56 bare `nominalP`. A parser
    that matches only one still "works" — the Period's quantity carries the same figure, so it
    falls through silently and reads half the units by accident. A fixture with only one tag
    form cannot express that.
    """
    tag = "nominalP" if bare_nominal else "nominalIP_PowerSystemResources.nominalP"
    return f"""
  <TimeSeries>
    <registeredResource.mRID codingScheme="A01">{eic}</registeredResource.mRID>
    <registeredResource.name>{name}</registeredResource.name>
    <MktPSRType>
      <psrType>{psr}</psrType>
      <{tag} unit="MAW">{mw}</{tag}>
    </MktPSRType>
  </TimeSeries>"""


def _unit_xml(*units: tuple[str, str, str, float]) -> str:
    blocks = "".join(_unit(eic, name, psr, mw) for eic, name, psr, mw in units)
    return (
        '<?xml version="1.0"?><GL_MarketDocument xmlns="urn:x">' + blocks + "</GL_MarketDocument>"
    )


def test_both_nominal_power_tag_names_in_one_document_are_read():
    """The real A71/A33 document uses TWO tag names for the same field. Matching one of them
    and letting the rest fall through to the Period's quantity is how a parser looks correct
    while reading half its units by accident — the quantity carries the same number."""
    xml = ('<?xml version="1.0"?><GL_MarketDocument xmlns="urn:x">'
           + _unit("E1", "Long tag", "B19", 120.3)
           + _unit("E2", "Bare tag", "B04", 480.0, bare_nominal=True)
           + "</GL_MarketDocument>")

    units = {u["unit_eic"]: u["nominal_mw"] for u in parse_production_units(xml)}
    assert units == {"E1": 120.3, "E2": 480.0}


# ─── the parser ───────────────────────────────────────────────────────────────


def test_units_carry_the_eic_the_outage_table_has_been_writing_all_along():
    units = parse_production_units(
        _unit_xml(("17W100P100P0001A", "CATTENOM 3", "B14", 1300.0)))

    assert units == [{
        "unit_eic": "17W100P100P0001A",
        "name": "CATTENOM 3",
        "psr_type": "B14",
        "nominal_mw": 1300.0,
    }]


def test_an_unknown_psr_code_survives_instead_of_raising():
    """A71/A33 returns B03, which is not in PSR_LABELS — nor is B25, which is already in the
    store as gen.B25. A KeyError here would take out the whole registry for a zone."""
    from backend.power.entsoe_grid import PSR_LABELS

    assert "B03" not in PSR_LABELS
    units = parse_production_units(_unit_xml(("X1", "Mystery Plant", "B03", 200.0)))
    assert units[0]["psr_type"] == "B03"
    assert PSR_LABELS.get("B03", "B03") == "B03", "labelled at read time, degrading gracefully"


def test_the_psr_type_is_the_RAW_code_not_the_label():
    """This table exists to join PowerOutage.unit_eic, and PowerOutage.psr_type is a raw code.
    InstalledCapacity and PowerGenMix store the LABEL. Storing a label here would mean joining a
    labelled table to a coded one — and PSR_LABELS has gaps, so the mapping is not injective."""
    units = parse_production_units(_unit_xml(("X1", "Some Plant", "B14", 900.0)))
    assert units[0]["psr_type"] == "B14"
    assert units[0]["psr_type"] != "Nuclear"


# ─── the denominator: the decision this PR is really about ────────────────────


def test_the_published_fleet_is_not_the_installed_fleet(db_session):
    """THE test. Both denominators seeded at the REAL measured DE-LU figures, and the severity
    call must still receive the A68 one. A 'simplification' that unified them would fire far
    more often, against a population the thresholds were never calibrated on."""
    db_session.add(InstalledCapacity(zone="DE_LU", year=2026, psr_type="Nuclear",
                                     capacity_mw=294_941.0))   # A68, measured
    db_session.add(ProductionUnit(zone="DE_LU", year=2026, unit_eic="X1", name="A",
                                  psr_type="B14", nominal_mw=65_193.0))   # A71/A33, measured
    db_session.commit()

    installed = installed_capacity_mw(db_session, "DE_LU")
    published = published_unit_capacity_mw(db_session, "DE_LU")

    assert installed == 294_941.0
    assert published == 65_193.0
    assert published < installed / 4, "a different population, not a smaller sample"

    # 10 GW offline: 3.4% of the real fleet (warning), but 15% of the published units (critical).
    assert forced_outage_severity(10_000.0, installed) != "critical"
    assert forced_outage_severity(10_000.0, published) == "critical", (
        "which is exactly why the published fleet must never be passed here"
    )


def test_the_driver_card_keeps_the_two_denominators_apart(db_session):
    """fleet_pct (A68) and published_fleet_pct (A71) are different numbers with different
    meanings and are never merged into one."""
    from datetime import date, timedelta

    from backend.models.energy import PowerGrid, PowerPriceDaily
    from backend.power.drivers import compute_drivers

    today = date(2026, 7, 1)
    for i in range(40):
        d = (today - timedelta(days=i)).isoformat()
        db_session.add(PowerPriceDaily(date=d, zone="DE_LU", mean_price=90.0, min_price=10.0,
                                       max_price=150.0, negative_hours=0))
        db_session.add(PowerGrid(date=d, zone="DE_LU", load_mw=50_000.0 + i * 100,
                                 wind_mw=10_000.0, solar_mw=5_000.0,
                                 residual_mw=35_000.0 + i * 100))
    db_session.add(InstalledCapacity(zone="DE_LU", year=2026, psr_type="Nuclear",
                                     capacity_mw=200_000.0))
    db_session.add(ProductionUnit(zone="DE_LU", year=2026, unit_eic="X1", name="A",
                                  psr_type="B14", nominal_mw=50_000.0))
    db_session.add(PowerOutage(
        mrid="M1", revision=1, doc_type="A77", zone="DE_LU", status="active",
        business_type="A54", nominal_mw=5_000.0, available_mw=0.0,
        start_utc="2026-06-01T00:00Z", end_utc="2027-01-01T00:00Z",
    ))
    db_session.commit()

    out = compute_drivers(db_session, "DE_LU", today=today)
    outage = out["outage"]

    assert outage["fleet_pct"] == 2.5, "5 GW of a 200 GW installed fleet"
    assert outage["published_fleet_pct"] == 10.0, "5 GW of a 50 GW published fleet"
    assert outage["fleet_pct"] != outage["published_fleet_pct"]


def test_a_zone_without_a68_still_gets_a_published_denominator(db_session):
    """The 18 zones with no A68 (all IT sub-zones, SK, CH, all Nordic sub-zones) have had NO
    relative outage figure at all. A71 answers for all 37."""
    db_session.add(ProductionUnit(zone="IT_NORD", year=2026, unit_eic="I1", name="X",
                                  psr_type="B04", nominal_mw=8_000.0))
    db_session.commit()

    assert installed_capacity_mw(db_session, "IT_NORD") is None
    assert published_unit_capacity_mw(db_session, "IT_NORD") == 8_000.0


# ─── the join: what the whole thing was for ───────────────────────────────────


def test_the_outage_board_shows_the_unit_name_from_the_registry(db_session):
    """PowerOutage.unit_eic has been written since the outage ingest was built and read by
    NOTHING. The fixture MUST have unit_name=None — with a name on the message, the join is
    never exercised and the test proves nothing."""
    db_session.add(PowerOutage(
        mrid="M1", revision=1, doc_type="A77", zone="DE_LU", status="active",
        business_type="A54", psr_type="B14",
        unit_name=None, unit_eic="17W100P100P0001A",
        nominal_mw=1_300.0, available_mw=0.0,
        start_utc="2026-06-01T00:00Z", end_utc="2027-01-01T00:00Z",
    ))
    db_session.add(ProductionUnit(zone="DE_LU", year=2026, unit_eic="17W100P100P0001A",
                                  name="CATTENOM 3", psr_type="B14", nominal_mw=1_300.0))
    db_session.commit()

    body = _client(db_session).get("/api/power/outages?zone=DE_LU").json()
    row = body["outages"][0]

    assert row["unit_name"] == "CATTENOM 3", "the board used to print the raw EIC here"
    assert row["unit_eic"] == "17W100P100P0001A"


def test_a_name_on_the_message_itself_wins_over_the_registry(db_session):
    db_session.add(PowerOutage(
        mrid="M1", revision=1, doc_type="A77", zone="DE_LU", status="active",
        business_type="A54", unit_name="As filed", unit_eic="E1",
        nominal_mw=100.0, available_mw=0.0,
        start_utc="2026-06-01T00:00Z", end_utc="2027-01-01T00:00Z",
    ))
    db_session.add(ProductionUnit(zone="DE_LU", year=2026, unit_eic="E1", name="From registry",
                                  psr_type="B14", nominal_mw=100.0))
    db_session.commit()

    body = _client(db_session).get("/api/power/outages?zone=DE_LU").json()
    assert body["outages"][0]["unit_name"] == "As filed"


# ─── wiring ───────────────────────────────────────────────────────────────────


def test_the_cache_source_is_not_the_generation_forecast_cache():
    """`entsoe_gen_total_forecast` is A71 + processType A01 — same document type, different
    process type, completely different document. Sharing the cache would serve back the wrong
    one, and it would look like a data bug rather than a wiring bug."""
    from backend.power.entsoe_units import CACHE_SOURCE

    assert CACHE_SOURCE == "entsoe_units"
    assert CACHE_SOURCE != "entsoe_gen_total_forecast"


def test_an_ingest_without_a_token_skips_loudly(db_session, monkeypatch):
    from backend.power import entsoe_units as eu

    monkeypatch.setattr(eu.settings, "entsoe_api_token", None)
    assert asyncio.run(eu.ingest_production_units(db_session, 2026)) == {"skipped": "no token"}


def test_reingesting_a_year_updates_instead_of_duplicating(db_session, monkeypatch):
    from pydantic import SecretStr

    from backend.power import entsoe_units as eu

    monkeypatch.setattr(eu.settings, "entsoe_api_token", SecretStr("t"))

    async def _fake(zone, year, *, overwrite=False):
        return _unit_xml(("X1", "Plant A", "B14", 900.0)) if zone == "DE_LU" else ""

    monkeypatch.setattr(eu, "_fetch_units_year", _fake)
    for _ in range(2):
        asyncio.run(eu.ingest_production_units(db_session, 2026, zones=["DE_LU"]))

    assert db_session.query(ProductionUnit).count() == 1
