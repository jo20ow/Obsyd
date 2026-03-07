import json
import time

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from backend.database import get_db
from backend.models.sentiment import GDELTVolume, SentimentScore, NewsHeadline
from backend.collectors.gdelt import KEYWORDS, _fetch_headlines

import httpx

router = APIRouter(prefix="/api/sentiment", tags=["sentiment"])

# Headlines cache (30 minutes)
_headlines_cache: list = []
_headlines_cache_ts: float = 0.0
_headlines_cache_source: str = "GDELT DOC 2.0"
HEADLINES_CACHE_TTL = 1800


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
async def get_headlines(db: Session = Depends(get_db)):
    """Get top energy headlines — Finnhub first, GDELT fallback (cached 30min)."""
    global _headlines_cache, _headlines_cache_ts, _headlines_cache_source

    now = time.monotonic()
    if _headlines_cache and (now - _headlines_cache_ts) < HEADLINES_CACHE_TTL:
        return {"source": _headlines_cache_source, "articles": _headlines_cache, "cached": True}

    # Try Finnhub headlines from DB first
    finnhub_rows = (
        db.query(NewsHeadline)
        .order_by(NewsHeadline.published_at.desc())
        .limit(15)
        .all()
    )

    if finnhub_rows:
        formatted = [
            {
                "title": row.headline,
                "url": row.url,
                "domain": row.source,
                "date": row.published_at.isoformat() if row.published_at else "",
                "summary": row.summary,
                "category": row.category,
            }
            for row in finnhub_rows
        ]
        source = "Finnhub"
    else:
        # Fallback to GDELT live fetch
        async with httpx.AsyncClient() as client:
            articles = await _fetch_headlines(client, max_records=15)

        formatted = [
            {
                "title": a.get("title", ""),
                "url": a.get("url", ""),
                "domain": a.get("domain", ""),
                "date": a.get("seendate", ""),
                "language": a.get("language", ""),
                "country": a.get("sourcecountry", ""),
            }
            for a in articles
        ]
        source = "GDELT DOC 2.0"

    if formatted:
        _headlines_cache = formatted
        _headlines_cache_ts = now
        _headlines_cache_source = source

    return {"source": source, "articles": formatted, "cached": False}


@router.get("/risk")
async def get_risk_score(db: Session = Depends(get_db)):
    """Get sentiment risk score (rule-based from GDELT tone, or LLM-based if configured)."""
    latest = (
        db.query(SentimentScore)
        .order_by(SentimentScore.date.desc())
        .first()
    )

    if not latest:
        return {"available": False, "score": None}

    try:
        factors = json.loads(latest.risk_factors)
    except (json.JSONDecodeError, TypeError):
        factors = []

    return {
        "available": True,
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
