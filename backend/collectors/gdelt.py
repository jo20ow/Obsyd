"""
GDELT DOC 2.0 Collector + optional LLM Sentiment.

Stufe 1 (automatic): Volume timeline + average tone for energy keywords.
Stufe 2 (BYOK LLM): AI risk score from top headlines (OpenAI or Anthropic).

GDELT API: https://api.gdeltproject.org/api/v2/doc/doc
No API key required. Public domain.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

import httpx

from backend.config import settings
from backend.database import SessionLocal
from backend.models.sentiment import GDELTVolume, SentimentScore

logger = logging.getLogger(__name__)

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
REQUEST_TIMEOUT = 20

# Primary keywords: fetched every 15 minutes
KEYWORDS_PRIMARY = [
    "oil price",
    "OPEC",
    "LNG",
    "oil supply disruption",
]

# Secondary keywords: fetched hourly only
KEYWORDS_SECONDARY = [
    "Suez Canal",
    "Strait of Hormuz",
    "refinery shutdown",
]

# All keywords (used for headlines query and cleanup)
KEYWORDS = KEYWORDS_PRIMARY + KEYWORDS_SECONDARY

CALL_DELAY = 5  # seconds between GDELT API calls to avoid 429

LLM_PROMPT = """You are an energy market analyst. Analyze these recent news headlines about energy markets and oil.

Headlines:
{headlines}

Respond in valid JSON only, no markdown:
{{"risk_score": <integer 1-10, where 1=no risk and 10=extreme supply disruption risk>, "risk_factors": ["factor1", "factor2", "factor3"]}}

Rate the CURRENT risk to global oil supply and prices based on these headlines. Focus on supply disruptions, geopolitical tensions, weather events, and OPEC decisions."""


async def _fetch_volume(client: httpx.AsyncClient, keyword: str) -> list[dict]:
    """Fetch 24h volume timeline for a keyword."""
    try:
        resp = await client.get(GDELT_URL, params={
            "query": keyword,
            "mode": "TimelineVol",
            "TIMESPAN": "1d",
            "format": "json",
        }, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        timeline = data.get("timeline", [])
        if timeline:
            return timeline[0].get("data", [])
        return []
    except Exception as e:
        logger.warning(f"GDELT volume fetch failed for '{keyword}': {e}")
        return []


async def _fetch_tone(client: httpx.AsyncClient, keyword: str) -> list[dict]:
    """Fetch 24h tone timeline for a keyword."""
    try:
        resp = await client.get(GDELT_URL, params={
            "query": keyword,
            "mode": "TimelineTone",
            "TIMESPAN": "1d",
            "format": "json",
        }, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        timeline = data.get("timeline", [])
        if timeline:
            return timeline[0].get("data", [])
        return []
    except Exception as e:
        logger.warning(f"GDELT tone fetch failed for '{keyword}': {e}")
        return []


async def _fetch_headlines(client: httpx.AsyncClient, max_records: int = 30) -> list[dict]:
    """Fetch top English headlines for energy keywords."""
    query = "(" + " OR ".join(f'"{k}"' for k in KEYWORDS[:4]) + ") sourcelang:english"
    try:
        resp = await client.get(GDELT_URL, params={
            "query": query,
            "mode": "artlist",
            "maxrecords": str(max_records),
            "format": "json",
            "sort": "hybridrel",
            "timespan": "1d",
        }, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        return data.get("articles", [])
    except Exception as e:
        logger.warning(f"GDELT headlines fetch failed: {e}")
        return []


async def _score_with_openai(headlines_text: str) -> dict | None:
    """Call OpenAI API for risk scoring."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.openai_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": LLM_PROMPT.format(headlines=headlines_text)}],
                    "temperature": 0.3,
                },
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            # Strip markdown fences if present
            content = content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            return json.loads(content)
    except Exception as e:
        logger.error(f"OpenAI sentiment scoring failed: {e}")
        return None


async def _score_with_anthropic(headlines_text: str) -> dict | None:
    """Call Anthropic API for risk scoring."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": settings.anthropic_api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 256,
                    "messages": [{"role": "user", "content": LLM_PROMPT.format(headlines=headlines_text)}],
                },
            )
            resp.raise_for_status()
            content = resp.json()["content"][0]["text"]
            content = content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            return json.loads(content)
    except Exception as e:
        logger.error(f"Anthropic sentiment scoring failed: {e}")
        return None


async def _collect_keywords(keywords: list[str]):
    """Collect volume + tone for a list of keywords with rate-limiting."""
    records = []

    async with httpx.AsyncClient() as client:
        for keyword in keywords:
            vol_data = await _fetch_volume(client, keyword)
            await asyncio.sleep(CALL_DELAY)
            tone_data = await _fetch_tone(client, keyword)
            await asyncio.sleep(CALL_DELAY)

            # Build tone lookup by timestamp
            tone_map = {d["date"]: d["value"] for d in tone_data}

            # Take last 6 data points (most recent hours)
            for point in vol_data[-6:]:
                ts = point.get("date", "")
                vol = point.get("value", 0.0)
                tone = tone_map.get(ts, 0.0)
                records.append(GDELTVolume(
                    keyword=keyword,
                    timestamp=ts,
                    volume=vol,
                    avg_tone=tone,
                ))

    if not records:
        return 0

    db = SessionLocal()
    try:
        # Delete old data (keep last 24h worth per keyword)
        for kw in keywords:
            count = db.query(GDELTVolume).filter(GDELTVolume.keyword == kw).count()
            if count > 48:  # keep ~48 data points per keyword
                oldest = (
                    db.query(GDELTVolume)
                    .filter(GDELTVolume.keyword == kw)
                    .order_by(GDELTVolume.timestamp.asc())
                    .limit(count - 48)
                    .all()
                )
                for old in oldest:
                    db.delete(old)

        db.add_all(records)
        db.commit()
        logger.info(f"GDELT: stored {len(records)} volume/tone records for {len(keywords)} keywords")
        return len(records)
    except Exception as e:
        db.rollback()
        logger.error(f"GDELT: DB write failed: {e}")
        return 0
    finally:
        db.close()


async def collect_gdelt_volume():
    """Stufe 1: Collect volume + tone for PRIMARY keywords (every 15 min)."""
    await _collect_keywords(KEYWORDS_PRIMARY)


async def collect_gdelt_volume_secondary():
    """Stufe 1b: Collect volume + tone for SECONDARY keywords (hourly)."""
    await _collect_keywords(KEYWORDS_SECONDARY)


async def collect_gdelt_sentiment():
    """Stufe 2 (BYOK): Fetch headlines and score with LLM."""
    if not (settings.openai_api_key or settings.anthropic_api_key):
        return

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Check if we already scored today
    db = SessionLocal()
    try:
        existing = db.query(SentimentScore).filter(SentimentScore.date == today).first()
        if existing:
            return
    finally:
        db.close()

    async with httpx.AsyncClient() as client:
        articles = await _fetch_headlines(client)

    if not articles:
        return

    headlines_text = "\n".join(
        f"- {a.get('title', '')}" for a in articles if a.get("title")
    )

    result = None
    source = ""

    if settings.anthropic_api_key:
        result = await _score_with_anthropic(headlines_text)
        source = "anthropic"

    if not result and settings.openai_api_key:
        result = await _score_with_openai(headlines_text)
        source = "openai"

    if not result:
        return

    risk_score = max(1, min(10, int(result.get("risk_score", 5))))
    risk_factors = result.get("risk_factors", [])

    db = SessionLocal()
    try:
        db.add(SentimentScore(
            date=today,
            risk_score=risk_score,
            risk_factors=json.dumps(risk_factors),
            source=source,
        ))
        db.commit()
        logger.info(f"GDELT Sentiment: risk_score={risk_score} via {source}")
    except Exception as e:
        db.rollback()
        logger.error(f"GDELT sentiment DB write failed: {e}")
    finally:
        db.close()
