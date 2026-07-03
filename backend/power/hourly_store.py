"""Single write/read path for the canonical hourly time-series (`power_hourly`).

`upsert_hourly` is idempotent (INSERT … ON CONFLICT DO UPDATE on the natural key
(series, zone, hour)) and batched so the write lock is released frequently — the
ingest process is the sole steady-state writer (roadmap Block 0/1). Dimension ids
(zone/series) are resolved get-or-create per call; the dims are tiny and indexed,
so we deliberately avoid a module-level cache (it would leak ids across the
per-test in-memory DBs and across a reconnect).
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from backend.models.energy import PowerHourly, SeriesDim, ZoneDim


def day_hour_ts(day: str, hour: int) -> int:
    """Epoch seconds at top-of-hour UTC for a 'YYYY-MM-DD' day + hour 0-23."""
    d = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(d.timestamp()) + hour * 3600

# Rows per multi-row INSERT. 4 cols × 2000 = 8000 bind params — well under SQLite's
# default limit across versions; small enough to release the write lock frequently.
_BATCH = 2000


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


def read_hourly(
    db: Session,
    series_key: str,
    zone_key: str,
    start_ts: int | None = None,
    end_ts: int | None = None,
) -> list[tuple[int, float]]:
    """Read (ts_utc, value) for one series+zone in [start_ts, end_ts), ordered by time.
    Returns [] if the series/zone is unknown. This is the core range scan the future
    /api/v1/series export builds on."""
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
    return [(int(ts), float(v)) for ts, v in q.order_by(PowerHourly.ts_utc.asc()).all()]
