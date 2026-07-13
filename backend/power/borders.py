"""The border layer — where the price series and the flow series finally meet.

Obsyd has stored day-ahead prices for 37 zones and hourly cross-border flows for
45 borders, and until now nothing in the codebase ever joined them. So the desk
could say DE-LU is at €140 and FR at €55, but never that the two cleared apart
for 61% of last month's hours, that the interconnector sat at its own historical
rail in 18% of them, or that power physically ran from the expensive zone to the
cheap one in 4%.

WHAT THESE NUMBERS ARE, AND ARE NOT
-----------------------------------
Under SDAC market coupling, two zones clearing at the SAME price means the
auction had no reason to split them; a spread means it did. It is tempting to
call that "the interconnector was binding" — and it is wrong to, at least in the
Core region, which allocates flow-based: there the binding constraint is a
network element inside the grid, not the border itself, and a spread can appear
between zones whose own interconnector is half empty.

So every metric here is DESCRIPTIVE STATISTICS on published records:
  * convergence — how often the two zones cleared at the same price
  * spread      — how far apart they cleared, and how that ranks in this
                  border's own history
  * at the rail — how often the physical flow reached this border's OWN 95th
                  percentile (an honest proxy: we hold no NTC, and in flow-based
                  regions no NTC is even published)
  * counter-price — how often power physically flowed from the expensive zone to
                  the cheap one. Physically real (loop and transit flows) and
                  exactly the thing a zonal price map cannot show you.
None of it is a trade, a forecast, or a claim about causation.

COVERAGE, STATED PLAINLY
------------------------
The flow series come from Energy-Charts at COUNTRY level, so a border only gets
metrics when BOTH sides are priced bidding zones. 26 of 45 borders qualify. The
other 19 touch a country aggregate that has no single price (IT, DK, NO, SE) or
no price at all (GB, LU) — they are reported as uncoverable, not hidden.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from backend.models.energy import PowerHourly, SeriesDim, ZoneDim
from backend.power.zones import POWER_ZONES

#: Two zones "cleared together" when their day-ahead prices differ by less than
#: this. Not 0.00: SDAC publishes to the cent, and a rounding cent is not a
#: market split.
COUPLED_EPS_EUR = 0.5

#: A flow is "at the rail" at or above this percentile of the border's OWN
#: |flow| history. We hold no NTC — and in flow-based regions ENTSO-E publishes
#: none — so the border's own realised maximum is the honest reference.
RAIL_PERCENTILE = 0.95

#: History the rail threshold is measured over.
RAIL_BASELINE_DAYS = 365

PRICE_SERIES = "price.dayahead"


def percentile(values: list[float], q: float) -> float | None:
    """Nearest-rank percentile. None on an empty list."""
    if not values:
        return None
    ordered = sorted(values)
    idx = min(int(round(q * (len(ordered) - 1))), len(ordered) - 1)
    return ordered[idx]


def border_metrics(
    prices_a: dict[int, float],
    prices_b: dict[int, float],
    flow: dict[int, float],
    rail_threshold: float | None,
) -> dict:
    """Descriptive statistics for one border over one window. Pure.

    `flow` is keyed the canonical way: net_mw > 0 means zone A exports to B.
    """
    hours = sorted(set(prices_a) & set(prices_b))
    spreads = [prices_a[t] - prices_b[t] for t in hours]
    abs_spreads = [abs(s) for s in spreads]
    n = len(hours)

    coupled = sum(1 for s in abs_spreads if s < COUPLED_EPS_EUR)
    split = [(t, prices_a[t] - prices_b[t]) for t in hours
             if abs(prices_a[t] - prices_b[t]) >= COUPLED_EPS_EUR]

    # Counter-price: power physically flows FROM the expensive zone TO the cheap
    # one. Only meaningful in hours the zones actually cleared apart.
    counter = 0
    for t, spread in split:
        f = flow.get(t)
        if f is None or f == 0:
            continue
        a_exports = f > 0
        a_expensive = spread > 0
        if a_exports == a_expensive:  # exporting into the cheaper zone
            counter += 1

    flow_hours = [abs(v) for t, v in flow.items() if t in set(hours)] if hours else []
    at_rail = (
        sum(1 for v in flow_hours if rail_threshold and v >= rail_threshold)
        if rail_threshold else 0
    )

    # The latest PRICE hour and the latest FLOW hour are not the same hour: the
    # day-ahead auction publishes into tomorrow, while physical flows only exist
    # up to now. Reading the flow at the price's timestamp returns None for every
    # border every afternoon — so each takes its own latest.
    latest_t = hours[-1] if hours else None
    latest_spread = prices_a[latest_t] - prices_b[latest_t] if latest_t else None
    latest_flow_t = max(flow) if flow else None
    latest_flow = flow[latest_flow_t] if latest_flow_t is not None else None

    return {
        "hours": n,
        "convergence_pct": round(100.0 * coupled / n, 1) if n else None,
        "mean_abs_spread": round(sum(abs_spreads) / n, 2) if n else None,
        "p95_abs_spread": round(percentile(abs_spreads, 0.95), 2) if abs_spreads else None,
        "latest_spread": round(latest_spread, 2) if latest_spread is not None else None,
        "latest_flow_mw": round(latest_flow, 1) if latest_flow is not None else None,
        "split_hours": len(split),
        "counter_price_hours": counter,
        "counter_price_pct": round(100.0 * counter / len(split), 1) if split else None,
        "at_rail_hours": at_rail,
        "at_rail_pct": round(100.0 * at_rail / len(flow_hours), 1) if flow_hours else None,
        "rail_threshold_mw": round(rail_threshold, 1) if rail_threshold else None,
        "spread_as_of": (
            datetime.fromtimestamp(latest_t, tz=timezone.utc).isoformat()
            if latest_t else None
        ),
        "flow_as_of": (
            datetime.fromtimestamp(latest_flow_t, tz=timezone.utc).isoformat()
            if latest_flow_t is not None else None
        ),
        "as_of": (
            datetime.fromtimestamp(latest_t, tz=timezone.utc).strftime("%Y-%m-%d")
            if latest_t else None
        ),
    }


#: The two grains a border can have, and they are NOT the same quantity.
#:
#: `flow.*`  — what the wires physically carried (Fraunhofer Energy-Charts, COUNTRY-level, so
#:             the 18 sub-zones have none).
#: `sched.*` — what the market agreed to move (ENTSO-E A09, BIDDING-ZONE-level, 63 borders
#:             including every sub-zone and the internal ones no country feed can express).
#:
#: Their difference is loop flow. Merging them into one namespace would make that difference
#: uncomputable and every existing number ambiguous, so they stay apart and the response says
#: which grain each border was read from.
PHYSICAL_PREFIX = "flow."
SCHEDULED_PREFIX = "sched."


def _flow_dims(db: Session, prefix: str = PHYSICAL_PREFIX) -> tuple[dict[int, str], dict[int, str]]:
    """(series_id → counterparty, zone_id → zone key) for one border grain.

    Resolving the ids FIRST is the whole performance story. power_hourly holds
    28.5M rows under a WITHOUT ROWID primary key of (series_id, zone_id, ts_utc);
    filtering on `series_dim.key LIKE 'flow.%'` cannot use that clustered key, so
    SQLite scanned the entire table. Filtering on `series_id IN (...)` seeks
    straight into it.
    """
    series = {
        sid: key[len(prefix):]
        for sid, key in db.query(SeriesDim.id, SeriesDim.key)
        .filter(SeriesDim.key.like(f"{prefix}%")).all()
    }
    zones = dict(db.query(ZoneDim.id, ZoneDim.key).all())
    return series, zones


def _flow_rows(db: Session, start_ts: int,
               prefix: str = PHYSICAL_PREFIX) -> dict[tuple[str, str], dict[int, float]]:
    """Every hourly cross-border value of one grain since `start_ts`, by canonical border.

    Call this for the DISPLAY window only — the rail threshold, the only thing
    that needs a year of history, is computed in SQL (_rail_thresholds).
    """
    series, zones = _flow_dims(db, prefix)
    if not series:
        return {}
    rows = (
        db.query(PowerHourly.series_id, PowerHourly.zone_id,
                 PowerHourly.ts_utc, PowerHourly.value)
        .filter(PowerHourly.series_id.in_(series), PowerHourly.ts_utc >= start_ts)
        .all()
    )
    out: dict[tuple[str, str], dict[int, float]] = {}
    for sid, zid, ts, value in rows:
        from_zone = zones.get(zid)
        if from_zone is None:
            continue
        out.setdefault((from_zone, series[sid]), {})[int(ts)] = float(value)
    return out


#: The rail thresholds are a 365-day statistic: they move by hours-old data about
#: as much as a coastline moves by one wave. Ranking a year of flows still costs
#: ~1.4 s, so the result is held for this long and the response says WHEN it was
#: computed. (Unlike the situation hero, where caching would have let a stale
#: as_of masquerade as fresh, nothing here is a freshness claim.)
RAIL_CACHE_TTL_SECONDS = 6 * 3600

#: Keyed by grain: a scheduled border's own p95 is a different number from a physical one's,
#: and sharing one cache between them would hand a border the other grain's rail.
_rail_cache: dict[str, dict] = {}


def rail_thresholds_cached(db: Session, start_ts: int, *, now: datetime | None = None,
                           prefix: str = PHYSICAL_PREFIX):
    """(thresholds, computed_at) — recomputed at most every RAIL_CACHE_TTL_SECONDS."""
    now = now or datetime.now(timezone.utc)
    entry = _rail_cache.get(prefix)
    if (
        entry is None
        or (now - entry["computed_at"]).total_seconds() >= RAIL_CACHE_TTL_SECONDS
    ):
        entry = {"values": _rail_thresholds(db, start_ts, prefix), "computed_at": now}
        _rail_cache[prefix] = entry
    return entry["values"], entry["computed_at"]


def reset_rail_cache() -> None:
    """Test isolation."""
    _rail_cache.clear()


def _rail_thresholds(db: Session, start_ts: int,
                     prefix: str = PHYSICAL_PREFIX) -> dict[tuple[str, str], float]:
    """The RAIL_PERCENTILE of |flow| per border, computed in SQL.

    Nearest-rank, identical to `percentile()` (pinned by test) — SQLite ranks the
    year of flows and returns one row per border instead of hundreds of thousands
    of rows to Python.
    """
    series, zones = _flow_dims(db, prefix)
    if not series:
        return {}
    sql = text("""
        WITH ranked AS (
            SELECT h.series_id AS sid,
                   h.zone_id AS zid,
                   ABS(h.value) AS av,
                   ROW_NUMBER() OVER (
                       PARTITION BY h.series_id, h.zone_id ORDER BY ABS(h.value)
                   ) AS rn,
                   COUNT(*) OVER (PARTITION BY h.series_id, h.zone_id) AS cnt
            FROM power_hourly h
            WHERE h.series_id IN :sids AND h.ts_utc >= :start
        )
        SELECT sid, zid, av FROM ranked
        WHERE rn = CAST(ROUND(:q * (cnt - 1)) AS INTEGER) + 1
    """).bindparams(bindparam("sids", expanding=True))
    rows = db.execute(
        sql, {"sids": list(series), "start": start_ts, "q": RAIL_PERCENTILE}
    ).all()
    return {
        (zones[zid], series[sid]): float(av)
        for sid, zid, av in rows
        if zid in zones and sid in series
    }


def _price_rows(db: Session, zones: set[str], start_ts: int) -> dict[str, dict[int, float]]:
    """Day-ahead prices for `zones` since `start_ts`. Same id-first rule as the
    flow query: joining through series_dim.key made SQLite scan all 28.5M rows."""
    sid = db.query(SeriesDim.id).filter(SeriesDim.key == PRICE_SERIES).scalar()
    if sid is None:
        return {}
    zone_ids = {
        zid: key
        for zid, key in db.query(ZoneDim.id, ZoneDim.key).filter(ZoneDim.key.in_(zones)).all()
    }
    if not zone_ids:
        return {}
    rows = (
        db.query(PowerHourly.zone_id, PowerHourly.ts_utc, PowerHourly.value)
        .filter(PowerHourly.series_id == sid,
                PowerHourly.zone_id.in_(zone_ids),
                PowerHourly.ts_utc >= start_ts)
        .all()
    )
    out: dict[str, dict[int, float]] = {}
    for zid, ts, value in rows:
        out.setdefault(zone_ids[zid], {})[int(ts)] = float(value)
    return out


def loop_flow(physical: dict[int, float], scheduled: dict[int, float]) -> dict | None:
    """physical − scheduled, over the hours BOTH grains cover. Pure.

    What the wires carried minus what the market agreed to move. It is not a claim about any
    single interconnector: in a flow-based region, power scheduled from A to B routes through
    whatever the physics allows, so the residual is transit and loop flow together. Descriptive.

    Returns None where only one grain exists — which is most sub-zone borders (no physical
    feed) and a few country ones. An absent number with a reason beats an invented one.
    """
    hours = sorted(set(physical) & set(scheduled))
    if not hours:
        return None
    diffs = [physical[h] - scheduled[h] for h in hours]
    return {
        "loop_hours": len(hours),
        "loop_mean_mw": round(sum(diffs) / len(diffs), 1),
        "loop_p95_mw": round(percentile([abs(d) for d in diffs], 0.95) or 0.0, 1),
    }


def compute_borders(db: Session, days: int = 30, *, now: datetime | None = None) -> dict:
    """Every border with a price on both sides, ranked by how far apart it clears.

    Reads BOTH grains and prefers the scheduled one where it exists, because that is the grain
    that resolves bidding zones: the physical feed is country-level, so it cannot see DK1 from
    DK2 at all. Where both exist, their difference is reported as loop flow.
    """
    now = now or datetime.now(timezone.utc)
    window_start = int((now - timedelta(days=days)).timestamp())
    rail_start = int((now - timedelta(days=RAIL_BASELINE_DAYS)).timestamp())

    physical = _flow_rows(db, window_start, PHYSICAL_PREFIX)
    scheduled = _flow_rows(db, window_start, SCHEDULED_PREFIX)
    if not physical and not scheduled:
        return {"available": False,
                "reason": "No cross-border flow series yet — check back shortly."}

    rails_phys, rails_at = rail_thresholds_cached(db, rail_start, now=now,
                                                  prefix=PHYSICAL_PREFIX)
    rails_sched, _ = rail_thresholds_cached(db, rail_start, now=now,
                                            prefix=SCHEDULED_PREFIX)

    priced = set(POWER_ZONES)
    all_borders = set(physical) | set(scheduled)
    joinable = {b for b in all_borders if b[0] in priced and b[1] in priced}
    uncoverable = sorted(f"{a}-{b}" for a, b in all_borders if (a, b) not in joinable)

    prices = _price_rows(db, {z for b in joinable for z in b}, window_start)

    out = []
    for a, b in sorted(joinable):
        sched = scheduled.get((a, b))
        phys = physical.get((a, b))
        # The scheduled grain is bidding-zone-resolved; the physical one is not. Where both
        # exist they describe the same border, and the scheduled one is the one that keeps
        # its meaning for a sub-zone.
        series = sched if sched else phys
        source = "scheduled" if sched else "physical"
        rails = rails_sched if sched else rails_phys

        m = border_metrics(prices.get(a, {}), prices.get(b, {}), series, rails.get((a, b)))
        if not m["hours"]:
            continue

        loops = loop_flow(phys, sched) if (phys and sched) else None
        out.append({
            "zone_a": a, "zone_b": b,
            "label": f"{POWER_ZONES[a]['label']}↔{POWER_ZONES[b]['label']}",
            "flow_source": source,
            "expensive_side": (
                None if m["latest_spread"] is None or abs(m["latest_spread"]) < COUPLED_EPS_EUR
                else (a if m["latest_spread"] > 0 else b)
            ),
            **m,
            **(loops or {
                "loop_hours": 0, "loop_mean_mw": None, "loop_p95_mw": None,
                "loop_reason": (
                    "no physical flow for this border — Energy-Charts reports by country, so "
                    "bidding sub-zones have none"
                    if not phys else
                    "no scheduled exchange for this border"
                ),
            }),
        })

    out.sort(key=lambda r: -(r["mean_abs_spread"] or 0))
    return {
        "available": bool(out),
        "days": days,
        "unit": "EUR/MWh",
        "coupled_eps_eur": COUPLED_EPS_EUR,
        "rail_percentile": RAIL_PERCENTILE,
        "rail_baseline_days": RAIL_BASELINE_DAYS,
        "rail_computed_at": rails_at.isoformat(),
        "borders": out,
        "uncoverable_borders": uncoverable,
        "note": (
            "Convergence = share of hours the two zones cleared within "
            f"{COUPLED_EPS_EUR} EUR/MWh. 'At the rail' = flow at or above this border's own "
            "95th percentile over the last year (we hold no NTC, and flow-based regions "
            "publish none). Counter-price = power ran from the expensive zone to the cheap "
            "one. `flow_source` says which grain the border was read from: 'scheduled' is "
            "ENTSO-E's bidding-zone schedule, 'physical' is the country-level metered flow. "
            "Loop flow = physical minus scheduled where both exist — transit and loop "
            "together, NOT a claim about any single interconnector. Descriptive statistics "
            "on published records; a spread is not a claim that this border was the binding "
            "constraint."
        ),
    }


def compute_spread(db: Session, a: str, b: str, days: int = 30,
                   *, now: datetime | None = None) -> dict:
    """One border, hour by hour: both prices, their spread, and the flow."""
    now = now or datetime.now(timezone.utc)
    window_start = int((now - timedelta(days=days)).timestamp())
    rail_start = int((now - timedelta(days=RAIL_BASELINE_DAYS)).timestamp())

    zone_a, zone_b = sorted([a, b])  # canonical border order
    if zone_a not in POWER_ZONES or zone_b not in POWER_ZONES:
        return {"available": False, "reason": f"Unknown zone in border {a}-{b}."}

    window_flows = _flow_rows(db, window_start)
    flow_window = window_flows.get((zone_a, zone_b))
    if flow_window is None:
        return {
            "available": False,
            "zone_a": zone_a, "zone_b": zone_b,
            "reason": (
                f"No flow series for {zone_a}-{zone_b}. Energy-Charts publishes flows at "
                "country level, so borders touching a sub-zoned market (Italian zones, "
                "DK1/DK2, Nordic zones) or an unpriced neighbour (GB, LU) have none."
            ),
        }

    prices = _price_rows(db, {zone_a, zone_b}, window_start)
    rail = rail_thresholds_cached(db, rail_start, now=now)[0].get((zone_a, zone_b))
    m = border_metrics(prices.get(zone_a, {}), prices.get(zone_b, {}), flow_window, rail)
    if not m["hours"]:
        return {
            "available": False, "zone_a": zone_a, "zone_b": zone_b,
            "reason": "No overlapping price hours for this border in the window.",
        }

    pa, pb = prices.get(zone_a, {}), prices.get(zone_b, {})
    series = [
        {
            "ts_utc": datetime.fromtimestamp(t, tz=timezone.utc).isoformat(),
            "price_a": round(pa[t], 2),
            "price_b": round(pb[t], 2),
            "spread": round(pa[t] - pb[t], 2),
            "flow_mw": round(flow_window[t], 1) if t in flow_window else None,
        }
        for t in sorted(set(pa) & set(pb))
    ]
    return {
        "available": True,
        "zone_a": zone_a, "zone_b": zone_b,
        "label": f"{POWER_ZONES[zone_a]['label']}↔{POWER_ZONES[zone_b]['label']}",
        "unit": "EUR/MWh",
        "days": days,
        "note": (
            f"spread = {POWER_ZONES[zone_a]['label']} − {POWER_ZONES[zone_b]['label']}; "
            f"flow > 0 = {POWER_ZONES[zone_a]['label']} exports. Descriptive."
        ),
        "data": series,
        **m,
    }
