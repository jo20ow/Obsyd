"""Sentiment vertical detector — elevated news-risk score.

The 1-10 risk score is rule-based (derived from GDELT tone, no LLM) and
persisted in ``SentimentScore.risk_score``.
"""

from __future__ import annotations

import json

from backend.models.sentiment import SentimentScore
from backend.signals.detectors.base import DetectorResult


def detect_sentiment_risk(db) -> list[DetectorResult]:
    row = db.query(SentimentScore).order_by(SentimentScore.date.desc()).first()
    if row is None or row.risk_score < 6:
        return []
    severity = "warning" if row.risk_score >= 8 else "info"

    top_factor = ""
    if row.risk_factors:
        try:
            factors = json.loads(row.risk_factors)
            if isinstance(factors, list) and factors:
                top_factor = f" Top factor: {factors[0]}"
        except (ValueError, TypeError):
            top_factor = ""

    return [
        DetectorResult(
            rule="sentiment_risk",
            zone="",
            vertical="sentiment",
            severity=severity,
            title=f"News risk {row.risk_score:.0f}/10 — elevated media negativity",
            detail=f"GDELT-derived geopolitical/energy news risk at {row.risk_score:.0f}/10 ({row.date}).{top_factor}",
        )
    ]
