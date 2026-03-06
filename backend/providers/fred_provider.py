"""
FRED Provider — daily oil prices from FRED (via obsyd.db/fred_series).

Provides: get_live_prices() (daily only, no intraday)
"""

import logging

from backend.collectors.portwatch_store import query_oil_prices

logger = logging.getLogger(__name__)


async def get_live_prices() -> dict:
    """
    Get latest WTI/Brent from FRED daily series.
    """
    oil = query_oil_prices(days=10)
    prices = {}
    for series_id, label in [("DCOILWTICO", "WTI"), ("DCOILBRENTEU", "BRENT")]:
        data = oil.get(series_id, [])
        if len(data) >= 2:
            latest = data[-1]
            prev = data[-2]
            change = latest["value"] - prev["value"]
            change_pct = (change / prev["value"]) * 100 if prev["value"] else 0
            prices[label] = {
                "symbol": series_id,
                "date": latest["date"],
                "current": latest["value"],
                "previous_close": prev["value"],
                "change": round(change, 4),
                "change_pct": round(change_pct, 4),
            }
    return {"source": "fred", "prices": prices}


async def get_intraday(symbol: str, interval: str = "15min", outputsize: int = 96) -> dict:
    """FRED does not provide intraday data."""
    return {"source": "fred", "symbol": symbol, "interval": interval, "data": []}
