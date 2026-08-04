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
    # How much of the day each mean stands on (backend/power/daily.py). A claim about a renewable
    # share needs both at 24: the load mean must be a day's, and every hour of the day must have
    # SOME generation in it — that is what tells "PT omits solar at night" (wind and gas still
    # report) apart from "the feed fell over for six hours" (nothing does).
    load_hours: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)   # 0-24
    gen_hours: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)    # 0-24, any fuel
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
# full-history range queries + CSV/Parquet export. A new series or zone is a
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


class PowerRevision(Base):
    """Revision ledger (Honest Record slice A1): one row per REAL value change
    observed at the single hourly write path (backend/power/hourly_store.py).

    "Real" = the (series, zone, hour) row already existed and the re-published
    value moved beyond the float-noise epsilon (REVISION_FLOOR/REVISION_REL_TOL
    in hourly_store). First-time arrivals are NOT revisions — they land in
    ingest_arrival's n_new instead. Derived series (residual.*) are excluded at
    write time: they restate whenever their inputs restate, so ledgering them
    would double-count every upstream revision.

    Forward-only by design: upstream re-fetches overwrite caches, so history
    before deploy is unrecoverable — the ledger accrues from first write.
    "Maturity" (real restatement vs normal provisional fill-in) is a READ-time
    concern for a later slice; everything beyond epsilon is stored. Epsilon
    caveat: the diff is against the CURRENT stored value, so successive
    sub-epsilon steps can accumulate real movement without ever ledgering.

    All timestamps follow power_hourly's convention: epoch seconds UTC.
    `observed_at` is the ingest wall clock, `ts_utc` the hour that changed."""

    __tablename__ = "power_revision"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    series_id: Mapped[int] = mapped_column(Integer, nullable=False)
    zone_id: Mapped[int] = mapped_column(Integer, nullable=False)
    ts_utc: Mapped[int] = mapped_column(Integer, nullable=False)  # epoch sec, top-of-hour UTC
    old_value: Mapped[float] = mapped_column(Float, nullable=False)
    new_value: Mapped[float] = mapped_column(Float, nullable=False)
    observed_at: Mapped[int] = mapped_column(Integer, nullable=False)  # epoch sec UTC

    # The read pattern of the later quality slices is "revisions for one series+zone
    # in a time window" — same shape as power_hourly's PK scan. New table, so
    # create_all creates the index everywhere; no migrations.py retrofit needed.
    __table_args__ = (
        Index("ix_power_revision_series_zone_ts", "series_id", "zone_id", "ts_utc"),
    )


class IngestArrival(Base):
    """Arrival log (Honest Record slice A1): ONE row per non-empty upsert batch
    (per series+zone call through hourly_store.upsert_hourly), not per point.

    A batch with nothing new and nothing changed still logs a row — it is
    evidence the source was fetched, and later slices read arrival cadence as
    fetch health ("no row" must mean "never polled", not "polled, unchanged").
    min/max_ts_new span only the batch's NEW hours (NULL when n_new == 0);
    frontier lag is derivable as observed_at − max_ts_new. Epoch seconds UTC
    throughout, like power_hourly.

    Retention: pruned to the trailing 90 days by the daily retention job
    (backend/collectors/retention.py, INGEST_ARRIVAL_RETENTION_DAYS) — the log
    grows by one row per scheduled fetch and is cadence evidence, not history.
    The revision ledger (power_revision) is deliberately NEVER pruned: the
    ledger is the product."""

    __tablename__ = "ingest_arrival"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    series_id: Mapped[int] = mapped_column(Integer, nullable=False)
    zone_id: Mapped[int] = mapped_column(Integer, nullable=False)
    observed_at: Mapped[int] = mapped_column(Integer, nullable=False)  # epoch sec UTC
    n_new: Mapped[int] = mapped_column(Integer, nullable=False)
    n_changed: Mapped[int] = mapped_column(Integer, nullable=False)
    min_ts_new: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    max_ts_new: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Arrival-cadence reads are "rows for one series+zone ordered by time" —
    # without this they full-scan a table growing by ~10^4 rows/day. New table,
    # so create_all creates it everywhere; no migrations.py retrofit needed.
    __table_args__ = (
        Index("ix_ingest_arrival_series_zone_observed", "series_id", "zone_id", "observed_at"),
    )


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
    table to a coded one — and PSR_LABELS grows gaps whenever ENTSO-E extends the codelist (B03
    was missing until 2026-07, B25 until 2026-08), so PSR_LABELS.get(code, code) is not
    injective in the way a join needs.
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


class UnitGeneration(Base):
    """Hourly per-plant output (ENTSO-E A73, actual generation per generation unit).

    One row per (unit_eic, hour-UTC): `mw` is the hourly MEAN of the published MTUs
    (PT60M value, or the mean of the four PT15M quarters), generation TimeSeries only —
    a pumped-storage unit's consumption TimeSeries is excluded at parse time, or pumping
    would count as generation.

    POPULATION HONESTY (same register as ProductionUnit above). A73 covers only the
    PUBLISHED units — ENTSO-E's ~100 MW threshold, dispatchable fuels only (probe
    2026-07-28, DE control areas: B02/B03/B04/B05/B06/B10/B11/B12/B17 — no wind, no
    solar, no nuclear). That is the production_unit registry's population, NOT the
    fleet, and even of that registry only 85 of DE-LU's 133 units answered.
    mw / nominal_mw is OUTPUT VS NAMEPLATE — a unit at 0% may be perfectly available
    and simply out of merit; availability is what the A77 outage feed says, not this.
    Publication lags DAYS, and unevenly per control area (probe: D-1/D-3 empty, D-7
    full; smoke: TenneT at D-2 while the other German CTAs sat at D-5) — so the newest
    hour trails the wall clock by days AND most units trail the newest hour. Never
    "live"; readers must surface each unit's OWN latest published hour with its own
    lag (sampling everyone at the zone-wide newest hour nulls most of the board).

    DELIBERATELY NOT power_hourly. Per-unit output's natural key is the UNIT, not the
    (series, zone) pair; 85+ EIC-named series keys would pollute the series catalog
    (which lists every key by doctrine, so the Explorer would drown in EICs); and the
    hot 28.5M-row canonical table should not absorb a foreign access pattern.
    Trade-off accepted: per-unit series are not exportable via /api/v1/series — the
    capped /api/power/units/history endpoint covers that need.

    `zone` is the INGEST-CONFIG zone (e.g. "DE_LU" spanning the four German control
    areas — see entsoe_unit_generation.A73_ZONES), indexed for the per-zone board read.
    Composite-PK + WITHOUT ROWID like PowerHourly: (unit_eic, ts_utc) is the clustering
    key for the dominant per-unit range scan. Auto-created by Base.metadata.create_all
    on startup, like every sibling table here (no migration machinery needed for new
    tables — migrations.py exists only for retro-fitting indexes/columns).
    """

    __tablename__ = "unit_generation"

    unit_eic: Mapped[str] = mapped_column(String, primary_key=True)
    ts_utc: Mapped[int] = mapped_column(Integer, primary_key=True)  # epoch sec, top-of-hour UTC
    mw: Mapped[float] = mapped_column(Float, nullable=False)        # hourly mean of the MTUs
    zone: Mapped[str] = mapped_column(String, nullable=False)

    # WITHOUT ROWID: the composite PK becomes the table's clustering key (PowerHourly's
    # convention — the per-unit /units/history range scan rides it directly).
    # The (zone, ts_utc) composite serves the OTHER access pattern — the board's
    # three zone+hour-range queries (zone max ts, per-unit GROUP-BY latest over the
    # trailing window, the latest-days span scan),
    # which would otherwise degrade to full-zone scans on a public endpoint. It also
    # covers plain zone lookups, so `zone` carries no redundant single-column index.
    # Existing DBs get the index via migrations.py (create_all never retro-fits).
    __table_args__ = (
        Index("ix_unit_generation_zone_ts", "zone", "ts_utc"),
        {"sqlite_with_rowid": False},
    )


class PowerOutage(Base):
    """ENTSO-E unavailability of generation units (A77) AND transmission infrastructure (A78).

    An EVENT, not a time series: one row per (mRID, revision) of an
    Unavailability_MarketDocument. Revision semantics are the core — messages
    are updated and withdrawn (docStatus A09); of 31 live DE_LU documents
    sampled 2026-07-11, 26 were withdrawn. The read side must always take the
    HIGHEST revision per mRID and hide withdrawn events; ingest keeps every
    revision as history.

    available_mw is the MINIMUM quantity over the Available_Period step
    function (curveType A03) — the worst case, which is what the desk
    headline should count. offline = nominal_mw − available_mw.

    A78 (spiked live 2026-07-21, DE_LU<->FR) describes an ASSET (line, PST,
    transformer — Asset_RegisteredResource) instead of a production unit:
    unit_name/unit_eic/location/psr_type are populated from that container instead
    of production_RegisteredResource.*, and nominal_mw is ALWAYS null — the schema
    never publishes a capacity baseline for transmission assets, only the reduced
    available_mw. counterparty_zone is A78-only (null for A77): ENTSO-E requires a
    DIRECTED zone pair for A78 (in_Domain/out_Domain), so ingest stores one row per
    queried direction under zone=in_Domain, counterparty_zone=out_Domain — mapped
    through ZONE_REGISTRY where possible, kept as a raw EIC when unmapped. Because a
    border publishes as two DISJOINT messages (one per direction — see the live
    spike in entsoe_outages.py), a zone's transmission outages live under BOTH
    `zone == that zone` AND `counterparty_zone == that zone` rows; the read side
    (latest_outage_revisions, doc_type="A78") matches either column, not just `zone`.
    """

    __tablename__ = "power_outage"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    mrid: Mapped[str] = mapped_column(String, nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    doc_type: Mapped[str] = mapped_column(String, nullable=False, default="A77")  # A77 generation / A78 transmission
    zone: Mapped[str] = mapped_column(String, nullable=False, index=True)
    counterparty_zone: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # A78 only
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


class ForecastScoreDaily(Base):
    """Per-(zone, series, UTC-day) error metrics for ENTSO-E's published
    day-ahead forecasts vs the published actuals — the persisted aggregate
    behind the Honest-Record forecast scoreboard. Posture B: this GRADES the
    TSOs' own forecasts; OBSYD forecasts nothing.

    `series` is one of load | residual | wind | solar (the canonical pair table
    in backend/power/forecast_score.py::FORECAST_PAIRS). `n_hours` counts hours
    where both forecast and actual exist; the other metrics are means over that
    set or a documented subset of it (see forecast_score.py).

    Sign convention: `bias` = mean(forecast − actual) — positive means the
    published forecast leaned HIGH. NOTE the /api/power/forecast-error endpoint
    reports the OPPOSITE sign (mean(actual − forecast)), kept unchanged for its
    existing readers.

    `mape` is stored for the load pair only (percent; hours whose |actual| is
    below the division floor are excluded) — NULL elsewhere. `mae_persistence`
    / `mae_seasonal` are the MAEs of naive baselines built from actuals alone
    (actual(t−24h) / actual(t−168h)), over the scored hours whose lagged actual
    exists; NULL when none does. Skill (1 − mae/mae_baseline) is derived at
    READ time — only the MAEs are stored, at full float precision, because
    rounding here would compound into the read-time ratio.

    Recomputed nightly over the trailing days (forecast/actual revisions
    drift): recompute replaces the row in place (idempotent), and a day the
    data no longer supports is deleted, not left to rot (episodes doctrine).
    Auto-created by Base.metadata.create_all on startup like every sibling
    table here.
    """

    __tablename__ = "forecast_score_daily"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    zone: Mapped[str] = mapped_column(String, nullable=False, index=True)
    series: Mapped[str] = mapped_column(String, nullable=False)             # load | residual | wind | solar
    date: Mapped[str] = mapped_column(String, nullable=False, index=True)   # YYYY-MM-DD (UTC day)
    n_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    mae: Mapped[Optional[float]] = mapped_column(Float, nullable=True)      # MW
    rmse: Mapped[Optional[float]] = mapped_column(Float, nullable=True)     # MW
    bias: Mapped[Optional[float]] = mapped_column(Float, nullable=True)     # MW, forecast − actual
    mape: Mapped[Optional[float]] = mapped_column(Float, nullable=True)     # %, load only
    mae_persistence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # MW
    mae_seasonal: Mapped[Optional[float]] = mapped_column(Float, nullable=True)     # MW
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow,
                                                 onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("zone", "series", "date", name="uq_forecast_score_zone_series_date"),
    )


class QualityDaily(Base):
    """Per-(zone, series, UTC-day) data-quality aggregate: completeness plus
    rule-based anomaly flags — the Honest-Record statement of what the published
    data looks like. Posture B: every row DESCRIBES the source's output (hours
    missing, physically implausible values); nothing here judges the market.

    `hours_present`/`hours_expected` count the series' native intervals: 24 for
    hourly series, 96 for `.qh` quarter-hour series (the column name keeps the
    dominant hourly reading; see backend/power/quality.py::hours_expected).

    `flags` is a JSON-encoded list of flag dicts
    (`{"rule", "hours": [epoch ts], "detail": {...}}`) — Text-JSON, the project
    convention (no native JSON type; see PowerPriceDaily.hourly_prices).
    Zone-level rules (currently gen_below_load_exports) live under the reserved
    series_key `_zone`; those rows carry hours_present = hours_expected = 0 and
    exist ONLY on flagged days.

    A day with no points still gets a row (hours_present=0) only while the
    series shows activity in the surrounding 30 days — otherwise the zone
    simply doesn't carry that series and a row would be noise. Recomputed
    nightly over the trailing window: recompute replaces the row in place
    (idempotent), and a day the data no longer supports is deleted, not left
    to rot (episodes doctrine). Auto-created by Base.metadata.create_all on
    startup like every sibling table here.
    """

    __tablename__ = "quality_daily"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    zone: Mapped[str] = mapped_column(String, nullable=False, index=True)
    series_key: Mapped[str] = mapped_column(String, nullable=False)          # e.g. "load.actual", "_zone"
    date: Mapped[str] = mapped_column(String, nullable=False, index=True)    # YYYY-MM-DD (UTC day)
    hours_present: Mapped[int] = mapped_column(Integer, nullable=False)
    hours_expected: Mapped[int] = mapped_column(Integer, nullable=False)
    flags: Mapped[str] = mapped_column(Text, nullable=False, default="[]")   # JSON list of flag dicts
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow,
                                                 onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("zone", "series_key", "date", name="uq_quality_daily_zone_series_date"),
    )
