"""
Crack Spread Collector — daily historical crack spread data via yfinance.

Scheduled daily at 22:00 UTC (after US market close).
First run backfills 1 year of history.
"""

import logging
from concurrent.futures import ThreadPoolExecutor

import yfinance as yf

from backend.database import SessionLocal
from backend.models.pro_features import CrackSpreadHistory

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=1)


def _calculate_gasoline_crack(rbob: float, wti: float) -> float:
    """Gasoline crack = RBOB ($/gal * 42) - WTI ($/bbl)."""
    return round(rbob * 42 - wti, 2)


def _calculate_ho_crack(ho: float, wti: float) -> float:
    """Heating oil crack = HO ($/gal * 42) - WTI ($/bbl)."""
    return round(ho * 42 - wti, 2)


def _calculate_321(rbob: float, ho: float, wti: float) -> float:
    """3:2:1 crack spread = (2 * gasoline_crack + 1 * ho_crack) / 3."""
    return round(((2 * rbob * 42) + (1 * ho * 42) - (3 * wti)) / 3, 2)


def _fetch_and_store():
    """Sync function: fetch yfinance history and store in DB."""
    db = SessionLocal()
    try:
        # Check how much history we already have
        latest = db.query(CrackSpreadHistory).order_by(CrackSpreadHistory.date.desc()).first()

        if latest:
            # Only fetch recent data (last 5 days to catch any gaps)
            period = "5d"
        else:
            # First run: backfill 1 year
            period = "1y"
            logger.info("Crack spreads: backfilling 1 year of history")

        # Fetch all three tickers
        import pandas as pd

        wti_hist = yf.download("CL=F", period=period, progress=False, auto_adjust=True)
        rbob_hist = yf.download("RB=F", period=period, progress=False, auto_adjust=True)
        ho_hist = yf.download("HO=F", period=period, progress=False, auto_adjust=True)

        if wti_hist.empty or rbob_hist.empty or ho_hist.empty:
            logger.warning("Crack spreads: one or more tickers returned empty data")
            return

        # Handle MultiIndex columns from yfinance
        for df in [wti_hist, rbob_hist, ho_hist]:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

        # Align on date
        combined = pd.DataFrame(
            {
                "wti": wti_hist["Close"],
                "rbob": rbob_hist["Close"],
                "ho": ho_hist["Close"],
            }
        ).dropna()

        if combined.empty:
            logger.warning("Crack spreads: no overlapping dates")
            return

        inserted = 0
        for idx, row in combined.iterrows():
            date_str = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10]
            wti = float(row["wti"])
            rbob = float(row["rbob"])
            ho = float(row["ho"])

            gasoline_crack = _calculate_gasoline_crack(rbob, wti)
            ho_crack = _calculate_ho_crack(ho, wti)
            spread_321 = _calculate_321(rbob, ho, wti)

            # Upsert: skip if date already exists
            existing = db.query(CrackSpreadHistory).filter(CrackSpreadHistory.date == date_str).first()
            if existing:
                # Update with latest values
                existing.wti_price = round(wti, 2)
                existing.rbob_price = round(rbob, 4)
                existing.ho_price = round(ho, 4)
                existing.gasoline_crack = gasoline_crack
                existing.heating_oil_crack = ho_crack
                existing.three_two_one_crack = spread_321
            else:
                db.add(
                    CrackSpreadHistory(
                        date=date_str,
                        wti_price=round(wti, 2),
                        rbob_price=round(rbob, 4),
                        ho_price=round(ho, 4),
                        gasoline_crack=gasoline_crack,
                        heating_oil_crack=ho_crack,
                        three_two_one_crack=spread_321,
                    )
                )
                inserted += 1

        db.commit()
        total = db.query(CrackSpreadHistory).count()
        logger.info("Crack spreads: inserted %d new rows (total: %d)", inserted, total)
    except Exception as e:
        db.rollback()
        logger.error("Crack spread collection failed: %s", e)
    finally:
        db.close()


async def collect_crack_spreads():
    """Async entry point for scheduler."""
    import asyncio

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(_executor, _fetch_and_store)
