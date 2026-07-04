"""Public data API v1 — the gridstatus-style programmatic layer over power_hourly.

A single generic series endpoint (JSON or CSV) plus a catalog and a meta/attribution
endpoint. Reads the canonical hourly store (backend/power/hourly_store.py). Free, but
lightly rate-limited per IP (reuses backend/auth/ratelimit). This is additive and
versioned — the legacy /api/power/* and /api/gas/* routes are unchanged.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.auth.ratelimit import allow, client_ip
from backend.database import get_db
from backend.models.energy import PowerHourly, SeriesDim
from backend.power.hourly_store import read_hourly
from backend.power.zones import POWER_ZONES

router = APIRouter(prefix="/api/v1", tags=["v1"])

MAX_JSON_POINTS = 100_000  # beyond this, JSON is refused with a "use format=csv" hint
DEFAULT_WINDOW_DAYS = 30
RATE_PER_MIN = 120  # per-IP requests/minute for the data API

ATTRIBUTION = [
    {"source": "ENTSO-E Transparency Platform", "for": "day-ahead prices, load, generation, forecasts",
     "license": "free reuse with attribution (ENTSO-E terms)"},
    {"source": "Fraunhofer Energy-Charts", "for": "cross-border physical flows", "license": "CC BY 4.0"},
    {"source": "GIE (AGSI/ALSI)", "for": "gas storage & LNG", "license": "free reuse with attribution"},
]
DISCLAIMER = (
    "Descriptive market observation from free official records — not investment advice, "
    "not a forecast. Values are hourly-canonical UTC; actuals carry a ~1h publication lag."
)


def _rate_limit(request: Request) -> None:
    if not allow([(f"v1:{client_ip(request)}", RATE_PER_MIN, 60.0)]):
        raise HTTPException(status_code=429, detail="Rate limit exceeded — slow down (120 req/min).")


def _parse_ts(s: str | None, default: datetime) -> datetime:
    if not s:
        return default
    try:
        if len(s) == 10:  # YYYY-MM-DD → midnight UTC
            return datetime.fromisoformat(s).replace(tzinfo=UTC)
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid datetime {s!r} (use YYYY-MM-DD or ISO 8601).") from exc


def _to_daily(points: list[tuple[int, float]]) -> list[tuple[str, float]]:
    """Aggregate hourly (ts_utc, value) to daily-mean keyed by UTC date string."""
    buckets: dict[str, list[float]] = {}
    for ts, v in points:
        day = datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d")
        buckets.setdefault(day, []).append(v)
    return [(d, round(sum(vs) / len(vs), 4)) for d, vs in sorted(buckets.items())]


@router.get("/meta")
async def meta(db: Session = Depends(get_db)):
    """Sources, licenses, enabled zones and available series — the API's front matter."""
    series = [{"key": k, "unit": u} for k, u in db.query(SeriesDim.key, SeriesDim.unit).order_by(SeriesDim.key).all()]
    return {
        "version": "v1",
        "zones": [{"key": k, "label": v["label"]} for k, v in POWER_ZONES.items()],
        "series": series,
        "resolutions": ["hourly", "daily"],
        "formats": ["json", "csv"],
        "attribution": ATTRIBUTION,
        "license": "AGPL-3.0-or-later",
        "disclaimer": DISCLAIMER,
    }


@router.get("/series/catalog")
async def catalog(db: Session = Depends(get_db)):
    """What's queryable: every series (key+unit), enabled zones, and the overall
    hourly coverage window."""
    series = [{"key": k, "unit": u} for k, u in db.query(SeriesDim.key, SeriesDim.unit).order_by(SeriesDim.key).all()]
    lo, hi = db.query(func.min(PowerHourly.ts_utc), func.max(PowerHourly.ts_utc)).first()
    return {
        "available": bool(series),
        "series": series,
        "zones": [{"key": k, "label": v["label"]} for k, v in POWER_ZONES.items()],
        "coverage": {
            "from": datetime.fromtimestamp(lo, UTC).isoformat() if lo else None,
            "to": datetime.fromtimestamp(hi, UTC).isoformat() if hi else None,
        },
        "series_count": len(series),
    }


@router.get("/series")
async def series(
    request: Request,
    series: str = Query(..., description="Series key, e.g. price.dayahead, load.actual, gen.B16"),
    zone: str = Query(..., description="Bidding zone key, e.g. DE_LU, FR, ES"),
    start: str | None = Query(None, description="YYYY-MM-DD or ISO 8601 (default: 30 days ago)"),
    end: str | None = Query(None, description="YYYY-MM-DD or ISO 8601 (default: now)"),
    resolution: str = Query("hourly", pattern="^(hourly|daily)$"),
    format: str = Query("json", pattern="^(json|csv)$"),
    db: Session = Depends(get_db),
    _rl: None = Depends(_rate_limit),
):
    """One series for one zone over a time range — the core data endpoint.

    Reads the canonical hourly store; `resolution=daily` aggregates to a daily mean.
    `format=csv` streams a download (unbounded range); `format=json` is capped at
    100k points (use CSV for larger pulls). Descriptive, not a forecast.
    """
    end_dt = _parse_ts(end, datetime.now(UTC))
    start_dt = _parse_ts(start, end_dt - timedelta(days=DEFAULT_WINDOW_DAYS))
    if start_dt >= end_dt:
        raise HTTPException(status_code=400, detail="start must be before end.")

    unit = db.query(SeriesDim.unit).filter(SeriesDim.key == series).scalar()
    points = read_hourly(db, series, zone, int(start_dt.timestamp()), int(end_dt.timestamp()))

    if resolution == "daily":
        rows = _to_daily(points)  # [(date_str, value)]
        tkey = "date"
    else:
        rows = [(datetime.fromtimestamp(ts, UTC).isoformat(), v) for ts, v in points]
        tkey = "datetime_utc"

    if format == "csv":
        zsafe = zone.replace("/", "_")
        fname = f"{series}_{zsafe}_{resolution}.csv"

        def _gen():
            yield f"{tkey},value\n"
            for t, v in rows:
                yield f"{t},{v}\n"

        return StreamingResponse(
            _gen(),
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="{fname}"',
                "X-Attribution": "ENTSO-E; Energy-Charts CC BY 4.0; GIE",
            },
        )

    if len(rows) > MAX_JSON_POINTS:
        return {
            "available": False,
            "series": series, "zone": zone, "resolution": resolution,
            "reason": f"{len(rows)} points exceed the JSON cap ({MAX_JSON_POINTS}); narrow the range or use format=csv.",
        }
    return {
        "available": bool(rows),
        "series": series,
        "zone": zone,
        "unit": unit,
        "resolution": resolution,
        "from": start_dt.isoformat(),
        "to": end_dt.isoformat(),
        "count": len(rows),
        "data": [{tkey: t, "value": v} for t, v in rows],
    }
