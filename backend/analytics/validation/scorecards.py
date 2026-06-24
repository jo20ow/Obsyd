"""Compute and persist per-signal scorecards.

For each continuous signal (a `*_history` table with a scalar level) and each
horizon, measure the forward-Brent-return relationship: rank IC, HAC t-stat,
and a top-tercile event study (mean forward return when the signal is high vs
the unconditional baseline). Results are upserted into `signal_scorecards`,
one row per (signal, horizon, as_of).

Honest by construction: n < MIN_CONFIDENT_N is never flagged confident; the
p-value is HAC-robust so overlapping windows don't masquerade as significance.
"""

from __future__ import annotations

import logging
import math

import numpy as np

from backend.analytics.validation import metrics
from backend.analytics.validation.prices import (
    BRENT_SERIES,
    TTF_SYMBOL,
    forward_log_returns,
    load_energy_price_map,
    load_price_map,
)
from backend.analytics.validation.weights import MIN_CONFIDENT_N

logger = logging.getLogger(__name__)

HORIZONS = (1, 7, 30)
TOP_TERCILE_Q = 2 / 3  # "signal high" = top third of its own distribution

# Continuous signals: (name, history-table, scalar column, forward-return target).
# target ∈ {"brent" (FRED oil), "ttf" (EnergyPrice gas)}. Models are resolved
# lazily inside the loader to avoid import cycles at module load.
SIGNAL_SPECS = (
    ("disruption_score", "DisruptionScoreHistory", "composite_score", "brent"),
    ("tonne_miles", "TonneMilesHistory", "tonne_miles_index", "brent"),
    ("freight_proxy", "FreightProxyHistory", "proxy_index", "brent"),
    # Gas residual predicts European GAS prices, not Brent → scored against TTF.
    ("gas_residual", "GasBalance", "z_score", "ttf"),
)


def _load_target_map(db, target: str) -> dict[str, float]:
    """Resolve a signal's forward-return target to a date→price map."""
    if target == "ttf":
        return load_energy_price_map(db, TTF_SYMBOL)
    return load_price_map(db, BRENT_SERIES)  # default / "brent"


def _two_sided_p(t_stat: float) -> float | None:
    """Normal-approx two-sided p-value for a t/z statistic (large-sample)."""
    if t_stat is None or (isinstance(t_stat, float) and math.isnan(t_stat)):
        return None
    # P(|Z| > |t|) = 2 * (1 - Phi(|t|)); Phi via erf, stdlib only.
    return float(2.0 * (1.0 - 0.5 * (1.0 + math.erf(abs(t_stat) / math.sqrt(2.0)))))


def _resolve_model(table_name: str):
    """Find a history model class by name across the analytics + gas modules."""
    from backend.models import analytics as analytics_models

    if hasattr(analytics_models, table_name):
        return getattr(analytics_models, table_name)
    from backend.models import gas as gas_models

    return getattr(gas_models, table_name)


def load_signal_series(db, table_name: str, value_col: str) -> tuple[list[str], np.ndarray]:
    """Return (dates, values) for a signal, one observation per day (last row
    of each day — dedupes sub-daily cadences like the 2-hourly disruption score).

    Tables with a `created_at` (the analytics history tables) are ordered by it
    so the last write per day wins; tables keyed one-row-per-day (GasBalance,
    whose `date` is the PK) are read directly."""
    model = _resolve_model(table_name)
    has_created = hasattr(model, "created_at")
    cols = [model.date, getattr(model, value_col)]
    order = [model.date.asc()]
    if has_created:
        cols.append(model.created_at)
        order.append(model.created_at.asc())
    rows = db.query(*cols).order_by(*order).all()

    by_day: dict[str, float] = {}
    for row in rows:
        date_str, value = row[0], row[1]
        if value is not None:
            by_day[date_str] = float(value)  # last write per day wins
    dates = sorted(by_day)
    return dates, np.array([by_day[d] for d in dates], dtype=float)


def score_signal(name: str, dates: list[str], values: np.ndarray, price_map: dict, horizon: int) -> dict:
    """Compute one scorecard dict for a signal at a horizon (no DB writes)."""
    fwd = forward_log_returns(price_map, dates, horizon)
    mask = np.isfinite(values) & np.isfinite(fwd)
    n = int(mask.sum())
    sig, ret = values[mask], fwd[mask]

    card = {
        "signal": name,
        "horizon_days": horizon,
        "n": n,
        "mode": "continuous",
        "ic": None,
        "hit_rate": None,
        "mean_fwd_high": None,
        "mean_fwd_base": None,
        "t_stat": None,
        "p_value": None,
        "confident": 1 if n >= MIN_CONFIDENT_N else 0,
    }
    if n < 3:
        return card

    ic = metrics.spearman_ic(sig, ret)
    t = metrics.newey_west_tstat(sig, ret, lag=horizon - 1)
    threshold = float(np.quantile(sig, TOP_TERCILE_Q))
    es = metrics.event_study(sig, ret, threshold, direction="above")

    card.update(
        ic=_clean(ic),
        t_stat=_clean(t),
        p_value=_two_sided_p(t),
        hit_rate=_clean(es["hit_rate"]),
        mean_fwd_high=_clean(es["mean_event"]),
        mean_fwd_base=_clean(es["mean_base"]),
    )
    return card


def _clean(x):
    if x is None:
        return None
    try:
        if np.isnan(x):
            return None
    except TypeError:
        return None
    return float(x)


def recompute_scorecards(db, as_of: str) -> list[dict]:
    """Compute every signal × horizon and upsert into signal_scorecards for
    `as_of` (YYYY-MM-DD). Returns the computed cards."""
    from backend.models.validation import SignalScorecard

    # Build one price map per distinct target (Brent via FRED, TTF via EnergyPrice).
    price_maps = {target: _load_target_map(db, target) for target in {s[3] for s in SIGNAL_SPECS}}
    cards: list[dict] = []

    for name, table_name, value_col, target in SIGNAL_SPECS:
        try:
            dates, values = load_signal_series(db, table_name, value_col)
        except Exception as e:
            logger.warning("scorecards: load failed for %s: %s", name, e)
            continue
        if len(dates) == 0:
            continue
        price_map = price_maps[target]
        for horizon in HORIZONS:
            card = score_signal(name, dates, values, price_map, horizon)
            cards.append(card)
            _upsert(db, SignalScorecard, card, as_of)

    db.commit()
    logger.info("scorecards: recomputed %d cards as_of %s", len(cards), as_of)
    return cards


def _upsert(db, model, card: dict, as_of: str) -> None:
    existing = (
        db.query(model)
        .filter(model.signal == card["signal"], model.horizon_days == card["horizon_days"], model.as_of == as_of)
        .first()
    )
    fields = dict(card, as_of=as_of)
    if existing:
        for key, val in fields.items():
            setattr(existing, key, val)
    else:
        db.add(model(**fields))


async def recompute_scorecards_job() -> dict:
    """Scheduler entry point. Never raises."""
    from datetime import datetime, timezone

    from backend.database import SessionLocal

    as_of = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    db = SessionLocal()
    try:
        cards = recompute_scorecards(db, as_of)
        return {"as_of": as_of, "count": len(cards)}
    except Exception as e:
        logger.error("scorecards job failed: %s", e)
        db.rollback()
        return {"as_of": as_of, "count": 0, "error": str(e)}
    finally:
        db.close()
