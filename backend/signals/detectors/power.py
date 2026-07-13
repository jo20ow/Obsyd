"""Power vertical detector — negative day-ahead price hours per zone.

Negative prices flag renewable oversupply (the grid pays to offload power). The
hour count is persisted per (date, zone) in ``PowerPriceDaily.negative_hours``.
"""

from __future__ import annotations

from backend.models.energy import PowerGrid, PowerPriceDaily
from backend.power.coverage import renewable_share_reliable
from backend.power.entsoe_grid import PSR_LABELS
from backend.signals.detectors.base import DetectorResult, trailing_zscore

# Dunkelflaute = renewables carry an unusually small share of load (wind+solar < 15%),
# so conventional generation must cover the residual. A fixed physical threshold is
# meaningful here (it is a defined grid condition, not a structurally-always-true count).
DUNKELFLAUTE_THRESHOLD = 0.15

# Negative day-ahead hours happen routinely in solar/wind-heavy zones — so flag only when
# TODAY is unusually high vs the zone's own recent norm (relative), not on a flat hour count.
NP_WINDOW = 45           # trailing days of negative-hour history per zone
NP_MIN_HOURS = 3         # ignore one or two negative hours (normal noise)
NP_WARN_Z = 2.0
NP_CRIT_Z = 3.0


def detect_negative_prices(db) -> list[DetectorResult]:
    zones = [z for (z,) in db.query(PowerPriceDaily.zone).distinct().all()]
    results: list[DetectorResult] = []
    for zone in zones:
        rows = (
            db.query(PowerPriceDaily)
            .filter(PowerPriceDaily.zone == zone)
            .order_by(PowerPriceDaily.date.desc())
            .limit(NP_WINDOW + 1)
            .all()
        )
        if not rows:
            continue
        row = rows[0]
        current = row.negative_hours
        if current < NP_MIN_HOURS:
            continue
        baseline = [r.negative_hours for r in rows[1:]]
        stat = trailing_zscore(current, baseline)
        if stat is None:
            continue  # too little history → no trustworthy "unusual" judgement yet
        z, mean, _, n = stat
        if z < NP_WARN_Z:
            continue
        results.append(
            DetectorResult(
                rule="negative_prices",
                zone=zone,
                vertical="power",
                severity="critical" if z >= NP_CRIT_Z else "warning",
                title=f"{zone}: unusually many negative-price hours ({current}h, {z:+.1f}σ)",
                detail=(
                    f"{current}h below 0 EUR/MWh on {row.date} vs ~{mean:.0f}h normal for {zone} "
                    f"(z {z:+.2f}; renewable oversupply / inflexible generation)."
                ),
                as_of=row.date,
            )
        )
    return results


def detect_dunkelflaute(db) -> list[DetectorResult]:
    """A day when wind+solar deliver unusually little FOR THIS ZONE, IN THIS MONTH.

    The old test was a flat 15% of load, asked of all 37 zones. On prod that left the radar
    standing at 27 simultaneous Dunkelflaute alerts — 77% of its entire output — led by
    "NO5: Dunkelflaute — renewables 0% of load". NO5 is hydro: it has no wind and no solar, and
    that sentence describes its fleet, not an event. NO1, NO5 and SK were below the threshold on
    100% of all days in the record.

    Now: the zone must HAVE a wind/solar fleet worth the word, and today must be in the bottom
    2% of that zone's own same-month history AND below the absolute threshold. Calibrated on the
    full record: 1.41% of zone-days, ~0.4 alerts a day across Europe, and 36 Dunkelflaute days
    for DE-LU in 5.5 years — about six a winter, which is what a German Dunkelflaute is.
    See backend/power/dunkelflaute.py.
    """
    from backend.power.dunkelflaute import is_dunkelflaute, zone_thresholds

    zones = [z for (z,) in db.query(PowerGrid.zone).distinct().all()]
    results: list[DetectorResult] = []
    thresholds_by_month: dict[str, dict] = {}

    for zone in zones:
        row = (
            db.query(PowerGrid)
            .filter(PowerGrid.zone == zone)
            .order_by(PowerGrid.date.desc())
            .first()
        )
        if row is None or not row.load_mw or row.load_mw <= 0:
            continue
        share = ((row.wind_mw or 0.0) + (row.solar_mw or 0.0)) / row.load_mw

        month = row.date[5:7]
        if month not in thresholds_by_month:      # one query per month, not per zone
            thresholds_by_month[month] = zone_thresholds(db, month)
        threshold = thresholds_by_month[month].get(zone, {})
        if not is_dunkelflaute(share, threshold):
            continue

        # Coverage guard: only trust a low renewable share when the zone's reported
        # generation plausibly covers its load. ENTSO-E A75 is incomplete for some
        # zones (NL), which fakes a near-zero share — suppress rather than cry wolf.
        if not renewable_share_reliable(db, row.date, zone, row.load_mw):
            continue

        results.append(
            DetectorResult(
                rule="dunkelflaute",
                zone=zone,
                vertical="power",
                severity="warning",
                title=f"{zone}: Dunkelflaute — renewables {share * 100:.0f}% of load",
                detail=(
                    f"Wind+solar carried only {share * 100:.1f}% of load on {row.date} — the "
                    f"bottom 2% of {zone}'s own record for this month "
                    f"(n={threshold['n_month']}; a normal day is {threshold['median_share'] * 100:.0f}%). "
                    f"Residual load carried by conventional generation."
                ),
                as_of=row.date,
            )
        )
    return results


# Forced (unplanned) outages are the intraday price mover. Where installed
# capacity (A68) is known, thresholds are capacity-relative — the same 1 GW is
# noise in DE_LU (295 GW fleet) and an emergency in DK2 (5 GW). Verified
# 2026-07-12: A68 covers 19/37 zones (missing: Italian sub-zones, SK, CH, all
# Nordic sub-zones), so the absolute v1 thresholds stay as the fallback there.
# MW floors keep tiny zones from flagging a single mid-size unit trip.
FORCED_OUTAGE_WARN_SHARE = 0.03
FORCED_OUTAGE_CRIT_SHARE = 0.08
FORCED_OUTAGE_WARN_FLOOR_MW = 300.0
FORCED_OUTAGE_CRIT_FLOOR_MW = 500.0
FORCED_OUTAGE_WARN_MW = 1_000.0
FORCED_OUTAGE_CRIT_MW = 3_000.0


def installed_capacity_mw(db, zone: str) -> float | None:
    """Total installed generation capacity (MW) for `zone`, latest A68 year.
    None when the zone has no capacity data (18/37 zones as of 2026-07)."""
    from sqlalchemy import func

    from backend.models.energy import InstalledCapacity

    year = (
        db.query(func.max(InstalledCapacity.year))
        .filter(InstalledCapacity.zone == zone)
        .scalar()
    )
    if year is None:
        return None
    total = (
        db.query(func.sum(InstalledCapacity.capacity_mw))
        .filter(InstalledCapacity.zone == zone, InstalledCapacity.year == year)
        .scalar()
    )
    return float(total) if total else None


def published_unit_capacity_mw(db, zone: str) -> float | None:
    """Total nominal MW of the zone's PUBLISHED production units (ENTSO-E A71/A33).

    A SEPARATE metric from installed_capacity_mw, and deliberately NOT a fallback for it.
    Measured on prod:

        DE-LU   A71/A33:  133 units,  65,193 MW      FR   A71/A33:  174 units,  93,903 MW
                A68    :             294,941 MW           A68    :             163,611 MW
                                     ──────────                                ──────────
                                      factor 4.5                                factor 1.7

    And the ratio is not even CONSTANT (NL: 2.7), so no correction factor could turn one into
    the other.

    A71/A33 lists only units above ENTSO-E's ~100 MW publication threshold — a different
    population, not a smaller sample of the same one. Wiring it in behind installed_capacity_mw
    would fire the A68-calibrated 3%/8% thresholds against a denominator several times too
    small, and the 19 A68 zones and the 18 A71 zones would then be measuring different
    populations under one threshold. That is exactly the cross-zone incomparability
    outage_history.py forbids in its own docstring.

    What it IS: the same population the A77 outages are drawn from. So "X GW of the zone's Y GW
    of published >=100 MW units is offline" is an honest sentence — with its own label — and it
    exists for all 37 zones, including the 18 that have no A68 at all.
    """
    from sqlalchemy import func

    from backend.models.energy import ProductionUnit

    year = (
        db.query(func.max(ProductionUnit.year))
        .filter(ProductionUnit.zone == zone)
        .scalar()
    )
    if year is None:
        return None
    total = (
        db.query(func.sum(ProductionUnit.nominal_mw))
        .filter(ProductionUnit.zone == zone, ProductionUnit.year == year)
        .scalar()
    )
    return float(total) if total else None


def forced_outage_severity(total_mw: float, installed_mw: float | None) -> str | None:
    """None / "warning" / "critical" for `total_mw` forced offline. Pure — shared
    by the radar detector and the situation-hero flag so they cannot drift."""
    if installed_mw:
        share = total_mw / installed_mw
        if share >= FORCED_OUTAGE_CRIT_SHARE and total_mw >= FORCED_OUTAGE_CRIT_FLOOR_MW:
            return "critical"
        if share >= FORCED_OUTAGE_WARN_SHARE and total_mw >= FORCED_OUTAGE_WARN_FLOOR_MW:
            return "warning"
        return None
    if total_mw >= FORCED_OUTAGE_CRIT_MW:
        return "critical"
    if total_mw >= FORCED_OUTAGE_WARN_MW:
        return "warning"
    return None


def latest_outage_revisions(
    db, zone: str | None = None, *, ending_after: str | None = None
) -> list:
    """Highest revision per (zone, mRID), resolved in SQL via a window function.

    Withdrawn rows are still RETURNED — ranking must happen before any status
    filter, because a withdrawn latest revision has to hide the event (filtering
    first would let an older active revision win and fabricate gigawatts; 26 of
    31 live-sampled documents were withdrawn revisions). Replaces loading every
    revision row into Python, which grew with the full revision history.

    `ending_after` prunes the ranked set to mRIDs with ANY revision ending at or
    after the cutoff — an event whose every revision ended in the past cannot be
    running/upcoming no matter which revision wins. The filter is per-mRID, not
    per-row: dropping single rows on end_utc would let an older longer revision
    beat a newer shortened one.
    """
    from sqlalchemy import func

    from backend.models.energy import PowerOutage

    rn = (
        func.row_number()
        .over(
            partition_by=[PowerOutage.zone, PowerOutage.mrid],
            order_by=PowerOutage.revision.desc(),
        )
        .label("rn")
    )
    ranked = db.query(PowerOutage.id.label("oid"), rn)
    if zone is not None:
        ranked = ranked.filter(PowerOutage.zone == zone)
    if ending_after is not None:
        relevant = db.query(PowerOutage.mrid).filter(PowerOutage.end_utc >= ending_after)
        if zone is not None:
            relevant = relevant.filter(PowerOutage.zone == zone)
        ranked = ranked.filter(PowerOutage.mrid.in_(relevant.subquery().select()))
    ranked = ranked.subquery()
    return (
        db.query(PowerOutage)
        .join(ranked, PowerOutage.id == ranked.c.oid)
        .filter(ranked.c.rn == 1)
        .all()
    )


def _running_forced(rows, now_iso: str) -> list:
    return [
        r for r in rows
        if r.status == "active"
        and r.business_type == "A54"
        and r.nominal_mw is not None
        and r.start_utc <= now_iso <= r.end_utc
    ]


def forced_outage_mw_now(db, zone: str) -> tuple[float, list]:
    """(total forced MW offline RIGHT NOW, contributing rows) for `zone`.

    Highest revision per mRID wins, withdrawn events vanish — the same
    semantics as /api/power/outages (shared latest_outage_revisions helper).
    """
    from datetime import datetime, timezone

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    rows = latest_outage_revisions(db, zone, ending_after=now_iso)
    running = _running_forced(rows, now_iso)
    total = sum(r.nominal_mw - (r.available_mw or 0.0) for r in running)
    return total, running


def forced_outage_totals_now(db) -> dict[str, float]:
    """Forced MW offline RIGHT NOW for EVERY zone in one aggregate query — the
    bulk counterpart of forced_outage_mw_now for /api/power/overview.

    Fully SQL-side: returning the latest-revision ROWS hydrated ~8.5k ORM
    entities per request (0.25s on prod — most A77 events end far in the
    future, so the ending_after prune barely bites). The overview only needs
    one SUM per zone; _running_forced's filters move into the outer query,
    which is exactly why ranking (all revisions) and filtering (rn=1 only)
    stay separate stages.
    """
    from datetime import datetime, timezone

    from sqlalchemy import func

    from backend.models.energy import PowerOutage

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    rn = (
        func.row_number()
        .over(
            partition_by=[PowerOutage.zone, PowerOutage.mrid],
            order_by=PowerOutage.revision.desc(),
        )
        .label("rn")
    )
    relevant = db.query(PowerOutage.mrid).filter(PowerOutage.end_utc >= now_iso)
    ranked = (
        db.query(PowerOutage.id.label("oid"), rn)
        .filter(PowerOutage.mrid.in_(relevant.subquery().select()))
        .subquery()
    )
    offline = func.sum(PowerOutage.nominal_mw - func.coalesce(PowerOutage.available_mw, 0.0))
    rows = (
        db.query(PowerOutage.zone, offline)
        .join(ranked, PowerOutage.id == ranked.c.oid)
        .filter(
            ranked.c.rn == 1,
            PowerOutage.status == "active",
            PowerOutage.business_type == "A54",
            PowerOutage.nominal_mw.isnot(None),
            PowerOutage.start_utc <= now_iso,
            PowerOutage.end_utc >= now_iso,
        )
        .group_by(PowerOutage.zone)
        .all()
    )
    return {zone: float(total) for zone, total in rows if total}


def detect_forced_outages(db) -> list[DetectorResult]:
    """Zones where forced (unplanned) generation loss is large right now."""
    from datetime import datetime, timezone

    from backend.models.energy import PowerOutage

    zones = [z for (z,) in db.query(PowerOutage.zone).distinct().all()]
    results: list[DetectorResult] = []
    for zone in zones:
        total, running = forced_outage_mw_now(db, zone)
        installed = installed_capacity_mw(db, zone)
        severity = forced_outage_severity(total, installed)
        if severity is None:
            continue
        share_txt = f" — {total / installed * 100:.0f}% of fleet" if installed else ""
        biggest = max(running, key=lambda r: r.nominal_mw - (r.available_mw or 0.0))
        biggest_mw = biggest.nominal_mw - (biggest.available_mw or 0.0)
        results.append(
            DetectorResult(
                rule="forced_outages",
                zone=zone,
                vertical="power",
                severity=severity,
                title=f"{zone}: {total / 1000:.1f} GW forced outages{share_txt}",
                detail=(
                    f"{len(running)} unplanned unavailabilities running now; largest: "
                    f"{biggest.unit_name or biggest.mrid} ({biggest_mw:.0f} MW, "
                    f"{PSR_LABELS.get(biggest.psr_type, biggest.psr_type)}, until {biggest.end_utc[:10]})."
                ),
                # The running state is assessed against wall-clock, so it is
                # current by construction; a stalled collector is the
                # watchdog's job (power_outages freshness spec), not this one.
                as_of=datetime.now(timezone.utc).date().isoformat(),
            )
        )
    return results


# ── Imbalance extremes — the intraday stress signal ──────────────────────────
# Imbalance (reBAP for DE_LU) is where grid stress prices first: ±1000 €/MWh
# quarter-hours vanish in day-ahead means. Daily PEAK |price| per zone is
# compared against the zone's own trailing norm; absolute floors keep quiet,
# low-variance zones from flagging noise-level "extremes".
IMB_WINDOW_DAYS = 45
IMB_WARN_Z = 2.5
IMB_CRIT_Z = 3.5
IMB_WARN_FLOOR_EUR = 300.0
IMB_CRIT_FLOOR_EUR = 500.0


def _imbalance_zones(db) -> list[str]:
    from backend.models.energy import PowerHourly, SeriesDim, ZoneDim

    sid = db.query(SeriesDim.id).filter(SeriesDim.key == "imbalance.price").scalar()
    if sid is None:
        return []
    rows = (
        db.query(ZoneDim.key)
        .join(PowerHourly, PowerHourly.zone_id == ZoneDim.id)
        .filter(PowerHourly.series_id == sid)
        .distinct()
        .all()
    )
    return [z for (z,) in rows]


def detect_imbalance_extremes(db) -> list[DetectorResult]:
    """Zones whose latest daily peak imbalance price is extreme vs their own norm."""
    from datetime import datetime, timedelta, timezone

    from backend.power.hourly_store import read_hourly

    start_ts = int(
        (datetime.now(timezone.utc) - timedelta(days=IMB_WINDOW_DAYS + 1)).timestamp()
    )
    results: list[DetectorResult] = []
    for zone in _imbalance_zones(db):
        points = read_hourly(db, "imbalance.price", zone, start_ts=start_ts)
        if not points:
            continue
        # Daily peak: the point with the largest magnitude, kept SIGNED for display.
        daily: dict[str, float] = {}
        for ts, v in points:
            day = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
            if day not in daily or abs(v) > abs(daily[day]):
                daily[day] = v
        days = sorted(daily)
        if len(days) < 2:
            continue
        current = daily[days[-1]]
        baseline = [abs(daily[d]) for d in days[:-1]]
        stat = trailing_zscore(abs(current), baseline)
        if stat is None:
            continue
        z, mean, _, _n = stat
        if z < IMB_WARN_Z or abs(current) < IMB_WARN_FLOOR_EUR:
            continue
        severity = (
            "critical" if z >= IMB_CRIT_Z and abs(current) >= IMB_CRIT_FLOOR_EUR else "warning"
        )
        results.append(
            DetectorResult(
                rule="imbalance_extreme",
                zone=zone,
                vertical="power",
                severity=severity,
                title=f"{zone}: imbalance peaked at {current:.0f} €/MWh ({z:+.1f}σ)",
                detail=(
                    f"Peak imbalance price {current:.0f} €/MWh on {days[-1]} vs ~{mean:.0f} "
                    f"normal daily peak (z {z:+.2f}). Imbalance is what being out of "
                    f"balance actually costs — the intraday stress gauge."
                ),
                as_of=days[-1],
                # Imbalance settles late (reBAP confirms ~2 weeks after delivery for
                # DE_LU); mirrors the imbalance_qh freshness window, not "power": 3.
                max_age_days=4,
            )
        )
    return results


# ── Day-ahead price spikes (both tails) ───────────────────────────────────────
# Relative-only z would fire on micro-variance zones; the €/MWh delta floor
# keeps "3σ above a flat 60±2 €" from paging anyone. Named price_spike — a
# user-rule template `dayahead_spike` (absolute threshold breach) already
# exists in the OTHER alert subsystem; distinct names keep the two apart.
SPIKE_WINDOW_DAYS = 45
SPIKE_WARN_Z = 2.5
SPIKE_CRIT_Z = 3.5
SPIKE_MIN_DELTA_EUR = 25.0


def detect_price_spikes(db) -> list[DetectorResult]:
    """Zones whose latest day-ahead daily mean sits far outside their own norm."""
    zones = [z for (z,) in db.query(PowerPriceDaily.zone).distinct().all()]
    results: list[DetectorResult] = []
    for zone in zones:
        rows = (
            db.query(PowerPriceDaily)
            .filter(PowerPriceDaily.zone == zone)
            .order_by(PowerPriceDaily.date.desc())
            .limit(SPIKE_WINDOW_DAYS + 1)
            .all()
        )
        if not rows:
            continue
        current = rows[0].mean_price
        if current is None:
            continue
        baseline = [r.mean_price for r in rows[1:] if r.mean_price is not None]
        stat = trailing_zscore(current, baseline)
        if stat is None:
            continue
        z, mean, _, _n = stat
        if abs(z) < SPIKE_WARN_Z or abs(current - mean) < SPIKE_MIN_DELTA_EUR:
            continue
        direction = "high" if z > 0 else "low"
        results.append(
            DetectorResult(
                rule="price_spike",
                zone=zone,
                vertical="power",
                severity="critical" if abs(z) >= SPIKE_CRIT_Z else "warning",
                title=f"{zone}: day-ahead unusually {direction} — {current:.0f} €/MWh ({z:+.1f}σ)",
                detail=(
                    f"Daily mean {current:.0f} €/MWh on {rows[0].date} vs ~{mean:.0f} €/MWh "
                    f"45d norm (z {z:+.2f}). Descriptive deviation vs the zone's own "
                    f"history, not a forecast."
                ),
                as_of=rows[0].date,
            )
        )
    return results


# ── Hydro reservoirs outside the seasonal band ────────────────────────────────


def detect_hydro_deviations(db) -> list[DetectorResult]:
    """Hydro zones whose reservoir filling left the same-ISO-week band of prior
    years. Weekly A72 data with ~2 weeks publication lag → own staleness window."""
    from datetime import datetime, timezone

    from backend.power.entsoe_hydro import HYDRO_ZONES, same_week_band
    from backend.power.hourly_store import read_hourly
    from backend.routes.power import HYDRO_STALE_DAYS

    results: list[DetectorResult] = []
    for zone in HYDRO_ZONES:
        points = read_hourly(db, "hydro.reservoir", zone)
        if not points:
            continue
        band = same_week_band(points)
        # band_n < 3 → the "band" is one or two old points, not a norm. Silence
        # beats a confident-sounding comparison against nothing.
        if band["band_n"] < 3 or band["vs_band"] not in ("below", "above"):
            continue
        ts, mwh = points[-1]
        as_of = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        twh = mwh / 1e6
        results.append(
            DetectorResult(
                rule="hydro_deviation",
                zone=zone,
                vertical="power",
                severity="warning",
                title=f"{zone}: reservoirs {band['vs_band']} the seasonal band ({twh:.1f} TWh)",
                detail=(
                    f"Filling {twh:.2f} TWh vs {band['band_min_twh']}–{band['band_max_twh']} TWh "
                    f"in the same ISO week across {band['band_n']} prior years. Hydro is "
                    f"stored energy — a level outside its own seasonal range moves the "
                    f"whole price stack."
                ),
                as_of=as_of,
                max_age_days=HYDRO_STALE_DAYS,
            )
        )
    return results


# ── Fresh all-time records ────────────────────────────────────────────────────

RECORD_SERIES_LABELS = {
    "price.dayahead": "day-ahead hour",
    "price.dayahead.qh": "day-ahead quarter-hour",
    "imbalance.price.qh": "imbalance quarter-hour",
    "load.actual": "load hour",
    "residual.actual": "residual-load hour",
}

#: A "record" over three months of history is a statement about our coverage,
#: not about the grid. Require at least a year of series history in the zone.
RECORD_MIN_COVERAGE_DAYS = 365


def detect_record_breaks(db) -> list[DetectorResult]:
    """One info ping per zone with all-time records set in the last 7 days."""
    from collections import defaultdict
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import func as _func

    from backend.models.energy import PowerHourly, PowerRecord, SeriesDim, ZoneDim
    from backend.routes.power import RECORD_FRESH_DAYS

    fresh_cutoff = int(
        (datetime.now(timezone.utc) - timedelta(days=RECORD_FRESH_DAYS)).timestamp()
    )
    rows = db.query(PowerRecord).filter(PowerRecord.ts_utc >= fresh_cutoff).all()
    if not rows:
        return []

    def _coverage_days(series_key: str, zone: str) -> float:
        sid = db.query(SeriesDim.id).filter(SeriesDim.key == series_key).scalar()
        zid = db.query(ZoneDim.id).filter(ZoneDim.key == zone).scalar()
        if sid is None or zid is None:
            return 0.0
        first = (
            db.query(_func.min(PowerHourly.ts_utc))
            .filter(PowerHourly.series_id == sid, PowerHourly.zone_id == zid)
            .scalar()
        )
        if first is None:
            return 0.0
        return (datetime.now(timezone.utc).timestamp() - first) / 86_400

    by_zone: dict[str, list[PowerRecord]] = defaultdict(list)
    for r in rows:
        if _coverage_days(r.series_key, r.zone) >= RECORD_MIN_COVERAGE_DAYS:
            by_zone[r.zone].append(r)

    results: list[DetectorResult] = []
    for zone, recs in by_zone.items():
        recs.sort(key=lambda r: r.ts_utc, reverse=True)
        newest = datetime.fromtimestamp(recs[0].ts_utc, tz=timezone.utc).strftime("%Y-%m-%d")
        parts = [
            f"{'highest' if r.kind == 'max' else 'lowest'} "
            f"{RECORD_SERIES_LABELS.get(r.series_key, r.series_key)} "
            f"{r.value:.0f} {r.unit or ''} on "
            f"{datetime.fromtimestamp(r.ts_utc, tz=timezone.utc):%Y-%m-%d}"
            for r in recs
        ]
        results.append(
            DetectorResult(
                rule="record_break",
                zone=zone,
                vertical="power",
                severity="info",
                title=f"{zone}: new all-time record this week"
                      + (f" (+{len(recs) - 1} more)" if len(recs) > 1 else ""),
                detail="; ".join(parts) + ". All-time within our coverage (≥1 year).",
                as_of=newest,
            )
        )
    return results


#: An active episode is only worth interrupting an analyst for if it is genuinely unusual for
#: the zone. Top 3 by duration in its own record — a sentence the radar could not say before,
#: because it only ever saw today.
EPISODE_RANK_TOP_N = 3

#: Ranking against a thin record is a statement about our coverage, not about the grid. The same
#: argument, and the same number, as records.py::RECORD_MIN_COVERAGE_DAYS.
EPISODE_MIN_HISTORY_DAYS = 365

_EPISODE_LABELS = {
    "dunkelflaute": "Dunkelflaute",
    "negative_prices": "negative-price run",
    "price_spike": "price spike",
}


def detect_episode_rank(db) -> list[DetectorResult]:
    """An episode that is RUNNING and ranks in the top N of its zone's own record.

    The payoff of the episode archive, and the sentence the radar has never been able to say:

        "DE-LU: Dunkelflaute running 3 days — 2nd-longest in our 5-year record
         (longest: 4 days, 2021-04-29)."

    Past tense, the zone's own history, a sample size, and no claim about tomorrow. "Running"
    means the episode reaches the newest day we hold — not that it will continue.
    """
    from backend.models.energy import PowerEpisode
    from backend.power.episodes import KINDS, zone_episodes

    zones = [z for (z,) in db.query(PowerEpisode.zone).distinct().all()]
    results: list[DetectorResult] = []

    for zone in zones:
        for kind in KINDS:
            data = zone_episodes(db, zone, kind)
            active = data.get("active")
            rank = data.get("rank") or {}
            if not active or rank.get("position") is None:
                continue
            if data.get("history_days", 0) < EPISODE_MIN_HISTORY_DAYS:
                continue
            if rank["position"] > EPISODE_RANK_TOP_N:
                continue

            label = _EPISODE_LABELS.get(kind, kind)
            years = data["history_days"] // 365
            results.append(
                DetectorResult(
                    rule="episode_rank",
                    zone=zone,
                    vertical="power",
                    severity="warning" if rank["position"] == 1 else "info",
                    title=(
                        f"{zone}: {label} running {active['duration_days']} days — "
                        f"{_ordinal(rank['position'])}-longest on record"
                    ),
                    detail=(
                        f"{active['start_date']} → {active['end_date']}. "
                        f"{_ordinal(rank['position'])}-longest of {rank['of']} "
                        f"{label}s in {years} years of {zone} data "
                        f"(longest: {rank['longest_days']} days, from {rank['longest_start']})."
                    ),
                    as_of=active["end_date"],
                )
            )
    return results


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:          # 11th, 12th, 13th — not 11st
        return f"{n}th"
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"
