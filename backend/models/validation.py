"""Persisted signal scorecards — the rolling track record per signal.

One row per (signal, horizon_days, as_of). Recomputed weekly from each
signal's *_history table vs forward Brent returns. See
docs/signal-validation.md and backend/analytics/validation/.
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class SignalScorecard(Base):
    __tablename__ = "signal_scorecards"
    __table_args__ = (UniqueConstraint("signal", "horizon_days", "as_of", name="uq_scorecard_signal_horizon_asof"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal: Mapped[str] = mapped_column(String, index=True)       # "disruption_score", ...
    horizon_days: Mapped[int] = mapped_column(Integer)            # 1 / 7 / 30
    as_of: Mapped[str] = mapped_column(String, index=True)        # YYYY-MM-DD of computation
    n: Mapped[int] = mapped_column(Integer, default=0)            # aligned observations
    mode: Mapped[str] = mapped_column(String, default="continuous")  # continuous | event
    ic: Mapped[float | None] = mapped_column(Float, nullable=True)         # Spearman rank IC
    hit_rate: Mapped[float | None] = mapped_column(Float, nullable=True)   # directional, top tercile
    mean_fwd_high: Mapped[float | None] = mapped_column(Float, nullable=True)  # fwd ret, signal-high
    mean_fwd_base: Mapped[float | None] = mapped_column(Float, nullable=True)  # unconditional baseline
    t_stat: Mapped[float | None] = mapped_column(Float, nullable=True)     # Newey-West HAC
    p_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    confident: Mapped[int] = mapped_column(Integer, default=0)    # 1 iff n >= MIN_CONFIDENT_N
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
