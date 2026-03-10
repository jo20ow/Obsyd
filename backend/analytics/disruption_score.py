"""
Supply Disruption Composite Score — 0-100 index combining 6 existing signals.

Measures how stressed the global oil supply chain is by combining:
  1. Hormuz transit drop (25%)
  2. Cape rerouting share (20%)
  3. Floating storage count (10%)
  4. Crack spread percentile (15%)
  5. Brent backwardation (15%)
  6. GDELT risk score (15%)

Scheduled: every 2 hours.
"""

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.database import SessionLocal
from backend.models.analytics import DisruptionScoreHistory
from backend.models.pro_features import CrackSpreadHistory
from backend.models.sentiment import SentimentScore
from backend.models.vessels import FloatingStorageEvent

logger = logging.getLogger(__name__)

PORTWATCH_DB = Path(__file__).parent.parent.parent / "data" / "portwatch.db"
HORMUZ_PORTID = "chokepoint6"

# Component weights
WEIGHTS = {
    "hormuz": 0.25,
    "cape": 0.20,
    "storage": 0.10,
    "crack": 0.15,
    "backwardation": 0.15,
    "sentiment": 0.15,
}


def _hormuz_component() -> float:
    """Score 0-100 based on Hormuz transit drop vs stable baseline.

    Uses the last 7 days of available data (crisis or not) and compares
    against a stable pre-crisis baseline: days 14-60 before latest date.
    This window avoids both PortWatch publication lag (days 0-7 gap)
    and any recent crisis onset (days 7-14 transition).
    """
    if not PORTWATCH_DB.exists():
        return 0.0

    conn = sqlite3.connect(str(PORTWATCH_DB))
    try:
        # Latest date with ANY Hormuz data
        last_row = conn.execute(
            "SELECT date FROM chokepoint_daily WHERE portid = ? ORDER BY date DESC LIMIT 1",
            (HORMUZ_PORTID,),
        ).fetchone()

        if not last_row:
            return 0.0

        last_date = last_row[0]
        last_dt = datetime.strptime(last_date, "%Y-%m-%d")

        # Recent: last 7 days of data
        recent_start = (last_dt - timedelta(days=6)).strftime("%Y-%m-%d")

        recent = conn.execute(
            "SELECT AVG(n_tanker) FROM chokepoint_daily WHERE portid = ? AND date >= ? AND date <= ?",
            (HORMUZ_PORTID, recent_start, last_date),
        ).fetchone()

        # Stable baseline: days 14-60 before latest (skips transition period)
        baseline_end = (last_dt - timedelta(days=14)).strftime("%Y-%m-%d")
        baseline_start = (last_dt - timedelta(days=60)).strftime("%Y-%m-%d")

        baseline = conn.execute(
            "SELECT AVG(n_tanker) FROM chokepoint_daily WHERE portid = ? AND date >= ? AND date <= ?",
            (HORMUZ_PORTID, baseline_start, baseline_end),
        ).fetchone()

        recent_val = recent[0] if recent and recent[0] is not None else 0
        baseline_val = baseline[0] if baseline and baseline[0] else 0

        if baseline_val <= 0:
            return 0.0

        drop_pct = ((baseline_val - recent_val) / baseline_val) * 100
        drop_pct = max(0, drop_pct)  # Only care about drops
        return min(100, drop_pct * 2)  # -50% drop = score 100
    finally:
        conn.close()


def _cape_component() -> float:
    """Score 0-100 based on Cape share percentage.

    Uses latest meaningful data to avoid PortWatch publication lag.
    """
    if not PORTWATCH_DB.exists():
        return 0.0

    conn = sqlite3.connect(str(PORTWATCH_DB))

    try:
        # Find latest date with meaningful data for both chokepoints
        last_good = conn.execute(
            "SELECT MAX(date) FROM chokepoint_daily WHERE portid IN ('chokepoint1', 'chokepoint7') AND n_tanker >= 5",
        ).fetchone()

        if not last_good or not last_good[0]:
            return 0.0

        cutoff = (datetime.strptime(last_good[0], "%Y-%m-%d") - timedelta(days=6)).strftime("%Y-%m-%d")
        end_date = last_good[0]

        suez = conn.execute(
            "SELECT AVG(n_tanker) FROM chokepoint_daily WHERE portid = 'chokepoint1' AND date >= ? AND date <= ?",
            (cutoff, end_date),
        ).fetchone()
        cape = conn.execute(
            "SELECT AVG(n_tanker) FROM chokepoint_daily WHERE portid = 'chokepoint7' AND date >= ? AND date <= ?",
            (cutoff, end_date),
        ).fetchone()

        s = suez[0] or 0
        c = cape[0] or 0
        combined = s + c
        if combined == 0:
            return 0.0

        share = c / combined
        if share < 0.20:
            return 0.0
        if share >= 0.40:
            return min(100, 50 + (share - 0.40) * 250)
        return (share - 0.20) / 0.20 * 50  # 20-40% maps to 0-50
    finally:
        conn.close()


def _storage_component(db) -> float:
    """Score 0-100 based on active floating storage events."""
    count = db.query(FloatingStorageEvent).filter(FloatingStorageEvent.status == "active").count()
    if count == 0:
        return 0.0
    if count <= 3:
        return 20.0
    if count <= 10:
        return 20 + (count - 3) / 7 * 30  # 3-10 → 20-50
    return min(100, 50 + (count - 10) * 3)  # 10+ → 50+


def _crack_component(db) -> float:
    """Score 0-100 based on 3-2-1 crack spread vs 1-year percentile."""
    rows = db.query(CrackSpreadHistory.three_two_one_crack).order_by(CrackSpreadHistory.date.desc()).limit(365).all()
    if len(rows) < 30:
        return 0.0

    values = sorted(r[0] for r in rows)
    current = rows[0][0]
    rank = sum(1 for v in values if v <= current)
    percentile = rank / len(values) * 100

    if percentile > 90:
        return 80.0
    if percentile > 75:
        return 50 + (percentile - 75) / 15 * 30  # 75-90 → 50-80
    if percentile > 50:
        return 20 + (percentile - 50) / 25 * 30  # 50-75 → 20-50
    return percentile / 50 * 20  # 0-50 → 0-20


def _backwardation_component() -> float:
    """Score 0-100 based on Brent front/next month spread."""
    try:
        from backend.signals.market_structure import _fetch_structure

        data = _fetch_structure()
        if not data or "BRENT" not in data.get("curves", {}):
            return 0.0

        brent = data["curves"]["BRENT"]
        spread_pct = brent.get("spread_pct", 0)

        if spread_pct >= 0:
            return 0.0  # Contango = no disruption signal
        abs_spread = abs(spread_pct)
        if abs_spread <= 1:
            return 20.0
        if abs_spread <= 2:
            return 20 + (abs_spread - 1) * 40  # 1-2% → 20-60
        if abs_spread <= 5:
            return 60 + (abs_spread - 2) / 3 * 40  # 2-5% → 60-100
        return 100.0
    except Exception as e:
        logger.debug("Backwardation component error: %s", e)
        return 0.0


def _sentiment_component(db) -> float:
    """Score 0-100 based on GDELT sentiment risk score (inverted)."""
    latest = db.query(SentimentScore).order_by(SentimentScore.created_at.desc()).first()
    if not latest:
        return 0.0

    risk = latest.risk_score
    if risk <= 3:
        return risk / 3 * 20  # 1-3 → 0-20
    if risk <= 6:
        return 20 + (risk - 3) / 3 * 30  # 4-6 → 20-50
    return 50 + (risk - 6) / 4 * 50  # 7-10 → 50-100


async def compute_disruption_score():
    """Compute and persist Supply Disruption Composite Score. Every 2 hours."""
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")

        hormuz = _hormuz_component()
        cape = _cape_component()
        storage = _storage_component(db)
        crack = _crack_component(db)
        backwardation = _backwardation_component()
        sentiment = _sentiment_component(db)

        composite = (
            hormuz * WEIGHTS["hormuz"]
            + cape * WEIGHTS["cape"]
            + storage * WEIGHTS["storage"]
            + crack * WEIGHTS["crack"]
            + backwardation * WEIGHTS["backwardation"]
            + sentiment * WEIGHTS["sentiment"]
        )

        db.add(
            DisruptionScoreHistory(
                date=today,
                composite_score=round(composite, 1),
                hormuz_component=round(hormuz, 1),
                cape_component=round(cape, 1),
                storage_component=round(storage, 1),
                crack_component=round(crack, 1),
                backwardation_component=round(backwardation, 1),
                sentiment_component=round(sentiment, 1),
            )
        )
        db.commit()

        logger.info(
            "Disruption score: %.1f (HOR=%.0f CAP=%.0f STO=%.0f CRA=%.0f BAC=%.0f SEN=%.0f)",
            composite,
            hormuz,
            cape,
            storage,
            crack,
            backwardation,
            sentiment,
        )
    except Exception as e:
        logger.error("Disruption score failed: %s", e)
        db.rollback()
    finally:
        db.close()
