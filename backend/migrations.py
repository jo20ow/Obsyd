"""
Lightweight idempotent schema migrations.

Obsyd uses `Base.metadata.create_all()` for table creation, which creates
missing tables but never adds missing columns to existing tables. This
module fills the gap with hand-written, idempotent ALTER TABLE / index
statements that are safe to run on every startup.

Add a new migration here when you add a column to an existing model.
Each migration must:
  - check first whether the change is already applied
  - swallow IntegrityError / OperationalError if SQLite races
  - log what it did

Migrations run AFTER `init_db()` from `backend.main`'s lifespan.
"""

import logging
from typing import Iterable

from sqlalchemy import inspect, text

from backend.database import engine

logger = logging.getLogger(__name__)


def _existing_columns(table: str) -> set[str]:
    insp = inspect(engine)
    if table not in insp.get_table_names():
        return set()
    return {col["name"] for col in insp.get_columns(table)}


def _add_column_if_missing(table: str, column: str, ddl_type: str) -> bool:
    """Idempotent ALTER TABLE ADD COLUMN. Returns True if a column was added."""
    if column in _existing_columns(table):
        return False
    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))
    logger.info("migrations: added column %s.%s (%s)", table, column, ddl_type)
    return True


def run_migrations() -> None:
    """Apply all pending column/index additions. Safe to call repeatedly."""
    applied: list[str] = []

    # 2026-05-20: in-app trial flow (Pro without LS for 14 days)
    if _add_column_if_missing("subscriptions", "trial_ends_at", "DATETIME"):
        applied.append("subscriptions.trial_ends_at")

    # 2026-05-20: onboarding drip for trial subs (day 0/2/5)
    if _add_column_if_missing("subscriptions", "drip_stage", "INTEGER"):
        applied.append("subscriptions.drip_stage")

    # 2026-06-24: store residual_mw on PowerGrid for signal-scorecard use
    if _add_column_if_missing("power_grid", "residual_mw", "REAL"):
        applied.append("power_grid.residual_mw")
        # One-time idempotent backfill: compute residual for all existing rows
        # where load_mw is known but residual_mw is still NULL.
        with engine.begin() as conn:
            conn.execute(text(
                "UPDATE power_grid "
                "SET residual_mw = load_mw - COALESCE(wind_mw, 0) - COALESCE(solar_mw, 0) "
                "WHERE residual_mw IS NULL AND load_mw IS NOT NULL"
            ))
        logger.info("migrations: backfilled power_grid.residual_mw from load_mw/wind_mw/solar_mw")

    # 2026-06-24: cross-border physical flows (A11) table
    # Base.metadata.create_all handles new tables, so no ALTER needed here.
    # This note documents the addition for the audit trail.

    # 2026-06-24: cross-vertical anomaly radar — tag each Alert with its data
    # vertical. SQLite ADD COLUMN ... DEFAULT 'oil' backfills existing rows, all
    # of which are oil/maritime detectors, so no separate UPDATE is needed.
    if _add_column_if_missing("alerts", "vertical", "VARCHAR DEFAULT 'oil'"):
        applied.append("alerts.vertical")

    # 2026-07-02: store the 24 hourly day-ahead prices (JSON-in-Text) for the
    # hourly-curve panel. Existing rows stay NULL and backfill lazily on the next
    # ingest (overwrite=True re-writes the recent window nightly).
    if _add_column_if_missing("power_price_daily", "hourly_prices", "TEXT"):
        applied.append("power_price_daily.hourly_prices")

    # Day-ahead wind/solar forecast alongside the load forecast → residual-load forecast.
    if _add_column_if_missing("power_load_forecast", "wind_forecast_mw", "REAL"):
        applied.append("power_load_forecast.wind_forecast_mw")
    if _add_column_if_missing("power_load_forecast", "solar_forecast_mw", "REAL"):
        applied.append("power_load_forecast.solar_forecast_mw")
    if _add_column_if_missing("power_load_forecast", "hourly_forecast", "TEXT"):
        applied.append("power_load_forecast.hourly_forecast")

    # 2026-07-12: composite index for the SQL revision-dedupe (highest revision
    # per (zone, mRID)) — the window scan runs on every /overview request and
    # radar pass; without the index it re-sorts the whole table each time.
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_power_outage_zone_mrid_revision "
            "ON power_outage (zone, mrid, revision)"
        ))

    # 2026-07-14: how much of the day each daily mean stands on. Existing rows stay NULL until the
    # re-derivation (backend/scripts/rederive_daily.py) fills them from the hourly store — and a
    # NULL means "unknown", so no claim that needs a complete day is made off one.
    if _add_column_if_missing("power_grid", "load_hours", "INTEGER"):
        applied.append("power_grid.load_hours")
    if _add_column_if_missing("power_grid", "gen_hours", "INTEGER"):
        applied.append("power_grid.gen_hours")

    # 2026-07-14: name the fuels that had no name. B03/B07/B08/B13 were missing from PSR_LABELS,
    # so the ingest stored the RAW code as psr_type and the mix legend read "gen.B03". Adding the
    # labels fixes new rows; without this, the record would carry the same fuel under two names
    # and the stacked mix would draw it as two fuels. Idempotent: after the first pass there is
    # nothing left to rename.
    _relabel_raw_psr_codes(applied)

    if applied:
        logger.info("migrations applied: %s", ", ".join(applied))
    else:
        logger.info("migrations: nothing to apply, schema up to date")


def _relabel_raw_psr_codes(applied: list[str]) -> None:
    """Rewrite psr_type rows still stored under a raw ENTSO-E code that now has a label.

    ONE pass per table (power_gen_mix is ~640k rows and psr_type is not indexed): a CASE over the
    codes, restricted to the rows that still carry one. On a healthy record it matches nothing.
    """
    from backend.power.entsoe_grid import PSR_LABELS

    codes = list(PSR_LABELS)
    case = " ".join(f"WHEN '{c}' THEN '{PSR_LABELS[c]}'" for c in codes)
    in_list = ", ".join(f"'{c}'" for c in codes)

    with engine.begin() as conn:
        tables = set(inspect(engine).get_table_names())
        for table in ("power_gen_mix", "installed_capacity"):
            if table not in tables:
                continue
            res = conn.execute(text(
                f"UPDATE {table} SET psr_type = CASE psr_type {case} ELSE psr_type END "
                f"WHERE psr_type IN ({in_list})"
            ))
            if res.rowcount:
                applied.append(f"{table}.psr_type: {res.rowcount} raw codes named")


def list_pending() -> Iterable[str]:
    """Diagnostic: report which migrations would still be applied."""
    pending: list[str] = []
    cols = _existing_columns("subscriptions")
    if "trial_ends_at" not in cols:
        pending.append("subscriptions.trial_ends_at")
    if "drip_stage" not in cols:
        pending.append("subscriptions.drip_stage")
    if "residual_mw" not in _existing_columns("power_grid"):
        pending.append("power_grid.residual_mw")
    if "vertical" not in _existing_columns("alerts"):
        pending.append("alerts.vertical")
    if "hourly_prices" not in _existing_columns("power_price_daily"):
        pending.append("power_price_daily.hourly_prices")
    return pending
