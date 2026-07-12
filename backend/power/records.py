"""All-time records per series × zone, recomputed nightly.

The cheapest "wow" from the gridstatus repertoire — "highest DE-LU day-ahead
hour on record" — derived entirely from power_hourly. Recompute is a plain SQL
min/max per (series, zone): always correct, no incremental state to corrupt.

Plausibility guard: ENTSO-E ingest hiccups have produced absurd points; a
record outside the plausible band is ignored rather than celebrated, and the
guard is per series family (prices can be negative; loads cannot).
"""

from __future__ import annotations

import logging

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.models.energy import PowerHourly, PowerRecord, SeriesDim, ZoneDim

logger = logging.getLogger(__name__)

#: Series worth a record headline. QH included: the record quarter-hour is
#: sharper than the record hour since the 15-min switch.
RECORD_SERIES = [
    "price.dayahead",
    "price.dayahead.qh",
    "imbalance.price.qh",
    "load.actual",
    "residual.actual",
]

# Plausible bands per series family (guard, not physics).
PRICE_MIN_PLAUSIBLE = -500.0
PRICE_MAX_PLAUSIBLE = 4_000.0
IMBALANCE_MIN_PLAUSIBLE = -20_000.0
IMBALANCE_MAX_PLAUSIBLE = 20_000.0
MW_MIN_PLAUSIBLE = 0.0
MW_MAX_PLAUSIBLE = 200_000.0
# No European bidding zone's real load dips below this — a "0 MW load" hour is
# an ENTSO-E gap artifact, and it produced a live bogus all-time-min record
# (SI, 2026-07-11) that the radar dutifully celebrated.
LOAD_MIN_PLAUSIBLE = 100.0
# Negative residual load is REAL (renewables exceeding load) and exactly the
# kind of record worth surfacing — the old 0-floor silently discarded it.
RESIDUAL_MIN_PLAUSIBLE = -100_000.0


def _bounds(series_key: str) -> tuple[float, float]:
    if series_key.startswith("imbalance."):
        return IMBALANCE_MIN_PLAUSIBLE, IMBALANCE_MAX_PLAUSIBLE
    if series_key.startswith("price."):
        return PRICE_MIN_PLAUSIBLE, PRICE_MAX_PLAUSIBLE
    if series_key.startswith("load."):
        return LOAD_MIN_PLAUSIBLE, MW_MAX_PLAUSIBLE
    if series_key.startswith("residual."):
        return RESIDUAL_MIN_PLAUSIBLE, MW_MAX_PLAUSIBLE
    return MW_MIN_PLAUSIBLE, MW_MAX_PLAUSIBLE


def compute_records(db: Session) -> list[PowerRecord]:
    """Recompute min/max records for every RECORD_SERIES × zone with data.
    Upserts one PowerRecord per (series, zone, kind); returns the rows."""
    out: list[PowerRecord] = []
    for series_key in RECORD_SERIES:
        sid_row = db.query(SeriesDim).filter(SeriesDim.key == series_key).first()
        if sid_row is None:
            continue
        lo, hi = _bounds(series_key)
        zone_ids = [
            z for (z,) in db.query(PowerHourly.zone_id)
            .filter(PowerHourly.series_id == sid_row.id).distinct()
        ]
        for zid in zone_ids:
            zone_key = db.query(ZoneDim.key).filter(ZoneDim.id == zid).scalar()
            if zone_key is None:
                continue
            base = db.query(PowerHourly).filter(
                PowerHourly.series_id == sid_row.id,
                PowerHourly.zone_id == zid,
                PowerHourly.value >= lo,
                PowerHourly.value <= hi,
            )
            for kind, agg in (("max", func.max), ("min", func.min)):
                extreme = base.with_entities(agg(PowerHourly.value)).scalar()
                if extreme is None:
                    continue
                # Evidence point: the (first) timestamp where the extreme occurred.
                ts = (
                    base.filter(PowerHourly.value == extreme)
                    .with_entities(func.min(PowerHourly.ts_utc))
                    .scalar()
                )
                row = (
                    db.query(PowerRecord)
                    .filter_by(series_key=series_key, zone=zone_key, kind=kind)
                    .first()
                )
                if row is None:
                    row = PowerRecord(series_key=series_key, zone=zone_key, kind=kind,
                                      value=extreme, ts_utc=ts, unit=sid_row.unit)
                    db.add(row)
                else:
                    row.value = extreme
                    row.ts_utc = ts
                    row.unit = sid_row.unit
                out.append(row)
    db.commit()
    return out
