"""Crypto vertical: CoinGecko parse → upsert → read endpoints (no network)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.crypto import coingecko
from backend.main import app
from backend.models.crypto import CryptoPrice

# Minimal CoinGecko /coins/markets rows.
SAMPLE = [
    {"id": "bitcoin", "current_price": 65000.0, "price_change_percentage_24h": 2.5, "market_cap": 1.3e12},
    {"id": "ethereum", "current_price": 3200.0, "price_change_percentage_24h": -1.2, "market_cap": 3.8e11},
    {"id": "unknown-coin", "current_price": 1.0, "market_cap": 100},  # not in basket → dropped
    {"id": "solana", "current_price": None, "market_cap": 5e10},       # no price → dropped
]


@pytest.fixture
def client(db_session):
    from backend.database import get_db
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_parse_markets_shapes_and_filters():
    rows = coingecko.parse_markets(SAMPLE)
    assert {r["symbol"] for r in rows} == {"BTC", "ETH"}  # basket-only, price required
    btc = next(r for r in rows if r["symbol"] == "BTC")
    assert btc["name"] == "Bitcoin"
    assert btc["price_usd"] == 65000.0
    assert btc["change_24h_pct"] == 2.5
    assert btc["market_cap"] == 1.3e12


async def test_collect_crypto_upserts(db_session, monkeypatch):
    async def fake_fetch():
        return SAMPLE

    monkeypatch.setattr(coingecko, "_fetch_markets", fake_fetch)
    res = await coingecko.collect_crypto(db_session)
    assert res["written"] == 2
    assert {r.symbol for r in db_session.query(CryptoPrice).all()} == {"BTC", "ETH"}

    # Idempotent upsert: same day again updates, doesn't duplicate.
    await coingecko.collect_crypto(db_session)
    assert db_session.query(CryptoPrice).count() == 2


async def test_collect_crypto_failsoft(db_session, monkeypatch):
    async def boom():
        raise RuntimeError("coingecko down")

    monkeypatch.setattr(coingecko, "_fetch_markets", boom)
    res = await coingecko.collect_crypto(db_session)  # must not raise
    assert res["written"] == 0


def test_prices_endpoint_sorted_by_market_cap(client, db_session):
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    db_session.add(CryptoPrice(date=day, symbol="ETH", name="Ethereum", price_usd=3200, change_24h_pct=-1.2, market_cap=3.8e11))
    db_session.add(CryptoPrice(date=day, symbol="BTC", name="Bitcoin", price_usd=65000, change_24h_pct=2.5, market_cap=1.3e12))
    db_session.commit()
    body = client.get("/api/crypto/prices").json()
    assert body["available"] is True and body["unit"] == "USD"
    assert [d["symbol"] for d in body["data"]] == ["BTC", "ETH"]  # market-cap desc


def test_prices_endpoint_empty(client, db_session):
    assert client.get("/api/crypto/prices").json()["available"] is False


def test_history_endpoint(client, db_session):
    base = datetime.now(timezone.utc).date()
    for i in range(3):
        d = (base - timedelta(days=2 - i)).isoformat()
        db_session.add(CryptoPrice(date=d, symbol="BTC", name="Bitcoin", price_usd=60000 + i * 1000, market_cap=1e12))
    db_session.commit()
    body = client.get("/api/crypto/history?symbol=btc&days=30").json()
    assert body["available"] is True and body["symbol"] == "BTC"
    assert len(body["data"]) == 3
    assert body["data"] == sorted(body["data"], key=lambda x: x["date"])


def test_history_unknown_symbol(client, db_session):
    assert client.get("/api/crypto/history?symbol=NOPE").json()["available"] is False
