"""Duration curves — the distribution view of a year of hours. Compute-on-read
(the marginal.py pattern): a sorted curve is a VIEW of series already in the
store, recomputed per request, never persisted — persisting one would freeze a
window the reader didn't choose.

    duration(series, window) = the window's hourly values sorted descending,
    read as "value exceeded in x% of hours"

The storage/flexibility-research workhorse: how many hours a year is the price
above a battery's spread target, how deep do the cheap hours run, how fat is
the residual-load peak a gas fleet must still cover. Descriptive arithmetic on
published hours — no model, no forecast.

The curve is downsampled to 101 exceedance points (p0..p100) for transport;
the exact percentile table rides beside it. Series whitelist is deliberate:
price and residual load are the two curves the literature actually uses, and
an open-ended series parameter would turn this into a second /series endpoint
with a sort bolted on.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from backend.power.hourly_store import read_hourly
from backend.power.zones import POWER_ZONES

SERIES = {
    "price": ("price.dayahead", "EUR/MWh"),
    "residual": ("residual.actual", "MW"),
}

MAX_DAYS = 3 * 365
PERCENTILES = (1, 5, 10, 25, 50, 75, 90, 95, 99)


def _quantile_desc(sorted_desc: list[float], exceed_pct: float) -> float:
    """Value exceeded in `exceed_pct`% of hours (linear interpolation)."""
    n = len(sorted_desc)
    if n == 1:
        return sorted_desc[0]
    i = (exceed_pct / 100.0) * (n - 1)
    lo = int(i)
    hi = min(lo + 1, n - 1)
    return sorted_desc[lo] + (sorted_desc[hi] - sorted_desc[lo]) * (i - lo)


def compute_duration(db: Session, zone: str, series: str, days: int) -> dict:
    if zone not in POWER_ZONES:
        return {"available": False, "zone": zone, "reason": f"Unknown zone {zone}."}
    if series not in SERIES:
        return {
            "available": False, "zone": zone,
            "reason": f"series must be one of {sorted(SERIES)}",
        }
    days = max(30, min(days, MAX_DAYS))
    key, unit = SERIES[series]
    now = datetime.now(timezone.utc)
    start_ts = int(now.timestamp()) - days * 86400
    rows = read_hourly(db, key, zone, start_ts=start_ts, max_rows=days * 25)
    if len(rows) < 24 * 20:
        return {
            "available": False, "zone": zone,
            "reason": f"Fewer than 20 days of {key} in the window — a curve on a fragment misleads.",
        }
    values = sorted((v for _, v in rows), reverse=True)
    newest = max(ts for ts, _ in rows)
    return {
        "available": True,
        "zone": zone,
        "zone_label": POWER_ZONES[zone]["label"],
        "series": key,
        "unit": unit,
        "window_days": days,
        "n_hours": len(values),
        "curve": [
            {"exceed_pct": p, "value": round(_quantile_desc(values, p), 2)}
            for p in range(0, 101)
        ],
        "percentiles": {
            f"p{p}": round(_quantile_desc(values, 100 - p), 2) for p in PERCENTILES
        },
        "mean": round(sum(values) / len(values), 2),
        "as_of": datetime.fromtimestamp(newest, tz=timezone.utc).strftime("%Y-%m-%d"),
        "note": (
            "Hours of the window sorted descending — value exceeded in x% of hours. "
            "Published day-ahead/actual hours only; descriptive, not a forecast."
        ),
    }
