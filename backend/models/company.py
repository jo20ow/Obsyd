"""Company reference model — the equities security-master seed.

Loaded from SEC EDGAR's public company_tickers.json (ticker → CIK → title). This
is the free, complete reference layer that lets the terminal look a company up by
ticker/name and then pull its filings + fundamentals from EDGAR on demand.
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cik: Mapped[str] = mapped_column(String, nullable=False, index=True)   # zero-padded 10-char
    ticker: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)  # upper
    title: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
