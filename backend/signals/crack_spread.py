"""
3:2:1 Crack Spread — refinery profitability indicator.

Formula: ((2 × RBOB × 42) + (1 × HO × 42) - (3 × WTI)) / 3
RBOB and HO are in $/gallon, WTI is $/barrel. 1 barrel = 42 gallons.
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import yfinance as yf

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=1)

# Cache
_crack_cache: dict | None = None
_crack_cache_ts: float = 0.0
CACHE_TTL = 300  # 5 minutes


def _calculate_spread(wti: float, rbob: float, ho: float) -> float:
    """3:2:1 crack spread in $/barrel."""
    return round(((2 * rbob * 42) + (1 * ho * 42) - (3 * wti)) / 3, 2)


def _fetch_crack_data() -> dict:
    """Fetch WTI, RBOB, HO prices and compute crack spread (sync, runs in thread)."""
    tickers = yf.Tickers("CL=F RB=F HO=F")

    prices = {}
    for sym, label in [("CL=F", "wti"), ("RB=F", "rbob"), ("HO=F", "ho")]:
        try:
            info = tickers.tickers[sym].fast_info
            prices[label] = info.last_price
            prices[f"{label}_prev"] = info.previous_close
        except Exception as e:
            logger.warning("Crack spread: failed to fetch %s: %s", sym, e)
            return None

    wti = prices.get("wti")
    rbob = prices.get("rbob")
    ho = prices.get("ho")

    if not all([wti, rbob, ho]):
        return None

    spread = _calculate_spread(wti, rbob, ho)

    # Historical averages from yfinance history
    avg_30d = None
    avg_90d = None
    percentile_1y = None
    try:
        hist = yf.Tickers("CL=F RB=F HO=F")
        wti_hist = hist.tickers["CL=F"].history(period="1y")
        rbob_hist = hist.tickers["RB=F"].history(period="1y")
        ho_hist = hist.tickers["HO=F"].history(period="1y")

        if not wti_hist.empty and not rbob_hist.empty and not ho_hist.empty:
            # Align on date
            import pandas as pd

            combined = pd.DataFrame(
                {
                    "wti": wti_hist["Close"],
                    "rbob": rbob_hist["Close"],
                    "ho": ho_hist["Close"],
                }
            ).dropna()

            if len(combined) > 0:
                combined["spread"] = combined.apply(
                    lambda r: _calculate_spread(r["wti"], r["rbob"], r["ho"]),
                    axis=1,
                )
                spreads = combined["spread"]

                if len(spreads) >= 30:
                    avg_30d = round(spreads.tail(30).mean(), 2)
                if len(spreads) >= 90:
                    avg_90d = round(spreads.tail(90).mean(), 2)
                if len(spreads) >= 60:
                    percentile_1y = int((spreads < spread).sum() / len(spreads) * 100)
    except Exception as e:
        logger.warning("Crack spread history failed: %s", e)

    return {
        "spread_321": spread,
        "wti": round(wti, 2),
        "rbob": round(rbob, 4),
        "ho": round(ho, 4),
        "rbob_barrel": round(rbob * 42, 2),
        "ho_barrel": round(ho * 42, 2),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "avg_30d": avg_30d,
        "avg_90d": avg_90d,
        "percentile_1y": percentile_1y,
    }


async def get_crack_spread() -> dict:
    """Get 3:2:1 crack spread with caching."""
    global _crack_cache, _crack_cache_ts

    now = time.monotonic()
    if _crack_cache and (now - _crack_cache_ts) < CACHE_TTL:
        return _crack_cache

    import asyncio

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(_executor, _fetch_crack_data)
    except Exception as e:
        logger.error("Crack spread fetch failed: %s", e)
        return _crack_cache or {"error": "unavailable"}

    if result:
        _crack_cache = result
        _crack_cache_ts = now

    return result or _crack_cache or {"error": "unavailable"}
