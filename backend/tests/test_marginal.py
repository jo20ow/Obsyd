"""Price-setting technology (estimated): the honesty is the spec.

The attribution is a stated ASSUMPTION (a fixed conventional merit order — the
repo holds no fuel, CO2 or efficiency inputs to compute a real one), so every
test here pins one of the ways the heuristic could quietly overclaim: a band
attributed below its dispatch thresholds, B20 claiming a price nobody can name,
flexible hydro sneaking into the thermal cost ladder, or a "tension" hour being
silently reclassified into whatever band the price happens to fit.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.power.hourly_store import upsert_hourly
from backend.power.marginal import (
    CONSISTENCY_BANDS,
    DISPATCHABLE_ORDER,
    HYDRO_FLEX,
    MERIT_BANDS,
    MIN_MW,
    MIN_SHARE_PCT,
    _consistency,
    attribute_hour,
    compute_marginal,
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


#: Fixed anchor at top of hour — the window math must never depend on the wall clock.
_NOW = datetime(2026, 3, 10, 12, tzinfo=timezone.utc)


def _ts(hours_ago: int) -> int:
    return int((_NOW - timedelta(hours=hours_ago)).timestamp())


def _seed_hour(db, hours_ago: int, price: float | None, gens: dict[str, float],
               zone: str = "DE_LU") -> None:
    """One zone-hour: an optional day-ahead price plus MW per gen.<Bxx> series."""
    t = _ts(hours_ago)
    if price is not None:
        upsert_hourly(db, "price.dayahead", zone, [(t, price)], unit="EUR/MWh")
    for code, mw in gens.items():
        upsert_hourly(db, f"gen.{code}", zone, [(t, mw)], unit="MW")


def _one(db, zone="DE_LU"):
    """The single attributed hour of a one-hour seed."""
    out = compute_marginal(db, zone, hours=168, now=_NOW)
    assert out["available"] is True
    assert len(out["hourly"]) == 1
    return out["hourly"][0]


# ─── the ladder itself ────────────────────────────────────────────────────────


def test_hydro_flex_is_never_a_rung_of_the_cost_ladder():
    """Reservoir and pumped storage bid opportunity cost, not fuel cost — they are
    attributed separately or not at all, never ranked as a thermal band."""
    assert "hydro_flex" not in DISPATCHABLE_ORDER
    band_codes = {code for _name, codes in MERIT_BANDS for code in codes}
    assert not (set(HYDRO_FLEX) & band_codes)
    assert "B20" not in band_codes, "a price attributed to 'Other' is an invented claim"


# ─── threshold boundaries (pure, DB-free) ─────────────────────────────────────
#
# The thresholds are inclusive and the hydro tie-break is strict. These exact-
# boundary cases exist because a >= that quietly becomes a > (or vice versa)
# survives every seeded scenario above — the seeds all sit comfortably off the
# boundaries. The float values are chosen so the shares compute EXACTLY.


def test_a_thermal_band_at_exactly_both_thresholds_qualifies():
    # Gas at exactly MIN_MW and exactly MIN_SHARE_PCT of the hour:
    # 100.0 * 200.0 / (200.0 / 0.015) == 1.5 exactly in float.
    total = MIN_MW / (MIN_SHARE_PCT / 100.0)
    att = attribute_hour(70.0, {"gas": MIN_MW, "must_run_renewables": total - MIN_MW}, total)
    assert att["tech"] == "gas"
    assert att["share_pct"] == MIN_SHARE_PCT
    assert att["mw"] == MIN_MW


def test_hydro_at_exactly_both_thresholds_qualifies():
    total = MIN_MW / (MIN_SHARE_PCT / 100.0)
    att = attribute_hour(45.0, {"hydro_flex": MIN_MW, "must_run_renewables": total - MIN_MW}, total)
    assert att["tech"] == "hydro_flex"


def test_an_exact_share_tie_goes_to_the_thermal_band():
    """"Exceeds" is strict: hydro must OUT-run the ladder, not match it — at an
    exact tie the qualifying thermal band keeps the hour."""
    att = attribute_hour(
        60.0,
        {"gas": 4_000.0, "hydro_flex": 4_000.0, "must_run_renewables": 2_000.0},
        10_000.0,
    )
    assert att["tech"] == "gas"


def test_consistency_band_bounds_are_inclusive():
    """A price exactly ON a band bound sits inside it; a cent past is tension."""
    gas_hi = CONSISTENCY_BANDS["gas"][1]
    lignite_lo = CONSISTENCY_BANDS["lignite"][0]
    assert _consistency("gas", gas_hi) == "ok"
    assert _consistency("gas", gas_hi + 0.01) == "tension"
    assert _consistency("lignite", lignite_lo) == "ok"
    assert _consistency("lignite", lignite_lo - 0.01) == "tension"


# ─── per-hour attribution ─────────────────────────────────────────────────────


def test_gas_sets_the_price_when_coal_is_below_the_floor(db_session):
    # Coal at 150 MW < MIN_MW: on paper more expensive rungs than gas exist, but
    # nothing on them is meaningfully dispatching.
    assert 150 < MIN_MW
    _seed_hour(db_session, 2, 80.0, {"B19": 10_000, "B05": 150, "B04": 5_000})
    h = _one(db_session)
    assert h["tech"] == "gas"
    assert h["mw"] == 5_000.0
    assert h["consistency"] == "ok"  # 80 sits inside the coarse gas band


def test_negative_and_zero_prices_override_a_qualifying_gas_band(db_session):
    """At or below zero the marginal-cost logic is moot: must-run regardless."""
    _seed_hour(db_session, 3, -5.0, {"B19": 8_000, "B04": 5_000})
    _seed_hour(db_session, 2, 0.0, {"B19": 8_000, "B04": 5_000})
    out = compute_marginal(db_session, "DE_LU", hours=168, now=_NOW)
    assert [h["tech"] for h in out["hourly"]] == ["must_run_renewables"] * 2
    assert out["hourly"][0]["mw"] == 8_000.0, "the must-run band's own output, not gas's"


def test_a_peaker_below_min_share_is_ignored(db_session):
    # Oil at 500 MW clears the MW floor but is 1.25% of a 40 GW hour — a trickle,
    # not the marginal fleet.
    _seed_hour(db_session, 2, 85.0, {"B19": 30_000, "B04": 9_500, "B06": 500})
    assert 100.0 * 500 / 40_000 < MIN_SHARE_PCT
    assert _one(db_session)["tech"] == "gas"


def test_a_peaker_below_min_mw_is_ignored(db_session):
    # Oil at 180 MW is 2% of a small hour — the share qualifies, the MW floor
    # does not. Both thresholds must hold.
    _seed_hour(db_session, 2, 85.0, {"B19": 7_000, "B04": 1_820, "B06": 180})
    assert 100.0 * 180 / 9_000 >= MIN_SHARE_PCT and 180 < MIN_MW
    assert _one(db_session)["tech"] == "gas"


def test_no_qualifying_dispatchable_falls_back_to_must_run(db_session):
    _seed_hour(db_session, 2, 30.0, {"B19": 9_000, "B16": 3_000})
    h = _one(db_session)
    assert h["tech"] == "must_run_renewables"
    # €30 is outside the must-run band — reported as tension, not reclassified.
    assert h["consistency"] == "tension"


def test_hydro_flex_wins_only_when_it_tops_every_qualifying_thermal_band(db_session):
    # Hydro at 69% of the hour vs gas at 25%: the reservoirs are the story.
    _seed_hour(db_session, 3, 60.0, {"B04": 2_000, "B12": 3_000, "B10": 2_500, "B19": 500})
    # Gas at 50% vs hydro at 30%: the thermal band stands.
    _seed_hour(db_session, 2, 60.0, {"B04": 5_000, "B12": 3_000, "B19": 2_000})
    out = compute_marginal(db_session, "DE_LU", hours=168, now=_NOW)
    first, second = out["hourly"]
    assert first["tech"] == "hydro_flex"
    assert first["mw"] == 5_500.0
    assert first["consistency"] == "ok", "opportunity cost has no expected band"
    assert second["tech"] == "gas"


def test_the_price_floor_override_beats_dominant_hydro(db_session):
    """The floor rule (a) is still first: at or below zero even a qualifying,
    dominant hydro band does not claim the hour."""
    _seed_hour(db_session, 2, 0.0, {"B12": 8_000, "B04": 1_000})
    assert _one(db_session)["tech"] == "must_run_renewables"


def test_a_nordic_reservoir_hour_is_attributed_to_hydro_not_must_run(db_session):
    """95% reservoir, no thermal fleet on at all: the reservoirs genuinely set
    the price (at whatever level their opportunity cost says), and calling the
    hour "must-run" would be wrong. Hydro beats the no-thermal fallback when it
    qualifies on its own share/MW thresholds."""
    _seed_hour(db_session, 2, 45.0, {"B12": 9_500, "B11": 300, "B19": 200})
    h = _one(db_session)
    assert h["tech"] == "hydro_flex"
    assert h["consistency"] == "ok", "opportunity cost has no expected band"


def test_hydro_below_its_own_thresholds_does_not_claim_a_no_thermal_hour(db_session):
    """hydro_flex needs the same MIN_SHARE_PCT/MIN_MW qualification as a thermal
    band: a 150 MW trickle of reservoir in a wind-dominated hour is not the
    price-setter, so the hour falls back to must-run."""
    _seed_hour(db_session, 2, 35.0, {"B12": 150, "B19": 8_000})
    assert 150 < MIN_MW
    assert _one(db_session)["tech"] == "must_run_renewables"


def test_tension_is_reported_never_reclassified(db_session):
    """Lignite at €300 contradicts the assumed order. The honest move is to SAY
    so — silently reattributing the hour to whatever band the price fits would
    turn the stated assumption into a hidden circular one."""
    _seed_hour(db_session, 2, 300.0, {"B19": 4_000, "B02": 4_000})
    h = _one(db_session)
    assert h["tech"] == "lignite"
    assert h["consistency"] == "tension"
    out = compute_marginal(db_session, "DE_LU", hours=168, now=_NOW)
    assert out["summary"]["consistent_pct"] == 0.0, "the canary the panel surfaces"


def test_b20_counts_toward_total_but_is_never_attributed(db_session):
    # 18 GW of "Other" dilutes gas to a 10% share — the denominator is honest —
    # but the price can only ever be attributed to a band somebody can name.
    _seed_hour(db_session, 3, 90.0, {"B20": 18_000, "B04": 2_000})
    # An hour of ONLY unattributable generation falls back to must-run, not "other".
    _seed_hour(db_session, 2, 50.0, {"B20": 10_000})
    out = compute_marginal(db_session, "DE_LU", hours=168, now=_NOW)
    first, second = out["hourly"]
    assert first["tech"] == "gas" and first["share_pct"] == 10.0
    assert second["tech"] == "must_run_renewables"
    assert "other" not in {h["tech"] for h in out["hourly"]}


# ─── skipped hours, aggregation, coverage ─────────────────────────────────────


def test_incomplete_hours_are_neither_attributed_nor_counted(db_session):
    _seed_hour(db_session, 4, 80.0, {"B19": 5_000, "B04": 5_000})  # complete
    _seed_hour(db_session, 3, 90.0, {})                            # price, no generation
    _seed_hour(db_session, 2, None, {"B19": 5_000, "B04": 5_000})  # generation, no price
    _seed_hour(db_session, 1, 70.0, {"B19": 0.0, "B04": 0.0})      # zero generation
    out = compute_marginal(db_session, "DE_LU", hours=168, now=_NOW)

    assert out["summary"]["attributed_hours"] == 1
    assert len(out["hourly"]) == 1
    assert out["summary"]["consistent_pct"] == 100.0, "skipped hours move no denominator"
    assert out["as_of"] == datetime.fromtimestamp(_ts(4), tz=timezone.utc).isoformat(), (
        "as_of is the newest ATTRIBUTED hour, not the newest priced one"
    )


def test_daily_aggregation_and_summary_shares_sum_sanely(db_session):
    """48 alternating hours: gas sets the even ones, the odd ones fall back to
    must-run (tension at €30). Shares per day and overall must close to 100."""
    for h in range(1, 49):
        if h % 2 == 0:
            _seed_hour(db_session, h, 80.0, {"B19": 5_000, "B04": 5_000})
        else:
            _seed_hour(db_session, h, 30.0, {"B19": 10_000, "B04": 100})
    out = compute_marginal(db_session, "DE_LU", hours=168, now=_NOW)

    assert out["summary"]["attributed_hours"] == 48
    assert out["summary"]["share_of_hours"] == {"gas": 50.0, "must_run_renewables": 50.0}
    assert out["summary"]["consistent_pct"] == 50.0
    assert len(out["daily"]) == 3, "48 hours ending mid-day span three UTC dates"
    for day in out["daily"]:
        assert sum(day["shares"].values()) == pytest.approx(100.0, abs=0.3)
    assert [d["date"] for d in out["daily"]] == sorted(d["date"] for d in out["daily"])


def test_hours_outside_the_window_are_not_read(db_session):
    _seed_hour(db_session, 200, 80.0, {"B19": 5_000, "B04": 5_000})  # beyond 168h
    out = compute_marginal(db_session, "DE_LU", hours=168, now=_NOW)
    assert out["available"] is False


def test_no_coverage_is_honest(db_session):
    out = compute_marginal(db_session, "DE_LU", hours=168, now=_NOW)
    assert out["available"] is False
    assert "No overlapping generation and day-ahead price hours" in out["reason"]
    assert compute_marginal(db_session, "ZZ", now=_NOW)["available"] is False


def test_zone_isolation(db_session):
    """FR's hours must never leak into DE_LU's attribution."""
    _seed_hour(db_session, 2, 80.0, {"B19": 5_000, "B04": 5_000}, zone="FR")
    assert compute_marginal(db_session, "DE_LU", hours=168, now=_NOW)["available"] is False
    assert compute_marginal(db_session, "FR", hours=168, now=_NOW)["available"] is True


# ─── route ────────────────────────────────────────────────────────────────────


def _seed_recent(db, zone="DE_LU"):
    """The route reads the real clock, so its seed floats with real 'now'.

    The CURRENT top-of-hour, not 'now minus 2h': a fixed offset crosses UTC
    midnight for two hours a day and the as_of-date assertion below would flake
    exactly the way the repo's freshness tests used to (fixed repo-wide in #111).
    """
    t = int(datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
            .timestamp())
    upsert_hourly(db, "price.dayahead", zone, [(t, 80.0)], unit="EUR/MWh")
    upsert_hourly(db, "gen.B19", zone, [(t, 10_000.0)], unit="MW")
    upsert_hourly(db, "gen.B04", zone, [(t, 5_000.0)], unit="MW")


def test_route_carries_the_honesty_strings(db_session):
    _seed_recent(db_session)
    body = _client(db_session).get("/api/power/marginal?zone=DE_LU").json()
    assert body["available"] is True
    assert "estimate" in body["method"], "the honesty label is spec, not decoration"
    assert "not a model of the SDAC auction" in body["note"]
    assert "not a forecast" in body["note"]
    # The standard panel freshness triple, date-grained like every sibling.
    assert body["as_of"] == datetime.now(timezone.utc).date().isoformat()
    assert body["age_days"] == 0 and body["stale"] is False


def test_route_clamps_hours(db_session):
    client = _client(db_session)
    assert client.get("/api/power/marginal?zone=DE_LU&hours=23").status_code == 422
    assert client.get("/api/power/marginal?zone=DE_LU&hours=745").status_code == 422
    assert client.get("/api/power/marginal?zone=DE_LU&hours=24").status_code == 200
    assert client.get("/api/power/marginal?zone=DE_LU&hours=744").status_code == 200, \
        "744 = a full 31-day month — the /v1/snapshot precedent /units/history follows"


def test_route_unavailable_still_answers_200_with_a_reason(db_session):
    body = _client(db_session).get("/api/power/marginal?zone=DE_LU").json()
    assert body["available"] is False and body["reason"]
    # No freshness triple on an unavailable body — matching every sibling's shape.
    assert "as_of" not in body and "age_days" not in body and "stale" not in body
