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


class FreightProxyHistory(Base):
    __tablename__ = "freight_proxy_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    fro_change: Mapped[float | None] = mapped_column(Float, nullable=True)
    stng_change: Mapped[float | None] = mapped_column(Float, nullable=True)
    dht_change: Mapped[float | None] = mapped_column(Float, nullable=True)
    insw_change: Mapped[float | None] = mapped_column(Float, nullable=True)
    proxy_index: Mapped[float] = mapped_column(Float, nullable=False)
    brent_corr_30d: Mapped[float | None] = mapped_column(Float, nullable=True)
    rerouting_corr_30d: Mapped[float | None] = mapped_column(Float, nullable=True)
    divergence_flag: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SupplyDemandBalance(Base):
    __tablename__ = "supply_demand_balance"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    world_production: Mapped[float | None] = mapped_column(Float, nullable=True)
    world_consumption: Mapped[float | None] = mapped_column(Float, nullable=True)
    implied_balance: Mapped[float | None] = mapped_column(Float, nullable=True)
    us_imports_eia: Mapped[float | None] = mapped_column(Float, nullable=True)
    houston_ais_tankers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    houston_deviation: Mapped[float | None] = mapped_column(Float, nullable=True)
    divergence_type: Mapped[str | None] = mapped_column(String, nullable=True)
    divergence_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DaysOfSupplyHistory(Base):
    __tablename__ = "days_of_supply_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    commercial_stocks: Mapped[float | None] = mapped_column(Float, nullable=True)
    spr_stocks: Mapped[float | None] = mapped_column(Float, nullable=True)
    product_supplied: Mapped[float | None] = mapped_column(Float, nullable=True)
    commercial_days: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_days: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_5y_days: Mapped[float | None] = mapped_column(Float, nullable=True)
    deviation: Mapped[float | None] = mapped_column(Float, nullable=True)
    trend_4w: Mapped[float | None] = mapped_column(Float, nullable=True)
    assessment: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
