"""A Dunkelflaute in a hydro zone is not an event. It is a description of the fleet.

The detector asked one flat question of all 37 zones — is wind+solar under 15% of load — and on
prod that left the radar standing at 27 simultaneous Dunkelflaute alerts, 77% of its entire
output, led by "NO5: Dunkelflaute — renewables 0% of load".

Measured across the whole record: NO1, NO5 and SK were under the threshold on 100% of all days.
SI 93%, CZ 81%, CH 78%, IT_NORD 76%. A flat threshold is not wrong in Germany. It is wrong as a
threshold for EUROPE.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from backend.models.energy import PowerGrid
from backend.power.borders import percentile
from backend.power.dunkelflaute import (
    ABSOLUTE_THRESHOLD,
    MIN_FLEET_SHARE,
    TAIL_PERCENTILE,
    is_dunkelflaute,
    zone_thresholds,
)

TODAY = date(2026, 6, 24)


def _seed(db, zone: str, shares: list[float], *, load: float = 60_000.0, month: int = 6):
    """One PowerGrid day per share, all in the same calendar month."""
    d = date(2026, month, 1)
    for i, share in enumerate(shares):
        day = d + timedelta(days=i)
        # Keep them inside the month by wrapping the year rather than the month.
        day = date(2026 - (i // 28), month, (i % 28) + 1)
        db.add(PowerGrid(date=day.isoformat(), zone=zone, load_mw=load,
                         wind_mw=load * share * 0.6, solar_mw=load * share * 0.4))
    db.commit()


# ─── gate 1: does the concept even apply? ─────────────────────────────────────


def test_a_zone_with_no_wind_or_solar_fleet_is_ineligible_with_a_reason(db_session):
    """NO5's median renewable share is 0.0%. "Renewables are 0% of load" is true every day of
    its life. The detector must say so and stay silent — not fire, and not vanish silently."""
    _seed(db_session, "NO5", [0.00] * 80)

    t = zone_thresholds(db_session, "06")["NO5"]

    assert t["eligible"] is False
    assert "no material wind/solar fleet" in t["reason"]
    assert is_dunkelflaute(0.0, t) is False, "its normal is not its emergency"


def test_a_zone_with_a_real_fleet_is_eligible(db_session):
    _seed(db_session, "DE_LU", [0.40] * 80)
    assert zone_thresholds(db_session, "06")["DE_LU"]["eligible"] is True


def test_the_fleet_gate_is_the_MEDIAN_not_todays_share(db_session):
    """A German winter day at 5% renewables must still be eligible — the gate asks what the zone
    NORMALLY has, not what it has today. Asking about today would gate away exactly the event."""
    _seed(db_session, "DE_LU", [0.40] * 79 + [0.05])
    t = zone_thresholds(db_session, "06")["DE_LU"]
    assert t["eligible"] is True
    assert t["median_share"] >= MIN_FLEET_SHARE


# ─── gate 2: is today unusual, for this zone, in this month? ──────────────────


def test_both_legs_are_required(db_session):
    """Unusually low FOR THIS ZONE, and low in absolute terms. Either alone is not enough."""
    threshold = {"eligible": True, "tail_share": 0.10, "median_share": 0.40, "n_month": 200}

    assert is_dunkelflaute(0.08, threshold) is True          # below both
    assert is_dunkelflaute(0.12, threshold) is False         # unusual? no — above the tail
    assert is_dunkelflaute(0.05, {**threshold, "tail_share": 0.30}) is True

    # A zone whose bad days are 30% renewable never gets told it is dark, however rare the day.
    assert is_dunkelflaute(0.25, {**threshold, "tail_share": 0.30}) is False
    assert ABSOLUTE_THRESHOLD == 0.15


def test_a_thin_month_makes_no_claim(db_session):
    """Below a real same-month record there is no tail to be in, so there is nothing to say —
    and 'no claim' must be visible, not an empty result that reads like 'all clear'."""
    _seed(db_session, "DE_LU", [0.40] * 10)
    t = zone_thresholds(db_session, "06")["DE_LU"]

    assert t["eligible"] is False
    assert "not enough" in t["reason"]


def test_the_tail_is_measured_per_MONTH(db_session):
    """A January in DE-LU is not a July. Pooling them would judge a normal winter day against
    summer sunshine, and every winter would look like an emergency."""
    _seed(db_session, "DE_LU", [0.15] * 80, month=1)   # dark winter: normal is 15%
    _seed(db_session, "DE_LU", [0.55] * 80, month=7)   # bright summer

    jan = zone_thresholds(db_session, "01")["DE_LU"]
    jul = zone_thresholds(db_session, "07")["DE_LU"]

    assert jan["tail_share"] < jul["tail_share"], "each month is judged against itself"


# ─── the arithmetic: one percentile convention for the whole codebase ─────────


def test_the_sql_percentile_matches_the_python_one(db_session):
    """This SQL computes a percentile; borders.percentile() computes a percentile. Two
    conventions in one codebase are two answers to the same question — and an off-by-one in the
    rank is exactly the drift that produced during development."""
    shares = [round(0.02 * i, 4) for i in range(1, 81)]   # 0.02 … 1.60, deliberately uneven
    _seed(db_session, "DE_LU", shares)

    t = zone_thresholds(db_session, "06")["DE_LU"]
    expected = percentile(shares, TAIL_PERCENTILE)

    assert t["tail_share"] == pytest.approx(expected, abs=1e-9)


def test_calibration_constants_are_what_the_measurement_said():
    """These are not taste. A decile fires 10% of days BY CONSTRUCTION — across 37 zones that is
    ~3 alerts every single day, which is a feed, not a radar. At 2% the measured rate on the full
    record is 1.41% of zone-days, ~0.4 alerts/day, and 36 Dunkelflaute days for DE-LU in 5.5
    years: about six a winter, which is what a German Dunkelflaute actually is."""
    assert TAIL_PERCENTILE == 0.02
    assert MIN_FLEET_SHARE == 0.10
    assert ABSOLUTE_THRESHOLD == 0.15
