from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models.alerts import Alert

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


@router.get("")
async def get_alerts(
    rule: str = Query(None, description="Filter by rule name"),
    zone: str = Query(None, description="Filter by zone"),
    severity: str = Query(None, description="Filter by severity"),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Get generated alerts, newest first."""
    query = db.query(Alert).order_by(Alert.created_at.desc())
    if rule:
        query = query.filter(Alert.rule == rule)
    if zone:
        query = query.filter(Alert.zone == zone)
    if severity:
        query = query.filter(Alert.severity == severity)
    rows = query.limit(limit).all()
    return [
        {
            "id": r.id,
            "rule": r.rule,
            "zone": r.zone,
            "severity": r.severity,
            "title": r.title,
            "detail": r.detail,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]
