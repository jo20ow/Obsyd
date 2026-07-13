"""The border layer: prices × flows.

The metrics are descriptive statistics, and the tests pin exactly that — above
all, that a spread is never turned into a claim about a binding constraint, and
that a border we cannot cover is REPORTED rather than quietly dropped.
"""
from __future__ import annotations

from datetime import datetime, timezone

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

_NOW = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)


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
