"""Cross-asset news reader endpoints (GDELT, free).

GET /api/news/topics       — curated topic list
GET /api/news/feed?topic=  — recent headlines for a topic (or ?q= free-text), FREE

Descriptive aggregation of public news headlines. Not investment advice.
"""

from fastapi import APIRouter, Query

from backend.news.gdelt_news import TOPICS, get_cached, query_for_topic, schedule_warm

router = APIRouter(prefix="/api/news", tags=["news"])


@router.get("/topics")
def topics():
    return {"topics": [{"key": k, "label": label} for k, label, _q in TOPICS]}


@router.get("/feed")
def feed(
    topic: str = Query(None, description="Curated topic key (see /topics)"),
    q: str = Query(None, description="Free-text query (overrides topic)"),
):
    """Recent English headlines for a curated topic or a free-text query.

    Cache-only + non-blocking: GDELT is flaky and aggressively rate-limited, so the
    request never waits on it. Curated topics are kept warm by the background prewarm;
    on a cache miss we schedule a background warm and return an empty (but honest)
    result immediately, so the next request has data."""
    if q:
        query = q.strip()[:200]
        label = q.strip()[:80]
    else:
        key = topic or "markets"
        query = query_for_topic(key)
        if query is None:
            return {"available": False, "reason": f"unknown topic: {key}"}
        label = key

    articles = get_cached(query)
    if articles is None:
        schedule_warm(query)  # populate for next time; don't block this request
        return {"available": False, "topic": label, "reason": "Warming up — check back shortly."}
    if not articles:
        return {"available": False, "topic": label, "reason": "No headlines right now — check back shortly."}
    return {"available": True, "topic": label, "source": "GDELT", "data": articles}
