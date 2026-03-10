"""
Related Equities Collector — daily snapshots with correlations.

Scheduled daily at 22:30 UTC (after market close).
Fetches price, change, 52w range, market cap, and WTI/Brent correlations.
"""

import logging
import time as _time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import yfinance as yf

from backend.database import SessionLocal
from backend.models.pro_features import EquitySnapshot

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=1)

EQUITY_UNIVERSE = [
    # Majors
    {"ticker": "XOM", "name": "ExxonMobil", "sector": "Majors"},
    {"ticker": "CVX", "name": "Chevron", "sector": "Majors"},
    {"ticker": "SHEL", "name": "Shell", "sector": "Majors"},
    {"ticker": "TTE", "name": "TotalEnergies", "sector": "Majors"},
    {"ticker": "BP", "name": "BP", "sector": "Majors"},
    # E&P
    {"ticker": "COP", "name": "ConocoPhillips", "sector": "E&P"},
    {"ticker": "EOG", "name": "EOG Resources", "sector": "E&P"},
    # Services
    {"ticker": "SLB", "name": "Schlumberger", "sector": "Services"},
    {"ticker": "HAL", "name": "Halliburton", "sector": "Services"},
    # Tanker
    {"ticker": "FRO", "name": "Frontline", "sector": "Tanker"},
    {"ticker": "STNG", "name": "Scorpio Tankers", "sector": "Tanker"},
    {"ticker": "DHT", "name": "DHT Holdings", "sector": "Tanker"},
    {"ticker": "INSW", "name": "Intl Seaways", "sector": "Tanker"},
    # LNG
    {"ticker": "GLNG", "name": "Golar LNG", "sector": "LNG"},
    {"ticker": "FLNG", "name": "FLEX LNG", "sector": "LNG"},
]


def _compute_correlation(stock_prices, benchmark_prices, window: int) -> float | None:
    """Compute Pearson correlation of daily returns over N trading days."""
    import pandas as pd

    if stock_prices is None or benchmark_prices is None:
        return None

    # Normalize timezone: strip tz info to avoid join errors
    stock = stock_prices.copy()
    bench = benchmark_prices.copy()
    if hasattr(stock.index, "tz") and stock.index.tz is not None:
        stock.index = stock.index.tz_localize(None)
    if hasattr(bench.index, "tz") and bench.index.tz is not None:
        bench.index = bench.index.tz_localize(None)

    stock_ret = stock.pct_change().dropna()
    bench_ret = bench.pct_change().dropna()

    # Align on index
    aligned = pd.DataFrame({"stock": stock_ret, "bench": bench_ret}).dropna()
    if len(aligned) < window:
        return None

    corr = aligned.tail(window)["stock"].corr(aligned.tail(window)["bench"])
    if corr != corr:  # NaN check
        return None
    return round(corr, 3)


def _fetch_and_store():
    """Sync: fetch equity data from yfinance and store snapshots."""
    db = SessionLocal()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        # First fetch WTI and Brent history for correlations
        logger.info("Equities: fetching WTI/Brent benchmarks for correlation")
        wti_hist = yf.download("CL=F", period="6mo", progress=False, auto_adjust=True)
        _time.sleep(1)
        brent_hist = yf.download("BZ=F", period="6mo", progress=False, auto_adjust=True)
        _time.sleep(1)

        import pandas as pd

        # Handle MultiIndex columns
        for df in [wti_hist, brent_hist]:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

        # Normalize timezone on benchmark data
        for df in [wti_hist, brent_hist]:
            if hasattr(df.index, "tz") and df.index.tz is not None:
                df.index = df.index.tz_localize(None)

        wti_close = wti_hist["Close"] if not wti_hist.empty else None
        brent_close = brent_hist["Close"] if not brent_hist.empty else None

        inserted = 0
        for entry in EQUITY_UNIVERSE:
            ticker = entry["ticker"]
            try:
                stock = yf.Ticker(ticker)
                info = stock.fast_info

                price = info.last_price
                prev = info.previous_close
                if not price or price <= 0:
                    logger.debug("Equities: skipping %s (no price)", ticker)
                    _time.sleep(2)
                    continue

                change_pct = round(((price - prev) / prev * 100), 2) if prev else 0.0

                # 52-week range
                high_52w = getattr(info, "year_high", None)
                low_52w = getattr(info, "year_low", None)
                market_cap = getattr(info, "market_cap", None)

                # Correlations: fetch 6mo stock history
                stock_hist = stock.history(period="6mo")
                if isinstance(stock_hist.columns, pd.MultiIndex):
                    stock_hist.columns = stock_hist.columns.get_level_values(0)
                stock_close = stock_hist["Close"] if not stock_hist.empty else None

                wti_corr = _compute_correlation(stock_close, wti_close, 30)
                brent_corr = _compute_correlation(stock_close, brent_close, 90)

                # Upsert
                existing = (
                    db.query(EquitySnapshot)
                    .filter(
                        EquitySnapshot.date == today,
                        EquitySnapshot.ticker == ticker,
                    )
                    .first()
                )

                if existing:
                    existing.price = round(price, 2)
                    existing.change_pct = change_pct
                    existing.wti_corr_30d = wti_corr
                    existing.brent_corr_90d = brent_corr
                    existing.high_52w = round(high_52w, 2) if high_52w else None
                    existing.low_52w = round(low_52w, 2) if low_52w else None
                    existing.market_cap = market_cap
                else:
                    db.add(
                        EquitySnapshot(
                            date=today,
                            ticker=ticker,
                            name=entry["name"],
                            sector=entry["sector"],
                            price=round(price, 2),
                            change_pct=change_pct,
                            wti_corr_30d=wti_corr,
                            brent_corr_90d=brent_corr,
                            high_52w=round(high_52w, 2) if high_52w else None,
                            low_52w=round(low_52w, 2) if low_52w else None,
                            market_cap=market_cap,
                        )
                    )
                    inserted += 1

                logger.debug(
                    "Equities: %s $%.2f (%+.1f%%) WTI_corr=%.2f",
                    ticker,
                    price,
                    change_pct,
                    wti_corr or 0,
                )
            except Exception as e:
                logger.warning("Equities: failed for %s: %s", ticker, e)

            # Rate limiting: 2s between each ticker
            _time.sleep(2)

        db.commit()
        logger.info("Equities: inserted %d snapshots for %s", inserted, today)
    except Exception as e:
        db.rollback()
        logger.error("Equity collection failed: %s", e)
    finally:
        db.close()


async def collect_equities():
    """Async entry point for scheduler."""
    import asyncio

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(_executor, _fetch_and_store)
