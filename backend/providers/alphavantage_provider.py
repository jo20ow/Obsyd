"""
Alpha Vantage Provider — wraps existing alphavantage.py collector.

Provides: get_live_prices() (daily, 15min cache)
Does NOT provide: get_intraday() (AV free tier has no commodity intraday)
"""

import logging

from backend.collectors.alphavantage import fetch_live_commodities
from backend.collectors.portwatch_store import query_oil_prices

logger = logging.getLogger(__name__)


async def get_live_prices() -> dict:
    """
    Fetch live commodity prices via Alpha Vantage.
    Returns standardized dict: {symbol: {price, change, change_pct, date}, ...}
    Falls back to FRED daily if AV is unavailable.
    """
    prices = await fetch_live_commodities()
    if prices:
        return {"source": "alphavantage", "prices": prices}

    # FRED fallback (daily oil prices from obsyd.db/fred_series)
    oil = query_oil_prices(days=10)
    fred_prices = {}
    for series_id, label in [("DCOILWTICO", "WTI"), ("DCOILBRENTEU", "BRENT")]:
        data = oil.get(series_id, [])
        if len(data) >= 2:
            latest = data[-1]
            prev = data[-2]
            change = latest["value"] - prev["value"]
            change_pct = (change / prev["value"]) * 100 if prev["value"] else 0
            fred_prices[label] = {
                "symbol": series_id,
                "date": latest["date"],
                "current": latest["value"],
                "previous_close": prev["value"],
                "change": round(change, 4),
                "change_pct": round(change_pct, 4),
            }
    if fred_prices:
        return {"source": "fred", "prices": fred_prices}

    return {"source": None, "prices": {}}


async def get_intraday(symbol: str, interval: str = "15min", outputsize: int = 96) -> dict:
    """Alpha Vantage free tier does not support commodity intraday."""
    return {"source": "alphavantage", "symbol": symbol, "interval": interval, "data": []}
