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

from sqlalchemy import DateTime, Float, Index, Integer, String, Text, UniqueConstraint
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


class InstalledCapacity(Base):
    """ENTSO-E installed generation capacity per production type (A68/A33) — annual, per
    zone. Reference/context data (how much wind/solar/gas/etc. a zone has), not a time
    series; kept out of power_hourly because it's yearly."""

    __tablename__ = "installed_capacity"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    zone: Mapped[str] = mapped_column(String, nullable=False, index=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    psr_type: Mapped[str] = mapped_column(String, nullable=False)  # readable label (PSR_LABELS)
    capacity_mw: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("zone", "year", "psr_type", name="uq_installed_capacity_zone_year_psr"),
    )


class ProductionUnit(Base):
    """ENTSO-E production units (A71 / processType A33) — the plants behind the EICs.

    PowerOutage has carried `unit_eic` since it was written and has never READ it: a dangling
    join key waiting for exactly this table. With it, the outage board says "CATTENOM 3" where
    it used to say `17W100P100P0001A`.

    THIS IS NOT THE INSTALLED FLEET, AND MUST NEVER BE USED AS ONE.
    A71/A33 lists only production UNITS above ENTSO-E's ~100 MW publication threshold. Measured:

        DE-LU   A71/A33:  133 units,  65,193 MW      FR   A71/A33:  174 units,  93,903 MW
                A68    :             294,941 MW           A68    :             163,611 MW
                                     ──────────                                ──────────
                                      factor 4.5                                factor 1.7

    And the ratio is not even CONSTANT (NL: 2.7), so no correction factor could turn one into
    the other.

    A different population, not a smaller sample of the same one. Firing forced_outage_severity's
    A68-calibrated 3%/8% thresholds against a several-times-too-small denominator would fire far more often
    — and the 19 A68 zones and the 18 A71 zones would then be measuring different populations
    under one threshold, which is precisely the cross-zone incomparability outage_history.py
    already forbids. What it IS good for: A71/A33 is the same population the A77 outages are
    drawn from, so "% of the zone's published >=100 MW units" is an honest number with its own
    label — and it exists for all 37 zones, including the 18 that have no A68 at all.

    psr_type stores the RAW B-CODE, deliberately. This table exists to join PowerOutage.unit_eic,
    and PowerOutage.psr_type is a raw code (labelled at read time), while InstalledCapacity and
    PowerGenMix store the readable LABEL. Choosing the label here would mean joining a labelled
    table to a coded one — and PSR_LABELS has real gaps (A71/A33 returns B03; the store already
    holds gen.B25), so PSR_LABELS.get(code, code) is not injective in the way a join needs.
    """

    __tablename__ = "production_unit"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    unit_eic: Mapped[str] = mapped_column(String, nullable=False, index=True)  # joins PowerOutage
    zone: Mapped[str] = mapped_column(String, nullable=False, index=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    psr_type: Mapped[str | None] = mapped_column(String, nullable=True)  # RAW B-code, see above
    nominal_mw: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("unit_eic", "year", name="uq_production_unit_eic_year"),
    )


class PowerOutage(Base):
    """ENTSO-E unavailability of generation/production units (A77/A78/A80).

    An EVENT, not a time series: one row per (mRID, revision) of an
    Unavailability_MarketDocument. Revision semantics are the core — messages
    are updated and withdrawn (docStatus A09); of 31 live DE_LU documents
    sampled 2026-07-11, 26 were withdrawn. The read side must always take the
    HIGHEST revision per mRID and hide withdrawn events; ingest keeps every
    revision as history.

    available_mw is the MINIMUM quantity over the Available_Period step
    function (curveType A03) — the worst case, which is what the desk
    headline should count. offline = nominal_mw − available_mw.
    """

    __tablename__ = "power_outage"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    mrid: Mapped[str] = mapped_column(String, nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    doc_type: Mapped[str] = mapped_column(String, nullable=False, default="A77")  # A77/A78/A80
    zone: Mapped[str] = mapped_column(String, nullable=False, index=True)
    business_type: Mapped[str] = mapped_column(String, nullable=False)  # A53 planned / A54 forced
    psr_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)   # raw B-code
    unit_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    unit_eic: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    location: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    nominal_mw: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    available_mw: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    start_utc: Mapped[str] = mapped_column(String, nullable=False, index=True)  # ISO "YYYY-MM-DDTHH:MMZ"
    end_utc: Mapped[str] = mapped_column(String, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")  # active | withdrawn
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("mrid", "revision", name="uq_power_outage_mrid_revision"),
        # Revision-dedupe ("highest revision per (zone, mRID)") is a window/group
        # scan on every /overview and radar run — this composite index turns it
        # into an index-only walk. Existing DBs get it via migrations.py.
        Index("ix_power_outage_zone_mrid_revision", "zone", "mrid", "revision"),
    )


class PowerRecord(Base):
    """All-time extreme per (series, zone, kind) — descriptive records like
    "highest DE-LU day-ahead hour". Recomputed nightly by SQL min/max over
    power_hourly (always correct, no incremental state); one row per key,
    updated in place. ts_utc points at the evidence."""

    __tablename__ = "power_record"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    series_key: Mapped[str] = mapped_column(String, nullable=False)
    zone: Mapped[str] = mapped_column(String, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String, nullable=False)  # max | min
    value: Mapped[float] = mapped_column(Float, nullable=False)
    ts_utc: Mapped[int] = mapped_column(Integer, nullable=False)  # epoch sec of the record point
    unit: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("series_key", "zone", "kind", name="uq_power_record_series_zone_kind"),
    )


class PowerEpisode(Base):
    """A stretch of grid stress as an OBJECT, not a daily flag.

    Grid stress is an episode: a Dunkelflaute runs for days, a negative-price weekend for a
    weekend. The radar only ever saw today. Worse, it could not have been taught otherwise from
    what it stored — `_upsert_alert` mutates the existing Alert row in place, slides created_at
    forward and DELETES older duplicates, so a five-day run collapses into one row that claims
    nothing about duration. The history was never written.

    So episodes are RE-DERIVED from the canonical series, nightly, in full — exactly the
    doctrine PowerRecord already follows. No incremental state means no state to corrupt.

    `depth_date` is the evidence pointer (records.py's discipline): the day the episode was at
    its worst, so a reader can go and look.

    Day grain, deliberately: the predicates live on PowerGrid and PowerPriceDaily, both daily.
    An hour-grained duration would be a precision we do not have.
    """

    __tablename__ = "power_episode"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String, nullable=False, index=True)   # see episodes.KINDS
    zone: Mapped[str] = mapped_column(String, nullable=False, index=True)
    start_date: Mapped[str] = mapped_column(String, nullable=False)         # YYYY-MM-DD
    end_date: Mapped[str] = mapped_column(String, nullable=False)
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False)
    depth: Mapped[float] = mapped_column(Float, nullable=False)             # the worst value
    depth_date: Mapped[str] = mapped_column(String, nullable=False)         # …and when
    mean_value: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)             # active | resolved
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow,
                                                 onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("kind", "zone", "start_date", name="uq_power_episode_kind_zone_start"),
    )
