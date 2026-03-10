"""Tanker Equity Freight Proxy — implied freight index from tanker stocks.

Since the Baltic Dirty Tanker Index (BDTI) is not freely available, this builds
a proxy from tanker equities already tracked in EquitySnapshot (FRO, STNG, DHT, INSW).

Index: equal-weighted daily returns, cumulative from base 100.
Correlations: 30d rolling vs Brent (via WTI proxy) and Cape rerouting share.
Divergence: when financial markets diverge from physical rerouting signals.
"""

import logging
from datetime import date, timedelta

from sqlalchemy import func

from backend.database import SessionLocal
from backend.models.analytics import FreightProxyHistory, TonneMilesHistory
from backend.models.pro_features import EquitySnapshot

logger = logging.getLogger(__name__)

TANKER_TICKERS = ["FRO", "STNG", "DHT", "INSW"]


async def compute_freight_proxy():
    """Daily freight proxy calculation from tanker equities."""
    db = SessionLocal()
    try:
        # Get latest equity date with tanker data
        latest_date = db.query(func.max(EquitySnapshot.date)).filter(EquitySnapshot.ticker.in_(TANKER_TICKERS)).scalar()
        if not latest_date:
            logger.warning("No equity data for freight proxy")
            return

        existing = db.query(FreightProxyHistory).filter(FreightProxyHistory.date == latest_date).first()
        if existing:
            logger.info("Freight proxy already computed for %s", latest_date)
            return

        snaps = (
            db.query(EquitySnapshot)
            .filter(
                EquitySnapshot.date == latest_date,
                EquitySnapshot.ticker.in_(TANKER_TICKERS),
            )
            .all()
        )

        changes = {}
        for s in snaps:
            if s.change_pct is not None:
                changes[s.ticker] = s.change_pct

        if len(changes) < 2:
            logger.warning("Insufficient tanker data (%d tickers)", len(changes))
            return

        daily_change = sum(changes.values()) / len(changes)

        # Cumulative index from last known value
        last_record = db.query(FreightProxyHistory).order_by(FreightProxyHistory.date.desc()).first()
        if last_record:
            proxy_index = last_record.proxy_index * (1 + daily_change / 100)
        else:
            proxy_index = 100.0 * (1 + daily_change / 100)

        brent_corr = _avg_wti_correlation(db, latest_date)
        rerouting_corr = _rerouting_correlation(db)
        divergence = _check_divergence(db)

        record = FreightProxyHistory(
            date=latest_date,
            fro_change=changes.get("FRO"),
            stng_change=changes.get("STNG"),
            dht_change=changes.get("DHT"),
            insw_change=changes.get("INSW"),
            proxy_index=round(proxy_index, 2),
            brent_corr_30d=brent_corr,
            rerouting_corr_30d=rerouting_corr,
            divergence_flag=divergence,
        )
        db.add(record)
        db.commit()
        logger.info(
            "Freight proxy: index=%.1f, brent_r=%s, rerouting_r=%s, div=%s",
            proxy_index,
            f"{brent_corr:.2f}" if brent_corr else "N/A",
            f"{rerouting_corr:.2f}" if rerouting_corr else "N/A",
            divergence or "none",
        )
    except Exception as e:
        logger.error("Freight proxy computation failed: %s", e)
        db.rollback()
    finally:
        db.close()


def _avg_wti_correlation(db, as_of_date):
    """Average WTI 30d correlation across tanker tickers."""
    rows = (
        db.query(EquitySnapshot.wti_corr_30d)
        .filter(
            EquitySnapshot.date == as_of_date,
            EquitySnapshot.ticker.in_(TANKER_TICKERS),
            EquitySnapshot.wti_corr_30d.isnot(None),
        )
        .all()
    )
    corrs = [r[0] for r in rows]
    return round(sum(corrs) / len(corrs), 3) if corrs else None


def _rerouting_correlation(db):
    """30d Pearson correlation between proxy_index and cape_share."""
    d30 = (date.today() - timedelta(days=30)).isoformat()

    proxy_rows = (
        db.query(FreightProxyHistory.date, FreightProxyHistory.proxy_index)
        .filter(FreightProxyHistory.date >= d30)
        .order_by(FreightProxyHistory.date.asc())
        .all()
    )
    tm_rows = (
        db.query(TonneMilesHistory.date, TonneMilesHistory.cape_share)
        .filter(TonneMilesHistory.date >= d30)
        .order_by(TonneMilesHistory.date.asc())
        .all()
    )

    if len(proxy_rows) < 5 or len(tm_rows) < 5:
        return None

    tm_map = {r.date: r.cape_share for r in tm_rows}
    pairs = [(p.proxy_index, tm_map[p.date]) for p in proxy_rows if p.date in tm_map and tm_map[p.date] is not None]
    if len(pairs) < 5:
        return None

    xs, ys = zip(*pairs)
    return round(_pearson(list(xs), list(ys)), 3)


def _check_divergence(db):
    """Check freight proxy vs rerouting divergence over 5 days."""
    history = db.query(FreightProxyHistory).order_by(FreightProxyHistory.date.desc()).limit(6).all()
    if len(history) < 5:
        return None

    proxy_falling = history[0].proxy_index < history[4].proxy_index
    proxy_rising = history[0].proxy_index > history[4].proxy_index

    tm = db.query(TonneMilesHistory).order_by(TonneMilesHistory.date.desc()).first()
    if not tm or tm.cape_share is None:
        return None

    cape_pct = tm.cape_share * 100
    if cape_pct > 35 and proxy_falling:
        return "FREIGHT_PROXY_DIVERGENCE"
    if cape_pct < 25 and proxy_rising:
        return "FREIGHT_PROXY_LEADS"
    return None


def _pearson(x, y):
    """Simple Pearson correlation coefficient."""
    n = len(x)
    if n < 3:
        return 0.0
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    cov = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
    std_x = sum((xi - mean_x) ** 2 for xi in x) ** 0.5
    std_y = sum((yi - mean_y) ** 2 for yi in y) ** 0.5
    if std_x == 0 or std_y == 0:
        return 0.0
    return cov / (std_x * std_y)
