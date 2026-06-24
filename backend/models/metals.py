"""Metals supply models.

CopperSupply stores monthly U.S. copper supply statistics from the USGS
Mineral Industry Surveys (MIS). Data are public domain. One row per month,
keyed on `date` = YYYY-MM-01, covering:
  - us_mine_production  — recoverable copper from U.S. mines (metric tons)
  - us_refined_production — total refined copper produced (metric tons)
  - us_refined_stocks   — U.S. refined copper stocks at end of period (metric tons)
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class CopperSupply(Base):
    """Monthly U.S. copper supply from USGS Mineral Industry Surveys (public domain)."""

    __tablename__ = "copper_supply"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[str] = mapped_column(
        String, nullable=False, unique=True, index=True
    )  # YYYY-MM-01
    us_mine_production: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True
    )  # metric tons, recoverable copper (T2 Total)
    us_refined_production: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True
    )  # metric tons (T4 Total refined)
    us_refined_stocks: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True
    )  # metric tons, end-of-period (T10 Total refined)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
