"""
Finnhub News Collector.

Fetches general market news from Finnhub, filters for energy/oil relevance,
and stores headlines in the database.

Finnhub free tier: 60 calls/min. We call once every 2 hours — well within limits.
API docs: https://finnhub.io/docs/api/market-news
"""

import logging
from datetime import datetime, timezone

import httpx

from backend.config import settings
from backend.database import SessionLocal
from backend.models.sentiment import NewsHeadline

logger = logging.getLogger(__name__)

FINNHUB_NEWS_URL = "https://finnhub.io/api/v1/news"
REQUEST_TIMEOUT = 20
MAX_HEADLINES = 100

# Keywords to filter energy-relevant headlines (case-insensitive)
ENERGY_KEYWORDS = [
    "oil", "crude", "opec", "lng", "refinery", "pipeline", "tanker",
    "brent", "wti", "natural gas", "petroleum", "energy",
    "iran", "saudi", "hormuz", "suez",
]


def _is_energy_relevant(headline: str, summary: str) -> bool:
    """Check if headline or summary contains energy-related keywords."""
    text = (headline + " " + summary).lower()
    return any(kw in text for kw in ENERGY_KEYWORDS)


async def collect_finnhub_news():
    """Fetch general news from Finnhub, filter for energy relevance, store in DB."""
    if not settings.finnhub_api_key:
        logger.debug("Finnhub: no API key configured, skipping")
        return

    db = SessionLocal()
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.get(FINNHUB_NEWS_URL, params={
                "category": "general",
                "token": settings.finnhub_api_key,
            })
            resp.raise_for_status()
            articles = resp.json()

        if not isinstance(articles, list):
            logger.warning("Finnhub: unexpected response format")
            return

        stored = 0
        for article in articles:
            headline = article.get("headline", "")
            summary = article.get("summary", "")

            if not _is_energy_relevant(headline, summary):
                continue

            # Deduplicate by headline
            existing = db.query(NewsHeadline).filter(
                NewsHeadline.headline == headline
            ).first()
            if existing:
                continue

            unix_ts = article.get("datetime", 0)
            published = datetime.fromtimestamp(unix_ts, tz=timezone.utc)

            db.add(NewsHeadline(
                source="finnhub",
                headline=headline,
                summary=summary,
                url=article.get("url", ""),
                published_at=published,
                category=article.get("category", ""),
            ))
            stored += 1

        db.commit()

        # Enforce max headlines — delete oldest beyond limit
        total = db.query(NewsHeadline).count()
        if total > MAX_HEADLINES:
            excess = total - MAX_HEADLINES
            oldest = (
                db.query(NewsHeadline)
                .order_by(NewsHeadline.published_at.asc())
                .limit(excess)
                .all()
            )
            for row in oldest:
                db.delete(row)
            db.commit()
            logger.info(f"Finnhub: pruned {excess} old headlines")

        logger.info(f"Finnhub: stored {stored} energy headlines ({total} total)")

    except httpx.HTTPStatusError as e:
        logger.error(f"Finnhub API error: {e.response.status_code} {e.response.text[:200]}")
    except Exception as e:
        logger.error(f"Finnhub news collection failed: {e}")
    finally:
        db.close()
