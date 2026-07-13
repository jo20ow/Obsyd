"""A25's quantity is unsigned. The sign lives in the domain pair.

Measured on PL over two days: **23 export blocks and 7 import blocks, and every one of the 172
quantities >= 0**. A parser that reads `<quantity>` and ignores the domains reports Poland
exporting 1.65 GW during the hours it was importing — plausible, well-formed, and inverted.

Note what the fixture has to contain to catch that. One with only export blocks parses
identically under both readings.
"""
from __future__ import annotations

import asyncio
from datetime import date

import pytest

from backend.power.entsoe_exchange import (
    NET_POSITION_SERIES,
    NET_POSITION_UNSUPPORTED,
    parse_net_position,
)
from backend.power.hourly_store import read_hourly
from backend.power.zones import ZONE_REGISTRY

PL = ZONE_REGISTRY["PL"]["eic"]
REGION = "REGION_CODE-----"   # the literal counter-domain ENTSO-E puts on the other side


def _block(out_domain: str, in_domain: str, start: str, end: str,
           points: list[tuple[int, float]]) -> str:
    pts = "".join(
        f"<Point><position>{p}</position><quantity>{q}</quantity></Point>" for p, q in points
    )
    return f"""
  <TimeSeries>
    <out_Domain.mRID>{out_domain}</out_Domain.mRID>
    <in_Domain.mRID>{in_domain}</in_Domain.mRID>
    <curveType>A03</curveType>
    <Period>
      <timeInterval><start>{start}</start><end>{end}</end></timeInterval>
      <resolution>PT60M</resolution>
      {pts}
    </Period>
  </TimeSeries>"""


def _doc(*blocks: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Publication_MarketDocument xmlns="urn:iec62325.351:tc57wg16:451-3:'
        'publicationdocument:7:0">' + "".join(blocks) + "</Publication_MarketDocument>"
    )


# ─── the sign ─────────────────────────────────────────────────────────────────


def test_the_sign_comes_from_the_domain_pair_not_the_quantity():
    """THE test. Two disjoint blocks: PL exports in the first hour, imports in the second. Both
    quantities are POSITIVE in the document — as every A25 quantity is.

    A fixture with only export blocks cannot express this bug. It would be no test."""
    xml = _doc(
        _block(PL, REGION, "2026-07-01T00:00Z", "2026-07-01T01:00Z", [(1, 1652.2)]),
        _block(REGION, PL, "2026-07-01T01:00Z", "2026-07-01T02:00Z", [(1, 900.0)]),
    )
    hours = sorted(parse_net_position(xml, PL).items())

    assert [v for _t, v in hours] == [1652.2, -900.0]
    assert hours[1][1] < 0, "in_Domain == the zone means the zone is IMPORTING"


def test_a_document_of_nothing_but_exports_still_parses_positive():
    """The degenerate case that made the naive parser look correct: when a zone never flips,
    ignoring the domains gives the right answer, and the bug hides."""
    xml = _doc(_block(PL, REGION, "2026-07-01T00:00Z", "2026-07-01T02:00Z",
                      [(1, 500.0), (2, 700.0)]))
    assert sorted(parse_net_position(xml, PL).values()) == [500.0, 700.0]


def test_a_block_about_neither_side_of_this_zone_is_ignored():
    """A document that carries someone else's pair must not be read as this zone's position."""
    other = ZONE_REGISTRY["FR"]["eic"]
    xml = _doc(_block(other, REGION, "2026-07-01T00:00Z", "2026-07-01T01:00Z", [(1, 3000.0)]))
    assert parse_net_position(xml, PL) == {}


def test_the_step_function_is_expanded_here_too():
    """A25 is A03 as well: one published point holds across the whole block. Reusing the A09
    parser is the point — two step parsers would be two chances to get it wrong."""
    xml = _doc(_block(PL, REGION, "2026-07-01T00:00Z", "2026-07-01T05:00Z", [(1, 1200.0)]))
    hours = parse_net_position(xml, PL)
    assert len(hours) == 5 and set(hours.values()) == {1200.0}


# ─── the request ──────────────────────────────────────────────────────────────


def test_the_mandatory_contract_type_is_sent():
    """Without contract_MarketAgreement.Type the API refuses the request outright:
    "Mandatory parameter Contract_MarketAgreement.Type is missing"."""
    import inspect

    from backend.power import entsoe_exchange as ex

    src = inspect.getsource(ex._fetch_net_position_week)
    assert '"contract_MarketAgreement.Type": CONTRACT_DAILY' in src
    assert ex.CONTRACT_DAILY == "A01"


def test_business_type_b09_is_not_the_geothermal_psr_type():
    """`B09` is already a psrType in this codebase ("Geothermal"). Same two characters,
    different registry. Named so nobody 'cleans up' the duplicate."""
    from backend.power.entsoe_exchange import NET_POSITION_BUSINESS_TYPE
    from backend.power.entsoe_grid import PSR_LABELS

    assert PSR_LABELS["B09"] == "Geothermal"
    assert NET_POSITION_BUSINESS_TYPE == "B09"  # the collision is real and must stay visible


def test_the_cache_source_is_its_own():
    from backend.power.entsoe_exchange import CACHE_SOURCE, NET_POSITION_CACHE_SOURCE

    assert NET_POSITION_CACHE_SOURCE == "entsoe_netpos"
    assert NET_POSITION_CACHE_SOURCE not in (CACHE_SOURCE, "entsoe_genmix", "entsoe_load")


def test_the_three_zones_without_a25_are_named_not_hidden():
    """GR, IE_SEM and CH answer with a clean 'no matching data'. A zone that simply fails to
    appear looks like a bug; a zone listed as unsupported is a fact about the data."""
    assert set(NET_POSITION_UNSUPPORTED) == {"GR", "IE_SEM", "CH"}


# ─── the ingest ───────────────────────────────────────────────────────────────


@pytest.fixture
def ingest(monkeypatch):
    from pydantic import SecretStr

    from backend.power import entsoe_exchange as ex

    monkeypatch.setattr(ex.settings, "entsoe_api_token", SecretStr("test-token"))

    def _install(docs: dict[str, str]):
        async def _fake(zone, week, *, overwrite=False):
            return docs.get(zone, "")

        monkeypatch.setattr(ex, "_fetch_net_position_week", _fake)
        return ex

    return _install


def test_the_ingest_writes_a_signed_series(db_session, ingest):
    ex = ingest({"PL": _doc(
        _block(PL, REGION, "2026-07-01T00:00Z", "2026-07-01T01:00Z", [(1, 1652.2)]),
        _block(REGION, PL, "2026-07-01T01:00Z", "2026-07-01T02:00Z", [(1, 900.0)]),
    )})
    asyncio.run(ex.ingest_net_positions(db_session, [date(2026, 6, 29)], zones=["PL"]))

    values = [v for _t, v in read_hourly(db_session, NET_POSITION_SERIES, "PL")]
    assert values == [1652.2, -900.0], "the zone exported, then imported"


def test_the_market_net_position_does_not_overwrite_the_physical_one(db_session, ingest):
    """`net_position` is already taken: drivers.py derives it from PowerFlow (physical,
    country-level, daily) and DriversPanel renders it. Shipping A25 under that name would
    silently redefine a live driver into a different quantity."""
    from backend.models.energy import SeriesDim

    ex = ingest({"PL": _doc(
        _block(PL, REGION, "2026-07-01T00:00Z", "2026-07-01T01:00Z", [(1, 100.0)]))})
    asyncio.run(ex.ingest_net_positions(db_session, [date(2026, 6, 29)], zones=["PL"]))

    keys = {k for (k,) in db_session.query(SeriesDim.key).all()}
    assert NET_POSITION_SERIES == "netpos.dayahead"
    assert "net_position" not in keys


def test_an_ingest_without_a_token_skips_loudly(db_session, monkeypatch):
    from backend.power import entsoe_exchange as ex

    monkeypatch.setattr(ex.settings, "entsoe_api_token", None)
    out = asyncio.run(ex.ingest_net_positions(db_session, [date(2026, 6, 29)]))
    assert out == {"skipped": "no token"}


def test_the_scheduled_border_sum_tracks_the_market_net_position(db_session, ingest):
    """The free cross-check, and it exists only because A09 landed first.

    A zone's net position and the sum of its scheduled border flows are computed from two
    different ENTSO-E documents with two different sign conventions — A09's sign comes from the
    canonical sorted pair, A25's from the domain pair. If either parser were inverted, they
    would disagree. They are NOT expected to be equal (A09/A05 is the TOTAL schedule, A25/A01
    is the day-ahead allocation), but they cannot point opposite ways.
    """
    from backend.power.hourly_store import upsert_hourly

    hour = 1_780_000_000
    # PL's scheduled borders: exports 900 to SE4, imports 300 from DE_LU.
    #   canonical pairs: (DE_LU, PL) → sched.PL under DE_LU, net > 0 = DE_LU exports
    #                    (PL, SE4)   → sched.SE4 under PL,   net > 0 = PL exports
    upsert_hourly(db_session, "sched.PL", "DE_LU", [(hour, 300.0)], unit="MW")
    upsert_hourly(db_session, "sched.SE4", "PL", [(hour, 900.0)], unit="MW")

    ex = ingest({"PL": _doc(
        _block(PL, REGION, "2026-06-29T00:00Z", "2026-06-29T01:00Z", [(1, 600.0)]))})
    asyncio.run(ex.ingest_net_positions(db_session, [date(2026, 6, 29)], zones=["PL"]))

    # Σ over PL's borders: +900 (out to SE4) − 300 (in from DE_LU) = +600. PL is a net exporter.
    border_sum = 900.0 - 300.0
    netpos = read_hourly(db_session, NET_POSITION_SERIES, "PL")[0][1]

    assert border_sum > 0 and netpos > 0, "both must say PL exported"
    assert (border_sum > 0) == (netpos > 0), "an inverted parser would disagree here"
