"""Fresh all-time day-ahead price records for the event tier — read from the
nightly-computed PowerRecord table (ONLY the canonical hourly series; the
.qh/.hh variants have short histories and their max is not a meaningful
extreme)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

FRESH_DAYS = 7


def fresh_price_records(db: Session, overview: dict, *, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    labels = {z["zone"]: z.get("zone_label", z["zone"]) for z in overview.get("zones", [])}
    try:
        from backend.models.energy import PowerRecord
    except Exception:
        return []
    cut = int((now - timedelta(days=FRESH_DAYS)).timestamp())
    rows = (db.query(PowerRecord)
            .filter(PowerRecord.series_key == "price.dayahead", PowerRecord.ts_utc >= cut)
            .all())
    return [{
        "fresh": True, "series": r.series_key, "kind": r.kind, "unit": r.unit,
        "value": r.value, "zone": r.zone, "zone_label": labels.get(r.zone, r.zone),
        "date": datetime.fromtimestamp(r.ts_utc, tz=timezone.utc).date().isoformat(),
    } for r in rows]
