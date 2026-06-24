from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models.alerts import Alert
from backend.signals.portwatch_alerts import check_chokepoint_anomalies

router = APIRouter(prefix="/api/alerts", tags=["alerts"])

# Sort order for the radar feed: most urgent first, then most recent.
_SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}


def _serialize(r: Alert) -> dict:
    return {
        "id": r.id,
        "rule": r.rule,
        "zone": r.zone,
        "vertical": r.vertical,
        "severity": r.severity,
        "title": r.title,
        "detail": r.detail,
        "created_at": r.created_at.isoformat(),
    }


@router.get("")
async def get_alerts(
    rule: str = Query(None, description="Filter by rule name"),
    zone: str = Query(None, description="Filter by zone"),
    vertical: str = Query(None, description="Filter by vertical (oil/gas/power/metals/sentiment)"),
    severity: str = Query(None, description="Filter by severity"),
    group_by_vertical: bool = Query(False, description="Return alerts grouped by vertical, severity-sorted"),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Get generated alerts, newest first (or grouped by vertical, severity-sorted)."""
    query = db.query(Alert).order_by(Alert.created_at.desc())
    if rule:
        query = query.filter(Alert.rule == rule)
    if zone:
        query = query.filter(Alert.zone == zone)
    if vertical:
        query = query.filter(Alert.vertical == vertical)
    if severity:
        query = query.filter(Alert.severity == severity)
    rows = query.limit(limit).all()
    items = [_serialize(r) for r in rows]

    if not group_by_vertical:
        return items

    # Group by vertical, each group severity-sorted (critical→warning→info), then newest.
    groups: dict[str, list[dict]] = {}
    for item in items:
        groups.setdefault(item["vertical"], []).append(item)
    for group in groups.values():
        # Stable sort: newest first, then promote by severity → severity primary, recency secondary.
        group.sort(key=lambda a: a["created_at"], reverse=True)
        group.sort(key=lambda a: _SEVERITY_RANK.get(a["severity"], 9))
    return {"verticals": groups, "total": len(items)}


@router.get("/portwatch")
async def get_portwatch_alerts():
    """Get current PortWatch chokepoint anomaly alerts (computed live from SQLite)."""
    alerts = check_chokepoint_anomalies()
    return {
        "source": "IMF PortWatch",
        "threshold_pct": 30,
        "alerts": alerts,
    }
