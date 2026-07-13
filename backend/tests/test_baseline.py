"""What does "normal" mean on this desk?

Every "+2.4σ vs its norm" on the site is a z-score against ONE trailing window, and the
length of that window decides what the word means. It was 120 days, which in March is
built from November through February — so an average March day was reported as +2.0σ of
solar. The desk was calling the seasons and labelling them anomalies.

These tests pin the PROPERTY that fixes it (a window must not mistake the calendar for
an event), not the number that currently encodes it.

WHY THE SERIES BELOW HAS WEATHER IN IT
--------------------------------------
The first version of this test used a clean sine wave — a season and nothing else — and
it said a SHORTER window was worse. That is true, and it is a trap: with no noise, the
only variation inside a window IS the seasonal drift, so it lands in the denominator,
and shrinking the window shrinks numerator and denominator together. Real power series
are a season with a large day-to-day weather term on top, and that term is what fills
the denominator. Only then does a shorter window help — which is exactly what the
backtest on 8 real zones showed (solar: 12.4% of ordinary days flagged >2σ at 120 days,
9.1% at 30). A test whose model of the world omits the weather will confidently defend
the bug.
"""
from __future__ import annotations

import math
import random
import statistics

from backend.power.baseline import BASELINE_DAYS
from backend.signals.detectors.base import MIN_BASELINE_N, trailing_zscore

OLD_WINDOW = 120


def _season_plus_weather(days: int, *, seed: int = 7) -> list[float]:
    """A quantity with a strong season AND real day-to-day weather — and NO anomaly.

    Solar output, near enough: an annual swing of ±100 around 200, with ±40 of weather
    on any given day. Nothing in this series is an event, so an honest baseline should
    almost never call one.
    """
    rng = random.Random(seed)
    return [
        200 + 100 * math.sin(2 * math.pi * d / 365.0) + rng.gauss(0, 40)
        for d in range(days)
    ]


def _z(series: list[float], day: int, window: int) -> float | None:
    stat = trailing_zscore(series[day], series[day - window : day])
    return None if stat is None else stat[0]


def _false_alarms(series: list[float], window: int) -> tuple[float, float]:
    """(mean |z|, share of days beyond 2σ) over a year of ordinary days."""
    zs = [abs(z) for d in range(365, len(series)) if (z := _z(series, d, window)) is not None]
    return statistics.fmean(zs), sum(1 for z in zs if z > 2) / len(zs)


def test_the_old_window_called_ordinary_days_anomalies():
    """The defect, reproduced: on a year in which nothing whatsoever happens, the
    120-day window flags more than a tenth of all days as 2σ events."""
    _mean, rate = _false_alarms(_season_plus_weather(1200), OLD_WINDOW)
    assert rate > 0.10, "if this stops holding, the old window was not the problem"


def test_the_desks_window_calls_them_ordinary():
    """The same year, the same non-events, measured against the window in production."""
    series = _season_plus_weather(1200)
    new_mean, new_rate = _false_alarms(series, BASELINE_DAYS)
    old_mean, old_rate = _false_alarms(series, OLD_WINDOW)

    assert new_rate < old_rate * 0.75, "the false-alarm rate must fall materially"
    assert new_mean < old_mean * 0.85, "and the whole distribution with it, not just the tail"


def test_a_real_anomaly_still_reads_as_one():
    """The goal is not a quiet desk, it is a right one. A day that genuinely breaks the
    pattern must still be flagged — a window can always be made silent by making it
    useless."""
    series = _season_plus_weather(1200)
    day = 900
    series[day] = 20.0  # the sun goes out

    z = _z(series, day, BASELINE_DAYS)
    assert z is not None and z < -2.0, "a short window must not be a blind window"


def test_the_window_never_starves_the_baseline():
    """Shortening the window shortens the sample. It must still clear the minimum the
    z-score refuses to work below — on prod every zone has 31-32 days of price and grid
    history inside any 30-day window, so there is room, but not a lot of it."""
    assert BASELINE_DAYS > MIN_BASELINE_N


def test_one_window_for_the_whole_desk():
    """Two definitions of "normal" on one screen is how a desk loses an analyst."""
    from backend.power.drivers import BASELINE_DAYS as drivers_window
    from backend.routes.power import SITUATION_BASELINE_DAYS as hero_window

    assert drivers_window == hero_window == BASELINE_DAYS
