"""Day-ahead price convergence per border, in ACER's own bands — the desk's
first analytics product built on a regulator-canonical definition rather than
a house convention.

DEFINITION (ACER Market Monitoring Report 2024, "Progress of EU electricity
wholesale market integration", Figure 29):

    full price convergence      |Δprice| ≤ 1 EUR/MWh
    moderate price convergence  1 < |Δprice| < 10 EUR/MWh
    low price convergence       |Δprice| > 10 EUR/MWh

measured on hourly day-ahead prices and expressed as % of hours. ACER's legend
leaves exactly 10.00 unassigned; this desk counts it as moderate (documented
choice, see docs/findings/2026-09-13-convergence-index.md). Carry ACER's own
footnote wherever the number is shown: "Reaching full price convergence is not
an objective, as it would require overinvestment in network infrastructure" —
100% is NOT the target, and a convergence index published without that line
invites exactly that misreading.

WHAT IS STORED (Pattern C — the derived_stats promotion doctrine): one point
per UTC day per canonical border (sorted pair, the flow.<TO>-under-<FROM>
convention), four series under the border's first zone:

    conv.full.<TO>    hours that day with |Δ| ≤ 1        (count, unit h)
    conv.low.<TO>     hours that day with |Δ| > 10       (count, unit h)
    conv.hours.<TO>   hours that day priced on BOTH sides (count, unit h)
    conv.spread.<TO>  mean |Δ| over those hours          (EUR/MWh)

Counts, not shares, deliberately: counts aggregate EXACTLY over any window
(DST days have 23/25 hours; daily percentages would smuggle in a rounding
error), and moderate derives exactly as hours − full − low. Only overlapping
priced hours are counted — an hour with one side missing is absent, never an
invented 0-spread hour.

CAVEATS THE NUMBER MUST TRAVEL WITH (methodology research + prod validation
2026-09, full numbers in the finding doc):
- Switzerland is not in SDAC: CH borders clear uncoupled. Their ~5-9% of
  hours inside the full band is CHANCE PROXIMITY of two independent auctions,
  not coupling — the band alone cannot distinguish an uncoupled border from a
  congested coupled one (prod check: CH-DE_LU 8.6% full vs DE_LU-NL 8.4% in
  2024). The `sdac: false` flag carries that distinction, so CH rows are
  flagged and excluded from the headline aggregate.
- Exact price equality is NOT the coupling test either: flow-based Core
  borders cleared exactly equal in only ~0.1% of 2024 hours (any binding
  network element anywhere in the region splits all zone prices slightly) —
  which is precisely why ACER uses a ±1 band, and why this desk follows it.
- Since 2025-10 SDAC clears 15-minute MTUs; price.dayahead is the hourly MEAN
  of the four QH prices, so post-break band counts measure convergence of
  hourly means — a mild structural break, documented, not corrected away.
- Decoupling incidents (e.g. the 2024-06-25 SDAC partial decoupling) are NOT
  excluded — the published prices of those hours are real published prices.
- The EU-wide aggregate is a share of BORDER-HOURS across the covered borders,
  unweighted by capacity — our convention, stated, not ACER's per-CCR cut.
Everything here is descriptive arithmetic on published auction prices;
no model, no forecast (Posture B).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from backend.models.energy import PowerHourly, SeriesDim, ZoneDim
from backend.power.border_registry import SCHEDULED_BORDERS
from backend.power.hourly_store import upsert_hourly
from backend.power.zones import ZONE_REGISTRY

logger = logging.getLogger(__name__)

#: ACER MMR 2024 band edges (EUR/MWh). FULL is inclusive (≤), LOW exclusive (>).
FULL_EUR = 1.0
LOW_EUR = 10.0

#: Zones outside SDAC market coupling: their borders are expected NOT to
#: converge, by market design. Flagged in every response, excluded from the
#: headline aggregate. (GB never appears here — it has no priced border at all.)
NON_SDAC_ZONES = frozenset({"CH"})

ACER_NOTE = (
    "Bands per ACER MMR 2024: full ≤1, moderate 1–10, low >10 EUR/MWh on hourly "
    "day-ahead prices, as % of hours. ACER's own caveat applies: reaching full "
    "price convergence is not an objective — it would require overinvestment in "
    "network infrastructure. EU aggregate = share of border-hours across covered "
    "SDAC borders, unweighted. CH borders are outside SDAC (flagged, excluded "
    "from the aggregate). Descriptive arithmetic on published auction prices."
)

_METRICS = ("full", "low", "hours", "spread")


def band_counts(spreads: list[float]) -> tuple[int, int]:
    """(full, low) hour counts for a list of |Δprice| values. Pure."""
    full = sum(1 for s in spreads if s <= FULL_EUR)
    low = sum(1 for s in spreads if s > LOW_EUR)
    return full, low


def _price_rows(db: Session, zones: set[str], start_ts: int,
                end_ts: int | None = None) -> dict[str, dict[int, float]]:
    """Day-ahead prices for `zones` in [start_ts, end_ts) — borders.py's id-first
    read with an end bound (the backfill walks year chunks)."""
    sid = db.query(SeriesDim.id).filter(SeriesDim.key == "price.dayahead").scalar()
    if sid is None:
        return {}
    zone_ids = {
        zid: key
        for zid, key in db.query(ZoneDim.id, ZoneDim.key).filter(ZoneDim.key.in_(zones)).all()
    }
    if not zone_ids:
        return {}
    q = (
        db.query(PowerHourly.zone_id, PowerHourly.ts_utc, PowerHourly.value)
        .filter(PowerHourly.series_id == sid,
                PowerHourly.zone_id.in_(zone_ids),
                PowerHourly.ts_utc >= start_ts)
    )
    if end_ts is not None:
        q = q.filter(PowerHourly.ts_utc < end_ts)
    out: dict[str, dict[int, float]] = {}
    for zid, ts, value in q.all():
        out.setdefault(zone_ids[zid], {})[int(ts)] = float(value)
    return out


def store_convergence(db: Session, start_day: date, end_day: date) -> int:
    """Compute + upsert the four conv.* series for every canonical border over
    [start_day, end_day] (inclusive). Full-window recompute (the records
    doctrine): a restated price restates its day's counts on the next run.
    Returns points written."""
    start_ts = int(datetime(start_day.year, start_day.month, start_day.day,
                            tzinfo=timezone.utc).timestamp())
    end_ts = int(datetime(end_day.year, end_day.month, end_day.day,
                          tzinfo=timezone.utc).timestamp()) + 86400
    zones = {z for border in SCHEDULED_BORDERS for z in border}
    prices = _price_rows(db, zones, start_ts, end_ts)

    written = 0
    for zone_a, zone_b in SCHEDULED_BORDERS:
        pa, pb = prices.get(zone_a), prices.get(zone_b)
        if not pa or not pb:
            continue
        by_day: dict[int, list[float]] = {}
        for ts in pa.keys() & pb.keys():
            day_ts = ts - (ts % 86400)
            by_day.setdefault(day_ts, []).append(abs(pa[ts] - pb[ts]))
        if not by_day:
            continue
        points: dict[str, list[tuple[int, float]]] = {m: [] for m in _METRICS}
        for day_ts, spreads in sorted(by_day.items()):
            full, low = band_counts(spreads)
            points["full"].append((day_ts, float(full)))
            points["low"].append((day_ts, float(low)))
            points["hours"].append((day_ts, float(len(spreads))))
            points["spread"].append((day_ts, sum(spreads) / len(spreads)))
        for metric, pts in points.items():
            unit = "EUR/MWh" if metric == "spread" else "h"
            written += upsert_hourly(db, f"conv.{metric}.{zone_b}", zone_a, pts, unit=unit)
    return written


def _conv_dims(db: Session) -> tuple[dict[int, tuple[str, str]], dict[int, str]]:
    """(series_id → (metric, counterparty), zone_id → zone key). Id-first — the
    borders.py performance rule: a LIKE join over 28M rows scans the table."""
    series: dict[int, tuple[str, str]] = {}
    for sid, key in db.query(SeriesDim.id, SeriesDim.key).filter(
            SeriesDim.key.like("conv.%")).all():
        parts = key.split(".", 2)
        if len(parts) == 3 and parts[1] in _METRICS:
            series[sid] = (parts[1], parts[2])
    zones = dict(db.query(ZoneDim.id, ZoneDim.key).all())
    return series, zones


def compute_convergence(db: Session, days: int = 365, *, now: datetime | None = None) -> dict:
    """Aggregate the stored daily series into per-border band shares over a
    trailing window. Exact: shares come from summed counts, moderate derives
    as hours − full − low."""
    now = now or datetime.now(timezone.utc)
    start_ts = int((now - timedelta(days=days)).timestamp())
    series, zones = _conv_dims(db)
    if not series:
        return {"available": False,
                "reason": "No convergence series yet — the nightly derived-stats job builds them."}

    rows = (
        db.query(PowerHourly.series_id, PowerHourly.zone_id,
                 PowerHourly.ts_utc, PowerHourly.value)
        .filter(PowerHourly.series_id.in_(series), PowerHourly.ts_utc >= start_ts)
        .all()
    )
    # (zone_a, counterparty) → {day_ts: {metric: value}} — kept per-day so the
    # window's mean spread can be recovered EXACTLY (each day's mean weighted by
    # that day's hour count; a plain mean of daily means would drift on DST and
    # partial days).
    acc: dict[tuple[str, str], dict[int, dict[str, float]]] = {}
    newest = None
    for sid, zid, ts, value in rows:
        zone_a = zones.get(zid)
        if zone_a is None:
            continue
        metric, zone_b = series[sid]
        acc.setdefault((zone_a, zone_b), {}).setdefault(int(ts), {})[metric] = float(value)
        newest = int(ts) if newest is None else max(newest, int(ts))

    out = []
    agg = {"full": 0.0, "low": 0.0, "hours": 0.0}
    for (zone_a, zone_b), days_map in acc.items():
        if zone_a not in ZONE_REGISTRY or zone_b not in ZONE_REGISTRY:
            continue
        full = sum(d.get("full", 0.0) for d in days_map.values())
        low = sum(d.get("low", 0.0) for d in days_map.values())
        hours = sum(d.get("hours", 0.0) for d in days_map.values())
        if not hours:
            continue
        moderate = hours - full - low
        spread_x_hours = sum(
            d["spread"] * d["hours"]
            for d in days_map.values()
            if "spread" in d and "hours" in d
        )
        sdac = zone_a not in NON_SDAC_ZONES and zone_b not in NON_SDAC_ZONES
        if sdac:
            agg["full"] += full
            agg["low"] += low
            agg["hours"] += hours
        out.append({
            "zone_a": zone_a, "zone_b": zone_b,
            "label": f"{ZONE_REGISTRY[zone_a]['label']}↔{ZONE_REGISTRY[zone_b]['label']}",
            "sdac": sdac,
            "hours": int(hours),
            "full_pct": round(100.0 * full / hours, 1),
            "moderate_pct": round(100.0 * moderate / hours, 1),
            "low_pct": round(100.0 * low / hours, 1),
            "mean_abs_spread": round(spread_x_hours / hours, 2),
        })
    if not out:
        return {"available": False,
                "reason": "No convergence data in the window."}
    out.sort(key=lambda r: r["full_pct"])

    total_hours = agg["hours"]
    return {
        "available": True,
        "days": days,
        "unit": "EUR/MWh",
        "thresholds": {"full_eur": FULL_EUR, "low_eur": LOW_EUR},
        "overall_sdac": {
            "hours": int(total_hours),
            "full_pct": round(100.0 * agg["full"] / total_hours, 1) if total_hours else None,
            "moderate_pct": round(100.0 * (total_hours - agg["full"] - agg["low"]) / total_hours, 1)
            if total_hours else None,
            "low_pct": round(100.0 * agg["low"] / total_hours, 1) if total_hours else None,
        },
        "borders": out,
        "as_of": (datetime.fromtimestamp(newest, tz=timezone.utc).strftime("%Y-%m-%d")
                  if newest is not None else None),
        "note": ACER_NOTE,
    }
