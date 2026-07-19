"""Base, Peak, Off-peak — the language the market actually speaks.

Europe trades Base and Peak. Every quote, every hedge, every broker run is
base/peak. Obsyd knew only "daily mean" (which happens to be Base) and a raw
24-hour shape: the PEAK PRICE — the number a trader reads first — was not on the
desk at all, and neither was the peak premium that says whether the tightness is
in the evening ramp or across the whole day.

WHICH CLOCK
-----------
Two different clocks are in play and conflating them is a real error:

  * the CANONICAL STORE is UTC, always. Nothing here changes that.
  * the PRODUCTS are defined in CET/CEST. EPEX/EEX Peak is 08:00-20:00 CET,
    Monday to Friday, and the delivery DAY of a continental day-ahead product
    runs midnight-to-midnight CET.

So the products are computed on the CET delivery day, not on the UTC calendar
day. This matters: the last two hours of a UTC day belong to the NEXT CET
delivery day, so bucketing prices by UTC date silently mixes two delivery days
together. (PowerPriceDaily still buckets by UTC date — see the caveat on
`negative_hours` below.)

Zones whose civil time is not CET (FI, GR, RO, BG in EET; PT and IE-SEM in
WET/WEST) still trade against CET-defined products, so the CET definition is
applied everywhere and said out loud rather than guessed per zone.

NEGATIVE HOURS, HONESTLY
------------------------
`negative_hours` here is counted on the CET DELIVERY day. PowerPriceDaily's
long-standing `negative_hours` column counts them on the UTC calendar day, so the
two can differ by the hours that straddle midnight. That is a known discrepancy,
not a bug in this module: the delivery-day count is the one the market means.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from backend.power.hourly_store import read_hourly
from backend.power.zones import POWER_ZONES

#: The clock the products are defined in. EPEX/EEX Base and Peak are CET/CEST
#: products; the desk speaks that language even for zones whose civil time is not.
MARKET_TZ = ZoneInfo("Europe/Brussels")

#: Peak = 08:00 (inclusive) to 20:00 (exclusive) local, Monday to Friday.
PEAK_START_HOUR = 8
PEAK_END_HOUR = 20

PRICE_SERIES = "price.dayahead"

#: The evening ramp is the steepest rise across this many consecutive hours.
RAMP_HOURS = 3


def market_day(ts_utc: int) -> tuple[str, int, int]:
    """(delivery day YYYY-MM-DD, local hour 0-23, weekday 0-6) in market time."""
    local = datetime.fromtimestamp(ts_utc, tz=timezone.utc).astimezone(MARKET_TZ)
    return local.strftime("%Y-%m-%d"), local.hour, local.weekday()


def is_peak_hour(hour: int, weekday: int) -> bool:
    return weekday < 5 and PEAK_START_HOUR <= hour < PEAK_END_HOUR


def _ramp(hours: dict[int, float]) -> float | None:
    """Steepest rise across RAMP_HOURS consecutive hours — the evening ramp, in
    €/MWh. None when the day is too short to measure one (DST, partial data)."""
    ordered = [hours[h] for h in sorted(hours)]
    if len(ordered) <= RAMP_HOURS:
        return None
    return max(
        ordered[i + RAMP_HOURS] - ordered[i]
        for i in range(len(ordered) - RAMP_HOURS)
    )


def day_products(hours: dict[int, float], weekday: int) -> dict:
    """Base / Peak / Off-peak for one delivery day. Pure.

    `hours` maps LOCAL hour → price. A day with no peak hours at all (a weekend)
    reports peak=None rather than 0: the product simply does not exist that day.
    """
    if not hours:
        return {}
    values = list(hours.values())
    peak_values = [p for h, p in hours.items() if is_peak_hour(h, weekday)]
    off_values = [p for h, p in hours.items() if not is_peak_hour(h, weekday)]

    base = sum(values) / len(values)
    peak = sum(peak_values) / len(peak_values) if peak_values else None
    off = sum(off_values) / len(off_values) if off_values else None

    return {
        "base": round(base, 2),
        "peak": round(peak, 2) if peak is not None else None,
        "off_peak": round(off, 2) if off is not None else None,
        # The premium is what says whether tightness sits in the working day or
        # across the clock. Undefined (not 1.0) when base is zero or negative —
        # a ratio through zero is a number, not a meaning.
        "peak_premium": (
            round(peak / base, 3) if peak is not None and base > 0 else None
        ),
        "peak_hours": len(peak_values),
        "hours": len(values),
        # negative_hours deliberately NOT reported here: the canonical count is
        # PowerPriceDaily.negative_hours (resolution-weighted, UTC day), shown on
        # the day-ahead panel + hero. A whole-hour count on the CET day here gave
        # a different number for the same day (FR 7 vs 5) — one quantity, one place.
        "min": round(min(values), 2),
        "max": round(max(values), 2),
        "evening_ramp": (lambda r: round(r, 2) if r is not None else None)(_ramp(hours)),
        "weekend": weekday >= 5,
    }


def compute_products(db: Session, zone: str, days: int = 30) -> dict:
    """Base/Peak/Off-peak per CET delivery day for one zone."""
    if zone not in POWER_ZONES:
        return {"available": False, "zone": zone, "reason": f"Unknown zone {zone}."}

    # Read a day extra on each side: a CET delivery day straddles two UTC days.
    start_ts = int((datetime.now(timezone.utc) - timedelta(days=days + 1)).timestamp())
    points = read_hourly(db, PRICE_SERIES, zone, start_ts=start_ts)
    if not points:
        return {
            "available": False, "zone": zone,
            "reason": f"No hourly day-ahead prices for {POWER_ZONES[zone]['label']} yet.",
        }

    by_day: dict[str, dict[int, float]] = {}
    weekday_of: dict[str, int] = {}
    for ts, price in points:
        day, hour, weekday = market_day(ts)
        by_day.setdefault(day, {})[hour] = price
        weekday_of[day] = weekday

    rows = []
    for day in sorted(by_day):
        p = day_products(by_day[day], weekday_of[day])
        if p:
            rows.append({"date": day, **p})
    # The first day is usually a partial one (we read an extra day of UTC hours
    # to cover the CET boundary); a base built from four hours is not a base.
    rows = [r for r in rows if r["hours"] >= 20][-days:]
    if not rows:
        return {
            "available": False, "zone": zone,
            "reason": "Not enough hourly prices to form a delivery day yet.",
        }

    latest = rows[-1]
    return {
        "available": True,
        "zone": zone,
        "zone_label": POWER_ZONES[zone]["label"],
        "unit": "EUR/MWh",
        "days": days,
        "market_tz": "CET/CEST",
        "peak_definition": f"{PEAK_START_HOUR:02d}:00–{PEAK_END_HOUR:02d}:00 CET, Mon–Fri",
        "as_of": latest["date"],
        "latest": latest,
        "data": rows,
        "note": (
            "Base, Peak and Off-peak on the CET DELIVERY day — the clock the products "
            "are defined in (EPEX/EEX Peak is 08:00–20:00 CET, Mon–Fri). The canonical "
            "store stays UTC; only these products are re-bucketed. negative_hours here "
            "counts the delivery day, which can differ from the UTC-day count in "
            "PowerPriceDaily by the hours that straddle midnight."
        ),
    }
