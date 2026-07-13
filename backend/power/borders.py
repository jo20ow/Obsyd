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


def _flow_rows(db: Session, start_ts: int) -> dict[tuple[str, str], dict[int, float]]:
    """Every hourly cross-border flow since `start_ts`, keyed by canonical border.

    One query for all borders — the per-border alternative was 26 round trips.
    """
    rows = (
        db.query(SeriesDim.key, ZoneDim.key, PowerHourly.ts_utc, PowerHourly.value)
        .join(PowerHourly, PowerHourly.series_id == SeriesDim.id)
        .join(ZoneDim, ZoneDim.id == PowerHourly.zone_id)
        .filter(SeriesDim.key.like("flow.%"), PowerHourly.ts_utc >= start_ts)
        .all()
    )
    out: dict[tuple[str, str], dict[int, float]] = {}
    for series_key, from_zone, ts, value in rows:
        to_zone = series_key[len("flow."):]
        out.setdefault((from_zone, to_zone), {})[int(ts)] = float(value)
    return out


def _price_rows(db: Session, zones: set[str], start_ts: int) -> dict[str, dict[int, float]]:
    rows = (
        db.query(ZoneDim.key, PowerHourly.ts_utc, PowerHourly.value)
        .join(PowerHourly, PowerHourly.zone_id == ZoneDim.id)
        .join(SeriesDim, SeriesDim.id == PowerHourly.series_id)
        .filter(SeriesDim.key == PRICE_SERIES,
                ZoneDim.key.in_(zones),
                PowerHourly.ts_utc >= start_ts)
        .all()
    )
    out: dict[str, dict[int, float]] = {}
    for zone, ts, value in rows:
        out.setdefault(zone, {})[int(ts)] = float(value)
    return out


def compute_borders(db: Session, days: int = 30, *, now: datetime | None = None) -> dict:
    """Every border with a price on both sides, ranked by how far apart it clears."""
    now = now or datetime.now(timezone.utc)
    window_start = int((now - timedelta(days=days)).timestamp())
    rail_start = int((now - timedelta(days=RAIL_BASELINE_DAYS)).timestamp())

    all_flows = _flow_rows(db, rail_start)
    if not all_flows:
        return {"available": False,
                "reason": "No cross-border flow series yet — check back shortly."}

    priced = set(POWER_ZONES)
    joinable = {b for b in all_flows if b[0] in priced and b[1] in priced}
    uncoverable = sorted(
        f"{a}-{b}" for a, b in all_flows if (a, b) not in joinable
    )

    prices = _price_rows(db, {z for b in joinable for z in b}, window_start)

    out = []
    for a, b in sorted(joinable):
        flow_full = all_flows[(a, b)]
        rail = percentile([abs(v) for v in flow_full.values()], RAIL_PERCENTILE)
        flow_window = {t: v for t, v in flow_full.items() if t >= window_start}
        m = border_metrics(
            prices.get(a, {}), prices.get(b, {}), flow_window, rail
        )
        if not m["hours"]:
            continue
        out.append({
            "zone_a": a, "zone_b": b,
            "label": f"{POWER_ZONES[a]['label']}↔{POWER_ZONES[b]['label']}",
            "expensive_side": (
                None if m["latest_spread"] is None or abs(m["latest_spread"]) < COUPLED_EPS_EUR
                else (a if m["latest_spread"] > 0 else b)
            ),
            **m,
        })

    out.sort(key=lambda r: -(r["mean_abs_spread"] or 0))
    return {
        "available": bool(out),
        "days": days,
        "unit": "EUR/MWh",
        "coupled_eps_eur": COUPLED_EPS_EUR,
        "rail_percentile": RAIL_PERCENTILE,
        "rail_baseline_days": RAIL_BASELINE_DAYS,
        "borders": out,
        "uncoverable_borders": uncoverable,
        "note": (
            "Convergence = share of hours the two zones cleared within "
            f"{COUPLED_EPS_EUR} EUR/MWh. 'At the rail' = physical flow at or above this "
            "border's own 95th percentile over the last year (we hold no NTC, and "
            "flow-based regions publish none). Counter-price = power physically ran "
            "from the expensive zone to the cheap one. Descriptive statistics on "
            "published records — a spread is not a claim that this interconnector was "
            "the binding constraint."
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

    flows = _flow_rows(db, rail_start)
    flow_full = flows.get((zone_a, zone_b))
    if flow_full is None:
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
    rail = percentile([abs(v) for v in flow_full.values()], RAIL_PERCENTILE)
    flow_window = {t: v for t, v in flow_full.items() if t >= window_start}
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
