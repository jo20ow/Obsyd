"""Capture rate: what a MWh of each technology actually earned.

The arithmetic is trivial; the honesty is not. Every test here guards one of the
three ways a capture rate quietly becomes a lie — the wrong denominator, too small
a sample, a division through zero — and the first of those is the one that matters:
divide solar by the mean of its OWN (midday) hours and the cannibalisation the
number exists to show disappears from it.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.power.capture import (
    MIN_DAYS,
    STALE_AFTER_DAYS,
    capture_metrics,
    compute_capture,
)
from backend.power.hourly_store import upsert_hourly


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


_T0 = int(datetime(2026, 3, 2, tzinfo=timezone.utc).timestamp())  # a Monday, 00:00 UTC


def _hours(day: int, values: dict[int, float]) -> dict[int, float]:
    """{hour-of-day: value} → {unix ts: value} on day `day` of the window."""
    return {_T0 + day * 86400 + h * 3600: v for h, v in values.items()}


# ─── the denominator ──────────────────────────────────────────────────────────


def test_solar_that_generates_only_in_cheap_hours_captures_below_baseload():
    """The cannibalisation story in one day."""
    prices = _hours(0, {h: (100.0 if h < 12 else 20.0) for h in range(24)})
    solar = _hours(0, {h: (0.0 if h < 12 else 1_000.0) for h in range(24)})
    m = capture_metrics(prices, solar)

    assert m["capture_price"] == 20.0
    assert m["baseload_price"] == 60.0
    assert m["value_factor"] == pytest.approx(1 / 3, rel=1e-3)


def test_hours_with_no_generation_row_do_not_move_the_denominator():
    """THE bug real data caught. ENTSO-E writes no row for solar at 03:00 — there is
    no output to report. If those hours were dropped from the baseload too, solar
    would be measured against midday prices, the value factor would snap back to 1.00
    and the cannibalisation would vanish from the very number built to show it.

    The denominator is the month's BASE product: every hour, same for every fuel."""
    prices = _hours(0, {h: (100.0 if h < 12 else 20.0) for h in range(24)})
    solar_no_rows = _hours(0, {h: 1_000.0 for h in range(12, 24)})   # nights absent
    solar_zero_rows = _hours(0, {h: (0.0 if h < 12 else 1_000.0) for h in range(24)})

    absent = capture_metrics(prices, solar_no_rows)
    zeroed = capture_metrics(prices, solar_zero_rows)

    assert absent["baseload_price"] == 60.0, "the whole day, not just the sunlit half"
    assert absent["capture_price"] == zeroed["capture_price"]
    assert absent["value_factor"] == zeroed["value_factor"], (
        "a missing row and an explicit zero are the same physical fact"
    )
    assert absent["hours"] == 12 and zeroed["hours"] == 24


def test_a_dispatchable_fleet_captures_above_baseload():
    prices = _hours(0, {h: (100.0 if h < 12 else 20.0) for h in range(24)})
    gas = _hours(0, {h: (1_000.0 if h < 12 else 0.0) for h in range(24)})   # runs when dear
    m = capture_metrics(prices, gas)
    assert m["value_factor"] == pytest.approx(100.0 / 60.0, rel=1e-3)


def test_no_value_factor_through_a_zero_baseload():
    prices = _hours(0, {0: 50.0, 1: -50.0})
    gen = _hours(0, {0: 100.0, 1: 100.0})
    m = capture_metrics(prices, gen)
    assert m["baseload_price"] == 0.0
    assert m["value_factor"] is None, "a ratio through zero is a number, not a meaning"


def test_negative_generation_share_is_the_technologys_own_output():
    prices = _hours(0, {0: -5.0, 1: 50.0, 2: 50.0, 3: 50.0})
    gen = _hours(0, {0: 300.0, 1: 100.0, 2: 100.0, 3: 100.0})
    assert capture_metrics(prices, gen)["negative_gen_pct"] == 50.0  # 300 of 600 MWh


def test_a_technology_that_produced_nothing_has_no_capture_price():
    prices = _hours(0, {0: 50.0})
    assert capture_metrics(prices, _hours(0, {0: 0.0})) is None
    assert capture_metrics(prices, {}) is None
    assert capture_metrics({}, _hours(0, {0: 100.0})) is None


# ─── DB-backed ────────────────────────────────────────────────────────────────


def _seed(db, zone="DE_LU", days=75, *, solar_days=None):
    """Solar produces in the cheap half of the day and writes NO row at night — the
    real ENTSO-E shape. Gas runs in the dear half. 75 days back guarantees at least
    one COMPLETE calendar month; capture is a monthly figure.

    `solar_days`: if set, solar only appears on that many days — a broken feed.
    """
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = now - timedelta(days=days)
    price_pts, solar_pts, gas_pts = [], [], []
    for i in range(days * 24):
        t = int((start + timedelta(hours=i)).timestamp())
        cheap = (i % 24) < 12
        price_pts.append((t, 20.0 if cheap else 100.0))
        if cheap and (solar_days is None or i // 24 < solar_days):
            solar_pts.append((t, 1_000.0))          # nothing at all outside daylight
        if not cheap:
            gas_pts.append((t, 800.0))
    upsert_hourly(db, "price.dayahead", zone, price_pts, unit="EUR/MWh")
    upsert_hourly(db, "gen.B16", zone, solar_pts, unit="MW")
    upsert_hourly(db, "gen.B04", zone, gas_pts, unit="MW")


def test_solar_with_no_night_rows_still_reads_as_cannibalised(db_session):
    """End to end, on the shape the store actually holds."""
    _seed(db_session)
    out = compute_capture(db_session, "DE_LU", months=3)
    assert out["available"] is True

    by_psr = {f["psr"]: f for f in out["fuels"]}
    solar, gas = by_psr["B16"]["latest"], by_psr["B04"]["latest"]

    assert solar["value_factor"] == pytest.approx(1 / 3, rel=0.05), "20 ÷ 60, not 20 ÷ 20"
    assert gas["value_factor"] > 1.0
    # Solar was priced at 20 in every hour it produced, yet is measured against 60:
    # the night hours it never saw are in the denominator, where they belong.
    assert solar["capture_price"] == 20.0
    assert solar["baseload_price"] == gas["baseload_price"] == 60.0
    assert solar["days"] >= MIN_DAYS, "present every day, absent every night"
    assert out["fuels"][0]["psr"] == "B16", "the cannibalised technology leads the table"
    assert out["min_days"] == MIN_DAYS
    assert "not a model and not a forecast" in out["note"]


def test_hydro_is_covered_because_hydro_is_the_nordic_fleet(db_session):
    """Without hydro, NO5 — a hydro zone — showed one stale line for a peaking gas
    plant while reservoir and run-of-river sat fully covered in the store. Reservoir
    is the most dispatchable plant in Europe and should out-earn baseload; run-of-river
    must run whatever the price does. Pumped storage stays out: it consumes as much as
    it produces, and a capture price on its generation leg alone is a half-truth."""
    from backend.power.capture import CAPTURE_FUELS

    assert {"B11", "B12"} <= set(CAPTURE_FUELS)
    assert "B10" not in CAPTURE_FUELS, "pumped storage is a round trip, not a fuel"

    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = now - timedelta(days=75)
    prices, reservoir, ror = [], [], []
    for i in range(75 * 24):
        t = int((start + timedelta(hours=i)).timestamp())
        cheap = (i % 24) < 12
        prices.append((t, 20.0 if cheap else 100.0))
        reservoir.append((t, 50.0 if cheap else 900.0))   # dispatched into the peak
        ror.append((t, 400.0))                            # runs regardless
    upsert_hourly(db_session, "price.dayahead", "DE_LU", prices, unit="EUR/MWh")
    upsert_hourly(db_session, "gen.B12", "DE_LU", reservoir, unit="MW")
    upsert_hourly(db_session, "gen.B11", "DE_LU", ror, unit="MW")

    by_psr = {f["psr"]: f["latest"] for f in compute_capture(db_session, "DE_LU", months=3)["fuels"]}
    assert by_psr["B12"]["value_factor"] > 1.0, "reservoir hydro chooses its hours"
    assert by_psr["B11"]["value_factor"] == pytest.approx(1.0, rel=1e-3), "run-of-river takes what it gets"


def test_a_technology_absent_for_whole_days_is_dropped(db_session):
    """The guard counts DAYS precisely so it can tell a night apart from an outage:
    solar missing every night still passes; a feed missing for whole days does not."""
    _seed(db_session, solar_days=5)
    out = compute_capture(db_session, "DE_LU", months=3)
    assert [f["psr"] for f in out["fuels"]] == ["B04"], "gas survives, the broken feed does not"


def test_a_fragment_of_a_month_is_not_a_month(db_session):
    """A capture rate quoted from two days is a headline waiting to be wrong."""
    _seed(db_session, days=2)
    out = compute_capture(db_session, "DE_LU", months=3)
    assert out["available"] is False
    assert "No complete month of hourly day-ahead prices" in out["reason"]


def test_asking_for_one_month_returns_one_month(db_session):
    """A rolling 31-day window starts mid-month, so its oldest month is always a
    fragment — and `months=1` would return nothing at all. The window is cut on
    calendar boundaries because the figure is a calendar figure."""
    _seed(db_session)
    out = compute_capture(db_session, "DE_LU", months=1)
    assert out["available"] is True
    assert out["fuels"][0]["latest"]["month"] == out["latest_month"]


def test_the_running_month_is_not_reported_as_a_month(db_session):
    """A month-to-date wearing a month's label invites the reader to compare a
    12-day July against a full June and see a trend that is really the calendar."""
    _seed(db_session)
    running = datetime.now(timezone.utc).strftime("%Y-%m")
    out = compute_capture(db_session, "DE_LU", months=6)

    assert out["latest_month"] < running
    assert all(r["month"] < running for f in out["fuels"] for r in f["data"])


def test_freshness_is_the_newest_hour_used_not_the_month_label(db_session):
    """The panel convention: as_of is a date, and it is the newest hour the numbers
    were actually built from. A monthly figure is legitimately weeks behind — a month
    only completes once — so it goes stale on a wider window than any daily panel."""
    _seed(db_session)
    out = compute_capture(db_session, "DE_LU", months=3)

    assert date.fromisoformat(out["as_of"]), "an ISO date, not '2026-06'"
    assert out["as_of"].startswith(out["latest_month"])
    assert out["age_days"] >= 0 and out["stale"] is False

    later = date.fromisoformat(out["as_of"]) + timedelta(days=STALE_AFTER_DAYS + 1)
    assert compute_capture(db_session, "DE_LU", months=3, today=later)["stale"] is True


def test_unknown_and_empty_zones_are_honest(db_session):
    assert compute_capture(db_session, "ZZ")["available"] is False
    out = compute_capture(db_session, "FR")
    assert out["available"] is False and "No complete month" in out["reason"]


def test_route(db_session):
    _seed(db_session)
    body = _client(db_session).get("/api/power/capture?zone=DE_LU&months=3").json()
    assert body["available"] is True
    assert body["baseload_price"] == 60.0
    assert body["fuels"][0]["latest"]["capture_price"] is not None
