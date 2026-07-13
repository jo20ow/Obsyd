"""Write down what is offline right now, because ENTSO-E will not remember it.

THE FINDING THAT MADE THIS NECESSARY
------------------------------------
The desk wanted to give forced outages a z-score ("6.7 GW offline, 2.1σ above normal")
and to compute a capacity margin. Both need a HISTORY of how much capacity was offline.
There isn't one, and it cannot be recovered. Counting the currently-published events by
the month they were running in, on prod:

    running in 2026-07 (now)   1013 events across 19 zones
    running in 2026-06          33
    running in 2026-05          15
    running in 2026-03           5

A77 is a NOTICE BOARD, not an archive: a TSO publishes an unavailability while it is
pending or running, and once it is over it comes down. Every day nobody writes it down
is a day of history destroyed. This module writes it down.

WHY ONLY THE CURRENT HOUR
-------------------------
The obvious shortcut — on each run, expand the published events across the last 24 hours
— produces a series that is quietly WRONG, and wrong in one direction. An outage that
ended six hours ago has already left the notice board, so the hours it was running in get
recomputed WITHOUT it. Backfilling from a feed that has already forgotten is how you build
a history that systematically undercounts exactly the events worth recording.

So each run records the one hour it can honestly speak for: this one. Run hourly, the
series fills in with no gaps and no fiction. A missed run leaves a hole, and a hole is
the correct representation of an hour nobody looked.

WHAT THE NUMBERS DO AND DO NOT MEAN
-----------------------------------
`outage.offline` is ALL published unavailability (planned + forced); `outage.forced` is
the A54 subset — the desk's headline, because planned maintenance is priced in months
ahead and a forced trip is not.

Neither is comparable ACROSS zones, and nothing here should ever be used to build a
cross-zone capacity margin. Publication completeness is a property of the TSO, not of the
fleet: Czechia has published 6,633 events and Germany — with the largest fleet in Europe —
94. A margin computed from that would say the German system is never tight. Per zone,
against that zone's OWN history, the series is honest: the coverage is stable within a
zone even where it is thin, so the delta stays true. That is the same rule the desk
applies to every proxy it publishes.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from backend.power.hourly_store import upsert_hourly
from backend.power.zones import POWER_ZONES
from backend.signals.detectors.power import latest_outage_revisions

#: All published unavailability, planned and forced.
SERIES_OFFLINE = "outage.offline"

#: The A54 subset: forced. What the desk leads with.
SERIES_FORCED = "outage.forced"

FORCED = "A54"


def offline_mw_at(rows, at_iso: str) -> tuple[float, float]:
    """(all offline MW, forced offline MW) at one instant, from latest-revision rows.

    Pure. `rows` must already be revision-resolved — a withdrawn latest revision hides
    the event, and filtering on status before ranking would let an older active revision
    win and fabricate gigawatts.
    """
    total = forced = 0.0
    for r in rows:
        if r.status != "active" or r.nominal_mw is None:
            continue
        if not (r.start_utc <= at_iso <= r.end_utc):
            continue
        mw = r.nominal_mw - (r.available_mw or 0.0)
        total += mw
        if r.business_type == FORCED:
            forced += mw
    return total, forced


def snapshot_outages(db: Session, *, now: datetime | None = None) -> dict:
    """Record this hour's offline capacity for every zone. Idempotent within the hour."""
    now = now or datetime.now(timezone.utc)
    hour = now.replace(minute=0, second=0, microsecond=0)
    ts = int(hour.timestamp())
    at_iso = hour.strftime("%Y-%m-%dT%H:%MZ")

    written = 0
    zones_with_outages = 0
    for zone in POWER_ZONES:
        rows = latest_outage_revisions(db, zone, ending_after=at_iso)
        if not rows:
            continue
        total, forced = offline_mw_at(rows, at_iso)
        # A zone with published events but nothing running right now records a ZERO, not
        # a gap: "nothing was offline" is a fact, and a baseline built only from the hours
        # something WAS offline would have no floor to measure against.
        written += upsert_hourly(db, SERIES_OFFLINE, zone, [(ts, total)], unit="MW")
        written += upsert_hourly(db, SERIES_FORCED, zone, [(ts, forced)], unit="MW")
        zones_with_outages += 1

    db.commit()
    return {"hour": at_iso, "zones": zones_with_outages, "points": written}
