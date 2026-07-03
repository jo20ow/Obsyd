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

from sqlalchemy import DateTime, Float, Integer, String, Text, UniqueConstraint
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


class PowerLoadForecast(Base):
    """ENTSO-E day-ahead total-load FORECAST (A65, processType A01), daily mean MW.

    Kept in its own table — NOT in PowerGrid — so future-dated forecast rows (e.g.
    tomorrow's D+1 forecast) never leak into the actual-based situation / Dunkelflaute
    computations, which read PowerGrid. Forecast-vs-actual is joined at read time.
    """

    __tablename__ = "power_load_forecast"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    date: Mapped[str] = mapped_column(String, nullable=False, index=True)   # YYYY-MM-DD
    zone: Mapped[str] = mapped_column(String, nullable=False, index=True)   # e.g. "DE_LU"
    forecast_mw: Mapped[float] = mapped_column(Float, nullable=False)       # day-ahead load forecast, daily mean MW
    # Day-ahead wind/solar forecast (A69) → residual-load forecast = load − wind − solar.
    wind_forecast_mw: Mapped[Optional[float]] = mapped_column(Float, nullable=True)   # B18+B19, daily mean MW
    solar_forecast_mw: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # B16, daily mean MW
    # JSON array of the 24 hourly forecast points [{"hour": 0-23, "load_mw", "wind_mw",
    # "solar_mw", "residual_mw"}] — tomorrow's price-driving residual-load shape.
    hourly_forecast: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("date", "zone", name="uq_power_load_forecast_date_zone"),)


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
    in MW (not totals); residual_mw = load − wind − solar is stored for
    direct use in signal scorecards (scored against POWER_DE forward price).

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
    residual_mw: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # load − wind − solar (MW)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("date", "zone", name="uq_power_grid_date_zone"),)


class PowerGenMix(Base):
    """Full ENTSO-E A75 generation mix in long format.

    One row per (date, zone, psr_type). gen_mw is the daily-mean MW for that
    production type. psr_type uses readable labels (e.g. "Nuclear", "Solar")
    mapped from raw ENTSO-E psrType codes (B01–B20).

    Source: ENTSO-E A75 (Actual Generation per Production Type), processType A16.
    Idempotent upsert by (date, zone, psr_type).
    """

    __tablename__ = "power_gen_mix"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    date: Mapped[str] = mapped_column(String, nullable=False, index=True)     # YYYY-MM-DD
    zone: Mapped[str] = mapped_column(String, nullable=False, index=True)     # e.g. "DE_LU"
    psr_type: Mapped[str] = mapped_column(String, nullable=False, index=True) # readable label or raw code
    gen_mw: Mapped[float] = mapped_column(Float, nullable=False)              # daily mean MW
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("date", "zone", "psr_type", name="uq_power_gen_mix_date_zone_psr"),
    )


class PowerFlow(Base):
    """Daily net cross-border physical electricity flow (ENTSO-E A11).

    One row per (date, from_zone, to_zone). net_mw is the daily-mean MW
    averaged over all hourly quantities in the A11 document.

    Sign convention: net_mw > 0 means net physical flow goes from_zone → to_zone;
    net_mw < 0 means the reverse net direction.

    Computed as:
        net_mw = mean(A11 where out_Domain=from_zone, in_Domain=to_zone)
               − mean(A11 where out_Domain=to_zone,   in_Domain=from_zone)

    Source: ENTSO-E A11 (Actual Cross-Border Physical Flows).
    """

    __tablename__ = "power_flow"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    date: Mapped[str] = mapped_column(String, nullable=False, index=True)        # YYYY-MM-DD
    from_zone: Mapped[str] = mapped_column(String, nullable=False, index=True)   # e.g. "DE_LU"
    to_zone: Mapped[str] = mapped_column(String, nullable=False, index=True)     # e.g. "FR"
    net_mw: Mapped[float] = mapped_column(Float, nullable=False)                 # daily mean MW (signed)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("date", "from_zone", "to_zone", name="uq_power_flow_date_from_to"),
    )


class PowerPriceDaily(Base):
    """Rich per-day electricity price stats for negative-price detection.

    One row per (date, zone). Stores mean/min/max price and a count of hours
    where the auction price was negative (EUR/MWh < 0) — a renewable-oversupply
    signature common in DE spring/summer.

    `mean_price` mirrors EnergyPrice(symbol="POWER_DE").close so the scorecard
    and spark-spread paths never need to touch this table.

    Source: ENTSO-E A44 (Day-Ahead Prices), DE-LU bidding zone.
    Idempotent upsert by (date, zone).
    """

    __tablename__ = "power_price_daily"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    date: Mapped[str] = mapped_column(String, nullable=False, index=True)   # YYYY-MM-DD
    zone: Mapped[str] = mapped_column(String, nullable=False, index=True)   # e.g. "DE_LU"
    mean_price: Mapped[float] = mapped_column(Float, nullable=False)        # EUR/MWh daily mean
    min_price: Mapped[float] = mapped_column(Float, nullable=False)         # EUR/MWh daily min
    max_price: Mapped[float] = mapped_column(Float, nullable=False)         # EUR/MWh daily max
    negative_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # count of hours < 0 EUR/MWh
    # JSON array of the 24 hourly auction prices [{"hour": 0-23, "price": EUR/MWh}], ordered.
    # Text-JSON (project convention, no native JSON type); nullable — older rows backfill lazily.
    hourly_prices: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("date", "zone", name="uq_power_price_daily_date_zone"),
    )


# ─── Canonical hourly time-series store (roadmap Block 0/1) ───────────────────
#
# One long table for ALL hourly power series across ALL zones — the backbone for
# gridstatus-parity range queries + CSV/Parquet export. A new series or zone is a
# row in a dim table (config-only); one write path (backend/power/hourly_store.py),
# one covering index (the PK). The existing daily-mean tables stay and are rolled up
# from here so current routes/scorecards keep reading unchanged.


class ZoneDim(Base):
    """Bidding-zone dimension (id ↔ zone key, e.g. 'DE_LU')."""

    __tablename__ = "zone_dim"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)


class SeriesDim(Base):
    """Series dimension (id ↔ series key, e.g. 'price.dayahead', 'load.actual')."""

    __tablename__ = "series_dim"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    unit: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class PowerHourly(Base):
    """One value per (series, zone, hour-UTC). Integer-keyed, WITHOUT ROWID so the
    PK is the clustering + covering index for the dominant (series, zone, range) scan.
    `ts_utc` = epoch seconds at top-of-hour UTC."""

    __tablename__ = "power_hourly"

    series_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    zone_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts_utc: Mapped[int] = mapped_column(Integer, primary_key=True)
    value: Mapped[float] = mapped_column(Float, nullable=False)

    # WITHOUT ROWID: the composite PK becomes the table's clustering key.
    __table_args__ = {"sqlite_with_rowid": False}
