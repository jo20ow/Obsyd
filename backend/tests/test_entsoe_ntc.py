"""A61 day-ahead NTC: two independent directed capacities per border, never netted.

The parser is shared with A09 (curveType A03, probe-verified 2026-07-28) — what is NEW
and worth pinning here is the storage convention: `ntc.<TO>` under `<FROM>`, ONE SERIES
PER DIRECTION. `flow.*`/`sched.*` net their two legs onto the sorted pair; NTC must not,
because A→B and B→A are independent offered capacities and BOTH are utilization
denominators. A fixture where the two directions carry the same value could not catch a
netting bug, so they never do here.
"""
from __future__ import annotations

import asyncio
from datetime import date

import pytest

from backend.power.entsoe_ntc import SERIES_PREFIX, ingest_ntc
from backend.power.hourly_store import read_hourly

DAY = "2026-07-01T00:00Z"


def _doc(points: list[tuple[int, float]], *, resolution: str = "PT60M",
         start: str = DAY, end: str = "2026-07-02T00:00Z") -> str:
    pts = "".join(
        f"<Point><position>{pos}</position><quantity>{qty}</quantity></Point>"
        for pos, qty in points
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Publication_MarketDocument xmlns="urn:iec62325.351:tc57wg16:451-3:publicationdocument:7:0">
  <TimeSeries>
    <curveType>A03</curveType>
    <Period>
      <timeInterval><start>{start}</start><end>{end}</end></timeInterval>
      <resolution>{resolution}</resolution>
      {pts}
    </Period>
  </TimeSeries>
</Publication_MarketDocument>"""


@pytest.fixture
def ingest(monkeypatch):
    """Run the ingest without a token and without a network — the same two traps the A09
    test fixture documents: the no-token guard makes credential-dependent tests pass
    locally and write nothing in CI, and a bare module assignment (instead of
    monkeypatch) would fake the fetch for every test after this one."""
    from pydantic import SecretStr

    from backend.power import entsoe_ntc as ntc

    monkeypatch.setattr(ntc.settings, "entsoe_api_token", SecretStr("test-token"))

    def _install(docs: dict[tuple[str, str], str]):
        async def _fake(out_zone, in_zone, month, *, overwrite=False):
            return docs.get((out_zone, in_zone), "")  # unlisted direction = clean ACK

        monkeypatch.setattr(ntc, "_fetch_ntc_month", _fake)
        return ntc

    return _install


def test_an_ingest_without_a_token_skips_loudly(db_session, monkeypatch):
    from backend.power import entsoe_ntc as ntc

    monkeypatch.setattr(ntc.settings, "entsoe_api_token", None)
    out = asyncio.run(ingest_ntc(db_session, [date(2026, 7, 1)]))

    assert out == {"skipped": "no token"}


def test_a_sparse_a61_document_densifies_like_the_step_function_it_is(db_session, ingest):
    """Probe fact: A61 answers curveType A03 with 1-48 points per window. A value holds
    until the next published position — three points must become a full day of capacity,
    not three isolated hours."""
    ingest({("ES", "FR"): _doc([(1, 1000.0), (13, 800.0)],
                               end="2026-07-02T00:00Z")})
    asyncio.run(ingest_ntc(db_session, [date(2026, 7, 1)], borders=[("ES", "FR")]))

    points = read_hourly(db_session, f"{SERIES_PREFIX}FR", "ES")
    assert len(points) == 24, "two published points, a full day densified"
    values = [v for _t, v in points]
    assert values[0] == 1000.0 and values[11] == 1000.0, "position 1 holds until the step"
    assert values[12] == 800.0 and values[23] == 800.0, "the step holds to the Period end"


def test_both_directions_are_stored_unnetted(db_session, ingest):
    """THE convention this module deviates on. ES→FR offered 1000, FR→ES offered 400:
    both series must exist, both positive, under their own storing zone. Netting them
    (as flow./sched. rightly do for their directed LEGS of one quantity) would destroy
    one of the two denominators utilization needs."""
    ingest({
        ("ES", "FR"): _doc([(1, 1000.0)], end="2026-07-01T02:00Z"),
        ("FR", "ES"): _doc([(1, 400.0)], end="2026-07-01T02:00Z"),
    })
    asyncio.run(ingest_ntc(db_session, [date(2026, 7, 1)], borders=[("ES", "FR")]))

    fwd = read_hourly(db_session, f"{SERIES_PREFIX}FR", "ES")
    rev = read_hourly(db_session, f"{SERIES_PREFIX}ES", "FR")
    assert [v for _t, v in fwd] == [1000.0, 1000.0], "ES→FR capacity, its own series"
    assert [v for _t, v in rev] == [400.0, 400.0], "FR→ES capacity, positive, NOT −400"


def test_an_empty_ack_direction_writes_nothing_and_stops_nothing(db_session, ingest):
    """A non-publishing border-month answers a clean Acknowledgement (probe-verified),
    which the fetch returns as "". That is data — the other direction of the same pair
    must still land, and no ghost series may appear for the silent one."""
    from backend.models.energy import SeriesDim

    ingest({("ES", "FR"): _doc([(1, 900.0)], end="2026-07-01T01:00Z")})
    out = asyncio.run(ingest_ntc(db_session, [date(2026, 7, 1)], borders=[("ES", "FR")]))

    assert out["direction_months"] == 1
    assert [v for _t, v in read_hourly(db_session, f"{SERIES_PREFIX}FR", "ES")] == [900.0]
    keys = {k for (k,) in db_session.query(SeriesDim.key).all()}
    assert f"{SERIES_PREFIX}ES" not in keys, "the silent direction leaves no ghost series"


def test_the_cache_source_collides_with_nothing(db_session):
    """entsoe_scheduled_exchange is A09, entsoe_netpos is A25, entsoe_gen_total_forecast
    is A71 — sharing any of them would serve the wrong document back from disk, and it
    would look like a data bug, not a wiring bug."""
    from backend.power.entsoe_exchange import CACHE_SOURCE as A09_SOURCE
    from backend.power.entsoe_exchange import NET_POSITION_CACHE_SOURCE
    from backend.power.entsoe_ntc import CACHE_SOURCE

    assert CACHE_SOURCE == "entsoe_a61"
    assert CACHE_SOURCE not in (A09_SOURCE, NET_POSITION_CACHE_SOURCE,
                                "entsoe_genmix", "entsoe_load", "entsoe_gen_total_forecast")
