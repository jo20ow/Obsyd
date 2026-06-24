"""Energy price + spread models.

`EnergyPrice` is a generic daily close store keyed by `(date, symbol)`. It is
the shared substrate for the energy vertical: TTF (gas), later EUA (carbon) and
electricity day-ahead prices. The signal-validation scorecard reads it as the
forward-return target for gas-side signals (TTF), the same way it reads FRED
for Brent.
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class EnergyPrice(Base):
    __tablename__ = "energy_prices"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    date: Mapped[str] = mapped_column(String, nullable=False, index=True)  # YYYY-MM-DD
    symbol: Mapped[str] = mapped_column(String, nullable=False, index=True)  # e.g. "TTF", "EUA", "POWER_DE"
    close: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("date", "symbol", name="uq_energy_price_date_symbol"),)
