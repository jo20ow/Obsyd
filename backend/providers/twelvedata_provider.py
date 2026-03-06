"""
Twelve Data Provider — batch commodity prices and intraday time series.

Free tier: 800 credits/day, 8 credits/min.
Batch requests (symbol=CL,BZ,...) cost 1 credit for all symbols.

Symbols:
  CL (WTI), BZ (Brent), NG (Natural Gas),
  GC (Gold), SI (Silver), HG (Copper),
  ZC (Corn), ZS (Soybeans), ZW (Wheat)
"""

import logging
import time
from datetime import datetime, timezone

import httpx

from backend.config import settings

logger = logging.getLogger(__name__)

BASE_URL = "https://api.twelvedata.com"

# Internal symbol -> Twelve Data symbol
SYMBOLS = {
    "WTI": "CL",
    "BRENT": "BZ",
    "NG": "NG",
    "GOLD": "GC",
    "SILVER": "SI",
    "COPPER": "HG",
    "CORN": "ZC",
    "SOYBEANS": "ZS",
    "WHEAT": "ZW",
}

# Reverse mapping for response parsing
_REVERSE = {v: k for k, v in SYMBOLS.items()}

# Display names for frontend
DISPLAY_NAMES = {
    "WTI": "WTI Crude Oil",
    "BRENT": "Brent Crude Oil",
    "NG": "Natural Gas",
    "GOLD": "Gold",
    "SILVER": "Silver",
    "COPPER": "Copper",
    "CORN": "Corn",
    "SOYBEANS": "Soybeans",
    "WHEAT": "Wheat",
}

# In-memory cache
_price_cache: dict = {}
_price_cache_ts: float = 0.0
CACHE_TTL = 900  # 15 minutes

# Credit tracking (resets daily)
_credits_used: int = 0
_credits_reset_date: str = ""


def _track_credit(n: int = 1):
    global _credits_used, _credits_reset_date
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if _credits_reset_date != today:
        _credits_used = 0
        _credits_reset_date = today
    _credits_used += n


def get_credits_used() -> dict:
    global _credits_used, _credits_reset_date
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if _credits_reset_date != today:
        _credits_used = 0
        _credits_reset_date = today
    return {"used": _credits_used, "limit": 800, "date": today}


async def get_live_prices() -> dict:
    """
    Batch-fetch all commodity prices in a single API call (1 credit).
    Uses /quote endpoint for price + change data.
    Results cached for 15 minutes.
    """
    global _price_cache, _price_cache_ts

    if not settings.twelvedata_api_key:
        return {"source": None, "prices": {}}

    now = time.monotonic()
    if _price_cache and (now - _price_cache_ts) < CACHE_TTL:
        return {"source": "twelvedata", "prices": _price_cache}

    td_symbols = ",".join(SYMBOLS.values())

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{BASE_URL}/quote",
                params={
                    "symbol": td_symbols,
                    "apikey": settings.twelvedata_api_key,
                },
            )
            resp.raise_for_status()
            data = resp.json()
        _track_credit(1)
    except httpx.HTTPError as e:
        logger.error(f"Twelve Data batch quote failed: {e}")
        return {"source": None, "prices": {}}

    # Handle API error response
    if "code" in data and data.get("status") == "error":
        logger.error(f"Twelve Data API error: {data.get('message', '')}")
        return {"source": None, "prices": {}}

    prices = {}
    for td_sym, our_label in _REVERSE.items():
        quote = data.get(td_sym)
        if not quote or quote.get("status") == "error":
            continue

        try:
            current = float(quote.get("close", 0))
            prev = float(quote.get("previous_close", 0))
            change = float(quote.get("change", 0))
            change_pct = float(quote.get("percent_change", 0))
            dt = quote.get("datetime", "")

            prices[our_label] = {
                "symbol": td_sym,
                "date": dt,
                "current": current,
                "previous_close": prev,
                "change": round(change, 4),
                "change_pct": round(change_pct, 4),
                "name": DISPLAY_NAMES.get(our_label, our_label),
            }
        except (ValueError, TypeError) as e:
            logger.warning(f"Twelve Data parse error for {td_sym}: {e}")
            continue

    if prices:
        _price_cache = prices
        _price_cache_ts = now
        logger.info(f"Twelve Data: cached {len(prices)} commodities")

    return {"source": "twelvedata", "prices": prices}


async def get_intraday(symbol: str, interval: str = "15min", outputsize: int = 96) -> dict:
    """
    Fetch intraday OHLCV time series for a single symbol.
    1 API credit per call.

    symbol: our label (WTI, BRENT, GOLD, etc.)
    interval: 1min, 5min, 15min, 30min, 1h, 2h, 4h
    outputsize: number of data points (max 5000)
    """
    if not settings.twelvedata_api_key:
        return {"source": None, "symbol": symbol, "interval": interval, "data": []}

    td_sym = SYMBOLS.get(symbol.upper())
    if not td_sym:
        return {"source": "twelvedata", "symbol": symbol, "interval": interval, "data": [], "error": f"Unknown symbol: {symbol}"}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{BASE_URL}/time_series",
                params={
                    "symbol": td_sym,
                    "interval": interval,
                    "outputsize": str(min(outputsize, 5000)),
                    "apikey": settings.twelvedata_api_key,
                },
            )
            resp.raise_for_status()
            data = resp.json()
        _track_credit(1)
    except httpx.HTTPError as e:
        logger.error(f"Twelve Data time_series failed for {td_sym}: {e}")
        return {"source": "twelvedata", "symbol": symbol, "interval": interval, "data": []}

    if data.get("status") == "error":
        logger.error(f"Twelve Data time_series error: {data.get('message', '')}")
        return {"source": "twelvedata", "symbol": symbol, "interval": interval, "data": []}

    values = data.get("values", [])
    ohlcv = []
    for v in values:
        try:
            ohlcv.append({
                "datetime": v["datetime"],
                "open": float(v["open"]),
                "high": float(v["high"]),
                "low": float(v["low"]),
                "close": float(v["close"]),
                "volume": int(v.get("volume", 0)),
            })
        except (ValueError, KeyError):
            continue

    # Twelve Data returns newest first, reverse for chronological order
    ohlcv.reverse()

    return {"source": "twelvedata", "symbol": symbol, "interval": interval, "data": ohlcv}
