"""Signal validation endpoints — track records + the disruption weight backtest.

GET /api/validation/scorecards         (public) — latest per-signal track record
GET /api/validation/disruption-weights (Pro)    — live weight backtest table
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.analytics.validation.weights import MIN_CONFIDENT_N, backtest_disruption
from backend.auth.dependencies import require_pro
from backend.database import get_db
from backend.models.validation import SignalScorecard

router = APIRouter(prefix="/api/validation", tags=["validation"])


@router.get("/scorecards")
async def get_scorecards(db: Session = Depends(get_db)):
    """Latest per-signal track record (one set per signal × horizon).

    Public, but deliberately honest: each card carries `n` and `confident`
    (n >= MIN_CONFIDENT_N). The frontend should show "building (n/30)" until a
    card is confident, and never present an unconfident card as a claim.
    """
    latest_as_of = db.query(func.max(SignalScorecard.as_of)).scalar()
    if not latest_as_of:
        return {"available": False, "min_confident_n": MIN_CONFIDENT_N, "signals": {}}

    rows = (
        db.query(SignalScorecard)
        .filter(SignalScorecard.as_of == latest_as_of)
        .order_by(SignalScorecard.signal.asc(), SignalScorecard.horizon_days.asc())
        .all()
    )
    signals: dict[str, list] = {}
    for r in rows:
        signals.setdefault(r.signal, []).append(
            {
                "horizon_days": r.horizon_days,
                "n": r.n,
                "ic": r.ic,
                "t_stat": r.t_stat,
                "p_value": r.p_value,
                "hit_rate": r.hit_rate,
                "mean_fwd_high": r.mean_fwd_high,
                "mean_fwd_base": r.mean_fwd_base,
                "confident": bool(r.confident),
            }
        )
    return {
        "available": True,
        "as_of": latest_as_of,
        "min_confident_n": MIN_CONFIDENT_N,
        "signals": signals,
    }


@router.get("/disruption-weights")
async def get_disruption_weights(
    horizon: int = Query(7, ge=1, le=60),
    _user=Depends(require_pro),
    db: Session = Depends(get_db),
):
    """Live disruption-score weight backtest at the requested horizon (Pro).

    Per-component IC + HAC t-stat, out-of-sample composite IC (current vs equal
    vs IC-fitted), and drop-one-out keep/drop verdicts. `confident` is False
    until there is enough matured history.
    """
    return backtest_disruption(db, horizon_days=horizon)
