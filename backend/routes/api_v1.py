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

from backend.api_guard import cached_coverage, heavy_query_guard
from backend.auth.ratelimit import allow, client_ip
from backend.collectors.freshness import evaluate_freshness
from backend.database import get_db
from backend.models.energy import InstalledCapacity, PowerGenMix, PowerHourly, SeriesDim, ZoneDim
from backend.power.hourly_store import RowCapExceeded, read_hourly
from backend.power.zones import DEFAULT_ZONE, POWER_ZONES, ZONE_REGISTRY

router = APIRouter(prefix="/api/v1", tags=["v1"])

MAX_JSON_POINTS = 100_000  # beyond this, JSON is refused with a "use format=csv" hint
MAX_SCAN_ROWS = 1_500_000  # per-request row cap on a single-series read (csv/parquet too)
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


def _to_daily(points: list[tuple[int, float]]) -> list[tuple[str, float, int]]:
    """Aggregate hourly (ts_utc, value) to (date, daily-mean, hours-averaged).

    The hour count travels with the mean because the last day of a live series is usually a
    stump: a mean of nine night hours is not a day, and the desk printed one as the day's price.
    A caller that wants only settled days filters on `hours == 24`.
    """
    buckets: dict[str, list[float]] = {}
    for ts, v in points:
        day = datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d")
        buckets.setdefault(day, []).append(v)
    return [(d, round(sum(vs) / len(vs), 4), len(vs)) for d, vs in sorted(buckets.items())]


@router.get("/meta")
def meta(db: Session = Depends(get_db), _rl: None = Depends(_rate_limit)):
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
def status(db: Session = Depends(get_db), _rl: None = Depends(_rate_limit), _g: None = Depends(heavy_query_guard)):
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
def zones(_rl: None = Depends(_rate_limit)):
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
def genmix(
    zone: str = Query(DEFAULT_ZONE, description="Bidding zone key"),
    start: str | None = Query(None, description="YYYY-MM-DD / ISO 8601 (default: 1 year ago)"),
    end: str | None = Query(None, description="default: now"),
    resolution: str = Query("monthly", pattern="^(daily|monthly)$"),
    format: str = Query("json", pattern="^(json|csv)$"),
    db: Session = Depends(get_db),
    _rl: None = Depends(_rate_limit),
    _g: None = Depends(heavy_query_guard),
):
    """Generation mix over time — every fuel (gen.<psr>) for one zone, aggregated to
    daily or monthly mean MW, in a wide shape ({t, <fuel>: mw, ...}) for a stacked area.
    `format=csv` streams the same wide table as a download. Shows the energy transition
    per zone. Descriptive."""
    z = zone if zone in POWER_ZONES else DEFAULT_ZONE
    end_dt = _parse_ts(end, datetime.now(UTC))
    start_dt = _parse_ts(start, end_dt - timedelta(days=365))
    # Read the CANONICAL daily generation table (PowerGenMix, daily-mean ÷24 per
    # daily.py — a fuel absent at night counts as 0). This is the SAME source the
    # /api/power/generation-mix panel uses, so the public API and the desk can't
    # disagree. Averaging power_hourly over PUBLISHED hours (the old path) divided
    # solar by ~18 daylight hours, overstating it (IT-Nord ~29%). Settled days only.
    rows = (
        db.query(PowerGenMix.date, PowerGenMix.psr_type, PowerGenMix.gen_mw)
        .filter(
            PowerGenMix.zone == z,
            PowerGenMix.date >= start_dt.strftime("%Y-%m-%d"),
            PowerGenMix.date <= end_dt.strftime("%Y-%m-%d"),
        )
        .all()
    )
    if not rows:
        return {"available": False, "zone": z, "reason": "No generation-mix data yet."}
    monthly = resolution == "monthly"
    buckets: dict[str, dict[str, list[float]]] = {}
    for date, psr, mw in rows:
        pk = date[:7] if monthly else date
        buckets.setdefault(pk, {}).setdefault(psr, []).append(mw)
    data = []
    for pk in sorted(buckets):
        row: dict = {"t": pk}
        # daily: one value per fuel already; monthly: mean of the daily means.
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
def snapshot(
    series: str = Query("price.dayahead", description="Series key to snapshot"),
    hours: int = Query(168, ge=1, le=744, description="Lookback window (default 7 days)"),
    start: str | None = Query(None, description="Override window start (ISO / YYYY-MM-DD)"),
    end: str | None = Query(None, description="Override window end (default: now)"),
    db: Session = Depends(get_db),
    _rl: None = Depends(_rate_limit),
    _g: None = Depends(heavy_query_guard),
):
    """Per-zone hourly values for one series over a window, aligned to a common
    timestamp grid ({timestamps: [...], zones: {zone: [v, ...]}}). Powers the map
    time-scrubber — one call, then the client slides the index. Descriptive."""
    end_dt = _parse_ts(end, datetime.now(UTC))
    start_dt = _parse_ts(start, end_dt - timedelta(hours=hours))
    # start/end overrides mustn't defeat the `hours` cap: this scans every zone,
    # so an unbounded window is a full-table fan-out. Hold the same 744h ceiling.
    if end_dt - start_dt > timedelta(hours=744):
        return {"available": False, "series": series,
                "reason": "Snapshot window exceeds 744h — narrow it, or use /series for long ranges."}
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
def capacity(
    zone: str = Query(DEFAULT_ZONE, description="Bidding zone key"),
    year: int | None = Query(None, description="Year (default: latest available)"),
    db: Session = Depends(get_db),
    _rl: None = Depends(_rate_limit),
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


@router.get("/units")
def get_production_units(
    zone: str = Query(..., description="Bidding zone key"),
    db: Session = Depends(get_db),
    _rl: None = Depends(_rate_limit),
):
    """The zone's published production units (ENTSO-E A71/A33) — reference data.

    NOT the installed fleet. A71/A33 lists only units above ENTSO-E's ~100 MW publication
    threshold: DE-LU reports 52 GW here against 295 GW of A68 installed capacity. It is a
    different population, not a smaller sample — but it IS the population the A77 outages are
    drawn from, and unlike A68 it exists for all 37 zones.
    """
    from backend.models.energy import ProductionUnit
    from backend.power.entsoe_grid import PSR_LABELS

    year = (
        db.query(func.max(ProductionUnit.year))
        .filter(ProductionUnit.zone == zone).scalar()
    )
    if year is None:
        return {"available": False, "zone": zone,
                "reason": f"No production-unit registry for {zone} yet."}

    rows = (
        db.query(ProductionUnit)
        .filter(ProductionUnit.zone == zone, ProductionUnit.year == year)
        .order_by(ProductionUnit.nominal_mw.desc().nullslast())
        .all()
    )
    total = sum(r.nominal_mw or 0.0 for r in rows)
    return {
        "available": True,
        "zone": zone,
        "year": year,
        "units": [
            {
                "unit_eic": r.unit_eic,
                "name": r.name,
                "psr_type": r.psr_type,
                "fuel": PSR_LABELS.get(r.psr_type, r.psr_type),
                "nominal_mw": r.nominal_mw,
            }
            for r in rows
        ],
        "count": len(rows),
        "published_capacity_mw": round(total, 1),
        "note": (
            "Published production units (ENTSO-E A71/A33) — only units above the ~100 MW "
            "publication threshold, so this is NOT the installed fleet (see /api/v1/capacity, "
            "A68). It is the population the outage messages are drawn from."
        ),
    }


def _coverage_window(db: Session) -> dict:
    """The overall hourly coverage window — a global min/max over power_hourly.

    ts_utc is the 3rd column of the (series_id, zone_id, ts_utc) PK with no
    standalone index, so this is a full scan (~28s cold on prod). Cached for an
    hour via cached_coverage: the window only moves when the hourly ingest runs.
    """
    lo, hi = db.query(func.min(PowerHourly.ts_utc), func.max(PowerHourly.ts_utc)).first()
    return {
        "from": datetime.fromtimestamp(lo, UTC).isoformat() if lo else None,
        "to": datetime.fromtimestamp(hi, UTC).isoformat() if hi else None,
    }


@router.get("/series/catalog")
def catalog(db: Session = Depends(get_db), _rl: None = Depends(_rate_limit), _g: None = Depends(heavy_query_guard)):
    """What's queryable: every series (key+unit), enabled zones, and the overall
    hourly coverage window."""
    series = [{"key": k, "unit": u} for k, u in db.query(SeriesDim.key, SeriesDim.unit).order_by(SeriesDim.key).all()]
    return {
        "available": bool(series),
        "series": series,
        "zones": [{"key": k, "label": v["label"]} for k, v in POWER_ZONES.items()],
        "coverage": cached_coverage(lambda: _coverage_window(db)),
        "series_count": len(series),
    }


@router.get("/series")
def series(
    request: Request,
    series: str = Query(..., description="Series key, e.g. price.dayahead, load.actual, gen.B16"),
    zone: str = Query(..., description="Bidding zone key, e.g. DE_LU, FR, ES"),
    start: str | None = Query(None, description="YYYY-MM-DD or ISO 8601 (default: 30 days ago)"),
    end: str | None = Query(None, description="YYYY-MM-DD or ISO 8601 (default: everything on record)"),
    resolution: str = Query("hourly", pattern="^(hourly|daily)$"),
    format: str = Query("json", pattern="^(json|csv|parquet)$"),
    db: Session = Depends(get_db),
    _rl: None = Depends(_rate_limit),
    _g: None = Depends(heavy_query_guard),
):
    """One series for one zone over a time range — the core data endpoint.

    Reads the canonical hourly store; `resolution=daily` aggregates to a daily mean and reports
    how many hours each mean averaged (`hours`; 24 = a settled day).

    `end` has NO default ceiling. It used to default to `now`, which quietly cut off the hours a
    day-ahead auction has already published for the rest of the delivery day: the desk charted the
    mean of the nine hours that happened to have elapsed (132.6 EUR/MWh) next to a panel showing
    the full cleared day (123.8). The market's published future is data, not speculation.

    `format=csv` streams a download (unbounded range); `format=json` is capped at
    100k points (use CSV for larger pulls). Descriptive, not a forecast.
    """
    end_dt = _parse_ts(end, None) if end else None
    start_dt = _parse_ts(start, (end_dt or datetime.now(UTC)) - timedelta(days=DEFAULT_WINDOW_DAYS))
    if end_dt is not None and start_dt >= end_dt:
        raise HTTPException(status_code=400, detail="start must be before end.")

    unit = db.query(SeriesDim.unit).filter(SeriesDim.key == series).scalar()
    try:
        points = read_hourly(
            db, series, zone,
            int(start_dt.timestamp()),
            int(end_dt.timestamp()) if end_dt is not None else None,
            max_rows=MAX_SCAN_ROWS,
        )
    except RowCapExceeded:
        return {
            "available": False, "series": series, "zone": zone, "resolution": resolution,
            "reason": (f"Range matches more than {MAX_SCAN_ROWS:,} rows — narrow start/end. "
                       "This is a per-request cap, not the coverage limit."),
        }

    if resolution == "daily":
        rows = [{"date": d, "value": v, "hours": n} for d, v, n in _to_daily(points)]
        tkey = "date"
        cols = ("date", "value", "hours")
    else:
        rows = [
            {"datetime_utc": datetime.fromtimestamp(ts, UTC).isoformat(), "value": v}
            for ts, v in points
        ]
        tkey = "datetime_utc"
        cols = ("datetime_utc", "value")

    if format == "csv":
        zsafe = zone.replace("/", "_")
        fname = f"{series}_{zsafe}_{resolution}.csv"

        def _gen():
            yield ",".join(cols) + "\n"
            for r in rows:
                yield ",".join(str(r[c]) for c in cols) + "\n"

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
        table = pa.table({c: [r[c] for r in rows] for c in cols})
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
        # The window actually served: with no `end` the ceiling is the record itself, and saying
        # "to: now" for data that runs past now would be the same lie in a different field.
        "to": end_dt.isoformat() if end_dt is not None else (rows[-1][tkey] if rows else None),
        "count": len(rows),
        "data": rows,
    }
