"""What counts as a Dunkelflaute, and in which zones the word means anything at all.

THE BUG THIS EXISTS TO FIX
--------------------------
The detector asked one question of all 37 zones: is wind+solar below 15% of load? On prod that
had the radar standing at **27 simultaneous Dunkelflaute alerts — 77% of its entire output** —
led by:

    "NO5: Dunkelflaute — renewables 0% of load"

NO5 is hydro. It has essentially no wind and no solar, and it never has. It is not in a
Dunkelflaute; the sentence is a description of its fleet, dressed as an event. Measured across
the full history:

    NO1, NO5, SK :  100.0% of all days below the threshold
    SI           :   93.2%
    CZ           :   80.7%
    CH           :   77.5%
    IT_NORD      :   76.0%

A flat physical threshold is not wrong in Germany. It is wrong as a threshold for EUROPE,
because it silently assumes every zone has a wind and solar fleet worth talking about.

TWO GATES
---------
1. **Does the concept apply?** A zone whose renewables are a marginal part of supply cannot have
   a Dunkelflaute — it has a hydro fleet, or a nuclear one. If the zone's MEDIAN renewable share
   over its own history is below MIN_FLEET_SHARE, the detector says so and stays silent. Seven
   zones fall here (CH, CZ, IT_NORD, NO1, NO5, SI, SK); thirty remain.

2. **Is today actually unusual, for this zone, in this month?** The share must fall in the bottom
   TAIL_PERCENTILE of that zone's OWN history for the SAME calendar month — a January in DE-LU is
   not a July — AND still be below the absolute threshold, so a zone whose bad days are 30%
   renewable never gets told it is dark.

CALIBRATION (measured, 30 eligible zones, ~5.5 years)
-----------------------------------------------------
    predicate                       zone-days firing     alerts/day across Europe
    flat 15% (what shipped)              ~40%                    27 standing
    own-month p2 AND < 15%                1.41%                   0.4

    DE-LU: 36 Dunkelflaute days in 5.5 years — about six a winter, which is what a German
    Dunkelflaute actually is. DK1 (median renewable share 71%): 16 days.

The threshold constant stays 15%: it is a real grid condition and it belongs in the conjunction.
What was missing was everything else.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

#: A defined grid condition: wind+solar carry less than this share of load. Kept — but as one
#: leg of a conjunction, never as the whole test.
ABSOLUTE_THRESHOLD = 0.15

#: Below this MEDIAN renewable share, a zone has no wind/solar fleet to speak of and the concept
#: does not apply. Chosen from the data: the seven zones under it (CH, CZ, IT_NORD, NO1, NO5, SI,
#: SK) sit at medians of 0.0%–9.8% and were firing on 76–100% of all days.
MIN_FLEET_SHARE = 0.10

#: Today must be in the bottom 2% of the zone's own same-month record. A decile fires 10% of days
#: BY CONSTRUCTION — with 37 zones that is ~3 alerts every single day, which is a feed, not a
#: radar. At 2% it is 0.4/day across Europe.
TAIL_PERCENTILE = 0.02

#: Below this many same-month observations there is no tail to speak of, so no claim is made.
MIN_MONTH_HISTORY = 60


#: One query for every zone's two thresholds. Called per detector run, so it must not be 37
#: queries plus a percentile in Python — power_grid is small, but the "aggregate in SQL" rule is
#: not a size rule, it is a habit. The percentile expression is borders.py's, verbatim, and a
#: test pins this SQL against borders.percentile() on real-shaped data.
_THRESHOLD_SQL = """
WITH d AS (
    SELECT zone,
           substr(date, 6, 2) AS mon,
           (COALESCE(wind_mw, 0) + COALESCE(solar_mw, 0)) / load_mw AS share
      FROM power_grid
     WHERE load_mw > 0
),
ranked_all AS (
    SELECT zone, share,
           ROW_NUMBER() OVER (PARTITION BY zone ORDER BY share) AS rn,
           COUNT(*)     OVER (PARTITION BY zone)                AS cnt
      FROM d
),
med AS (
    SELECT zone, share AS median_share, cnt AS n_all
      FROM ranked_all
     WHERE rn = (cnt + 1) / 2
),
ranked_month AS (
    SELECT zone, share,
           ROW_NUMBER() OVER (PARTITION BY zone ORDER BY share) AS rn,
           COUNT(*)     OVER (PARTITION BY zone)                AS cnt
      FROM d
     WHERE mon = :mon
),
tail AS (
    -- The SAME nearest-rank expression borders.py uses, character for character. A second
    -- percentile convention in one codebase is a second answer to the same question.
    SELECT zone, share AS tail_share, cnt AS n_month
      FROM ranked_month
     WHERE rn = CAST(ROUND(:q * (cnt - 1)) AS INTEGER) + 1
)
SELECT m.zone, m.median_share, m.n_all, t.tail_share, t.n_month
  FROM med m LEFT JOIN tail t ON t.zone = m.zone
"""


def zone_thresholds(db: Session, month: str) -> dict[str, dict]:
    """{zone: {median_share, tail_share, n_month, eligible, reason}} for one calendar month."""
    rows = db.execute(
        text(_THRESHOLD_SQL), {"mon": month, "q": TAIL_PERCENTILE}
    ).all()

    out: dict[str, dict] = {}
    for zone, median_share, _n_all, tail_share, n_month in rows:
        if median_share is None or median_share < MIN_FLEET_SHARE:
            out[zone] = {
                "eligible": False,
                "median_share": median_share,
                "reason": (
                    f"{zone} has no material wind/solar fleet "
                    f"({(median_share or 0) * 100:.0f}% of load on a median day) — a "
                    "Dunkelflaute is not a condition this zone can be in."
                ),
            }
            continue
        if tail_share is None or (n_month or 0) < MIN_MONTH_HISTORY:
            out[zone] = {
                "eligible": False,
                "median_share": median_share,
                "reason": (
                    f"Only {n_month or 0} days of {zone} history for this month — not enough "
                    "to say what is unusual in it."
                ),
            }
            continue
        out[zone] = {
            "eligible": True,
            "median_share": float(median_share),
            "tail_share": float(tail_share),
            "n_month": int(n_month),
        }
    return out


def is_dunkelflaute(share: float, threshold: dict) -> bool:
    """Both legs. Unusually dark FOR THIS ZONE IN THIS MONTH, and dark in absolute terms."""
    if not threshold.get("eligible"):
        return False
    return share < threshold["tail_share"] and share < ABSOLUTE_THRESHOLD
