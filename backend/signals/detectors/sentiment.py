"""Sentiment vertical detector — elevated news-risk score.

The 1-10 risk score is rule-based (derived from GDELT tone, no LLM) and
persisted in ``SentimentScore.risk_score``.
"""

from __future__ import annotations

import json

from backend.models.sentiment import SentimentScore
from backend.signals.detectors.base import DetectorResult, trailing_zscore

SENT_WINDOW = 30          # trailing days of risk-score history
SENT_ABS_EXTREME = 8.0    # absolute ceiling: always notable regardless of baseline
SENT_REL_FLOOR = 6.0      # for a relative "unusual jump", require at least moderate risk
SENT_REL_Z = 2.0


def detect_sentiment_risk(db) -> list[DetectorResult]:
    rows = db.query(SentimentScore).order_by(SentimentScore.date.desc()).limit(SENT_WINDOW + 1).all()
    if not rows:
        return []
    row = rows[0]

    # Two ways to be notable: an absolute extreme, OR an unusual jump vs the recent norm.
    elevated_abs = row.risk_score >= SENT_ABS_EXTREME
    stat = trailing_zscore(row.risk_score, [r.risk_score for r in rows[1:]])
    elevated_rel = stat is not None and stat[0] >= SENT_REL_Z and row.risk_score >= SENT_REL_FLOOR
    if not (elevated_abs or elevated_rel):
        return []
    severity = "warning" if elevated_abs else "info"

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
            as_of=row.date,
        )
    ]
