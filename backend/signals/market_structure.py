"""
Market Structure — Contango / Backwardation detection via futures curve.

Compares front-month vs next-month futures prices:
  Contango:       front < next  (storage profitable, bearish near-term)
  Backwardation:  front > next  (tight supply, bullish near-term)

Uses yfinance for real futures prices. No API key needed.
"""

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import yfinance as yf

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=1)

# CME month codes: F=Jan, G=Feb, H=Mar, J=Apr, K=May, M=Jun,
#                  N=Jul, Q=Aug, U=Sep, V=Oct, X=Nov, Z=Dec
_MONTH_CODES = "FGHJKMNQUVXZ"


def _next_month_symbol(prefix: str, exchange: str, offset: int = 2) -> str:
    """Generate next-month futures symbol based on current date.

    CL=F is always the front month. We need the month AFTER the front month.
    WTI offset=2, Brent offset=3 (ICE expires earlier).
    """
    now = datetime.now(timezone.utc)
    m = now.month + offset
    y = now.year
    if m > 12:
        m -= 12
        y += 1
    code = _MONTH_CODES[m - 1]
    yy = y % 100
    return f"{prefix}{code}{yy}.{exchange}"


def _get_contracts() -> dict:
    """Build contract config with auto-rotating next-month symbols.

    WTI (NYMEX): front month is ~1 month ahead, next = +2 months
    Brent (ICE): expires a month earlier, so next = +3 months
    """
    return {
        "WTI": {
            "front": "CL=F",
            "next": _next_month_symbol("CL", "NYM", offset=2),
            "unit": "$/bbl",
        },
        "BRENT": {
            "front": "BZ=F",
            "next": _next_month_symbol("BZ", "NYM", offset=3),
            "unit": "$/bbl",
        },
    }

# Cache
_cache: dict | None = None
_cache_ts: float = 0.0
_cache_lock: asyncio.Lock | None = None
CACHE_TTL = 600  # 10 minutes


def _get_lock() -> asyncio.Lock:
    global _cache_lock
    if _cache_lock is None:
        _cache_lock = asyncio.Lock()
    return _cache_lock


def _fetch_structure() -> dict:
    """Fetch front and next month prices for WTI + Brent (sync, runs in thread)."""
    contracts = _get_contracts()
    all_syms = []
    for c in contracts.values():
        all_syms.extend([c["front"], c["next"]])

    tickers = yf.Tickers(" ".join(all_syms))
    results = {}

    for name, cfg in contracts.items():
        try:
            front_t = tickers.tickers[cfg["front"]]
            next_t = tickers.tickers[cfg["next"]]

            front_price = front_t.fast_info.last_price
            next_price = next_t.fast_info.last_price

            if not front_price or not next_price or front_price <= 0 or next_price <= 0:
                logger.warning(f"market_structure: missing price for {name}")
                continue

            spread = round(next_price - front_price, 2)
            spread_pct = round(spread / front_price * 100, 2)

            if spread > 0.05:
                structure = "contango"
            elif spread < -0.05:
                structure = "backwardation"
            else:
                structure = "flat"

            results[name] = {
                "front_month": round(front_price, 2),
                "next_month": round(next_price, 2),
                "front_symbol": cfg["front"],
                "next_symbol": cfg["next"],
                "spread": spread,
                "spread_pct": spread_pct,
                "structure": structure,
                "unit": cfg["unit"],
            }
        except Exception as e:
            logger.warning(f"market_structure: {name} failed: {e}")

    return results


async def get_market_structure() -> dict:
    """Return current contango/backwardation state for WTI and Brent."""
    global _cache, _cache_ts

    now = time.monotonic()
    if _cache and (now - _cache_ts) < CACHE_TTL:
        return _cache

    async with _get_lock():
        # Double-check after acquiring lock
        now = time.monotonic()
        if _cache and (now - _cache_ts) < CACHE_TTL:
            return _cache

        loop = asyncio.get_event_loop()
        try:
            curves = await loop.run_in_executor(_executor, _fetch_structure)
        except Exception as e:
            logger.error(f"market_structure fetch failed: {e}")
            return {"source": "yfinance", "curves": {}, "summary": "unavailable"}

        # Determine overall market summary
        structures = [v["structure"] for v in curves.values()]
        if all(s == "backwardation" for s in structures):
            summary = "backwardation"
        elif all(s == "contango" for s in structures):
            summary = "contango"
        elif structures:
            summary = "mixed"
        else:
            summary = "unavailable"

        result = {
            "source": "yfinance",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "curves": curves,
            "summary": summary,
        }

        _cache = result
        _cache_ts = now
        return result
