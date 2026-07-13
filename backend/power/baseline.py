"""How long is "normal"? — the one window every "vs its own norm" on the desk uses.

The desk's most-repeated claim is a comparison: "€142/MWh, +2.4σ vs its norm",
"wind 2.8σ below normal", ELEVATED instead of CALM. Every one of those sentences is
a z-score against a trailing window, and the length of that window decides what the
word "normal" means. It had never been measured. It was 120 days.

WHAT 120 DAYS ACTUALLY SAID
---------------------------
A 120-day window ending in March is built from November, December, January and
February. Comparing a March day against it does not measure whether the day is
unusual — it measures that it is March. Backtested over 8 zones × 2 years, the
mean z-score by calendar month (which should be ~0 in every month, since an average
March day is not an anomaly):

                          Jan    Apr    Jul    Oct    swing
    solar     120d      -0.49  +1.43  +0.44  -1.32     3.30   ← the season, not the sun
    load      120d      +1.01  -1.21  -0.01  +0.78     2.51
    price     120d      +0.68  -0.68  +0.37  +0.16     1.36
    residual  120d      +0.69  -0.74  +0.27  +0.17     1.48

An average March day was being reported as +2.0σ of solar. The desk was calling the
seasons and labelling them anomalies. In the headline state this showed up as: on a
November day the desk was non-CALM 25% of the time, on a February day 4% — a 21-point
swing driven by nothing but the calendar.

At 30 days every one of those biases roughly halves (solar 3.30 → 1.53, load 2.51 →
1.37, residual 1.48 → 0.87, wind 1.06 → 0.65, price 1.36 → 0.92; state swing 21 → 12
points) while the share of days flagged barely moves. A short window tracks the season
instead of straddling it, and — the reason a seasonal baseline is NOT the answer here —
it also tracks the FLEET.

WHY NOT COMPARE AGAINST THE SAME WEEK IN PRIOR YEARS
----------------------------------------------------
That is the obvious fix and it is worse, because Europe keeps building. Measured the
same way, a same-calendar-window-in-prior-years baseline puts solar at a mean of
+1.26σ and calls 31% of ALL days more than 2σ above normal — it is not reading the
weather, it is reading Germany's installed capacity. Residual load inherits the same
trend with the opposite sign (mean −0.43σ). Rescaling prior years to this year's level
was tried and is unstable (it moved Spanish solar from +0.2σ to −3.1σ).

A trailing window has neither problem: 30 days ago the fleet was the same size.

(Reservoir hydro is the exception that proves the rule — it is SO seasonal that a
trailing window is useless, and its own module compares against the same ISO week in
prior years. It can, because hydro capacity does not change. See entsoe_hydro.py::
same_week_band.)

The residue is honest and disclosed: solar still carries ~1.5σ of seasonal swing at 30
days, because within a month the spring sun really does climb. Every response ships
`baseline_days` so the window is never implicit.
"""

from __future__ import annotations

#: The trailing window every "vs its own norm" on the power desk measures against —
#: the situation hero, the driver card, the overview matrix. ONE window: two different
#: definitions of "normal" on the same screen is how a desk loses an analyst's trust.
#:
#: 30 days, chosen by the backtest above: it halves the seasonal bias of the 120-day
#: window it replaces, keeps the flag rate stable, and never falls below MIN_BASELINE_N
#: (every zone has 31-32 days of price and grid history in any 30-day window).
BASELINE_DAYS = 30
