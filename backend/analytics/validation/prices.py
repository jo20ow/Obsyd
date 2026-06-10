"""Forward-return series from FRED daily oil prices.

Ground truth for signal validation. Mirrors the look-up discipline already
used in signals/alert_outcomes.py: prices are taken "on or before" a target
date (FRED skips weekends/holidays), and a horizon return is only emitted
once the price series has actually caught up to the target date — never
peeking at data that didn't exist yet.
"""

from __future__ import annotations

import bisect
from datetime import datetime, timedelta

import numpy as np

from backend.models.prices import FREDSeries

BRENT_SERIES = "DCOILBRENTEU"
WTI_SERIES = "DCOILWTICO"


def load_price_map(db, series_id: str = BRENT_SERIES) -> dict[str, float]:
    """date(YYYY-MM-DD) -> price for a FRED series, ascending."""
    rows = (
        db.query(FREDSeries.date, FREDSeries.value)
        .filter(FREDSeries.series_id == series_id)
        .order_by(FREDSeries.date.asc())
        .all()
    )
    return {r.date: float(r.value) for r in rows if r.value is not None}


def _on_or_before(sorted_dates: list[str], price_map: dict[str, float], target: str) -> float | None:
    idx = bisect.bisect_right(sorted_dates, target) - 1
    if idx < 0:
        return None
    return price_map[sorted_dates[idx]]


def forward_log_returns(
    price_map: dict[str, float],
    signal_dates: list[str],
    horizon_days: int,
) -> np.ndarray:
    """Forward log return over `horizon_days` calendar days for each signal date.

    return[i] = ln( P(date_i + h) / P(date_i) ), using on-or-before lookup at
    both ends. Emits nan when the price series hasn't reached date_i + h yet
    (so it is never computed with look-ahead) or when a price is missing/zero.
    """
    if not price_map:
        return np.full(len(signal_dates), np.nan)
    sorted_dates = sorted(price_map)
    last_known = sorted_dates[-1]
    out = np.full(len(signal_dates), np.nan)
    for i, d in enumerate(signal_dates):
        target = (datetime.strptime(d, "%Y-%m-%d") + timedelta(days=horizon_days)).strftime("%Y-%m-%d")
        if target > last_known:
            continue  # data hasn't caught up — leave nan, fill on a later run
        p0 = _on_or_before(sorted_dates, price_map, d)
        ph = _on_or_before(sorted_dates, price_map, target)
        if p0 and ph and p0 > 0 and ph > 0:
            out[i] = float(np.log(ph / p0))
    return out
