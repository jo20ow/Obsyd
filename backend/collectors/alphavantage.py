"""
Alpha Vantage Commodity Collector (BYOK).

Fetches daily commodity prices (WTI, Brent, Natural Gas).
Free tier: 25 calls/day. Responses cached for 15 minutes.
"""

import asyncio
import logging
import time

import httpx

from backend.config import settings

logger = logging.getLogger(__name__)

AV_BASE = "https://www.alphavantage.co/query"

COMMODITIES = {
    "WTI": {"function": "WTI", "description": "WTI Crude Oil (Daily)"},
    "BRENT": {"function": "BRENT", "description": "Brent Crude Oil (Daily)"},
    "NG": {"function": "NATURAL_GAS", "description": "Henry Hub Natural Gas (Daily)"},
}

CACHE_TTL = 900  # 15 minutes in seconds

_cache: dict[str, dict] = {}
_cache_ts: float = 0.0


async def _fetch_commodity(function: str) -> list[dict]:
    """Fetch a single commodity series from Alpha Vantage."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.get(
                AV_BASE,
                params={
                    "function": function,
                    "interval": "daily",
                    "apikey": settings.alpha_vantage_api_key,
                },
            )
            resp.raise_for_status()
            data = resp.json()

            if "Note" in data or "Information" in data:
                msg = data.get("Note") or data.get("Information", "")
                logger.warning(f"Alpha Vantage {function}: rate limit or info: {msg}")
                return []

            return data.get("data", [])
        except httpx.HTTPError as e:
            logger.error(f"Alpha Vantage {function} fetch failed: {e}")
            return []


async def fetch_live_commodities() -> dict:
    """
    Fetch daily commodity prices from Alpha Vantage.
    Results are cached in-memory for 15 minutes to respect the 25 calls/day limit.

    Returns dict keyed by label (WTI, BRENT, NG) with current price and change info.
    Returns empty dict if no Alpha Vantage key is configured.
    """
    global _cache, _cache_ts

    if not settings.alpha_vantage_api_key:
        return {}

    now = time.monotonic()
    if _cache and (now - _cache_ts) < CACHE_TTL:
        return _cache

    results = {}
    first = True
    for label, cfg in COMMODITIES.items():
        if not first:
            await asyncio.sleep(2)
        first = False
        rows = await _fetch_commodity(cfg["function"])
        if len(rows) < 2:
            continue

        # Alpha Vantage returns newest first: [{"date": "2024-03-01", "value": "78.26"}, ...]
        # Find the two most recent rows with valid values
        valid = []
        for row in rows:
            val = row.get("value", ".")
            if val != ".":
                valid.append(row)
            if len(valid) == 2:
                break

        if len(valid) < 2:
            continue

        current = float(valid[0]["value"])
        previous = float(valid[1]["value"])
        change = current - previous
        change_pct = (change / previous) * 100 if previous != 0 else 0.0

        results[label] = {
            "symbol": cfg["function"],
            "date": valid[0]["date"],
            "current": current,
            "previous_close": previous,
            "change": round(change, 4),
            "change_pct": round(change_pct, 4),
        }

    if results:
        _cache = results
        _cache_ts = now
        logger.info(f"Alpha Vantage: cached {len(results)} commodities for 15min")

    return results
