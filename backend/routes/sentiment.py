import json

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from backend.database import get_db
from backend.config import settings
from backend.models.sentiment import GDELTVolume, SentimentScore
from backend.collectors.gdelt import KEYWORDS, _fetch_headlines

import httpx

router = APIRouter(prefix="/api/sentiment", tags=["sentiment"])


@router.get("/volume")
async def get_volume(
    keyword: str = Query(None, description="Filter by keyword"),
    db: Session = Depends(get_db),
):
    """Get GDELT news volume + tone timeline per keyword."""
    query = db.query(GDELTVolume).order_by(GDELTVolume.timestamp.desc())
    if keyword:
        query = query.filter(GDELTVolume.keyword == keyword)
    rows = query.limit(200).all()

    # Group by keyword
    by_keyword = {}
    for r in rows:
        kw = r.keyword
        if kw not in by_keyword:
            by_keyword[kw] = []
        by_keyword[kw].append({
            "timestamp": r.timestamp,
            "volume": r.volume,
            "avg_tone": r.avg_tone,
        })

    return {
        "source": "GDELT DOC 2.0",
        "keywords": by_keyword,
    }


@router.get("/headlines")
async def get_headlines():
    """Get top current energy headlines from GDELT."""
    async with httpx.AsyncClient() as client:
        articles = await _fetch_headlines(client, max_records=15)

    return {
        "source": "GDELT DOC 2.0",
        "articles": [
            {
                "title": a.get("title", ""),
                "url": a.get("url", ""),
                "domain": a.get("domain", ""),
                "date": a.get("seendate", ""),
                "language": a.get("language", ""),
                "country": a.get("sourcecountry", ""),
            }
            for a in articles
        ],
    }


@router.get("/risk")
async def get_risk_score(db: Session = Depends(get_db)):
    """Get AI-generated risk score (requires BYOK LLM key)."""
    has_llm = bool(settings.openai_api_key or settings.anthropic_api_key)

    latest = (
        db.query(SentimentScore)
        .order_by(SentimentScore.date.desc())
        .first()
    )

    if not latest:
        return {"available": has_llm, "score": None}

    try:
        factors = json.loads(latest.risk_factors)
    except (json.JSONDecodeError, TypeError):
        factors = []

    return {
        "available": has_llm,
        "score": {
            "date": latest.date,
            "risk_score": latest.risk_score,
            "risk_factors": factors,
            "source": latest.source,
        },
    }


@router.get("/status")
async def get_sentiment_status(db: Session = Depends(get_db)):
    """Check if GDELT data is flowing."""
    count = db.query(func.count(GDELTVolume.id)).scalar()
    return {"active": count > 0, "record_count": count}
