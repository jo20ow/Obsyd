"""Models for Pro features: Crack Spread History, Equity Snapshots, Email Subscribers."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class CrackSpreadHistory(Base):
    __tablename__ = "crack_spread_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    wti_price: Mapped[float] = mapped_column(Float, nullable=False)
    rbob_price: Mapped[float] = mapped_column(Float, nullable=False)
    ho_price: Mapped[float] = mapped_column(Float, nullable=False)
    gasoline_crack: Mapped[float] = mapped_column(Float, nullable=False)
    heating_oil_crack: Mapped[float] = mapped_column(Float, nullable=False)
    three_two_one_crack: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class EquitySnapshot(Base):
    __tablename__ = "equity_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[str] = mapped_column(String, nullable=False, index=True)
    ticker: Mapped[str] = mapped_column(String, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    sector: Mapped[str] = mapped_column(String, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=True)
    change_pct: Mapped[float] = mapped_column(Float, nullable=True)
    wti_corr_30d: Mapped[float | None] = mapped_column(Float, nullable=True)
    brent_corr_90d: Mapped[float | None] = mapped_column(Float, nullable=True)
    high_52w: Mapped[float | None] = mapped_column(Float, nullable=True)
    low_52w: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_cap: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        # Unique constraint: one snapshot per ticker per date
        {"sqlite_autoincrement": True},
    )


class EmailSubscriber(Base):
    __tablename__ = "email_subscribers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    tier: Mapped[str] = mapped_column(String, default="pro")
    unsubscribe_token: Mapped[str] = mapped_column(String, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    subscribed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    unsubscribed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
