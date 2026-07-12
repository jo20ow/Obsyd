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
    """Latest day where wind+solar cover an unusually small share of load, per zone."""
    zones = [z for (z,) in db.query(PowerGrid.zone).distinct().all()]
    results: list[DetectorResult] = []
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
        if share >= DUNKELFLAUTE_THRESHOLD:
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
                    f"Wind+solar only {share * 100:.1f}% of load on {row.date} (<15% threshold); "
                    f"residual load carried by conventional generation."
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
    """Forced MW offline RIGHT NOW for EVERY zone in one query — the bulk
    counterpart of forced_outage_mw_now for /api/power/overview."""
    from collections import defaultdict
    from datetime import datetime, timezone

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    totals: dict[str, float] = defaultdict(float)
    for r in _running_forced(latest_outage_revisions(db, ending_after=now_iso), now_iso):
        totals[r.zone] += r.nominal_mw - (r.available_mw or 0.0)
    return dict(totals)


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
