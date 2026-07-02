from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models.alerts import Alert
from backend.signals.portwatch_alerts import check_chokepoint_anomalies

router = APIRouter(prefix="/api/alerts", tags=["alerts"])

# Sort order for the radar feed: most urgent first, then most recent.
_SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}


def _query_alerts(db, *, max_age_hours, limit, rule=None, zone=None, vertical=None, severity=None):
    """Shared radar query: alerts refreshed within the window, newest first.

    Single source of truth for the JSON feed and the RSS feed so they can't drift.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    query = db.query(Alert).filter(Alert.created_at > cutoff).order_by(Alert.created_at.desc())
    if rule:
        query = query.filter(Alert.rule == rule)
    if zone:
        query = query.filter(Alert.zone == zone)
    if vertical:
        query = query.filter(Alert.vertical == vertical)
    if severity:
        query = query.filter(Alert.severity == severity)
    return query.limit(limit).all()


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
    max_age_hours: int = Query(48, ge=1, le=720, description="Only alerts refreshed within this window (radar = what's abnormal NOW)"),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Get current alerts, newest first (or grouped by vertical, severity-sorted).

    Alerts are deduped on (rule, zone) within 24h and their timestamp is bumped on every
    re-fire, so a still-active anomaly always falls inside `max_age_hours` while a resolved
    one ages out — keeping the radar feed to what is currently abnormal, not weeks of history.
    """
    rows = _query_alerts(
        db, max_age_hours=max_age_hours, limit=limit,
        rule=rule, zone=zone, vertical=vertical, severity=severity,
    )
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


@router.get("/rss")
async def alerts_rss(
    vertical: str = Query(None, description="Filter by vertical (oil/gas/power/metals/sentiment)"),
    max_age_hours: int = Query(48, ge=1, le=720),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """The anomaly radar as an RSS 2.0 feed — a shareable distribution artifact.

    Same query as the JSON feed (`_query_alerts`); stdlib serialization, no new dep.
    """
    rows = _query_alerts(db, max_age_hours=max_age_hours, limit=limit, vertical=vertical)
    items = "".join(
        "<item>"
        f"<title>{escape(r.title or '')}</title>"
        f"<description>{escape(r.detail or '')}</description>"
        f"<category>{escape(r.vertical or '')}</category>"
        f'<guid isPermaLink="false">obsyd-alert-{r.id}</guid>'
        f"<link>https://obsyd.dev/#alert-{r.id}</link>"
        f"<pubDate>{format_datetime(r.created_at.replace(tzinfo=timezone.utc))}</pubDate>"
        "</item>"
        for r in rows
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0"><channel>'
        "<title>OBSYD Anomaly Radar</title>"
        "<link>https://obsyd.dev</link>"
        "<description>Cross-vertical anomaly radar — negative prices, Dunkelflaute, "
        "day-ahead deviations and cross-commodity signals from the official record.</description>"
        f"{items}</channel></rss>"
    )
    return Response(content=xml, media_type="application/rss+xml")


@router.get("/portwatch")
async def get_portwatch_alerts():
    """Get current PortWatch chokepoint anomaly alerts (computed live from SQLite)."""
    alerts = check_chokepoint_anomalies()
    return {
        "source": "IMF PortWatch",
        "threshold_pct": 30,
        "alerts": alerts,
    }
