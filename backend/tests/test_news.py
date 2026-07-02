"""Cross-asset news reader: pure parse + endpoints (no network)."""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.news import gdelt_news
from backend.news.gdelt_news import parse_articles
from backend.routes import news as news_routes

RAW = [
    {"url": "https://a.com/1", "title": "Gas prices jump", "domain": "a.com", "seendate": "20260702T120000Z"},
    {"url": "https://a.com/1", "title": "Gas prices jump", "domain": "a.com", "seendate": "20260702T120000Z"},  # dup url
    {"url": "https://b.com/2", "title": "", "domain": "b.com", "seendate": "20260702T110000Z"},  # empty title
    {"url": "https://c.com/3", "title": "OPEC meets", "domain": "c.com", "seendate": "20260702T100000Z"},
]


@pytest.fixture
def client():
    return TestClient(app)


def test_parse_articles_dedupes_and_normalises():
    out = parse_articles(RAW)
    assert [a["url"] for a in out] == ["https://a.com/1", "https://c.com/3"]  # dup + empty dropped
    assert out[0]["source"] == "a.com"
    assert out[0]["published"] == "2026-07-02T12:00:00Z"


def test_topics_endpoint(client):
    keys = {t["key"] for t in client.get("/api/news/topics").json()["topics"]}
    assert {"markets", "energy", "crypto", "rates"} <= keys


def test_feed_endpoint_topic(client, monkeypatch):
    monkeypatch.setattr(
        news_routes,
        "get_cached",
        lambda query, **kw: [
            {"title": "Fed holds", "url": "https://x.com/a", "source": "x.com", "published": "2026-07-02T09:00:00Z"}
        ],
    )
    body = client.get("/api/news/feed?topic=rates").json()
    assert body["available"] is True and body["topic"] == "rates" and len(body["data"]) == 1


def test_feed_unknown_topic(client):
    assert client.get("/api/news/feed?topic=nope").json()["available"] is False


def test_feed_freetext_query(client, monkeypatch):
    seen = {}

    def fake_cached(query, **kw):
        seen["q"] = query
        return [{"title": "t", "url": "u", "source": "s", "published": None}]

    monkeypatch.setattr(news_routes, "get_cached", fake_cached)
    body = client.get("/api/news/feed?q=lithium supply").json()
    assert body["available"] is True and seen["q"] == "lithium supply"


def test_feed_miss_is_non_blocking_and_schedules_warm(client, monkeypatch):
    """A cache miss must NOT fetch inline (no blocking on GDELT) — it returns an
    honest empty result and schedules a background warm for next time."""
    monkeypatch.setattr(news_routes, "get_cached", lambda query, **kw: None)
    warmed = {}
    monkeypatch.setattr(news_routes, "schedule_warm", lambda query, **kw: warmed.setdefault("q", query))

    body = client.get("/api/news/feed?topic=energy").json()
    assert body["available"] is False
    assert warmed["q"] == news_routes.query_for_topic("energy")


def test_get_feed_never_caches_empty_serves_stale(monkeypatch):
    """A transient empty/failed fetch (e.g. GDELT 429) must not poison the cache —
    the feed keeps serving the last good result instead of going blank."""
    gdelt_news._cache.clear()

    calls = {"n": 0}

    async def fake_fetch(query, max_records, timespan):
        calls["n"] += 1
        if calls["n"] == 1:
            return [{"url": "https://x.com/1", "title": "First", "domain": "x.com", "seendate": "20260702T120000Z"}]
        return []  # subsequent fetches come back empty (rate-limited / transient)

    monkeypatch.setattr(gdelt_news, "_fetch", fake_fetch)

    first = asyncio.run(gdelt_news.get_feed("q", timespan="3d", max_records=25))
    assert len(first) == 1

    # Expire the TTL so the next call actually re-fetches (and gets empty).
    key = "q|3d|25"
    ts, data = gdelt_news._cache[key]
    gdelt_news._cache[key] = (ts - gdelt_news._TTL - 1, data)

    second = asyncio.run(gdelt_news.get_feed("q", timespan="3d", max_records=25))
    assert len(second) == 1 and second[0]["url"] == "https://x.com/1"  # stale served, not empty
    assert calls["n"] == 2  # it really did re-fetch
