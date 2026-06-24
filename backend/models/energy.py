"""Energy price + spread models.

`EnergyPrice` is a generic daily close store keyed by `(date, symbol)`. It is
the shared substrate for the energy vertical: TTF (gas), later EUA (carbon) and
electricity day-ahead prices. The signal-validation scorecard reads it as the
forward-return target for gas-side signals (TTF), the same way it reads FRED
for Brent.

`SparkSpreadHistory` stores the daily clean-gas-power generation margin:
  spark_spread = power_price − gas_price × heat_rate
where heat_rate = 1 / CCGT_efficiency (default 2.0 for 50% fleet efficiency).
CO₂/clean-spark columns exist but are nullable and unpopulated — EUA ticker
is deferred until a reliable free source is confirmed.
"""

from datetime import datetime
from typing import Optional

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


class SparkSpreadHistory(Base):
    """Daily spark spread: power − gas × heat_rate (EUR/MWh).

    One row per calendar day, computed from EnergyPrice POWER_DE (day-ahead
    electricity) and TTF (Dutch gas front-month). heat_rate = 1 / CCGT_efficiency.

    CO₂/clean-spark columns are reserved for when EUA data becomes reliably
    available; they are always NULL until then.
    """

    __tablename__ = "spark_spread_history"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    date: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)  # YYYY-MM-DD
    power_price: Mapped[float] = mapped_column(Float, nullable=False)   # EUR/MWh (POWER_DE)
    gas_price: Mapped[float] = mapped_column(Float, nullable=False)     # EUR/MWh (TTF)
    heat_rate: Mapped[float] = mapped_column(Float, nullable=False)     # MWh_gas / MWh_el  (1/efficiency)
    spark_spread: Mapped[float] = mapped_column(Float, nullable=False)  # EUR/MWh  (power − gas × heat_rate)
    # CO₂ / clean-spark — deferred (EUA ticker TBD)
    co2_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)          # EUR/tCO₂
    clean_spark_spread: Mapped[Optional[float]] = mapped_column(Float, nullable=True) # EUR/MWh
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PowerGrid(Base):
    """Daily-mean electricity grid metrics for residual-load analysis.

    One row per (date, zone). load_mw, wind_mw, solar_mw are daily means
    in MW (not totals); residual_load = load − wind − solar is derived on
    read, never stored.

    Sources:
      load_mw  — ENTSO-E A65 (Actual Total Load), processType A16
      wind_mw  — ENTSO-E A75 (Actual Generation), psrType B18+B19
      solar_mw — ENTSO-E A75 (Actual Generation), psrType B16
    """

    __tablename__ = "power_grid"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    date: Mapped[str] = mapped_column(String, nullable=False, index=True)   # YYYY-MM-DD
    zone: Mapped[str] = mapped_column(String, nullable=False, index=True)   # e.g. "DE_LU"
    load_mw: Mapped[Optional[float]] = mapped_column(Float, nullable=True)   # daily mean MW
    wind_mw: Mapped[Optional[float]] = mapped_column(Float, nullable=True)   # daily mean MW (B18+B19)
    solar_mw: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # daily mean MW (B16)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("date", "zone", name="uq_power_grid_date_zone"),)
