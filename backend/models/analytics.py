"""Models for derived analytics — Tonne-Miles, Disruption Score, EIA Prediction."""

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class TonneMilesHistory(Base):
    __tablename__ = "tonne_miles_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    tonne_miles_raw: Mapped[float] = mapped_column(Float, nullable=False)
    tonne_miles_index: Mapped[float] = mapped_column(Float, nullable=False)
    cape_share: Mapped[float] = mapped_column(Float, nullable=False)
    tanker_count_by_class: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    avg_distance: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DisruptionScoreHistory(Base):
    __tablename__ = "disruption_score_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[str] = mapped_column(String, nullable=False, index=True)
    composite_score: Mapped[float] = mapped_column(Float, nullable=False)
    hormuz_component: Mapped[float] = mapped_column(Float, default=0)
    cape_component: Mapped[float] = mapped_column(Float, default=0)
    storage_component: Mapped[float] = mapped_column(Float, default=0)
    crack_component: Mapped[float] = mapped_column(Float, default=0)
    backwardation_component: Mapped[float] = mapped_column(Float, default=0)
    sentiment_component: Mapped[float] = mapped_column(Float, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class MarketReport(Base):
    __tablename__ = "market_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[str] = mapped_column(String, nullable=False, index=True)
    full_report: Mapped[str] = mapped_column(Text, nullable=False)
    sections_json: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    headlines_json: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    signals_count: Mapped[int] = mapped_column(Integer, default=0)
    disruption_score: Mapped[float] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class EIAPredictionHistory(Base):
    __tablename__ = "eia_prediction_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    prediction: Mapped[str] = mapped_column(String, nullable=False)  # BUILD / DRAW / NEUTRAL
    actual_eia_change: Mapped[float] = mapped_column(Float, nullable=True)
    correct: Mapped[int] = mapped_column(Integer, nullable=True)  # 1=correct, 0=wrong, NULL=pending
    tanker_count: Mapped[int] = mapped_column(Integer, nullable=False)
    tanker_count_30d_avg: Mapped[float] = mapped_column(Float, nullable=True)
    anchored_ratio: Mapped[float] = mapped_column(Float, nullable=True)
    anchored_ratio_30d_avg: Mapped[float] = mapped_column(Float, nullable=True)
    pearson_r: Mapped[float] = mapped_column(Float, nullable=True)
    optimal_lag_days: Mapped[int] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
