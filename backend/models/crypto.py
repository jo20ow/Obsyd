"""Crypto price model.

CryptoPrice stores a daily-updated quote per asset from CoinGecko's free public
markets API (no key). One row per (date, symbol), upserted through the day, so
`price_usd` / `change_24h_pct` are the latest snapshot and the accumulating daily
rows give a history series for charting. Crypto is the one asset class whose
real-time data is genuinely free & redistributable — the cornerstone of the
cross-asset terminal's live breadth.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class CryptoPrice(Base):
    __tablename__ = "crypto_prices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[str] = mapped_column(String, nullable=False, index=True)   # YYYY-MM-DD (UTC)
    symbol: Mapped[str] = mapped_column(String, nullable=False, index=True)  # e.g. "BTC"
    name: Mapped[str] = mapped_column(String, nullable=False)                # e.g. "Bitcoin"
    price_usd: Mapped[float] = mapped_column(Float, nullable=False)
    change_24h_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    market_cap: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("date", "symbol", name="uq_crypto_prices_date_symbol"),)
