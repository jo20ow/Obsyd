from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.auth.dependencies import require_pro
from backend.collectors.jodi import collect_jodi
from backend.database import get_db
from backend.models.jodi import JODIProduction

router = APIRouter(prefix="/api/jodi", tags=["jodi"])


@router.get("/production")
async def get_jodi_production(
    country: str = Query(None, description="Filter by ISO 2-letter country code"),
    limit: int = Query(120, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    """Get JODI crude oil production, consumption, and stock data by country."""
    query = db.query(JODIProduction).order_by(JODIProduction.date.desc())
    if country:
        query = query.filter(JODIProduction.country == country.upper())
    rows = query.limit(limit).all()
    return [
        {
            "country": r.country,
            "country_name": r.country_name,
            "date": r.date,
            "production": r.production,
            "consumption": r.consumption,
            "stocks": r.stocks,
        }
        for r in rows
    ]


@router.get("/summary")
async def get_jodi_summary(db: Session = Depends(get_db)):
    """Get latest available data point per country."""
    # Subquery: max date per country
    latest = (
        db.query(
            JODIProduction.country,
            func.max(JODIProduction.date).label("max_date"),
        )
        .filter(JODIProduction.production.isnot(None))
        .group_by(JODIProduction.country)
        .subquery()
    )

    rows = (
        db.query(JODIProduction)
        .join(
            latest,
            (JODIProduction.country == latest.c.country) & (JODIProduction.date == latest.c.max_date),
        )
        .order_by(JODIProduction.production.desc())
        .all()
    )

    return [
        {
            "country": r.country,
            "country_name": r.country_name,
            "date": r.date,
            "production": r.production,
            "consumption": r.consumption,
            "stocks": r.stocks,
        }
        for r in rows
    ]


@router.post("/collect")
async def trigger_jodi_collection(_user=Depends(require_pro)):
    """Manually trigger JODI data collection."""
    await collect_jodi()
    return {"status": "ok", "message": "JODI collection complete"}
