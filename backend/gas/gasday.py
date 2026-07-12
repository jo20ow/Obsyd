"""The European gas day — the calendar the gas market actually keeps.

A gas day runs 06:00–06:00 LOCAL (CET/CEST), not 00:00–00:00 UTC. Every gas
source Obsyd ingests already reports on it: AGSI/ALSI hand us `gasDayStart`
verbatim, ENTSOG paginates by gas day. Only ENTSO-E power burn — the one
MEASURED demand component of the balance — was bucketed by UTC calendar day,
so the residual engine (`backend/gas/balance.py`, which the code itself calls
"this is the product") subtracted a UTC-day demand from a gas-day supply.

A ~6h offset smears roughly a quarter of each day's power burn into the
neighbouring day. That is worst exactly when power burn swings hardest
day-to-day — cold snaps and Dunkelflaute — i.e. precisely the episodes the
residual flag exists to catch.

DST is handled by the local-time rule rather than a fixed UTC offset: the gas
day boundary stays at 06:00 local, so gas days are 23h/25h long across the
switch, as the market defines them.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

#: The EU gas day is defined in CET/CEST. Brussels (= Berlin/Paris) carries it.
GAS_DAY_TZ = ZoneInfo("Europe/Brussels")

#: Local hour at which one gas day ends and the next begins.
GAS_DAY_START_HOUR = 6


def gas_day(ts: datetime) -> str:
    """The gas day (YYYY-MM-DD) a timestamp belongs to.

    Everything from 06:00 local on day D up to (not including) 06:00 local on
    D+1 is gas day D. Naive timestamps are read as UTC (ENTSO-E is UTC), so a
    missing tzinfo can never silently shift a whole series by an hour.
    """
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    local = ts.astimezone(GAS_DAY_TZ)
    return (local - timedelta(hours=GAS_DAY_START_HOUR)).date().isoformat()
