"""Near-real-time read path: what happened TODAY, so far.

The daily rollup tables (PowerPriceDaily/PowerGrid/…) that the situation hero and
every panel read only ever hold COMPLETE days — the nightly job writes them once a
day is over. The canonical hourly store (backend/power/hourly_store.py) already
fills in hour by hour as ENTSO-E publishes (the ~30-min intraday scheduler job,
`_run_power_intraday`), but nothing reads it that way: the desk simply has no view
of "today" until tomorrow. `compute_live` is that missing read.

WHAT IT SHOWS
-------------
Every hour of the shown day: the published ACTUAL (load, residual, per-fuel
generation, net cross-border flow) where ENTSO-E has published it yet, alongside
the day-ahead FORECAST and auction PRICE for that same hour — published up front,
so future hours of today are real data, not a guess. An hour with no actual yet is
a gap (null), never a zero and never a prediction of ours (Posture B): this module
computes no forecast, it only joins ENTSO-E's own published forecast against its
own later actual.

FALLBACK
--------
Shortly after UTC midnight, ENTSO-E has not published today's first actual hour
yet — `load.actual` for today is empty. Rather than show an empty desk for that
window, the endpoint falls back to yesterday's (complete) day and says so
(`showing: "yesterday"`).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from backend.models.energy import PowerHourly, SeriesDim
from backend.power.hourly_store import read_hourly
from backend.power.zones import POWER_ZONES

#: A day is 24 hourly points; the flow-border scan touches a handful of series
#: per zone. Every read below is capped well above what one day can hold, so a
#: cap is never actually hit — it exists only to fail loudly if it ever were.
_MAX_ROWS = 48

_DAY_SECONDS = 24 * 3600

_NOTE = (
    "Actuals lag ENTSO-E publication by roughly 1-2 hours; hours without a "
    "published actual yet show as gaps. Forecast/price columns are the TSO's "
    "and the auction's own day-ahead figures for the same hour — this desk "
    "never predicts, it only compares what already happened to what was "
    "already published in advance."
)


def _day_start(ts: datetime) -> int:
    """Epoch seconds at 00:00 UTC of the calendar day `ts` falls on."""
    start = ts.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(start.timestamp())


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _discover_gen_by_fuel(
    db: Session, zone: str, start_ts: int, end_ts: int
) -> dict[str, dict[int, float]]:
    """{psr_code: {ts_utc: mw}} for every gen.<Bxx> series with at least one point
    for `zone` in the window.

    Series discovery mirrors the existing convention (backend/power/capture.py's
    `_ids`, backend/routes/power.py::get_flows_hourly): SeriesDim is a small,
    global dimension table (not per-zone), so candidate keys are listed from it
    ONCE and then each is read for THIS zone with a bounded, indexed read — never
    a scan filtered on the joined series_dim.key.
    """
    keys = [k for (k,) in db.query(SeriesDim.key).filter(SeriesDim.key.like("gen.%")).all()]
    out: dict[str, dict[int, float]] = {}
    for key in sorted(keys):
        pts = read_hourly(db, key, zone, start_ts, end_ts, max_rows=_MAX_ROWS)
        if pts:
            out[key.removeprefix("gen.")] = dict(pts)
    return out


def _zone_net_flow(db: Session, zone: str, start_ts: int, end_ts: int) -> dict[int, float]:
    """Per-hour net cross-border flow for `zone` across ALL its borders, positive
    = zone exports. Empty dict (never a false zero) when the zone has no flow
    series at all — callers must read it with `.get(ts)`, never `[ts]`.

    Sign convention (see backend/power/energy_charts_flows.py's module docstring
    and backend/routes/power.py::get_flows_hourly, which this mirrors exactly): a
    border is stored ONCE, as series ``flow.<TO>`` under zone ``<FROM>`` (the
    canonical-sorted first zone of the pair), with net_mw > 0 meaning FROM
    exports. `zone` may be either side of any given border:
      * zone is the storing (FROM) side: its exports live as ``flow.<neighbor>``
        under itself — read with the native sign.
      * zone is the counterparty (TO) side: the series lives as ``flow.<zone>``
        under each neighbour instead — sign must be flipped.
    """
    per_hour: dict[int, list[float]] = defaultdict(list)

    flow_keys = [k for (k,) in db.query(SeriesDim.key).filter(SeriesDim.key.like("flow.%")).all()]
    for key in flow_keys:
        neighbor = key.removeprefix("flow.")
        if neighbor == zone:
            continue
        for ts, v in read_hourly(db, key, zone, start_ts, end_ts, max_rows=_MAX_ROWS):
            per_hour[ts].append(v)

    sid = db.query(SeriesDim.id).filter(SeriesDim.key == f"flow.{zone}").scalar()
    if sid is not None:
        rows = (
            db.query(PowerHourly.ts_utc, PowerHourly.value)
            .filter(
                PowerHourly.series_id == sid,
                PowerHourly.ts_utc >= start_ts,
                PowerHourly.ts_utc < end_ts,
            )
            .all()
        )
        for ts, v in rows:
            per_hour[int(ts)].append(-float(v))

    return {ts: sum(vs) for ts, vs in per_hour.items()}


def compute_live(db: Session, zone: str, *, now: datetime | None = None) -> dict:
    """Every hour of TODAY (or yesterday, if today has no actuals yet): published
    actual load/residual/generation/net-flow alongside the day-ahead
    forecast/price for the same hour. Pure apart from `db`; `now` is injectable
    for tests and defaults to the real wall clock.
    """
    if zone not in POWER_ZONES:
        return {"available": False, "zone": zone, "reason": f"Unknown zone {zone}."}

    now = now or datetime.now(timezone.utc)
    zone_label = POWER_ZONES[zone]["label"]

    today_start = _day_start(now)
    today_end = today_start + _DAY_SECONDS

    load_today = read_hourly(db, "load.actual", zone, today_start, today_end, max_rows=_MAX_ROWS)
    if load_today:
        showing = "today"
        day_start, day_end = today_start, today_end
        load_actual = dict(load_today)
    else:
        showing = "yesterday"
        day_start, day_end = today_start - _DAY_SECONDS, today_start
        load_actual = dict(
            read_hourly(db, "load.actual", zone, day_start, day_end, max_rows=_MAX_ROWS)
        )

    if not load_actual:
        return {
            "available": False,
            "zone": zone,
            "reason": f"No recent load data for {zone_label} yet — check back shortly.",
        }

    load_fc = dict(read_hourly(db, "load.forecast", zone, day_start, day_end, max_rows=_MAX_ROWS))
    price = dict(read_hourly(db, "price.dayahead", zone, day_start, day_end, max_rows=_MAX_ROWS))
    residual_actual = dict(
        read_hourly(db, "residual.actual", zone, day_start, day_end, max_rows=_MAX_ROWS)
    )
    residual_fc = dict(
        read_hourly(db, "residual.forecast", zone, day_start, day_end, max_rows=_MAX_ROWS)
    )
    gen_fc = dict(
        read_hourly(db, "generation.forecast", zone, day_start, day_end, max_rows=_MAX_ROWS)
    )
    gen_by_fuel = _discover_gen_by_fuel(db, zone, day_start, day_end)
    net_flow = _zone_net_flow(db, zone, day_start, day_end)

    def _mw(v: float | None) -> float | None:
        return round(v, 1) if v is not None else None

    def _eur(v: float | None) -> float | None:
        return round(v, 2) if v is not None else None

    hours: list[dict] = []
    for h in range(24):
        ts = day_start + h * 3600
        gen_at_hour = {
            fuel: _mw(vals[ts]) for fuel, vals in gen_by_fuel.items() if ts in vals
        }
        hours.append({
            "ts_utc": _iso(ts),
            "load": _mw(load_actual.get(ts)),
            "load_fc": _mw(load_fc.get(ts)),
            "price": _eur(price.get(ts)),
            "residual": _mw(residual_actual.get(ts)),
            "residual_fc": _mw(residual_fc.get(ts)),
            "gen_fc": _mw(gen_fc.get(ts)),
            "net_flow": _mw(net_flow.get(ts)),
            "gen": gen_at_hour,
        })

    latest_actual_ts = max(load_actual)
    latest_actual_iso = _iso(latest_actual_ts)
    lag_minutes = int((now - datetime.fromtimestamp(latest_actual_ts + 3600, tz=timezone.utc))
                      .total_seconds() // 60)

    a = load_actual.get(latest_actual_ts)
    f = load_fc.get(latest_actual_ts)
    load_vs_forecast_pct = round((a - f) / f * 100.0, 2) if a is not None and f else None

    gen_vals_now = [vals[latest_actual_ts] for vals in gen_by_fuel.values()
                    if latest_actual_ts in vals]
    gen_total_now = round(sum(gen_vals_now), 1) if gen_vals_now else None

    now_hour_ts = int(now.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
                      .timestamp())
    price_now = _eur(price.get(now_hour_ts))

    return {
        "available": True,
        "zone": zone,
        "zone_label": zone_label,
        "showing": showing,
        "hours": hours,
        "latest_actual_ts": latest_actual_iso,
        "lag_minutes": lag_minutes,
        "summary": {
            "load_vs_forecast_pct": load_vs_forecast_pct,
            "gen_total_now": gen_total_now,
            "price_now": price_now,
        },
        "note": _NOTE,
    }
