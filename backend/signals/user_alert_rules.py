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

from backend.models.vessels import FloatingStorageEvent, GeofenceEvent

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
