"""
Rule-based Sentiment Risk Score from GDELT Tone data.

Score scale: 1 (low risk) to 10 (high risk)
Maps GDELT avg_tone (-10 to +10) to a risk score.

Runs every 6 hours via scheduler.
"""

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import func

from backend.database import SessionLocal
from backend.models.sentiment import GDELTVolume, SentimentScore

logger = logging.getLogger(__name__)


def _tone_to_risk(avg_tone: float) -> float:
    """Map GDELT tone to risk score (1-10). More negative = higher risk."""
    # GDELT tone ranges roughly -10 to +10
    # Energy news is typically negative (-3 to 0), so calibrate accordingly
    if avg_tone <= -5:
        return 9 + min(1, (-avg_tone - 5) / 5)  # 9-10
    elif avg_tone <= -3:
        return 7 + ((-avg_tone - 3) / 2)  # 7-9
    elif avg_tone <= -1:
        return 4 + ((-avg_tone - 1) / 2) * 3  # 4-7
    elif avg_tone <= 1:
        return 3 + (1 - avg_tone) / 2  # 2.5-4
    else:
        return max(1, 3 - avg_tone / 2)  # 1-3


def _classify_risk(score: float) -> list[str]:
    """Generate risk factors description based on score level."""
    factors = []
    if score >= 8:
        factors.append("Strongly negative energy news sentiment")
    elif score >= 6:
        factors.append("Negative energy news sentiment")
    elif score >= 4:
        factors.append("Moderately negative news tone")
    else:
        factors.append("Neutral to positive news sentiment")
    return factors


async def compute_sentiment_score():
    """Compute rule-based sentiment risk score from GDELT tone data."""
    db = SessionLocal()
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Get recent tone data (last 24h of records)
        recent = (
            db.query(
                func.avg(GDELTVolume.avg_tone).label("avg_tone"),
                func.sum(GDELTVolume.volume).label("total_volume"),
                func.count(GDELTVolume.id).label("n_records"),
            )
            .first()
        )

        if not recent or recent.n_records == 0 or recent.avg_tone is None:
            logger.info("Sentiment scorer: no GDELT data available")
            return

        avg_tone = recent.avg_tone
        total_volume = recent.total_volume or 0
        n_records = recent.n_records

        # Base risk from tone
        risk = _tone_to_risk(avg_tone)

        # Volume multiplier: if volume is significantly above average per record,
        # amplify the risk (lots of negative news = worse than a little negative news)
        avg_vol_per_record = total_volume / n_records if n_records > 0 else 0
        if avg_vol_per_record > 2.0 and risk > 5:
            risk = min(10, risk * 1.15)

        risk_score = round(max(1, min(10, risk)), 1)
        factors = _classify_risk(risk_score)
        factors.append(f"Avg tone: {avg_tone:.2f} ({n_records} data points)")

        # Upsert: update today's score or create new
        existing = db.query(SentimentScore).filter(SentimentScore.date == today).first()
        if existing:
            existing.risk_score = risk_score
            existing.risk_factors = json.dumps(factors)
            existing.source = "gdelt_tone"
        else:
            db.add(SentimentScore(
                date=today,
                risk_score=risk_score,
                risk_factors=json.dumps(factors),
                source="gdelt_tone",
            ))

        db.commit()
        logger.info(f"Sentiment score: {risk_score}/10 (tone={avg_tone:.2f})")

    except Exception as e:
        db.rollback()
        logger.error(f"Sentiment scoring failed: {e}")
    finally:
        db.close()
