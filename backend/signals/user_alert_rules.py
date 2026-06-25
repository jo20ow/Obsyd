"""
Rule-template evaluators for user-defined alerts (W4 feature).

Each evaluator is a pure(ish) function:
    evaluator(db: Session, params: dict, *, now: datetime) -> EvaluatorResult | None

Returns None if the rule did not trigger this run. The Evaluator is
intentionally read-only against the DB; persistence of the matched
event is handled by the caller (the scheduler). All evaluators must
fail-soft: any internal exception is logged + returns None, never
crashes the scheduler loop.

Schema for `params` is documented in each TEMPLATE entry; the route
layer validates input against TEMPLATES before persisting an AlertRule.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.models.energy import PowerPriceDaily
from backend.models.gas import GasBalance
from backend.models.vessels import FloatingStorageEvent, GeofenceEvent
from backend.power.zones import POWER_ZONES
from backend.signals.detectors.base import trailing_zscore
from backend.signals.detectors.power import NP_CRIT_Z, NP_MIN_HOURS, NP_WARN_Z, NP_WINDOW

logger = logging.getLogger(__name__)


@dataclass
class EvaluatorResult:
    """A successful trigger — payload is JSON-serialisable."""

    title: str
    detail: str
    payload: dict[str, Any]


# Canonical zone keys (mirrors backend/geofences/zones.py)
KNOWN_ZONES = {"hormuz", "suez", "malacca", "panama", "cape", "houston"}


# ---------------------------------------------------------------------------
# 1) chokepoint_anomaly
#    params: {"zone": "hormuz", "threshold_pct": 15.0, "direction": "above"|"below"|"either"}
#    Triggers when today's GeofenceEvent.tanker_count deviates ≥ threshold_pct
#    from the trailing-30d average for that zone.
# ---------------------------------------------------------------------------
def evaluate_chokepoint_anomaly(
    db: Session,
    params: dict,
    *,
    now: datetime,
) -> EvaluatorResult | None:
    zone = params.get("zone")
    threshold_pct = float(params.get("threshold_pct", 15))
    direction = params.get("direction", "either")
    if zone not in KNOWN_ZONES:
        return None
    if direction not in ("above", "below", "either"):
        return None

    latest = (
        db.query(GeofenceEvent)
        .filter(GeofenceEvent.zone == zone)
        .order_by(GeofenceEvent.date.desc())
        .first()
    )
    if not latest:
        return None

    baseline_cutoff = (now - timedelta(days=30)).strftime("%Y-%m-%d")
    baseline_rows = (
        db.query(GeofenceEvent)
        .filter(
            GeofenceEvent.zone == zone,
            GeofenceEvent.date >= baseline_cutoff,
            GeofenceEvent.date < latest.date,
        )
        .all()
    )
    if len(baseline_rows) < 5:
        # Not enough history to be statistically meaningful.
        return None

    counts = [r.tanker_count for r in baseline_rows if r.tanker_count is not None]
    if not counts:
        return None
    baseline_avg = sum(counts) / len(counts)
    if baseline_avg <= 0:
        return None

    diff_pct = (latest.tanker_count - baseline_avg) / baseline_avg * 100.0
    if abs(diff_pct) < threshold_pct:
        return None
    if direction == "above" and diff_pct < 0:
        return None
    if direction == "below" and diff_pct > 0:
        return None

    arrow = "↑" if diff_pct >= 0 else "↓"
    title = f"{zone.upper()} transit {arrow}{abs(diff_pct):.0f}%"
    detail = (
        f"{latest.tanker_count} tankers in {zone} ({latest.date}) — "
        f"30d avg {baseline_avg:.1f}, deviation {diff_pct:+.1f}%."
    )
    return EvaluatorResult(
        title=title,
        detail=detail,
        payload={
            "zone": zone,
            "today_count": latest.tanker_count,
            "baseline_30d": round(baseline_avg, 2),
            "deviation_pct": round(diff_pct, 2),
            "date": latest.date,
        },
    )


# ---------------------------------------------------------------------------
# 2) floating_storage_surge
#    params: {"zone": "hormuz"|"*", "min_vessels": 3, "window_days": 7}
#    Triggers when ≥min_vessels FloatingStorageEvents (status="active") are
#    present in the zone within the last window_days. Use zone "*" for any.
# ---------------------------------------------------------------------------
def evaluate_floating_storage_surge(
    db: Session,
    params: dict,
    *,
    now: datetime,
) -> EvaluatorResult | None:
    zone = params.get("zone", "*")
    min_vessels = int(params.get("min_vessels", 3))
    window_days = int(params.get("window_days", 7))
    if min_vessels < 1 or window_days < 1:
        return None
    if zone != "*" and zone not in KNOWN_ZONES:
        return None

    cutoff = now - timedelta(days=window_days)
    q = (
        db.query(func.count(FloatingStorageEvent.id))
        .filter(
            FloatingStorageEvent.status == "active",
            FloatingStorageEvent.last_seen >= cutoff,
        )
    )
    if zone != "*":
        q = q.filter(FloatingStorageEvent.zone == zone)
    count = q.scalar() or 0
    if count < min_vessels:
        return None

    zone_label = zone.upper() if zone != "*" else "GLOBAL"
    title = f"{zone_label} floating storage: {count} active"
    detail = (
        f"{count} tankers held in floating storage in {zone_label} "
        f"over the last {window_days} days (threshold {min_vessels})."
    )
    return EvaluatorResult(
        title=title,
        detail=detail,
        payload={
            "zone": zone,
            "count": count,
            "min_vessels": min_vessels,
            "window_days": window_days,
        },
    )


# ---------------------------------------------------------------------------
# 3) crack_spread_breach
#    params: {"direction": "above"|"below", "threshold_usd": 22.0}
#    Triggers when current 3:2:1 crack spread crosses threshold in
#    the chosen direction. Snapshot from CrackSpreadHistory (latest row).
# ---------------------------------------------------------------------------
def evaluate_crack_spread_breach(
    db: Session,
    params: dict,
    *,
    now: datetime,
) -> EvaluatorResult | None:
    direction = params.get("direction")
    threshold = params.get("threshold_usd")
    if direction not in ("above", "below"):
        return None
    try:
        threshold = float(threshold)
    except (TypeError, ValueError):
        return None

    from backend.models.pro_features import CrackSpreadHistory

    latest = (
        db.query(CrackSpreadHistory)
        .order_by(CrackSpreadHistory.date.desc())
        .first()
    )
    if not latest or latest.three_two_one_crack is None:
        return None
    spread = float(latest.three_two_one_crack)

    if direction == "above" and spread < threshold:
        return None
    if direction == "below" and spread > threshold:
        return None

    cmp = ">" if direction == "above" else "<"
    title = f"Crack spread {cmp} ${threshold:.2f}: now ${spread:.2f}"
    detail = (
        f"3:2:1 crack spread is ${spread:.2f}/bbl "
        f"({cmp} your ${threshold:.2f} threshold) on {latest.date}."
    )
    return EvaluatorResult(
        title=title,
        detail=detail,
        payload={
            "spread_321": spread,
            "threshold_usd": threshold,
            "direction": direction,
            "date": latest.date,
        },
    )


# ---------------------------------------------------------------------------
# 4) negative_prices  (power vertical)
#    params: {"zone": "DE_LU"|"FR"|"NL"}
#    Triggers when a zone's latest negative day-ahead price-hour count is
#    unusually high vs its own trailing norm. Ports detectors/power.py
#    (detect_negative_prices) to the per-user single-zone evaluator shape,
#    reusing the same window/threshold constants (kept in one place).
# ---------------------------------------------------------------------------
def evaluate_negative_prices(
    db: Session,
    params: dict,
    *,
    now: datetime,
) -> EvaluatorResult | None:
    zone = params.get("zone")
    if zone not in POWER_ZONES:
        return None

    rows = (
        db.query(PowerPriceDaily)
        .filter(PowerPriceDaily.zone == zone)
        .order_by(PowerPriceDaily.date.desc())
        .limit(NP_WINDOW + 1)
        .all()
    )
    if not rows:
        return None
    row = rows[0]
    current = row.negative_hours
    if current is None or current < NP_MIN_HOURS:
        return None
    baseline = [r.negative_hours for r in rows[1:] if r.negative_hours is not None]
    stat = trailing_zscore(current, baseline)
    if stat is None:
        return None  # too little history → no trustworthy "unusual" judgement
    z, mean, _, _n = stat
    if z < NP_WARN_Z:
        return None

    sev = "critical" if z >= NP_CRIT_Z else "warning"
    return EvaluatorResult(
        title=f"{zone}: unusually many negative-price hours ({current}h, {z:+.1f}σ)",
        detail=(
            f"{current}h below 0 EUR/MWh on {row.date} vs ~{mean:.0f}h normal for {zone} "
            f"(z {z:+.2f}; renewable oversupply / inflexible generation)."
        ),
        payload={
            "zone": zone,
            "negative_hours": current,
            "zscore": round(z, 2),
            "baseline_mean": round(mean, 2),
            "severity": sev,
            "date": row.date,
        },
    )


# ---------------------------------------------------------------------------
# 5) gas_balance  (gas vertical, EU-wide — no params)
#    Triggers when the latest EU gas-balance residual carries a WATCH/SIGNAL
#    flag. Ports detectors/gas.py (detect_gas_balance) — surfaces the level +
#    dominant-mover that backend/gas/balance.py already persisted.
# ---------------------------------------------------------------------------
_GAS_FLAG_LEVELS = {"SIGNAL", "WATCH"}


def evaluate_gas_balance(
    db: Session,
    params: dict,
    *,
    now: datetime,
) -> EvaluatorResult | None:
    row = db.query(GasBalance).order_by(GasBalance.date.desc()).first()
    if row is None or not row.flag:
        return None
    level, _, mover = row.flag.partition(":")
    if level not in _GAS_FLAG_LEVELS:
        return None

    z = row.z_score if row.z_score is not None else 0.0
    resid = row.residual_7d if row.residual_7d is not None else 0.0
    mover_txt = f", dominant mover {mover}" if mover else ""
    return EvaluatorResult(
        title=f"EU gas balance {level.lower()}: residual {z:+.1f}σ vs 90d",
        detail=(
            f"7d residual {resid:+.0f} GWh/d, z-score {z:+.2f} vs trailing 90 days"
            f"{mover_txt} (as of {row.date})."
        ),
        payload={
            "level": level,
            "zscore": round(z, 2),
            "residual_7d": round(resid, 2),
            "date": row.date,
        },
    )


# ---------------------------------------------------------------------------
# Template registry — exposed to the route layer for client-side schema
# discovery and for params validation.
# ---------------------------------------------------------------------------
TEMPLATES: dict[str, dict] = {
    "chokepoint_anomaly": {
        "label": "Chokepoint transit anomaly",
        "summary": "Notify me when a chokepoint's daily transit count deviates from its 30d average.",
        "params_schema": {
            "zone": {"type": "enum", "options": sorted(KNOWN_ZONES), "required": True},
            "threshold_pct": {"type": "number", "min": 5, "max": 200, "default": 15},
            "direction": {
                "type": "enum",
                "options": ["above", "below", "either"],
                "default": "either",
            },
        },
        "evaluator": evaluate_chokepoint_anomaly,
    },
    "floating_storage_surge": {
        "label": "Floating storage surge",
        "summary": "Notify me when N+ tankers sit in floating storage in a zone (or globally).",
        "params_schema": {
            "zone": {
                "type": "enum",
                "options": ["*", *sorted(KNOWN_ZONES)],
                "default": "*",
            },
            "min_vessels": {"type": "number", "min": 1, "max": 100, "default": 3},
            "window_days": {"type": "number", "min": 1, "max": 30, "default": 7},
        },
        "evaluator": evaluate_floating_storage_surge,
    },
    "crack_spread_breach": {
        "label": "3:2:1 crack spread breach",
        "summary": "Notify me when the 3:2:1 crack spread crosses a $/bbl threshold.",
        "params_schema": {
            "direction": {
                "type": "enum",
                "options": ["above", "below"],
                "required": True,
            },
            "threshold_usd": {"type": "number", "min": 0, "max": 100, "required": True},
        },
        "evaluator": evaluate_crack_spread_breach,
    },
    "negative_prices": {
        "label": "Negative power prices (renewable oversupply)",
        "summary": "Notify me when a power zone has unusually many negative day-ahead price hours vs its own norm.",
        "params_schema": {
            "zone": {"type": "enum", "options": list(POWER_ZONES), "required": True},
        },
        "evaluator": evaluate_negative_prices,
    },
    "gas_balance": {
        "label": "EU gas balance signal",
        "summary": "Notify me when the EU gas-balance residual flags a WATCH or SIGNAL deviation vs its 90d norm.",
        "params_schema": {},
        "evaluator": evaluate_gas_balance,
    },
}


def evaluator_for(rule_type: str) -> Callable | None:
    entry = TEMPLATES.get(rule_type)
    return entry["evaluator"] if entry else None


def validate_params(rule_type: str, params: dict) -> tuple[bool, str | None]:
    """Minimal sanity-check before persisting an AlertRule.

    Returns (ok, error_message_or_None). Keeps the route handler thin.
    """
    template = TEMPLATES.get(rule_type)
    if not template:
        return False, f"unknown rule_type: {rule_type}"
    schema = template["params_schema"]
    for key, spec in schema.items():
        if spec.get("required") and key not in params:
            return False, f"missing required param: {key}"
        if key in params and spec["type"] == "enum":
            if params[key] not in spec["options"]:
                return False, f"{key} must be one of {spec['options']}"
        if key in params and spec["type"] == "number":
            try:
                v = float(params[key])
            except (TypeError, ValueError):
                return False, f"{key} must be a number"
            if "min" in spec and v < spec["min"]:
                return False, f"{key} below min ({spec['min']})"
            if "max" in spec and v > spec["max"]:
                return False, f"{key} above max ({spec['max']})"
    return True, None
