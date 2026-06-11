"""ENTSOG ingestion: point registry → classification → physical flows.

API (public, no key): https://transparency.entsog.eu/api/v1
  /operatorpointdirections          — the point registry
  /operationalData (Physical Flow)  — daily flows (kWh/d) per point

Mechanics:
  - Stable point_id = operatorKey|pointKey|directionKey (rename-resilient).
  - Double-counting: classification keeps only the EU side (tSOCountry∈EU), so
    a supplier reporting its own side of the same physical point is dropped.
  - Flows convert kWh/d → GWh/d via the row's own `unit`.
  - Empty `value` ("") → no row (missing, never a silent zero).
  - Forward-fill ≤2 days (interpolated=1); a ≥3-day gap stays empty.
  - provisional=1 for the most-recent 2 gas days; overwritten to 0 on re-ingest
    once the day is no longer recent.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

import httpx
from sqlalchemy.orm import Session

from backend.gas import raw_cache
from backend.gas.classification import classify_point
from backend.gas.units import coerce_float, kwh_per_day_to_gwh_per_day
from backend.models.gas import GasFlow, GasPoint

logger = logging.getLogger(__name__)

ENTSOG_BASE = "https://transparency.entsog.eu/api/v1"
PROVISIONAL_DAYS = 2
_PAGE = 10_000


def make_point_id(row: dict) -> str:
    return f"{row.get('operatorKey', '')}|{row.get('pointKey', '')}|{row.get('directionKey', '')}"


# ─── point registry ──────────────────────────────────────────────────────────


async def fetch_point_registry(*, overwrite: bool = False) -> list[dict]:
    """GET the full point registry (raw-cached under a fixed key)."""

    async def _fetch() -> dict:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.get(f"{ENTSOG_BASE}/operatorpointdirections", params={"limit": 20000})
            resp.raise_for_status()
            return resp.json()

    # Registry is slowly-changing; cache under the first-of-month bucket.
    payload = await raw_cache.fetch_or_cache("entsog", "operatorpointdirections", date.today().replace(day=1), _fetch, overwrite=overwrite)
    return payload.get("operatorpointdirections", [])


async def sync_points(db: Session, *, overwrite: bool = False) -> dict:
    """Classify every registry point and upsert into gas_points. Returns counts."""
    rows = await fetch_point_registry(overwrite=overwrite)
    # The registry repeats the same operator|point|direction across validity
    # periods; collapse to one row per point_id (last wins) before upserting.
    unique: dict[str, dict] = {}
    for row in rows:
        unique[make_point_id(row)] = row

    by_class: dict[str, int] = {}
    for pid, row in unique.items():
        cls = classify_point(row)
        point_class = cls.point_class if cls else None
        counterparty = cls.counterparty if cls else None
        active = 1 if cls else 0
        if point_class:
            by_class[point_class] = by_class.get(point_class, 0) + 1

        existing = db.get(GasPoint, pid)
        if existing:
            existing.name = row.get("pointLabel", "") or existing.name
            existing.operator = row.get("operatorLabel", "") or existing.operator
            existing.point_class = point_class
            existing.counterparty = counterparty
            existing.active = active
        else:
            db.add(
                GasPoint(
                    point_id=pid,
                    name=row.get("pointLabel", ""),
                    operator=row.get("operatorLabel", ""),
                    point_class=point_class,
                    counterparty=counterparty,
                    active=active,
                )
            )
    db.commit()
    logger.info("entsog.sync_points: %d registry rows, classified=%s", len(rows), by_class)
    return {"total": len(rows), "by_class": by_class}


# ─── physical flows ──────────────────────────────────────────────────────────


async def fetch_flows_day(day: str, *, overwrite: bool = False) -> list[dict]:
    """All Physical Flow rows for one gas day (paginated, raw-cached)."""
    dt = datetime.strptime(day, "%Y-%m-%d").date()

    async def _fetch() -> dict:
        rows: list[dict] = []
        async with httpx.AsyncClient(timeout=120) as client:
            offset = 0
            while True:
                resp = await client.get(
                    f"{ENTSOG_BASE}/operationalData",
                    params={
                        "indicator": "Physical Flow",
                        "periodType": "day",
                        "from": day,
                        "to": day,
                        "limit": _PAGE,
                        "offset": offset,
                    },
                )
                resp.raise_for_status()
                page = resp.json().get("operationalData", [])
                rows.extend(page)
                if len(page) < _PAGE:
                    break
                offset += _PAGE
        return {"operationalData": rows}

    payload = await raw_cache.fetch_or_cache("entsog", f"flows_{day}", dt, _fetch, overwrite=overwrite)
    return payload.get("operationalData", [])


def _active_points(db: Session) -> dict[str, GasPoint]:
    return {p.point_id: p for p in db.query(GasPoint).filter(GasPoint.active == 1).all()}


def _is_provisional(day: str, reference: date) -> int:
    d = datetime.strptime(day, "%Y-%m-%d").date()
    return 1 if (reference - d).days < PROVISIONAL_DAYS else 0


def _upsert_flow(db: Session, day: str, point_id: str, direction: str, value_gwh: float, provisional: int, interpolated: int) -> None:
    existing = db.get(GasFlow, (day, point_id, direction))
    if existing:
        # Never demote a confirmed value back to provisional unless the value changed.
        existing.value_gwh = value_gwh
        existing.interpolated = interpolated
        existing.provisional = provisional
    else:
        db.add(
            GasFlow(
                date=day,
                point_id=point_id,
                direction=direction,
                value_gwh=value_gwh,
                provisional=provisional,
                interpolated=interpolated,
            )
        )


async def ingest_flows(db: Session, days: list[str], *, reference: date | None = None, overwrite: bool = False) -> dict:
    """Ingest physical flows for `days` (chronological). Filters to classified
    points, converts to GWh/d, then forward-fills ≤2-day gaps per point."""
    reference = reference or date.today()
    active = _active_points(db)
    stats = {"days": len(days), "rows_written": 0, "interpolated": 0}

    for day in days:
        rows = await fetch_flows_day(day, overwrite=overwrite)
        prov = _is_provisional(day, reference)
        for row in rows:
            pid = make_point_id(row)
            if pid not in active:
                continue
            unit = (row.get("unit") or "").lower()
            raw = coerce_float(row.get("value"))
            if raw is None:
                continue  # missing — leave a gap, fill step handles ≤2 days
            if unit not in ("kwh/d", ""):
                logger.warning("entsog: unexpected unit %r at %s %s", unit, day, pid)
            value_gwh = kwh_per_day_to_gwh_per_day(raw)
            _upsert_flow(db, day, pid, row.get("directionKey", ""), value_gwh, prov, 0)
            stats["rows_written"] += 1
        db.commit()

    stats["interpolated"] = _forward_fill(db, days, active, max_gap=PROVISIONAL_DAYS)
    db.commit()
    logger.info("entsog.ingest_flows: %s", stats)
    return stats


def _forward_fill(db: Session, days: list[str], active: dict[str, GasPoint], max_gap: int) -> int:
    """Carry the last known value forward over ≤max_gap missing days per point.
    Only fills points that already have at least one real observation in range."""
    if not days:
        return 0
    day_set = set(days)
    filled = 0
    # Pull existing real (non-interpolated) flows in range, grouped by (point,direction).
    existing = (
        db.query(GasFlow)
        .filter(GasFlow.date.in_(days), GasFlow.interpolated == 0)
        .all()
    )
    series: dict[tuple[str, str], dict[str, float]] = {}
    for f in existing:
        series.setdefault((f.point_id, f.direction), {})[f.date] = f.value_gwh

    for (pid, direction), by_day in series.items():
        if pid not in active:
            continue
        last_day: date | None = None
        last_val: float | None = None
        for day in days:
            d = datetime.strptime(day, "%Y-%m-%d").date()
            if day in by_day:
                last_day, last_val = d, by_day[day]
                continue
            if last_day is None or last_val is None:
                continue
            gap = (d - last_day).days
            if 1 <= gap <= max_gap and day in day_set:
                if db.get(GasFlow, (day, pid, direction)) is None:
                    db.add(GasFlow(date=day, point_id=pid, direction=direction, value_gwh=last_val, provisional=1, interpolated=1))
                    filled += 1
    return filled
