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
from backend.collectors.freshness import evaluate_freshness
from backend.database import get_db
from backend.models.energy import InstalledCapacity, PowerHourly, SeriesDim, ZoneDim
from backend.power.entsoe_grid import PSR_LABELS
from backend.power.hourly_store import read_hourly
from backend.power.zones import DEFAULT_ZONE, POWER_ZONES, ZONE_REGISTRY

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


@router.get("/status")
async def status(db: Session = Depends(get_db)):
    """Honest data-coverage view: per-source + per-zone freshness for the power/gas
    desk (from the shared freshness spec). `healthy` is true when every product-critical
    source is within its window. The transparency answer to a black-box feed — 'here is
    exactly what is fresh and what is stale right now.'"""
    fr = evaluate_freshness(db)
    # Only the power/gas desk's own sources (the dormant non-power probes stay internal).
    keep = {"power_flows", "gas_balance", "ttf"}
    items = [
        {"key": k, "fresh": v["fresh"], "last_seen": v["last_seen"], "max_age_days": v["max_age_days"]}
        for k, v in sorted(fr.items())
        if k in keep or k.startswith("power_dayahead:") or k.startswith("power_grid:")
    ]
    healthy = all(i["fresh"] for i in items) if items else False
    return {
        "healthy": healthy,
        "fresh_count": sum(1 for i in items if i["fresh"]),
        "total": len(items),
        "sources": items,
    }


@router.get("/zones")
async def zones():
    """Every bidding zone in the registry with its enablement + flow-mapping flags —
    the single source of truth for zone selectors/navigation across the frontend."""
    enabled = set(POWER_ZONES)
    return {
        "default": DEFAULT_ZONE,
        "enabled_keys": [z for z in ZONE_REGISTRY if z in enabled],
        "zones": [
            {
                "key": k,
                "label": v["label"],
                "ec_country": v.get("ec_country"),
                "has_flows": v.get("ec_country") is not None,
                "enabled": k in enabled,
            }
            for k, v in ZONE_REGISTRY.items()
        ],
    }


@router.get("/genmix")
async def genmix(
    zone: str = Query(DEFAULT_ZONE, description="Bidding zone key"),
    start: str | None = Query(None, description="YYYY-MM-DD / ISO 8601 (default: 1 year ago)"),
    end: str | None = Query(None, description="default: now"),
    resolution: str = Query("monthly", pattern="^(daily|monthly)$"),
    format: str = Query("json", pattern="^(json|csv)$"),
    db: Session = Depends(get_db),
):
    """Generation mix over time — every fuel (gen.<psr>) for one zone, aggregated to
    daily or monthly mean MW, in a wide shape ({t, <fuel>: mw, ...}) for a stacked area.
    `format=csv` streams the same wide table as a download. Shows the energy transition
    per zone. Descriptive."""
    z = zone if zone in POWER_ZONES else DEFAULT_ZONE
    end_dt = _parse_ts(end, datetime.now(UTC))
    start_dt = _parse_ts(start, end_dt - timedelta(days=365))
    zid = db.query(ZoneDim.id).filter(ZoneDim.key == z).scalar()
    gen = db.query(SeriesDim.id, SeriesDim.key).filter(SeriesDim.key.like("gen.%")).all()
    if zid is None or not gen:
        return {"available": False, "zone": z, "reason": "No generation-mix data yet."}
    sid_label = {sid: PSR_LABELS.get(key.split(".", 1)[1], key) for sid, key in gen}
    rows = (
        db.query(PowerHourly.series_id, PowerHourly.ts_utc, PowerHourly.value)
        .filter(
            PowerHourly.zone_id == zid,
            PowerHourly.series_id.in_(list(sid_label)),
            PowerHourly.ts_utc >= int(start_dt.timestamp()),
            PowerHourly.ts_utc < int(end_dt.timestamp()),
        )
        .all()
    )
    fmt = "%Y-%m-%d" if resolution == "daily" else "%Y-%m"
    buckets: dict[str, dict[str, list[float]]] = {}
    for sid, ts, v in rows:
        pk = datetime.fromtimestamp(ts, UTC).strftime(fmt)
        buckets.setdefault(pk, {}).setdefault(sid_label[sid], []).append(v)
    data = []
    for pk in sorted(buckets):
        row: dict = {"t": pk}
        for label, vals in buckets[pk].items():
            row[label] = round(sum(vals) / len(vals), 1)
        data.append(row)
    fuels = sorted({label for per in buckets.values() for label in per})

    if format == "csv":
        zsafe = z.replace("/", "_")
        fname = f"genmix_{zsafe}_{resolution}.csv"
        header = "t" + ("," + ",".join(fuels) if fuels else "") + "\n"

        def _gen():
            yield header
            for row in data:
                yield ",".join([row["t"]] + [str(row.get(f, "")) for f in fuels]) + "\n"

        return StreamingResponse(
            _gen(),
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="{fname}"',
                "X-Attribution": "ENTSO-E",
            },
        )

    return {
        "available": bool(data),
        "zone": z,
        "resolution": resolution,
        "unit": "MW",
        "fuels": fuels,
        "from": start_dt.isoformat(),
        "to": end_dt.isoformat(),
        "data": data,
    }


@router.get("/snapshot")
async def snapshot(
    series: str = Query("price.dayahead", description="Series key to snapshot"),
    hours: int = Query(168, ge=1, le=744, description="Lookback window (default 7 days)"),
    start: str | None = Query(None, description="Override window start (ISO / YYYY-MM-DD)"),
    end: str | None = Query(None, description="Override window end (default: now)"),
    db: Session = Depends(get_db),
):
    """Per-zone hourly values for one series over a window, aligned to a common
    timestamp grid ({timestamps: [...], zones: {zone: [v, ...]}}). Powers the map
    time-scrubber — one call, then the client slides the index. Descriptive."""
    end_dt = _parse_ts(end, datetime.now(UTC))
    start_dt = _parse_ts(start, end_dt - timedelta(hours=hours))
    sid = db.query(SeriesDim.id).filter(SeriesDim.key == series).scalar()
    unit = db.query(SeriesDim.unit).filter(SeriesDim.key == series).scalar()
    if sid is None:
        return {"available": False, "series": series, "reason": "Unknown series."}
    zid_key = {zid: k for k, zid in db.query(ZoneDim.key, ZoneDim.id).all() if k in POWER_ZONES}
    rows = (
        db.query(PowerHourly.zone_id, PowerHourly.ts_utc, PowerHourly.value)
        .filter(
            PowerHourly.series_id == sid,
            PowerHourly.ts_utc >= int(start_dt.timestamp()),
            PowerHourly.ts_utc < int(end_dt.timestamp()),
        )
        .all()
    )
    by: dict[tuple[str, int], float] = {}
    ts_set: set[int] = set()
    for zid, ts, v in rows:
        key = zid_key.get(zid)
        if key is None:
            continue
        by[(key, ts)] = v
        ts_set.add(ts)
    timestamps = sorted(ts_set)
    zones: dict[str, list] = {}
    for key in zid_key.values():
        col = [round(by[(key, ts)], 2) if (key, ts) in by else None for ts in timestamps]
        if any(x is not None for x in col):
            zones[key] = col
    return {
        "available": bool(timestamps and zones),
        "series": series,
        "unit": unit,
        "timestamps": [datetime.fromtimestamp(ts, UTC).isoformat() for ts in timestamps],
        "zones": zones,
    }


@router.get("/capacity")
async def capacity(
    zone: str = Query(DEFAULT_ZONE, description="Bidding zone key"),
    year: int | None = Query(None, description="Year (default: latest available)"),
    db: Session = Depends(get_db),
):
    """Installed generation capacity per production type (MW) for a zone-year — ENTSO-E
    A68 annual reference data. Defaults to the latest available year. Pan-EU context
    (how much wind/solar/gas/etc. a zone has), descriptive."""
    z = zone if zone in POWER_ZONES else DEFAULT_ZONE
    if year is None:
        year = db.query(func.max(InstalledCapacity.year)).filter(InstalledCapacity.zone == z).scalar()
    if year is None:
        return {"available": False, "zone": z, "reason": "No installed-capacity data yet."}
    rows = (
        db.query(InstalledCapacity)
        .filter(InstalledCapacity.zone == z, InstalledCapacity.year == year)
        .order_by(InstalledCapacity.capacity_mw.desc())
        .all()
    )
    return {
        "available": bool(rows),
        "zone": z,
        "year": year,
        "unit": "MW",
        "total_mw": round(sum(r.capacity_mw for r in rows), 1),
        "data": [{"psr_type": r.psr_type, "capacity_mw": r.capacity_mw} for r in rows],
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
    format: str = Query("json", pattern="^(json|csv|parquet)$"),
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

    if format == "parquet":
        try:
            import io

            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError:
            raise HTTPException(
                status_code=501, detail="Parquet export is unavailable on this server; use format=csv."
            ) from None
        table = pa.table({tkey: [t for t, _ in rows], "value": [v for _, v in rows]})
        buf = io.BytesIO()
        pq.write_table(table, buf)
        zsafe = zone.replace("/", "_")
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="application/vnd.apache.parquet",
            headers={
                "Content-Disposition": f'attachment; filename="{series}_{zsafe}_{resolution}.parquet"',
                "X-Attribution": "ENTSO-E; Energy-Charts CC BY 4.0; GIE",
            },
        )

    if len(rows) > MAX_JSON_POINTS:
        return {
            "available": False,
            "series": series, "zone": zone, "resolution": resolution,
            "reason": f"{len(rows)} points exceed the JSON cap ({MAX_JSON_POINTS}); narrow the range or use format=csv or parquet.",
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
