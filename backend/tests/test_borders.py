"""The border layer: prices × flows.

The metrics are descriptive statistics, and the tests pin exactly that — above
all, that a spread is never turned into a claim about a binding constraint, and
that a border we cannot cover is REPORTED rather than quietly dropped.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.power.borders import (
    COUPLED_EPS_EUR,
    border_metrics,
    compute_borders,
    compute_spread,
    percentile,
)
from backend.power.hourly_store import upsert_hourly

_NOW = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)  # now-relative: the db-backed route tests filter on a window ending "now"


def _client(db):
    from backend.database import get_db
    from backend.main import app

    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    from backend.power.borders import reset_rail_cache

    reset_rail_cache()  # the rail cache is module-level; never leak it between tests
    yield
    from backend.main import app

    app.dependency_overrides.clear()
    reset_rail_cache()


def _hours(n: int, end: datetime = _NOW) -> list[int]:
    base = int(end.timestamp()) // 3600 * 3600
    return [base - i * 3600 for i in range(n - 1, -1, -1)]


# ─── pure metrics ─────────────────────────────────────────────────────────────


def test_percentile_nearest_rank():
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.95) == 4.0
    assert percentile([], 0.95) is None


def test_fully_coupled_border_is_100_percent_convergence():
    ts = _hours(24)
    a = {t: 50.0 for t in ts}
    b = {t: 50.0 for t in ts}          # same clearing price every hour
    m = border_metrics(a, b, {t: 1000.0 for t in ts}, rail_threshold=2000.0)
    assert m["convergence_pct"] == 100.0
    assert m["split_hours"] == 0
    assert m["counter_price_pct"] is None, "no split hours → no counter-price statement"
    assert m["mean_abs_spread"] == 0.0


def test_a_cent_of_rounding_is_not_a_market_split():
    ts = _hours(10)
    m = border_metrics({t: 50.0 for t in ts}, {t: 50.01 for t in ts}, {}, None)
    assert m["convergence_pct"] == 100.0, f"< {COUPLED_EPS_EUR} EUR is the same price"


def test_counter_price_hours_are_flow_from_expensive_to_cheap():
    """The metric that a zonal price map structurally cannot show: power running
    the 'wrong' way. A exports (flow > 0) while A is the EXPENSIVE zone."""
    ts = _hours(4)
    a = {t: 100.0 for t in ts}   # A expensive
    b = {t: 40.0 for t in ts}    # B cheap
    flow = {ts[0]: 800.0, ts[1]: 800.0, ts[2]: -500.0, ts[3]: -500.0}
    m = border_metrics(a, b, flow, rail_threshold=1000.0)
    assert m["split_hours"] == 4
    assert m["counter_price_hours"] == 2, "the two hours A exported into the cheaper zone"
    assert m["counter_price_pct"] == 50.0


def test_at_the_rail_uses_the_borders_own_history():
    ts = _hours(4)
    a = {t: 60.0 for t in ts}
    b = {t: 55.0 for t in ts}
    flow = {ts[0]: 300.0, ts[1]: 900.0, ts[2]: 1000.0, ts[3]: 100.0}
    m = border_metrics(a, b, flow, rail_threshold=900.0)
    assert m["at_rail_hours"] == 2
    assert m["at_rail_pct"] == 50.0
    assert m["rail_threshold_mw"] == 900.0


def test_no_rail_threshold_means_no_rail_claim():
    ts = _hours(3)
    m = border_metrics({t: 60.0 for t in ts}, {t: 50.0 for t in ts},
                       {t: 9_999.0 for t in ts}, rail_threshold=None)
    assert m["at_rail_hours"] == 0


# ─── DB-backed ────────────────────────────────────────────────────────────────


def _seed_border(db, zone_a="DE_LU", zone_b="FR", hours=48, price_a=90.0, price_b=45.0,
                 flow_mw=1_500.0):
    ts = _hours(hours)
    upsert_hourly(db, "price.dayahead", zone_a, [(t, price_a) for t in ts], unit="EUR/MWh")
    upsert_hourly(db, "price.dayahead", zone_b, [(t, price_b) for t in ts], unit="EUR/MWh")
    # canonical: series flow.<B> under zone <A>; net > 0 = A exports to B
    upsert_hourly(db, f"flow.{zone_b}", zone_a, [(t, flow_mw) for t in ts], unit="MW")


def test_compute_borders_ranks_by_spread_and_names_the_expensive_side(db_session):
    _seed_border(db_session)
    out = compute_borders(db_session, days=7, now=_NOW)
    assert out["available"] is True
    row = next(r for r in out["borders"] if (r["zone_a"], r["zone_b"]) == ("DE_LU", "FR"))
    assert row["mean_abs_spread"] == 45.0
    assert row["expensive_side"] == "DE_LU"
    assert row["convergence_pct"] == 0.0, "the zones never cleared together"
    # DE-LU exports 1.5 GW while being the expensive zone → every split hour counts
    assert row["counter_price_pct"] == 100.0


def test_borders_report_what_they_cannot_cover(db_session):
    """Energy-Charts flows are country-level, so a border touching IT/DK/NO/SE/GB
    has no priced counterpart. Those must be NAMED, not silently dropped — the
    absence is a coverage fact the user has to be able to see."""
    _seed_border(db_session)
    ts = _hours(24)
    upsert_hourly(db_session, "flow.IT", "AT", [(t, 500.0) for t in ts], unit="MW")

    out = compute_borders(db_session, days=7, now=_NOW)
    assert "AT-IT" in out["uncoverable_borders"]
    assert all(r["zone_b"] != "IT" for r in out["borders"])


def test_compute_spread_is_order_independent_and_signs_from_a(db_session):
    _seed_border(db_session)
    fwd = compute_spread(db_session, "DE_LU", "FR", days=7, now=_NOW)
    rev = compute_spread(db_session, "FR", "DE_LU", days=7, now=_NOW)
    assert fwd["zone_a"] == rev["zone_a"] == "DE_LU", "canonical border order"
    assert fwd["data"][-1]["spread"] == 45.0
    assert fwd["data"][-1]["flow_mw"] == 1_500.0


def test_spread_on_a_border_without_flows_is_honest(db_session):
    _seed_border(db_session)  # seeds DE_LU-FR only
    out = compute_spread(db_session, "DE_LU", "NL", days=7, now=_NOW)
    assert out["available"] is False
    assert "country level" in out["reason"]


def test_borders_route(db_session):
    _seed_border(db_session)
    body = _client(db_session).get("/api/power/borders?days=7").json()
    assert body["available"] is True
    assert body["coupled_eps_eur"] == COUPLED_EPS_EUR
    assert "not a claim" in body["note"], "the honesty caveat travels with the data"


def test_spread_route(db_session):
    _seed_border(db_session)
    body = _client(db_session).get("/api/power/spread?a=FR&b=DE_LU&days=7").json()
    assert body["available"] is True and body["zone_a"] == "DE_LU"
    assert len(body["data"]) > 0


def test_borders_empty_is_unavailable(db_session):
    out = compute_borders(db_session, days=7, now=_NOW)
    assert out["available"] is False and "reason" in out


def test_latest_flow_is_the_latest_FLOW_hour_not_the_latest_price_hour():
    """The day-ahead auction publishes into tomorrow; physical flows only exist
    up to now. Reading the flow at the newest PRICE timestamp returned None for
    every border every afternoon."""
    ts = _hours(6)
    prices = {t: 100.0 for t in ts}
    prices_b = {t: 60.0 for t in ts}
    flow = {t: 700.0 for t in ts[:-2]}      # flows stop two hours before the prices do

    m = border_metrics(prices, prices_b, flow, rail_threshold=1000.0)
    assert m["latest_flow_mw"] == 700.0, "must not read the flow at a future price hour"
    assert m["spread_as_of"] > m["flow_as_of"], "prices reach further than flows"


def test_sql_rail_threshold_equals_the_python_percentile(db_session):
    """Speed must not cost correctness: the SQL nearest-rank must land on exactly
    the same value the pure percentile() helper does — otherwise 'at the rail'
    quietly means something different than the docs say."""
    from backend.power.borders import RAIL_PERCENTILE, _rail_thresholds

    ts = _hours(50)
    # deliberately lumpy, with negatives (import hours) so |flow| matters
    values = [(-1) ** i * (100.0 * i + 7.0) for i in range(len(ts))]
    upsert_hourly(db_session, "flow.FR", "DE_LU", list(zip(ts, values)), unit="MW")

    sql = _rail_thresholds(db_session, min(ts))
    py = percentile([abs(v) for v in values], RAIL_PERCENTILE)
    assert sql[("DE_LU", "FR")] == pytest.approx(py)


def test_rail_cache_recomputes_only_after_its_ttl(db_session):
    """The rail threshold is a 365-day statistic — it must not be re-ranked on
    every request (that cost 1.4s on prod), and it must not go stale forever."""
    from datetime import timedelta

    from backend.power.borders import (
        RAIL_CACHE_TTL_SECONDS,
        rail_thresholds_cached,
        reset_rail_cache,
    )

    reset_rail_cache()
    ts = _hours(30)
    upsert_hourly(db_session, "flow.FR", "DE_LU", [(t, 1_000.0) for t in ts], unit="MW")

    t0 = datetime(2026, 7, 12, 8, 0, tzinfo=timezone.utc)
    first, at1 = rail_thresholds_cached(db_session, min(ts), now=t0)
    assert first[("DE_LU", "FR")] == 1_000.0 and at1 == t0

    # New, much larger flows arrive — inside the TTL the cached value must hold.
    upsert_hourly(db_session, "flow.FR", "DE_LU",
                  [(t + 3600 * 100, 9_000.0) for t in ts], unit="MW")
    cached, at2 = rail_thresholds_cached(db_session, min(ts),
                                         now=t0 + timedelta(seconds=RAIL_CACHE_TTL_SECONDS - 1))
    assert cached[("DE_LU", "FR")] == 1_000.0, "still the cached value"
    assert at2 == t0, "and the response says when it was computed"

    fresh, at3 = rail_thresholds_cached(db_session, min(ts),
                                        now=t0 + timedelta(seconds=RAIL_CACHE_TTL_SECONDS))
    assert fresh[("DE_LU", "FR")] == 9_000.0, "past the TTL it recomputes"
    assert at3 > at2
    reset_rail_cache()


# ─── the scheduled grain (A09) ────────────────────────────────────────────────


@pytest.fixture
def sub_zones(monkeypatch):
    """DK1/DK2 exist in ZONE_REGISTRY but are not enabled in the test env (which serves the
    original three). Prod runs all 37. Enable them here so the sub-zone property can be
    expressed at all — a test that can only see DE_LU/FR/NL cannot prove anything about the
    zones the A09 ingest exists for."""
    from backend.power import borders
    from backend.power.zones import POWER_ZONES as REAL
    from backend.power.zones import ZONE_REGISTRY

    monkeypatch.setattr(
        borders, "POWER_ZONES",
        {**REAL, "DK1": ZONE_REGISTRY["DK1"], "DK2": ZONE_REGISTRY["DK2"]},
    )


def _seed_scheduled(db, zone_a, zone_b, hours=48, price_a=60.0, price_b=95.0,
                    sched_mw=800.0):
    """A bidding-zone border that the country-level physical feed cannot see at all."""
    ts = _hours(hours)
    upsert_hourly(db, "price.dayahead", zone_a, [(t, price_a) for t in ts], unit="EUR/MWh")
    upsert_hourly(db, "price.dayahead", zone_b, [(t, price_b) for t in ts], unit="EUR/MWh")
    upsert_hourly(db, f"sched.{zone_b}", zone_a, [(t, sched_mw) for t in ts], unit="MW")


def test_the_sub_zones_finally_have_borders(db_session, sub_zones):
    """THE point of the A09 ingest. DK1↔DK2 is a border INSIDE Denmark: Energy-Charts reports
    Denmark as one country, so this border could not exist in the physical grain even in
    principle. Before A09 it was not merely uncovered — it was unrepresentable."""
    _seed_scheduled(db_session, "DK1", "DK2")

    out = compute_borders(db_session, days=7, now=_NOW)
    row = next(r for r in out["borders"] if (r["zone_a"], r["zone_b"]) == ("DK1", "DK2"))

    assert row["flow_source"] == "scheduled"
    assert row["expensive_side"] == "DK2"
    assert "DK1-DK2" not in out["uncoverable_borders"]


def test_loop_flow_is_physical_minus_scheduled_where_both_exist(db_session):
    """What the wires carried minus what the market agreed to move. On a border with both
    grains this is a number no free EU tool publishes."""
    ts = _hours(24)
    upsert_hourly(db_session, "price.dayahead", "DE_LU", [(t, 90.0) for t in ts], unit="EUR/MWh")
    upsert_hourly(db_session, "price.dayahead", "FR", [(t, 45.0) for t in ts], unit="EUR/MWh")
    upsert_hourly(db_session, "flow.FR", "DE_LU", [(t, 1_500.0) for t in ts], unit="MW")
    upsert_hourly(db_session, "sched.FR", "DE_LU", [(t, 1_100.0) for t in ts], unit="MW")

    out = compute_borders(db_session, days=7, now=_NOW)
    row = next(r for r in out["borders"] if (r["zone_a"], r["zone_b"]) == ("DE_LU", "FR"))

    assert row["flow_source"] == "scheduled", "the bidding-zone grain wins where both exist"
    assert row["loop_hours"] == 24
    assert row["loop_mean_mw"] == 400.0, "1500 physical − 1100 scheduled"


def test_a_border_with_only_one_grain_says_WHY_it_has_no_loop_flow(db_session, sub_zones):
    """A missing number with a reason beats an invented one — and a silently absent field
    reads as a bug rather than as coverage."""
    _seed_scheduled(db_session, "DK1", "DK2")

    out = compute_borders(db_session, days=7, now=_NOW)
    row = next(r for r in out["borders"] if (r["zone_a"], r["zone_b"]) == ("DK1", "DK2"))

    assert row["loop_mean_mw"] is None
    assert "Energy-Charts reports by country" in row["loop_reason"]


def test_the_two_grains_never_share_a_rail_threshold(db_session):
    """"At the rail" is measured against a border's OWN 95th percentile. A scheduled series
    and a physical one are different quantities, so one cache for both would hand a border the
    other grain's rail and quietly mis-state congestion."""
    from backend.power.borders import PHYSICAL_PREFIX, SCHEDULED_PREFIX, rail_thresholds_cached

    ts = _hours(48)
    upsert_hourly(db_session, "flow.FR", "DE_LU", [(t, 1_000.0) for t in ts], unit="MW")
    upsert_hourly(db_session, "sched.FR", "DE_LU", [(t, 300.0) for t in ts], unit="MW")

    start = int((_NOW - timedelta(days=365)).timestamp())
    phys, _ = rail_thresholds_cached(db_session, start, now=_NOW, prefix=PHYSICAL_PREFIX)
    sched, _ = rail_thresholds_cached(db_session, start, now=_NOW, prefix=SCHEDULED_PREFIX)

    assert phys[("DE_LU", "FR")] == 1_000.0
    assert sched[("DE_LU", "FR")] == 300.0


def test_a_country_aggregate_is_SUPERSEDED_not_uncoverable(db_session, sub_zones):
    """After A09, "DE_LU-DK is uncoverable" is a lie.

    `flow.DK` is Energy-Charts' aggregate for Denmark — not DK1, not DK2 — and before the
    scheduled grain it was a genuine hole: no price on the other side, nothing to join. It is not
    a hole any more. DE-LU↔DK1 and DE-LU↔DK2 are both covered now, at bidding-zone level, and
    reporting the aggregate as a gap would claim a blindness the desk no longer has.

    GB is different, and stays named: we carry no zone for it at all (it left ENTSO-E's day-ahead
    publication after Brexit), and no grain will ever resolve it."""
    _seed_scheduled(db_session, "DK1", "DK2")
    ts = _hours(24)
    upsert_hourly(db_session, "flow.DK", "DE_LU", [(t, 500.0) for t in ts], unit="MW")
    upsert_hourly(db_session, "flow.GB", "FR", [(t, 900.0) for t in ts], unit="MW")

    out = compute_borders(db_session, days=7, now=_NOW)

    assert "DE_LU-DK" in out["superseded_aggregate_flows"]
    assert "DE_LU-DK" not in out["uncoverable_borders"], "the desk sees DK1 and DK2 now"

    assert "FR-GB" in out["uncoverable_borders"]
    assert "FR-GB" not in out["superseded_aggregate_flows"], "GB is not a zone we carry"
