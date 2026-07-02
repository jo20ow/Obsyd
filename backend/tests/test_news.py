"""Cross-asset news reader: pure parse + endpoints (no network)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.main import app
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
    async def fake_feed(query, **kw):
        return [{"title": "Fed holds", "url": "https://x.com/a", "source": "x.com", "published": "2026-07-02T09:00:00Z"}]

    monkeypatch.setattr(news_routes, "get_feed", fake_feed)
    body = client.get("/api/news/feed?topic=rates").json()
    assert body["available"] is True and body["topic"] == "rates" and len(body["data"]) == 1


def test_feed_unknown_topic(client, monkeypatch):
    async def fake_feed(query, **kw):
        return []

    monkeypatch.setattr(news_routes, "get_feed", fake_feed)
    assert client.get("/api/news/feed?topic=nope").json()["available"] is False


def test_feed_freetext_query(client, monkeypatch):
    seen = {}

    async def fake_feed(query, **kw):
        seen["q"] = query
        return [{"title": "t", "url": "u", "source": "s", "published": None}]

    monkeypatch.setattr(news_routes, "get_feed", fake_feed)
    body = client.get("/api/news/feed?q=lithium supply").json()
    assert body["available"] is True and seen["q"] == "lithium supply"
