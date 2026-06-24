"""Energy price collector — daily close history via yfinance into `EnergyPrice`.

Mirrors `crack_spreads.py`: first run backfills a long window, thereafter only
the last few days. Upsert keyed on (date, symbol). Currently ingests TTF
(Dutch gas front-month); EUA / power day-ahead slot in via SYMBOLS later.

Scheduled daily at 22:15 UTC (after US close, like the crack/equity jobs).
"""

import logging
from concurrent.futures import ThreadPoolExecutor

import yfinance as yf

from backend.database import SessionLocal
from backend.models.energy import EnergyPrice

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=1)

# db_symbol -> yfinance ticker. Add "EUA"/"POWER_*" here as the energy vertical grows.
SYMBOLS = {
    "TTF": "TTF=F",  # Dutch TTF natural gas front-month (EUR/MWh)
}


def _store_symbol(db, db_symbol: str, ticker: str) -> int:
    """Fetch one ticker's daily closes and upsert into EnergyPrice. Returns rows inserted."""
    import pandas as pd

    have_any = (
        db.query(EnergyPrice).filter(EnergyPrice.symbol == db_symbol).first() is not None
    )
    period = "5d" if have_any else "max"
    if not have_any:
        logger.info("Energy prices: backfilling full history for %s (%s)", db_symbol, ticker)

    hist = yf.download(ticker, period=period, progress=False, auto_adjust=True)
    if hist.empty:
        logger.warning("Energy prices: %s (%s) returned empty data", db_symbol, ticker)
        return 0
    if isinstance(hist.columns, pd.MultiIndex):
        hist.columns = hist.columns.get_level_values(0)

    closes = hist["Close"].dropna()
    inserted = 0
    for idx, value in closes.items():
        date_str = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10]
        close = round(float(value), 4)
        existing = (
            db.query(EnergyPrice)
            .filter(EnergyPrice.date == date_str, EnergyPrice.symbol == db_symbol)
            .first()
        )
        if existing:
            existing.close = close
        else:
            db.add(EnergyPrice(date=date_str, symbol=db_symbol, close=close))
            inserted += 1
    return inserted


def _fetch_and_store():
    """Sync entry: fetch every configured symbol and upsert."""
    db = SessionLocal()
    try:
        total_new = 0
        for db_symbol, ticker in SYMBOLS.items():
            try:
                total_new += _store_symbol(db, db_symbol, ticker)
            except Exception as e:  # one bad ticker must not abort the rest
                logger.error("Energy prices: %s failed: %s", db_symbol, e)
        db.commit()
        total = db.query(EnergyPrice).count()
        logger.info("Energy prices: inserted %d new rows (total: %d)", total_new, total)
    except Exception as e:
        db.rollback()
        logger.error("Energy price collection failed: %s", e)
    finally:
        db.close()


async def collect_energy_prices():
    """Async entry point for the scheduler."""
    import asyncio

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(_executor, _fetch_and_store)
