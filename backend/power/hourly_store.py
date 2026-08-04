"""Single write/read path for the canonical hourly time-series (`power_hourly`).

`upsert_hourly` is idempotent (INSERT … ON CONFLICT DO UPDATE on the natural key
(series, zone, hour)) and batched so the write lock is released frequently — the
ingest process is the sole steady-state writer (roadmap Block 0/1). Dimension ids
(zone/series) are resolved get-or-create per call; the dims are tiny and indexed,
so we deliberately avoid a module-level cache (it would leak ids across the
per-test in-memory DBs and across a reconnect).

Since Honest Record slice A1 every batch is also silently ledgered (see
`_record_ledger`): real value changes → `power_revision`, one `ingest_arrival`
row per non-empty batch. Transparent to all callers — same signature, same
transaction, one extra indexed SELECT per batch.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone

from sqlalchemy import insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from backend.models.energy import IngestArrival, PowerHourly, PowerRevision, SeriesDim, ZoneDim


def day_hour_ts(day: str, hour: int) -> int:
    """Epoch seconds at top-of-hour UTC for a 'YYYY-MM-DD' day + hour 0-23."""
    d = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(d.timestamp()) + hour * 3600

# Rows per multi-row INSERT. 4 cols × 2000 = 8000 bind params — well under SQLite's
# default limit across versions; small enough to release the write lock frequently.
_BATCH = 2000

# --- Revision ledger (Honest Record slice A1) --------------------------------
# An overwrite only counts as a revision when the re-published value moved by
#   abs(new − old) > max(REVISION_FLOOR, REVISION_REL_TOL · abs(old))
# — the absolute floor kills float noise on small magnitudes (prices near 0),
# the relative term keeps the guard proportionate on MW-scale series.
REVISION_FLOOR = 0.5
REVISION_REL_TOL = 0.001

# Derived series are not revision-ledgered: residual.* restates whenever its
# inputs (load/wind/solar) restate, so ledgering it would double-count every
# upstream revision. Their arrival rows are still written.
REVISION_EXCLUDED_PREFIXES = ("residual.",)


def _get_or_create_id(db: Session, model, key: str, **extra) -> int:
    row = db.query(model).filter(model.key == key).first()
    if row is None:
        row = model(key=key, **extra)
        db.add(row)
        db.flush()  # assign the autoincrement id without a full commit
    return row.id


def resolve_zone_id(db: Session, zone_key: str) -> int:
    return _get_or_create_id(db, ZoneDim, zone_key)


def resolve_series_id(db: Session, series_key: str, unit: str | None = None) -> int:
    return _get_or_create_id(db, SeriesDim, series_key, unit=unit)


def upsert_hourly(
    db: Session,
    series_key: str,
    zone_key: str,
    points: Iterable[tuple[int, float]],
    *,
    unit: str | None = None,
) -> int:
    """Upsert (ts_utc, value) points for one series+zone. Returns rows written.

    `points` = iterable of (epoch-seconds-at-top-of-hour-UTC, value). None values are
    skipped. Idempotent: re-running with the same keys overwrites the value in place.
    """
    series_id = resolve_series_id(db, series_key, unit)
    zone_id = resolve_zone_id(db, zone_key)
    rows = [
        {"series_id": series_id, "zone_id": zone_id, "ts_utc": int(ts), "value": float(v)}
        for ts, v in points
        if v is not None
    ]
    if not rows:
        return 0
    _record_ledger(db, series_key, series_id, zone_id, rows)
    written = 0
    for i in range(0, len(rows), _BATCH):
        chunk = rows[i : i + _BATCH]
        stmt = sqlite_insert(PowerHourly).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=["series_id", "zone_id", "ts_utc"],
            set_={"value": stmt.excluded.value},
        )
        db.execute(stmt)
        written += len(chunk)
    db.commit()
    return written


def _record_ledger(db: Session, series_key: str, series_id: int, zone_id: int, rows: list[dict]) -> None:
    """Diff the incoming batch against the stored values and write the Honest-
    Record ledger: per-point PowerRevision rows for real changes plus ONE
    IngestArrival row per (non-empty) batch. Must run BEFORE the upsert — it
    reads the pre-overwrite values — and shares the caller's transaction (the
    upsert's db.commit() lands data + ledger atomically).

    ONE indexed SELECT over the batch's ts range (rides the power_hourly PK),
    never per-row — backfill batches can be a whole month. A range read may
    sweep in existing hours the batch doesn't touch; only batch keys are
    diffed, so that is just a few spare rows in memory, not extra queries."""
    observed_at = int(datetime.now(timezone.utc).timestamp())
    # Last-wins dedupe mirrors what the ON CONFLICT upsert leaves behind should
    # a batch carry the same hour twice.
    incoming = {r["ts_utc"]: r["value"] for r in rows}
    existing = dict(
        db.query(PowerHourly.ts_utc, PowerHourly.value)
        .filter(
            PowerHourly.series_id == series_id,
            PowerHourly.zone_id == zone_id,
            PowerHourly.ts_utc >= min(incoming),
            PowerHourly.ts_utc <= max(incoming),
        )
        .all()
    )
    new_ts = [ts for ts in incoming if ts not in existing]
    ledgered = not series_key.startswith(REVISION_EXCLUDED_PREFIXES)
    n_changed = 0
    revision_rows: list[dict] = []
    for ts, new_v in incoming.items():
        old_v = existing.get(ts)
        # power_hourly.value is NOT NULL, so None strictly means "no row yet" —
        # a first arrival (counted in n_new above), never a revision.
        if old_v is None:
            continue
        if abs(new_v - old_v) <= max(REVISION_FLOOR, REVISION_REL_TOL * abs(old_v)):
            continue  # float noise / provisional jitter below epsilon
        n_changed += 1
        # Excluded (derived) series still COUNT their changes in the arrival row
        # (a batch-level stat, no double-count risk) — they just get no
        # per-point revision rows.
        if ledgered:
            revision_rows.append(
                {
                    "series_id": series_id,
                    "zone_id": zone_id,
                    "ts_utc": ts,
                    "old_value": old_v,
                    "new_value": new_v,
                    "observed_at": observed_at,
                }
            )
    if revision_rows:
        db.execute(insert(PowerRevision), revision_rows)
    # A no-change batch still logs its arrival: evidence of a fetch ("no row"
    # must mean "never polled", not "polled, unchanged").
    db.add(
        IngestArrival(
            series_id=series_id,
            zone_id=zone_id,
            observed_at=observed_at,
            n_new=len(new_ts),
            n_changed=n_changed,
            min_ts_new=min(new_ts) if new_ts else None,
            max_ts_new=max(new_ts) if new_ts else None,
        )
    )


def upsert_day_hours(
    db: Session,
    series_key: str,
    zone_key: str,
    day_hours: dict[str, dict[int, float]],
    *,
    unit: str | None = None,
) -> int:
    """Upsert a {day: {hour: value}} mapping (the shape the hourly parsers return)
    into power_hourly, converting (day, hour) → top-of-hour-UTC epoch."""
    points = [
        (day_hour_ts(day, h), v)
        for day, hours in day_hours.items()
        for h, v in hours.items()
    ]
    return upsert_hourly(db, series_key, zone_key, points, unit=unit)


class RowCapExceeded(Exception):
    """A read matched more rows than max_rows — the caller should narrow the range."""

    def __init__(self, cap: int):
        self.cap = cap
        super().__init__(f"result exceeds {cap} rows")


def read_hourly(
    db: Session,
    series_key: str,
    zone_key: str,
    start_ts: int | None = None,
    end_ts: int | None = None,
    max_rows: int | None = None,
) -> list[tuple[int, float]]:
    """Read (ts_utc, value) for one series+zone in [start_ts, end_ts), ordered by time.
    Returns [] if the series/zone is unknown. This is the core range scan the
    /api/v1/series export builds on.

    `max_rows` caps a single read so one request can't materialise an unbounded
    result into memory (and, for parquet, three copies of it). We fetch one row
    past the cap and raise RowCapExceeded rather than silently truncate — a
    truncated series is a wrong series."""
    sid = db.query(SeriesDim.id).filter(SeriesDim.key == series_key).scalar()
    zid = db.query(ZoneDim.id).filter(ZoneDim.key == zone_key).scalar()
    if sid is None or zid is None:
        return []
    q = db.query(PowerHourly.ts_utc, PowerHourly.value).filter(
        PowerHourly.series_id == sid, PowerHourly.zone_id == zid
    )
    if start_ts is not None:
        q = q.filter(PowerHourly.ts_utc >= start_ts)
    if end_ts is not None:
        q = q.filter(PowerHourly.ts_utc < end_ts)
    q = q.order_by(PowerHourly.ts_utc.asc())
    if max_rows is not None:
        q = q.limit(max_rows + 1)
    rows = [(int(ts), float(v)) for ts, v in q.all()]
    if max_rows is not None and len(rows) > max_rows:
        raise RowCapExceeded(max_rows)
    return rows


def iter_border_points(
    db: Session,
    zone: str,
    start_ts: int | None = None,
    end_ts: int | None = None,
    max_rows: int | None = None,
) -> list[tuple[str, int, float]]:
    """(neighbor, ts_utc, signed_mw) for every cross-border flow point touching
    `zone` in [start_ts, end_ts) — the ONE place the flow sign convention is
    implemented, so `/flows/hourly` (backend/routes/power.py::get_flows_hourly)
    and the live-desk net-flow reader (backend/power/live.py) can never drift
    apart on it.

    Sign convention (see backend/power/energy_charts_flows.py's module
    docstring): a border is stored ONCE, as series ``flow.<TO>`` under zone
    ``<FROM>`` (the canonical alphabetically-first zone of the pair), with
    net_mw > 0 meaning FROM exports. `zone` may be either side of any given
    border:
      * zone is the storing (FROM) side: its own exports live as
        ``flow.<neighbor>`` under itself — read with the native sign (Case A).
      * zone is the counterparty (TO) side: the series lives as
        ``flow.<zone>`` under each neighbour instead — sign is flipped (Case B).

    `max_rows` bounds each Case-A (native) per-neighbor read exactly like any
    other `read_hourly` call. Case B (the counterparty side) combines every
    neighbour that stores `zone` into ONE query and is intentionally NOT
    capped the same way: it is bounded only by the caller's own
    [start_ts, end_ts) window times this zone's border count — never large in
    practice (the busiest European zone has a handful of borders), so an
    explicit cap here would just be a second number to keep in sync with the
    window every caller already chooses.
    """
    out: list[tuple[str, int, float]] = []

    # Case A: zone is the canonical sorted-FIRST side of some borders — its own exports.
    for (key,) in db.query(SeriesDim.key).filter(SeriesDim.key.like("flow.%")).all():
        neighbor = key.removeprefix("flow.")
        if neighbor == zone:
            continue
        for ts, v in read_hourly(db, key, zone, start_ts, end_ts, max_rows=max_rows):
            out.append((neighbor, ts, v))

    # Case B: zone is the canonical sorted-SECOND side — series flow.<zone> under the
    # neighbours; flip sign (native value is FROM's export, i.e. zone's import).
    sid = db.query(SeriesDim.id).filter(SeriesDim.key == f"flow.{zone}").scalar()
    if sid is not None:
        q = (
            db.query(ZoneDim.key, PowerHourly.ts_utc, PowerHourly.value)
            .join(PowerHourly, PowerHourly.zone_id == ZoneDim.id)
            .filter(PowerHourly.series_id == sid)
        )
        if start_ts is not None:
            q = q.filter(PowerHourly.ts_utc >= start_ts)
        if end_ts is not None:
            q = q.filter(PowerHourly.ts_utc < end_ts)
        for neighbor, ts, v in q.order_by(PowerHourly.ts_utc.asc()).all():
            out.append((neighbor, int(ts), -float(v)))

    return out
