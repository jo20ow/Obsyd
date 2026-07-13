"""A09 is a step function, and every other parser in this repo assumes it isn't.

curveType A03 publishes a point only where the value CHANGES. DE→FR publishes 26 of 192 PT15M
slots for a day. Every existing parser (parse_load, parse_load_hourly, parse_generation_hourly,
parse_imbalance_*) assumes A01 — one point per slot — so handed an A09 document it drops 86% of
the timeline and then averages whichever quarter-hours survived into an "hourly mean".

Note what the fixtures below have to look like to catch that. A fixture with dense sequential
points parses identically under both readings and proves nothing.
"""
from __future__ import annotations

import pytest

from backend.power.entsoe_exchange import (
    SERIES_PREFIX,
    net_exchange,
    parse_step_series,
)
from backend.power.hourly_store import read_hourly

DAY = "2026-07-01T00:00Z"


def _doc(points: list[tuple[int, float]], *, resolution: str = "PT15M",
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


# ─── the step function ────────────────────────────────────────────────────────


def test_a_published_value_holds_until_the_next_one():
    """THE test, on the real DE→FR shape: a TWO-day PT15M period (192 slots) carrying three
    published positions. An A01 parser sees three quarter-hours and calls the other 189
    missing. Nothing is missing — the value simply did not change.

    The period length is load-bearing. A one-day fixture has only 96 slots, so positions 142
    and 192 would never land and the step would never be exercised: the test would pass
    against a parser that ignores steps entirely. That is not a test, and this file was
    briefly guilty of it."""
    hours = parse_step_series(
        _doc([(1, 1000.0), (142, 400.0), (177, -250.0)], end="2026-07-03T00:00Z"))

    assert len(hours) == 48, "two full days, densified — not 3 slots"
    values = [v for _h, v in sorted(hours.items())]
    assert values[0] == 1000.0
    assert values[10] == 1000.0, "position 1 still holds at 10:00 — it never changed"
    # position 142 → (142-1)*15min = 35h15 → hour 35. It holds from there until position 177.
    assert values[36] == 400.0, "the step landed and held"
    assert values[47] == -250.0, "and the last step holds to the end (a reversed flow)"


def test_the_last_point_holds_to_the_end_of_the_period():
    """A border that settles once in the morning publishes one point. Without reading the
    Period's `end`, the series would stop at that point instead of running the whole day —
    truncating most of the day for exactly the quietest borders."""
    hours = parse_step_series(_doc([(1, 750.0)], resolution="PT60M",
                                   end="2026-07-01T06:00Z"))

    assert len(hours) == 6, "one published point, six hours of Period"
    assert all(v == 750.0 for v in hours.values())


def test_the_hourly_mean_averages_all_four_quarter_hours_not_just_the_published_ones():
    """The second-order bug the naive parser produces. In an hour whose value steps from 100
    to 200 halfway through, the honest hourly figure is 150 — not 200, which is what you get
    when only the published slot survives."""
    # PT15M, an hour-long period: slots 1..4. Value steps at slot 3.
    hours = parse_step_series(
        _doc([(1, 100.0), (3, 200.0)], end="2026-07-01T01:00Z"))

    assert len(hours) == 1
    assert list(hours.values())[0] == pytest.approx(150.0), "(100+100+200+200)/4"


def test_a_pt60m_nordic_border_parses():
    """The Nordic borders (SE1→FI, NO1→NO2, SE3→SE4) and all 2023 history are PT60M. A fixture
    that is only ever PT15M cannot express a resolution bug."""
    hours = parse_step_series(
        _doc([(1, 500.0), (3, 800.0)], resolution="PT60M", end="2026-07-01T04:00Z"))

    assert len(hours) == 4
    assert [v for _h, v in sorted(hours.items())] == [500.0, 500.0, 800.0, 800.0]


def test_an_a01_dense_document_still_parses():
    """A03 is the general case; A01 (a point in every slot) is the degenerate one and must
    keep working — this parser replaces nothing else if it cannot read a dense document."""
    hours = parse_step_series(
        _doc([(i, 100.0 * i) for i in range(1, 5)], end="2026-07-01T01:00Z"))

    assert list(hours.values())[0] == pytest.approx(250.0), "(100+200+300+400)/4"


def test_an_empty_document_is_empty_not_an_exception():
    assert parse_step_series(_doc([])) == {}


# ─── the direction ────────────────────────────────────────────────────────────


def test_net_is_the_directed_difference_not_a_single_leg():
    """A09 reports each direction as a non-negative magnitude. Store one leg and an hour with
    500 MW out and 800 MW in reads as a 500 MW export while the zone was importing 300."""
    net = net_exchange({100: 500.0}, {100: 800.0})
    assert net[100] == pytest.approx(-300.0)


def test_an_hour_present_in_only_one_leg_nets_against_zero():
    """No schedule in one direction is a schedule of zero, not an absence — otherwise a
    one-way hour would silently vanish from the series."""
    net = net_exchange({100: 600.0}, {})
    assert net[100] == pytest.approx(600.0)
    net = net_exchange({}, {100: 600.0})
    assert net[100] == pytest.approx(-600.0)


# ─── the namespace ────────────────────────────────────────────────────────────


def test_the_cache_source_is_not_the_genmix_or_the_forecast_cache():
    """`entsoe_gen_total_forecast` is already taken by A71 + processType A01, and
    `_fetch_zone_month` silently defaults any non-A65 doctype to the GENMIX cache. Either
    collision serves back the wrong document, and it looks like a data bug, not a wiring bug."""
    from backend.power.entsoe_exchange import CACHE_SOURCE

    assert CACHE_SOURCE == "entsoe_scheduled_exchange"
    assert CACHE_SOURCE not in ("entsoe_genmix", "entsoe_load", "entsoe_gen_total_forecast")


@pytest.fixture
def ingest(monkeypatch):
    """Run the ingest without a token and without a network.

    Two traps, both of which bit this file. The ingest guards on
    `settings.entsoe_api_token` and returns {"skipped": "no token"} without it — so these
    tests passed on my machine (token in the env) and wrote NOTHING in CI, where there is
    none. A test that depends on ambient credentials is not a test of the code.

    And the fetch must be replaced with monkeypatch, not assigned onto the module: a bare
    assignment survives the test and silently fakes the fetch for everything after it.
    """
    from pydantic import SecretStr

    from backend.power import entsoe_exchange as ex

    monkeypatch.setattr(ex.settings, "entsoe_api_token", SecretStr("test-token"))

    def _install(docs: dict[tuple[str, str], str]):
        async def _fake(out_zone, in_zone, month, *, overwrite=False):
            return docs.get((out_zone, in_zone), _doc([], resolution="PT60M",
                                                      end="2026-07-01T01:00Z"))

        monkeypatch.setattr(ex, "_fetch_exchange_month", _fake)
        return ex

    return _install


def test_an_ingest_without_a_token_skips_loudly(db_session, monkeypatch):
    """The guard that made the two tests below pass on my machine and fail in CI. It is
    correct production behaviour — it just has to be SAID in a test, not discovered in a red
    build. Without it, "wrote nothing" and "was never asked to write" look identical."""
    import asyncio
    from datetime import date

    from backend.power import entsoe_exchange as ex

    monkeypatch.setattr(ex.settings, "entsoe_api_token", None)
    out = asyncio.run(ex.ingest_scheduled_exchanges(db_session, [date(2026, 7, 1)]))

    assert out == {"skipped": "no token"}


def test_scheduled_series_never_land_in_the_physical_flow_namespace(db_session, ingest):
    """Scheduled and physical MW are different quantities. One namespace holding both makes
    loop flow (physical − scheduled) uncomputable and every existing `flow.*` ambiguous."""
    import asyncio
    from datetime import date

    from backend.models.energy import SeriesDim

    ex = ingest({("DK1", "DK2"): _doc([(1, 400.0)], resolution="PT60M",
                                      end="2026-07-01T02:00Z")})
    asyncio.run(ex.ingest_scheduled_exchanges(
        db_session, [date(2026, 7, 1)], borders=[("DK1", "DK2")]))

    keys = {k for (k,) in db_session.query(SeriesDim.key).all()}
    assert f"{SERIES_PREFIX}DK2" in keys
    assert not any(k.startswith("flow.") for k in keys), "physical namespace untouched"

    points = read_hourly(db_session, f"{SERIES_PREFIX}DK2", "DK1")
    assert [v for _t, v in points] == [400.0, 400.0], "DK1 exports 400 MW to DK2"


def test_the_sign_follows_the_canonical_sorted_pair(db_session, ingest):
    """`net > 0` means the sorted-FIRST zone exports — byte-identical to the `flow.<TO>` under
    `<FROM>` convention. Get this backwards and every border on the desk reads inverted."""
    import asyncio
    from datetime import date

    # DK2 sends 900 MW to DK1: the sorted-first zone (DK1) is IMPORTING.
    ex = ingest({("DK2", "DK1"): _doc([(1, 900.0)], resolution="PT60M",
                                      end="2026-07-01T01:00Z")})
    asyncio.run(ex.ingest_scheduled_exchanges(
        db_session, [date(2026, 7, 1)], borders=[("DK1", "DK2")]))

    points = read_hourly(db_session, f"{SERIES_PREFIX}DK2", "DK1")
    assert points[0][1] == pytest.approx(-900.0), "negative = DK1 imports"
