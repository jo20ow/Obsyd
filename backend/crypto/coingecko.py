"""CoinGecko free public markets API — real-time crypto quotes (no API key).

Crypto is the cross-asset terminal's one genuinely-free real-time feed. We pull a
curated basket from `/coins/markets` (price + 24h change + market cap), parse it to
our shape, and upsert one row per (UTC date, symbol) so the latest quote plus an
accumulating daily history are both available. Fail-soft: any error logs + returns 0.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from backend.models.crypto import CryptoPrice

logger = logging.getLogger(__name__)

# Curated basket: (CoinGecko id, ticker symbol, display name). Source of truth for
# the crypto vertical + the watchlist catalog.
CRYPTO_ASSETS: list[tuple[str, str, str]] = [
    ("bitcoin", "BTC", "Bitcoin"),
    ("ethereum", "ETH", "Ethereum"),
    ("solana", "SOL", "Solana"),
    ("ripple", "XRP", "XRP"),
    ("cardano", "ADA", "Cardano"),
    ("dogecoin", "DOGE", "Dogecoin"),
    ("avalanche-2", "AVAX", "Avalanche"),
    ("chainlink", "LINK", "Chainlink"),
    ("polkadot", "DOT", "Polkadot"),
    ("litecoin", "LTC", "Litecoin"),
]

_ID_TO_META = {gid: (sym, name) for gid, sym, name in CRYPTO_ASSETS}
_IDS = ",".join(gid for gid, _s, _n in CRYPTO_ASSETS)

_MARKETS_URL = "https://api.coingecko.com/api/v3/coins/markets"


async def _fetch_markets() -> list[dict]:
    """GET the curated basket from CoinGecko /coins/markets (usd)."""
    params = {
        "vs_currency": "usd",
        "ids": _IDS,
        "order": "market_cap_desc",
        "price_change_percentage": "24h",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(_MARKETS_URL, params=params)
        resp.raise_for_status()
        return resp.json()


def parse_markets(raw: list[dict]) -> list[dict]:
    """CoinGecko markets rows → [{symbol, name, price_usd, change_24h_pct, market_cap}].

    Only assets in our curated basket are kept (by CoinGecko id); prefers our
    canonical symbol/name over CoinGecko's. Rows without a usable price are dropped.
    """
    out: list[dict] = []
    for r in raw or []:
        meta = _ID_TO_META.get(r.get("id"))
        if meta is None:
            continue
        price = r.get("current_price")
        if price is None:
            continue
        symbol, name = meta
        out.append({
            "symbol": symbol,
            "name": name,
            "price_usd": float(price),
            "change_24h_pct": (
                float(r["price_change_percentage_24h"])
                if r.get("price_change_percentage_24h") is not None else None
            ),
            "market_cap": float(r["market_cap"]) if r.get("market_cap") is not None else None,
        })
    return out


def _upsert(db: Session, day: str, row: dict) -> None:
    existing = (
        db.query(CryptoPrice)
        .filter(CryptoPrice.date == day, CryptoPrice.symbol == row["symbol"])
        .first()
    )
    if existing:
        existing.name = row["name"]
        existing.price_usd = row["price_usd"]
        existing.change_24h_pct = row["change_24h_pct"]
        existing.market_cap = row["market_cap"]
    else:
        db.add(CryptoPrice(date=day, **row))


async def collect_crypto(db: Session) -> dict:
    """Fetch the basket and upsert today's (UTC) row per asset. Fail-soft."""
    try:
        raw = await _fetch_markets()
    except Exception as exc:  # noqa: BLE001 — collector must never crash the scheduler
        logger.warning("crypto: CoinGecko fetch failed: %s", exc)
        return {"written": 0, "error": str(exc)}

    rows = parse_markets(raw)
    if not rows:
        logger.warning("crypto: CoinGecko returned no usable rows")
        return {"written": 0}

    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for row in rows:
        _upsert(db, day, row)
    db.commit()
    logger.info("crypto: upserted %d assets for %s", len(rows), day)
    return {"written": len(rows), "date": day}
