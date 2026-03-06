"""
Yahoo Finance Provider — real commodity futures prices via yfinance.

No API key required. No rate limits.
Provides: get_live_prices() and get_intraday()

Symbols:
  CL=F  WTI Crude Oil Futures
  BZ=F  Brent Crude Oil Futures
  NG=F  Natural Gas Futures
  GC=F  Gold Futures
  SI=F  Silver Futures
  HG=F  Copper Futures
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import yfinance as yf

logger = logging.getLogger(__name__)

SYMBOLS = {
    "WTI": "CL=F",
    "BRENT": "BZ=F",
    "NG": "NG=F",
    "GOLD": "GC=F",
    "SILVER": "SI=F",
    "COPPER": "HG=F",
}

DISPLAY_NAMES = {
    "WTI": "WTI Crude (CL=F)",
    "BRENT": "Brent Crude (BZ=F)",
    "NG": "Natural Gas (NG=F)",
    "GOLD": "Gold (GC=F)",
    "SILVER": "Silver (SI=F)",
    "COPPER": "Copper (HG=F)",
}

_REVERSE = {v: k for k, v in SYMBOLS.items()}

# Cache
_price_cache: dict = {}
_price_cache_ts: float = 0.0
CACHE_TTL = 300  # 5 minutes

_executor = ThreadPoolExecutor(max_workers=2)


def _fetch_quotes() -> dict:
    """Fetch all commodity quotes in one batch (runs in thread — yfinance is sync)."""
    tickers_str = " ".join(SYMBOLS.values())
    tickers = yf.Tickers(tickers_str)
    prices = {}

    for label, yf_sym in SYMBOLS.items():
        try:
            t = tickers.tickers[yf_sym]
            info = t.fast_info
            last = info.last_price
            prev = info.previous_close
            if not last or last <= 0:
                continue
            change = last - prev if prev else 0
            change_pct = (change / prev * 100) if prev else 0
            prices[label] = {
                "symbol": yf_sym,
                "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "current": round(last, 4),
                "previous_close": round(prev, 4) if prev else 0,
                "change": round(change, 4),
                "change_pct": round(change_pct, 4),
                "name": DISPLAY_NAMES.get(label, label),
            }
        except Exception as e:
            logger.warning(f"yfinance quote failed for {yf_sym}: {e}")
            continue

    return prices


async def get_live_prices() -> dict:
    global _price_cache, _price_cache_ts

    now = time.monotonic()
    if _price_cache and (now - _price_cache_ts) < CACHE_TTL:
        return {"source": "yfinance", "prices": _price_cache}

    import asyncio
    loop = asyncio.get_event_loop()
    try:
        prices = await loop.run_in_executor(_executor, _fetch_quotes)
    except Exception as e:
        logger.error(f"yfinance batch fetch failed: {e}")
        return {"source": None, "prices": {}}

    if prices:
        _price_cache = prices
        _price_cache_ts = now
        logger.info(f"yfinance: cached {len(prices)} commodity prices")

    return {"source": "yfinance", "prices": prices}


def _fetch_intraday(yf_sym: str, interval: str, period: str) -> list[dict]:
    """Fetch intraday OHLCV (runs in thread)."""
    t = yf.Ticker(yf_sym)
    df = t.history(interval=interval, period=period)
    if df.empty:
        return []
    ohlcv = []
    for idx, row in df.iterrows():
        ohlcv.append({
            "datetime": idx.strftime("%Y-%m-%d %H:%M:%S"),
            "open": round(float(row["Open"]), 4),
            "high": round(float(row["High"]), 4),
            "low": round(float(row["Low"]), 4),
            "close": round(float(row["Close"]), 4),
            "volume": int(row.get("Volume", 0)),
        })
    return ohlcv


# Map TD-style intervals to yfinance interval + period
_INTERVAL_MAP = {
    "1min": ("1m", "1d"),
    "5min": ("5m", "5d"),
    "15min": ("15m", "5d"),
    "30min": ("30m", "5d"),
    "1h": ("1h", "1mo"),
    "1day": ("1d", "3mo"),
}


async def get_intraday(symbol: str, interval: str = "15min", outputsize: int = 96) -> dict:
    yf_sym = SYMBOLS.get(symbol.upper())
    if not yf_sym:
        return {"source": "yfinance", "symbol": symbol, "interval": interval, "data": [],
                "error": f"Unknown symbol: {symbol}. Available: {', '.join(SYMBOLS.keys())}"}

    yf_interval, yf_period = _INTERVAL_MAP.get(interval, ("15m", "5d"))

    import asyncio
    loop = asyncio.get_event_loop()
    try:
        ohlcv = await loop.run_in_executor(_executor, _fetch_intraday, yf_sym, yf_interval, yf_period)
    except Exception as e:
        logger.error(f"yfinance intraday failed for {yf_sym}: {e}")
        return {"source": "yfinance", "symbol": symbol, "interval": interval, "data": []}

    if outputsize and len(ohlcv) > outputsize:
        ohlcv = ohlcv[-outputsize:]

    return {
        "source": "yfinance",
        "symbol": symbol,
        "interval": interval,
        "data": ohlcv,
        "is_proxy": False,
        "proxy_symbol": None,
    }
