"""A day is 24 hours, and a mean divides by the day — not by the points that happened to arrive.

`parse_load` and `parse_generation_by_type` built the daily tables with `sum(vals) / len(vals)`:
the mean over the points ENTSO-E PUBLISHED. That is three bugs wearing one coat.

1. THE CURRENT DAY. At 10:31 UTC the record holds nine hours, and their mean was stored as the
   day. The radar read it and announced "PT: Dunkelflaute — renewables 11% of load"; by the
   afternoon PT was at 22%, because the sun had come up. The alert was an artifact of a half-full
   day, not an event.

2. THE MISSING ZEROS. Several zones do not publish solar at night — PT sends 18 points, not 24.
   Dividing by 18 states the mean of the DAYLIGHT hours and calls it the day: PT's 2026-07-13
   solar was stored as 1619 MW when the day's true mean is 1147. A third too high, on a settled
   day, throughout the history — which lifts the renewable share, sinks the residual load, and
   feeds both into the Dunkelflaute predicate and every z-score on the desk.

3. THE DOUBLE COUNT. The daily parse appended every Point in the document, including the ones a
   revised, overlapping period restates; the hourly parse averaged per hour. So the daily table
   and the canonical hourly store disagreed about the same day (DE-LU solar: 19,988 vs 19,364).

All three come from the same decision: the daily tables were built by a SECOND parse of the XML
instead of being derived from the hourly store. They are derived from it now, and this is the rule.
"""
from __future__ import annotations

import pytest

from backend.power.daily import HOURS_PER_DAY, daily_from_hours

# PT, 2026-07-13, as it actually sits in the store: solar published 05:00-22:00 UTC only.
PT_SOLAR = {h: v for h, v in zip(range(5, 23), [
    120, 700, 1600, 2600, 3300, 3700, 3800, 3700, 3400, 2900, 2300, 1700, 1100, 500, 90, 5, 0, 0,
], strict=True)}


def test_a_fuel_that_is_dark_at_night_is_averaged_over_the_whole_day():
    """The 18 published points are the daylight ones. The other six are not missing data — they
    are the nights ENTSO-E does not bother to send, and a solar farm at 03:00 produces zero."""
    row = daily_from_hours(load_hours={h: 6000.0 for h in range(24)},
                           gen_hours={"B16": PT_SOLAR})

    published_mean = sum(PT_SOLAR.values()) / len(PT_SOLAR)
    day_mean = sum(PT_SOLAR.values()) / HOURS_PER_DAY

    assert row["solar_mw"] == pytest.approx(day_mean, abs=0.5)
    assert row["solar_mw"] < published_mean * 0.8, "the old rule was a third too high"


def test_load_is_averaged_over_the_hours_it_has():
    """Load is never zero, so an absent hour is a HOLE, not a zero — averaging it in as one would
    invent a demand collapse. Its mean is the mean of what was published; the hour count says how
    much that was."""
    row = daily_from_hours(load_hours={h: 6000.0 for h in range(20)}, gen_hours={})

    assert row["load_mw"] == pytest.approx(6000.0)
    assert row["load_hours"] == 20


def test_the_row_carries_the_hours_behind_it():
    """Completeness is data, not a guess: the desk and the radar refuse to judge a day whose
    generation feed has holes, and this is what they read."""
    row = daily_from_hours(
        load_hours={h: 6000.0 for h in range(24)},
        gen_hours={"B16": PT_SOLAR, "B19": {h: 500.0 for h in range(24)}},
    )

    assert row["load_hours"] == 24
    # Wind publishes through the night, so every hour of the day has generation in it: the solar
    # gaps are the unsent zeros, not a feed that fell over.
    assert row["gen_hours"] == 24


def test_a_generation_feed_with_a_real_hole_is_visible_as_one():
    """If NOTHING is published for an hour, that hour is unaccounted for — and the row says so."""
    row = daily_from_hours(
        load_hours={h: 6000.0 for h in range(24)},
        gen_hours={"B16": {h: 100.0 for h in range(12)}, "B19": {h: 500.0 for h in range(12)}},
    )

    assert row["gen_hours"] == 12


def test_residual_is_load_minus_the_day_means():
    row = daily_from_hours(
        load_hours={h: 10_000.0 for h in range(24)},
        gen_hours={"B16": {h: 2_400.0 for h in range(24)},           # 2400 MW day mean
                   "B18": {h: 600.0 for h in range(24)},             # wind offshore
                   "B19": {h: 1_000.0 for h in range(24)}},          # wind onshore
    )

    assert row["wind_mw"] == pytest.approx(1_600.0)
    assert row["solar_mw"] == pytest.approx(2_400.0)
    assert row["residual_mw"] == pytest.approx(6_000.0)


def test_a_zone_that_publishes_no_load_makes_no_residual():
    """IE-SEM has published no A65 load since 2025-10-23. Its generation is still real; its
    residual is not a number anyone has."""
    row = daily_from_hours(load_hours={}, gen_hours={"B19": {h: 900.0 for h in range(24)}})

    assert row["load_mw"] is None
    assert row["residual_mw"] is None
    assert row["load_hours"] == 0
    assert row["wind_mw"] == pytest.approx(900.0)


def test_the_mix_carries_every_fuel_by_the_same_rule():
    row = daily_from_hours(
        load_hours={h: 5_000.0 for h in range(24)},
        gen_hours={"B16": PT_SOLAR, "B04": {h: 1_200.0 for h in range(24)}},
    )

    assert row["mix"]["B04"] == pytest.approx(1_200.0)
    assert row["mix"]["B16"] == pytest.approx(sum(PT_SOLAR.values()) / HOURS_PER_DAY, abs=0.5)
