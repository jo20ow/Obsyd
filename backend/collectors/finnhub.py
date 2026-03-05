"""
Finnhub Collector (BYOK).

Forex quotes only. Commodity futures are NOT supported on Finnhub's free tier.
For commodity prices use Alpha Vantage (alphavantage.py).
Free tier: 60 calls/minute.
"""

import logging

import httpx

from backend.config import settings

logger = logging.getLogger(__name__)

FINNHUB_BASE = "https://finnhub.io/api/v1"

FOREX_SYMBOLS = {
    "EUR_USD": "OANDA:EUR_USD",
    "GBP_USD": "OANDA:GBP_USD",
    "USD_JPY": "OANDA:USD_JPY",
}


async def fetch_forex_quote(symbol: str) -> dict | None:
    """Fetch a single forex quote from Finnhub. Returns None on failure."""
    if not settings.finnhub_api_key:
        return None

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(
                f"{FINNHUB_BASE}/quote",
                params={"symbol": symbol, "token": settings.finnhub_api_key},
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("c", 0) == 0:
                return None
            return data
        except httpx.HTTPError as e:
            logger.error(f"Finnhub {symbol} fetch failed: {e}")
            return None


async def fetch_forex_prices() -> dict:
    """
    Fetch live forex prices from Finnhub.

    Returns dict keyed by pair label (EUR_USD, GBP_USD, USD_JPY).
    Returns empty dict if no Finnhub key configured.
    """
    if not settings.finnhub_api_key:
        return {}

    results = {}
    for label, symbol in FOREX_SYMBOLS.items():
        quote = await fetch_forex_quote(symbol)
        if quote is None:
            continue
        results[label] = {
            "symbol": symbol,
            "current": quote["c"],
            "previous_close": quote["pc"],
            "change": quote["d"],
            "change_pct": quote["dp"],
            "high": quote["h"],
            "low": quote["l"],
        }

    return results
