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

    if applied:
        logger.info("migrations applied: %s", ", ".join(applied))
    else:
        logger.info("migrations: nothing to apply, schema up to date")


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
    return pending
