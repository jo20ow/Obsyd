"""Revision ledger + arrival log (Honest Record slice A1): every batch through
the single hourly write path records what changed (power_revision) and what
arrived (ingest_arrival). Silent capture only — no API/UI in this slice."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import event

from backend.models.energy import IngestArrival, PowerRevision
from backend.power.hourly_store import (
    REVISION_FLOOR,
    REVISION_REL_TOL,
    read_hourly,
    resolve_series_id,
    resolve_zone_id,
    upsert_hourly,
)

H = 3600
BASE = 1_700_000_000  # arbitrary fixed epoch (matches test_hourly_store convention)


def _now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def test_fresh_insert_logs_one_arrival_and_no_revisions(db_session):
    pts = [(BASE + i * H, 100.0 + i) for i in range(24)]
    upsert_hourly(db_session, "load.actual", "DE_LU", pts, unit="MW")

    assert db_session.query(PowerRevision).count() == 0
    arrivals = db_session.query(IngestArrival).all()
    assert len(arrivals) == 1  # one row per batch, not per point
    a = arrivals[0]
    assert a.series_id == resolve_series_id(db_session, "load.actual")
    assert a.zone_id == resolve_zone_id(db_session, "DE_LU")
    assert a.n_new == 24
    assert a.n_changed == 0
    assert a.min_ts_new == BASE
    assert a.max_ts_new == BASE + 23 * H
    # observed_at is the UTC wall clock at ingest (frontier lag = observed_at − max_ts_new).
    assert abs(a.observed_at - _now_ts()) < 60


def test_identical_reupsert_logs_arrival_with_zero_counts(db_session):
    pts = [(BASE, 50.0), (BASE + H, 60.0)]
    upsert_hourly(db_session, "price.dayahead", "FR", pts)
    upsert_hourly(db_session, "price.dayahead", "FR", pts)

    assert db_session.query(PowerRevision).count() == 0
    arrivals = db_session.query(IngestArrival).order_by(IngestArrival.id).all()
    # A no-change batch STILL logs an arrival row: it is evidence the source was
    # fetched (later slices read arrival cadence as fetch health), and dropping
    # it would make "no row" ambiguous between "never polled" and "polled, same".
    assert len(arrivals) == 2
    a = arrivals[1]
    assert a.n_new == 0
    assert a.n_changed == 0
    assert a.min_ts_new is None
    assert a.max_ts_new is None


def test_all_none_batch_logs_nothing(db_session):
    # None values are skipped before the batch forms → empty batch → no arrival.
    upsert_hourly(db_session, "solar.forecast", "DE_LU", [(BASE, None), (BASE + H, None)])
    assert db_session.query(IngestArrival).count() == 0
    assert db_session.query(PowerRevision).count() == 0


def test_change_beyond_epsilon_writes_revision(db_session):
    upsert_hourly(db_session, "price.dayahead", "DE_LU", [(BASE, 50.0), (BASE + H, 60.0)])
    upsert_hourly(db_session, "price.dayahead", "DE_LU", [(BASE, 55.0)])

    revs = db_session.query(PowerRevision).all()
    assert len(revs) == 1
    r = revs[0]
    assert r.series_id == resolve_series_id(db_session, "price.dayahead")
    assert r.zone_id == resolve_zone_id(db_session, "DE_LU")
    assert r.ts_utc == BASE
    assert r.old_value == 50.0
    assert r.new_value == 55.0
    assert abs(r.observed_at - _now_ts()) < 60

    a = db_session.query(IngestArrival).order_by(IngestArrival.id).all()[-1]
    assert a.n_new == 0
    assert a.n_changed == 1


def test_change_below_absolute_floor_is_not_a_revision(db_session):
    upsert_hourly(db_session, "price.dayahead", "DE_LU", [(BASE, 50.0)])
    # |Δ| = 0.3 ≤ max(FLOOR=0.5, 0.001·50) → float noise, not a revision.
    assert abs(50.3 - 50.0) <= max(REVISION_FLOOR, REVISION_REL_TOL * 50.0)
    upsert_hourly(db_session, "price.dayahead", "DE_LU", [(BASE, 50.3)])
    assert db_session.query(PowerRevision).count() == 0
    a = db_session.query(IngestArrival).order_by(IngestArrival.id).all()[-1]
    assert a.n_changed == 0


def test_change_below_relative_epsilon_is_not_a_revision(db_session):
    # Large magnitudes: the 0.1% relative term dominates the 0.5 floor.
    upsert_hourly(db_session, "load.actual", "DE_LU", [(BASE, 60_000.0)])
    upsert_hourly(db_session, "load.actual", "DE_LU", [(BASE, 60_040.0)])  # Δ=40 ≤ 60
    assert db_session.query(PowerRevision).count() == 0
    upsert_hourly(db_session, "load.actual", "DE_LU", [(BASE, 60_100.0)])  # Δ=60 ≤ 60.04 (0.1% of 60_040) → below relative epsilon
    assert db_session.query(PowerRevision).count() == 0
    upsert_hourly(db_session, "load.actual", "DE_LU", [(BASE, 61_000.0)])  # Δ=900 > 60.1
    assert db_session.query(PowerRevision).count() == 1


def test_residual_series_change_is_not_revision_ledgered(db_session):
    # Derived series restate whenever their inputs restate — ledgering them would
    # double-count every upstream revision. Arrival rows are still written.
    upsert_hourly(db_session, "residual.load", "DE_LU", [(BASE, 30_000.0)])
    upsert_hourly(db_session, "residual.load", "DE_LU", [(BASE, 20_000.0)])
    assert db_session.query(PowerRevision).count() == 0
    assert db_session.query(IngestArrival).count() == 2


def test_new_row_is_arrival_not_revision(db_session):
    upsert_hourly(db_session, "load.actual", "NL", [(BASE, 1.0)])
    upsert_hourly(db_session, "load.actual", "NL", [(BASE + H, 2.0)])  # new hour only
    assert db_session.query(PowerRevision).count() == 0
    a = db_session.query(IngestArrival).order_by(IngestArrival.id).all()[-1]
    assert a.n_new == 1
    assert a.min_ts_new == a.max_ts_new == BASE + H


def test_mixed_batch_counts_new_and_changed(db_session):
    upsert_hourly(db_session, "price.dayahead", "NL", [(BASE, 50.0), (BASE + H, 60.0)])
    # One real change, one unchanged, one new hour.
    upsert_hourly(
        db_session,
        "price.dayahead",
        "NL",
        [(BASE, 40.0), (BASE + H, 60.0), (BASE + 2 * H, 70.0)],
    )
    revs = db_session.query(PowerRevision).all()
    assert [(r.ts_utc, r.old_value, r.new_value) for r in revs] == [(BASE, 50.0, 40.0)]
    a = db_session.query(IngestArrival).order_by(IngestArrival.id).all()[-1]
    assert a.n_new == 1
    assert a.n_changed == 1
    assert a.min_ts_new == a.max_ts_new == BASE + 2 * H


def test_duplicate_ts_in_batch_diffs_last_wins(db_session):
    # A batch carrying the same hour twice is diffed against the LAST value —
    # mirroring what the ON CONFLICT upsert leaves behind. Here the last value
    # equals the stored one, so nothing is a revision.
    upsert_hourly(db_session, "price.dayahead", "DE_LU", [(BASE, 50.0)])
    upsert_hourly(db_session, "price.dayahead", "DE_LU", [(BASE, 99.0), (BASE, 50.0)])
    assert db_session.query(PowerRevision).count() == 0
    assert read_hourly(db_session, "price.dayahead", "DE_LU") == [(BASE, 50.0)]


def test_duplicate_new_ts_counts_once(db_session):
    upsert_hourly(db_session, "price.dayahead", "DE_LU", [(BASE, 99.0), (BASE, 50.0)])
    a = db_session.query(IngestArrival).one()
    assert a.n_new == 1
    assert a.min_ts_new == a.max_ts_new == BASE


def test_ledger_issues_bounded_selects_per_batch(db_session):
    """The diff read must be ONE indexed SELECT per batch — a month-sized backfill
    batch must not degrade to per-row lookups (budget mirrors
    test_power_overview.test_bulk_uses_fixed_query_count)."""
    month = [(BASE + i * H, float(i)) for i in range(744)]
    upsert_hourly(db_session, "load.actual", "DE_LU", month)  # seed

    statements = []

    def _count(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", _count)
    try:
        # Re-upsert with every value shifted → 744 revisions, still bounded SELECTs.
        upsert_hourly(db_session, "load.actual", "DE_LU", [(ts, v + 10.0) for ts, v in month])
    finally:
        event.remove(engine, "before_cursor_execute", _count)
    # 2 dim resolves + 1 diff read (+ headroom for driver chatter).
    assert len(statements) <= 5, f"{len(statements)} SELECTs — ledger regressed to per-row reads"
    assert db_session.query(PowerRevision).count() == 744
