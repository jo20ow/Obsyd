"""The daily tables, derived from the canonical hourly store — one source, one answer.

WHY THIS MODULE EXISTS
----------------------
`power_grid` and `power_gen_mix` used to be built by a SECOND parse of the ENTSO-E XML, next to
the one that fills `power_hourly`. Two parses of one document is two answers to one question, and
all three of the following came out of that gap:

1. **The current day was stored as a day.** The daily mean was `sum(points) / len(points)` over
   whatever had been published so far — at 10:31 UTC, a mean of nine night-and-morning hours. The
   radar read the newest row and announced "PT: Dunkelflaute — renewables 11% of load"; six hours
   later PT sat at 22%, because the sun had come up. Nothing had happened. The day had.

2. **Missing zeros became a higher mean.** Several zones do not publish solar at night — PT sends
   18 points. Dividing by 18 gives the mean of the DAYLIGHT hours and calls it the day: PT's solar
   for 2026-07-13 was stored as 1619 MW where the day's mean is 1147, a third too high, on a
   SETTLED day, for as long as the record goes back. That lifts the renewable share, sinks the
   residual load, and feeds both into the Dunkelflaute predicate and the z-scores.

3. **Revised points were counted twice.** The daily parse appended every Point in the document,
   including those an overlapping, revised period restates. The hourly parse averaged per hour. So
   the daily table and the hourly store disagreed about the same day (DE-LU solar: 19,988 vs
   19,364 MW) — and the desk quoted whichever one you happened to ask.

THE RULES
---------
* **Generation is averaged over the DAY** (24 UTC hours): an hour with no point is a zero the
  publisher did not send, because a solar farm at 03:00 produces zero and everyone knows it.
* **Load is averaged over the hours it HAS**: load is never zero, so an absent hour is a hole, not
  a zero, and averaging it in as one would invent a demand collapse. The row carries `load_hours`
  so a reader can see how much of the day that mean stands on.
* **`gen_hours` counts the hours in which ANY fuel was published.** That is what separates "PT
  omits solar at night" (wind, gas and hydro still report — the day is covered) from "the feed
  fell over for six hours" (nothing reports — the day is not).
* **Only finished days become rows.** A day that is not over is not a day: `days_to_derive` stops
  at yesterday, so the newest row in `power_grid` is a settled day BY CONSTRUCTION, and every
  consumer of "the latest row" — radar, hero, matrix, /grid, episodes, alert rules — is right
  without having to know any of this.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

#: A day is 24 UTC hours. Days are keyed in UTC, so this holds across DST.
HOURS_PER_DAY = 24

#: The psrTypes the grid table lifts out of the mix by name.
PSR_SOLAR = "B16"
PSR_WIND_OFFSHORE = "B18"
PSR_WIND_ONSHORE = "B19"


def daily_from_hours(
    load_hours: dict[int, float],
    gen_hours: dict[str, dict[int, float]],
) -> dict:
    """One day's daily-mean row from its hour maps. Pure — no DB, no clock.

    `load_hours`  {hour_utc: MW}
    `gen_hours`   {psrType: {hour_utc: MW}} — GENERATION only (consumption series are a separate
                  key in the store and must never be summed into the mix; see CONSUMPTION_SUFFIX).

    Returns {load_mw, wind_mw, solar_mw, residual_mw, load_hours, gen_hours, mix}.
    """
    load_mw = sum(load_hours.values()) / len(load_hours) if load_hours else None

    mix = {
        psr: sum(hours.values()) / HOURS_PER_DAY
        for psr, hours in gen_hours.items()
        if hours
    }
    covered: set[int] = set()
    for hours in gen_hours.values():
        covered |= set(hours)

    wind_mw = mix.get(PSR_WIND_OFFSHORE, 0.0) + mix.get(PSR_WIND_ONSHORE, 0.0)
    solar_mw = mix.get(PSR_SOLAR, 0.0)

    # No load → no demand → no residual (IE-SEM has published none since 2025-10-23). No
    # generation at all → wind/solar are not zero, they are unknown, so neither is the residual.
    residual_mw = load_mw - wind_mw - solar_mw if load_mw is not None and mix else None

    return {
        "load_mw": load_mw,
        "wind_mw": wind_mw if mix else None,
        "solar_mw": solar_mw if mix else None,
        "residual_mw": residual_mw,
        "load_hours": len(load_hours),
        "gen_hours": len(covered),
        "mix": mix,
    }


def share_is_claimable(load_hours: int | None, gen_hours: int | None) -> bool:
    """Can this day carry a statement about the renewable SHARE of load?

    Only with a full day on both legs: a load mean standing on 24 hours, and generation reported
    in every hour of the day. The second is what tells "PT omits solar at night" (wind and gas
    still report — nothing is missing) from "the feed fell over for six hours" (nothing reports —
    and calling those hours zero would manufacture a Dunkelflaute out of an outage).

    NULL means unknown — a row written before the hour counts existed — and unknown is not a yes.
    """
    return load_hours == HOURS_PER_DAY and gen_hours == HOURS_PER_DAY


def days_to_derive(days: Iterable[str], *, now: datetime | None = None) -> list[str]:
    """The subset of `days` that are OVER in UTC — the only ones that can be a daily mean.

    Everything downstream reads "the latest row" and treats it as a day. Keeping the current day
    out of the table is what makes that true, at the source, for all of them at once.
    """
    now = now or datetime.now(UTC)
    today = now.strftime("%Y-%m-%d")
    return sorted(d for d in days if d < today)
