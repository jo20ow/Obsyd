from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.collectors.freshness import evaluate_freshness
from backend.database import get_db

router = APIRouter()


@router.get("/health")
async def health_check(db: Session = Depends(get_db)):
    """Liveness + DB readiness. Returns 503 if the DB is unreachable,
    so the health-check cron restarts the service."""
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        raise HTTPException(status_code=503, detail="database unreachable")
    return {"status": "ok", "service": "obsyd"}


@router.get("/api/health/collectors")
async def collector_status(db: Session = Depends(get_db)):
    """Which data collectors are fresh (not just ever-written).

    Product-critical sources (ENTSO-E day-ahead/grid, Energy-Charts flows, gas
    balance, yfinance TTF/COPPER) are judged by their DATA's delivery date, not
    the write timestamp — they're rewritten nightly, so a write-time probe would
    look fresh even with a frozen data frontier. See backend/collectors/freshness.py.
    """
    status = evaluate_freshness(db)
    return {
        **{k: v["fresh"] for k, v in status.items()},
        "last_seen": {k: v["last_seen"] for k, v in status.items()},
    }
