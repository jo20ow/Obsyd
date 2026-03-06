"""
Twelve Data Provider — commodity prices via best available symbols.

Free Tier limitations:
  - 800 credits/day, 8 credits/minute
  - NO commodity futures (CL, BZ, NG, HG)
  - NO precious metals forex except XAU/USD
  - ETFs and US stocks available

Strategy:
  - XAU/USD for gold (real spot price, free tier)
  - Oil/gas ETFs as price-action proxies (not real $/bbl prices)
  - Intraday charts work for all symbols
"""

import logging
import time
from datetime import datetime, timezone

import httpx

from backend.config import settings

logger = logging.getLogger(__name__)

BASE_URL = "https://api.twelvedata.com"

# Internal label -> Twelve Data symbol
# Note: ETF prices ≠ commodity spot prices. They track direction/momentum.
SYMBOLS = {
    "GOLD": "XAU/USD",     # Gold spot (real $/oz price)
    "WTI_ETF": "USO",      # WTI Oil ETF (price-action proxy)
    "BRENT_ETF": "BNO",    # Brent Oil ETF (price-action proxy)
    "NG_ETF": "UNG",       # Natural Gas ETF (price-action proxy)
    "SILVER_ETF": "SLV",   # Silver ETF (price-action proxy)
    "COPPER_ETF": "COPX",  # Copper miners ETF (price-action proxy)
}

# For intraday chart requests — maps user-facing symbol to TD symbol
INTRADAY_SYMBOLS = {
    "WTI": "USO",
    "BRENT": "BNO",
    "NG": "UNG",
    "GOLD": "XAU/USD",
    "SILVER": "SLV",
    "COPPER": "COPX",
}

_REVERSE = {v: k for k, v in SYMBOLS.items()}

DISPLAY_NAMES = {
    "GOLD": "Gold Spot (XAU/USD)",
    "WTI_ETF": "WTI Oil ETF (USO)",
    "BRENT_ETF": "Brent Oil ETF (BNO)",
    "NG_ETF": "Nat Gas ETF (UNG)",
    "SILVER_ETF": "Silver ETF (SLV)",
    "COPPER_ETF": "Copper ETF (COPX)",
}

# In-memory cache
_price_cache: dict = {}
_price_cache_ts: float = 0.0
CACHE_TTL = 900  # 15 minutes

# Credit tracking
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
    Fetch prices via Twelve Data.
    Only GOLD (XAU/USD) is a real spot price.
    ETF prices are included for metals/energy panel display.
    Results cached for 15 minutes.
    """
    global _price_cache, _price_cache_ts

    if not settings.twelvedata_api_key:
        return {"source": None, "prices": {}}

    now = time.monotonic()
    if _price_cache and (now - _price_cache_ts) < CACHE_TTL:
        return {"source": "twelvedata", "prices": _price_cache}

    td_symbols = ",".join(SYMBOLS.values())  # 6 symbols = 6 credits

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(
                f"{BASE_URL}/quote",
                params={
                    "symbol": td_symbols,
                    "apikey": settings.twelvedata_api_key,
                },
            )
            resp.raise_for_status()
            data = resp.json()
        _track_credit(len(SYMBOLS))
    except httpx.HTTPError as e:
        logger.error(f"Twelve Data batch quote failed: {e}")
        return {"source": None, "prices": {}}

    if isinstance(data, dict) and data.get("status") == "error":
        logger.warning(f"Twelve Data API: {data.get('message', '')}")
        return {"source": None, "prices": {}}

    prices = {}
    for td_sym, our_label in _REVERSE.items():
        quote = data.get(td_sym)
        if not quote or (isinstance(quote, dict) and quote.get("status") == "error"):
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
                "current": round(current, 4),
                "previous_close": round(prev, 4),
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
        logger.info(f"Twelve Data: cached {len(prices)} symbols ({len(SYMBOLS)} credits)")

    return {"source": "twelvedata", "prices": prices}


async def get_intraday(symbol: str, interval: str = "15min", outputsize: int = 96) -> dict:
    """
    Fetch intraday OHLCV time series. 1 credit per call.
    Uses ETF proxies for intraday price-action charts.
    """
    if not settings.twelvedata_api_key:
        return {"source": None, "symbol": symbol, "interval": interval, "data": []}

    td_sym = INTRADAY_SYMBOLS.get(symbol.upper())
    if not td_sym:
        return {"source": "twelvedata", "symbol": symbol, "interval": interval, "data": [],
                "error": f"Unknown symbol: {symbol}. Available: {', '.join(INTRADAY_SYMBOLS.keys())}"}

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
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
        logger.warning(f"Twelve Data time_series: {data.get('message', '')}")
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

    ohlcv.reverse()

    return {"source": "twelvedata", "symbol": symbol, "interval": interval, "data": ohlcv}
