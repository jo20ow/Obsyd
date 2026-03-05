from datetime import datetime

from sqlalchemy import String, Float, DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class EIAPrice(Base):
    """EIA energy prices and inventory data (WTI, Brent, NG, Cushing stocks)."""
    __tablename__ = "eia_prices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    series_id: Mapped[str] = mapped_column(String, index=True)
    period: Mapped[str] = mapped_column(String)  # e.g. "2024-01-05"
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(String, default="")
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class FREDSeries(Base):
    """FRED macro data (DXY, yield curve, CPI, Fed Funds Rate)."""
    __tablename__ = "fred_series"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    series_id: Mapped[str] = mapped_column(String, index=True)
    date: Mapped[str] = mapped_column(String)
    value: Mapped[float] = mapped_column(Float)
    description: Mapped[str] = mapped_column(String, default="")
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
