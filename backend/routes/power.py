"""Electricity + spark spread read endpoints.

GET /api/power/day-ahead?days=120&zone=DE_LU  — FREE
    ENTSO-E A44 daily mean EUR/MWh series for the requested bidding zone.
    Supported zones: DE_LU (default), FR, NL.
    Returns EnergyPrice rows + richer PowerPriceDaily stats (negative prices etc.)
    Each response includes `zone` (resolved) and `zones` (all supported zone keys).

GET /api/power/spark-spread?days=120  — PRO
    Historical spark spread (power − gas × heat_rate).
    DE-LU only (SparkSpreadHistory has no zone column); stays DE-only intentionally.

GET /api/power/grid?days=120&zone=DE_LU  — FREE
    ENTSO-E A65 (load) + A75 (wind + solar) for the requested bidding zone.
    Returns PowerGrid rows with residual_mw, renewable_share, dunkelflaute flag.

GET /api/power/generation-mix?days=30&zone=DE_LU  — FREE
    Full ENTSO-E A75 generation mix for the requested bidding zone.

GET /api/power/flows?days=30  — FREE
    Energy-Charts CBPF cross-border physical flows (CC BY 4.0).
    All real borders of DE-LU, FR, NL and their neighbours.
    net_mw > 0 = net export from from_zone to to_zone.

All endpoints follow the `{"available": bool, "data": [...]}` envelope used
throughout the gas vertical (see backend/routes/gas.py).
"""

import json
from datetime import date as _date
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models.energy import (
    EnergyPrice,
    PowerFlow,
    PowerGenMix,
    PowerGrid,
    PowerLoadForecast,
    PowerPriceDaily,
    SparkSpreadHistory,
)
from backend.power.coverage import renewable_share_reliable
from backend.power.energy_charts_flows import ATTRIBUTION
from backend.power.zones import DEFAULT_ZONE, POWER_ZONES
from backend.signals.detectors.base import severity_from_zscore, trailing_zscore

#: The situation hero is stale when its newest data lags wall-clock by more than
#: this many days. Day-ahead prices and realised grid data are ~daily, so a gap
#: beyond a day signals a frozen collector rather than normal publication lag.
SITUATION_STALE_DAYS = 1

router = APIRouter(prefix="/api/power", tags=["power"])

_ZONE_KEYS = list(POWER_ZONES.keys())


def _resolve_zone(zone: str) -> str:
    """Validate zone against POWER_ZONES; fall back to DEFAULT_ZONE with a note.

    Returns the resolved (canonical) zone key string.
    """
    if zone in POWER_ZONES:
        return zone
    # Unknown zone: fall back silently to default (400 is too loud for a
    # missing backfill; the response `zones` list tells the caller what's valid).
    return DEFAULT_ZONE


def _window(days: int) -> tuple[str, str]:
    """Return (start_iso, end_iso) for the last `days` calendar days (UTC)."""
    end = datetime.utcnow().date()
    start = end - timedelta(days=days)
    return start.isoformat(), end.isoformat()


# ─── Day-ahead prices (free) ──────────────────────────────────────────────────


@router.get("/day-ahead")
async def get_day_ahead(
    days: int = Query(120, ge=1, le=1500),
    zone: str = Query(DEFAULT_ZONE, description="Bidding zone key: DE_LU, FR, NL"),
    db: Session = Depends(get_db),
):
    """ENTSO-E day-ahead electricity prices for a bidding zone (EUR/MWh). Free tier.

    `zone` defaults to DE_LU. Unknown zones fall back to DE_LU.
    Each response includes `zone` (resolved) and `zones` (all supported zone keys).

    When PowerPriceDaily rows are available, each data point includes:
      close          — daily mean EUR/MWh (identical to EnergyPrice.close)
      min_price      — daily minimum EUR/MWh (can be negative)
      max_price      — daily maximum EUR/MWh
      negative_hours — hours where the auction price was < 0
      negative       — true if negative_hours > 0
    negative_days counts how many days in the window had at least one negative hour.
    latest contains the most recent row's fields.

    Falls back to EnergyPrice-only behaviour if PowerPriceDaily is empty.
    """
    resolved_zone = _resolve_zone(zone)
    zone_cfg = POWER_ZONES[resolved_zone]
    symbol = zone_cfg["price_symbol"]
    date_from, date_to = _window(days)

    # Primary path: richer PowerPriceDaily table
    daily_rows = (
        db.query(PowerPriceDaily)
        .filter(
            PowerPriceDaily.zone == resolved_zone,
            PowerPriceDaily.date >= date_from,
            PowerPriceDaily.date <= date_to,
        )
        .order_by(PowerPriceDaily.date.asc())
        .all()
    )

    if daily_rows:
        def _daily_dict(r: PowerPriceDaily) -> dict:
            return {
                "date": r.date,
                "close": r.mean_price,
                "min_price": r.min_price,
                "max_price": r.max_price,
                "negative_hours": r.negative_hours,
                "negative": r.negative_hours > 0,
            }

        data = [_daily_dict(r) for r in daily_rows]
        latest = data[-1]
        negative_days = sum(1 for d in data if d["negative"])
        return {
            "available": True,
            "zone": resolved_zone,
            "zones": _ZONE_KEYS,
            "symbol": symbol,
            "unit": "EUR/MWh",
            "from": date_from,
            "to": date_to,
            "negative_days": negative_days,
            "latest": latest,
            "data": data,
        }

    # Fallback: legacy EnergyPrice rows (no min/negative_hours available)
    rows = (
        db.query(EnergyPrice)
        .filter(
            EnergyPrice.symbol == symbol,
            EnergyPrice.date >= date_from,
            EnergyPrice.date <= date_to,
        )
        .order_by(EnergyPrice.date.asc())
        .all()
    )
    if not rows:
        return {
            "available": False,
            "zone": resolved_zone,
            "zones": _ZONE_KEYS,
            "reason": f"No day-ahead prices for {zone_cfg['label']} yet — check back shortly.",
        }
    data = [{"date": r.date, "close": r.close} for r in rows]
    return {
        "available": True,
        "zone": resolved_zone,
        "zones": _ZONE_KEYS,
        "symbol": symbol,
        "unit": "EUR/MWh",
        "from": date_from,
        "to": date_to,
        "negative_days": 0,
        "latest": data[-1],
        "data": data,
    }


def _dedupe_hourly(points: list) -> list[dict]:
    """Collapse a stored hourly series to one mean price per hour (ascending).

    Defensive: legacy rows (from before the parser aggregated) and any future
    ENTSO-E revision quirk can hold multiple points per hour — never show that.
    """
    acc: dict[int, list[float]] = {}
    for p in points or []:
        h, v = p.get("hour"), p.get("price")
        if h is None or v is None:
            continue
        acc.setdefault(int(h), []).append(float(v))
    return [{"hour": h, "price": round(sum(vs) / len(vs), 2)} for h, vs in sorted(acc.items())]


@router.get("/day-ahead/hourly")
async def get_day_ahead_hourly(
    zone: str = Query(DEFAULT_ZONE, description="Bidding zone key: DE_LU, FR, NL"),
    date: str = Query(None, description="YYYY-MM-DD; default = latest day with hourly data"),
    db: Session = Depends(get_db),
):
    """The 24 hourly day-ahead prices for one day — the peak/off-peak shape behind
    the daily mean. Free tier. Defaults to the most recent day that has an hourly
    series. Returns {available, zone, date, unit, data:[{"hour","price"}]}.
    """
    resolved_zone = _resolve_zone(zone)
    q = db.query(PowerPriceDaily).filter(
        PowerPriceDaily.zone == resolved_zone,
        PowerPriceDaily.hourly_prices.isnot(None),
    )
    if date:
        q = q.filter(PowerPriceDaily.date == date)
    row = q.order_by(PowerPriceDaily.date.desc()).first()
    if row is None or not row.hourly_prices:
        return {
            "available": False,
            "zone": resolved_zone,
            "zones": _ZONE_KEYS,
            "reason": f"No hourly day-ahead data for {POWER_ZONES[resolved_zone]['label']} yet — check back shortly.",
        }
    try:
        data = _dedupe_hourly(json.loads(row.hourly_prices))
    except (ValueError, TypeError):
        data = []
    return {
        "available": bool(data),
        "zone": resolved_zone,
        "zones": _ZONE_KEYS,
        "date": row.date,
        "unit": "EUR/MWh",
        "data": data,
    }


# ─── Spark spread (Pro) ───────────────────────────────────────────────────────


@router.get("/spark-spread")
async def get_spark_spread(
    days: int = Query(120, ge=7, le=1500),
    db: Session = Depends(get_db),
):
    """Spark spread history (power − gas × heat_rate, EUR/MWh).

    `latest` contains the most recent row for dashboard widgets.
    `data` is the full window sorted ascending for charting.
    CO₂ and clean-spark fields are included in the schema but will be null
    until EUA data ingestion is implemented.
    """
    date_from, date_to = _window(days)
    rows = (
        db.query(SparkSpreadHistory)
        .filter(
            SparkSpreadHistory.date >= date_from,
            SparkSpreadHistory.date <= date_to,
        )
        .order_by(SparkSpreadHistory.date.asc())
        .all()
    )
    if not rows:
        return {
            "available": False,
            "reason": "Spark spread isn't available yet — check back shortly.",
        }

    def _row_dict(r: SparkSpreadHistory) -> dict:
        return {
            "date": r.date,
            "power_price": r.power_price,
            "gas_price": r.gas_price,
            "heat_rate": r.heat_rate,
            "spark_spread": r.spark_spread,
            "co2_price": r.co2_price,
            "clean_spark_spread": r.clean_spark_spread,
        }

    latest = _row_dict(rows[-1])
    return {
        "available": True,
        "unit": "EUR/MWh",
        "heat_rate_note": "1 / CCGT_efficiency; default efficiency = 0.50",
        "co2_note": "co2_price and clean_spark_spread are deferred (EUA ticker TBD)",
        "latest": latest,
        "from": date_from,
        "to": date_to,
        "data": [_row_dict(r) for r in rows],
    }


# ─── Grid load + renewables (free) ───────────────────────────────────────────

#: Renewable share threshold below which a day is flagged as Dunkelflaute.
DUNKELFLAUTE_THRESHOLD = 0.15


def _compute_grid_row(r: PowerGrid) -> dict:
    """Derive residual_mw, renewable_share, and dunkelflaute flag for one row.

    None wind_mw / solar_mw are treated as 0 (they can be stored None when
    genuinely near-zero during ingest failures).
    """
    wind = r.wind_mw or 0.0
    solar = r.solar_mw or 0.0
    load = r.load_mw or 0.0

    residual_mw = load - wind - solar
    renewable_share = (wind + solar) / load if load > 0 else 0.0
    dunkelflaute = renewable_share < DUNKELFLAUTE_THRESHOLD

    return {
        "date": r.date,
        "load_mw": r.load_mw,
        "wind_mw": r.wind_mw,
        "solar_mw": r.solar_mw,
        "residual_mw": round(residual_mw, 2),
        "renewable_share": round(renewable_share, 4),
        "dunkelflaute": dunkelflaute,
    }


@router.get("/grid")
async def get_grid(
    days: int = Query(120, ge=1, le=1500),
    zone: str = Query(DEFAULT_ZONE, description="Bidding zone key: DE_LU, FR, NL"),
    db: Session = Depends(get_db),
):
    """ENTSO-E grid load + wind + solar for a bidding zone (daily mean MW). Free tier.

    `zone` defaults to DE_LU. Unknown zones fall back to DE_LU.
    Each response includes `zone` (resolved) and `zones` (all supported zone keys).

    Returns residual_mw (load − wind − solar), renewable_share, and a
    Dunkelflaute flag (renewable_share < 15%) per day.  `latest` contains
    the most recent row; `dunkelflaute_days` is the count within the window.
    """
    resolved_zone = _resolve_zone(zone)
    date_from, date_to = _window(days)
    rows = (
        db.query(PowerGrid)
        .filter(
            PowerGrid.zone == resolved_zone,
            PowerGrid.date >= date_from,
            PowerGrid.date <= date_to,
        )
        .order_by(PowerGrid.date.asc())
        .all()
    )
    if not rows:
        return {
            "available": False,
            "zone": resolved_zone,
            "zones": _ZONE_KEYS,
            "reason": f"No grid data for {POWER_ZONES[resolved_zone]['label']} yet — check back shortly.",
        }

    data = [_compute_grid_row(r) for r in rows]
    latest = data[-1]
    dunkelflaute_days = sum(1 for d in data if d["dunkelflaute"])

    return {
        "available": True,
        "zone": resolved_zone,
        "zones": _ZONE_KEYS,
        "unit": "MW",
        "threshold_note": f"dunkelflaute = renewable_share < {DUNKELFLAUTE_THRESHOLD:.0%}",
        "latest": latest,
        "dunkelflaute_days": dunkelflaute_days,
        "from": date_from,
        "to": date_to,
        "data": data,
    }


@router.get("/load-forecast")
async def get_load_forecast(
    days: int = Query(30, ge=1, le=365),
    zone: str = Query(DEFAULT_ZONE, description="Bidding zone key: DE_LU, FR, NL"),
    db: Session = Depends(get_db),
):
    """ENTSO-E day-ahead total-load forecast vs actual (daily mean MW). The trailing
    rows carry a forecast error; the newest row(s) with a forecast but no actual yet
    are the forward view (tomorrow's expected demand). Descriptive, not a price call."""
    resolved_zone = _resolve_zone(zone)
    date_from, _ = _window(days)

    fc_rows = (
        db.query(PowerLoadForecast)
        .filter(PowerLoadForecast.zone == resolved_zone, PowerLoadForecast.date >= date_from)
        .order_by(PowerLoadForecast.date.asc())
        .all()
    )
    if not fc_rows:
        return {
            "available": False,
            "zone": resolved_zone,
            "zones": _ZONE_KEYS,
            "reason": f"No load forecast for {POWER_ZONES[resolved_zone]['label']} yet — check back shortly.",
        }

    actual = {
        r.date: r.load_mw
        for r in db.query(PowerGrid)
        .filter(PowerGrid.zone == resolved_zone, PowerGrid.date >= date_from)
        .all()
    }

    data = []
    for r in fc_rows:
        a = actual.get(r.date)
        err = round(a - r.forecast_mw, 2) if a is not None else None
        err_pct = round((a - r.forecast_mw) / r.forecast_mw * 100, 2) if a is not None and r.forecast_mw else None
        data.append({
            "date": r.date,
            "forecast_mw": r.forecast_mw,
            "actual_mw": a,
            "error_mw": err,
            "error_pct": err_pct,
        })

    forward = [d for d in data if d["actual_mw"] is None]  # forecast without actual yet = tomorrow
    errs = [abs(d["error_pct"]) for d in data if d["error_pct"] is not None]
    mape = round(sum(errs) / len(errs), 1) if errs else None

    return {
        "available": True,
        "zone": resolved_zone,
        "zones": _ZONE_KEYS,
        "unit": "MW",
        "mape_pct": mape,  # mean absolute % forecast error over days with an actual
        "forward": forward,
        "from": date_from,
        "data": data,
    }


# ─── Power situation synthesis (the desk top-line) ───────────────────────────
#
# The coherence keystone of the power desk: instead of six unconnected panels,
# join day-ahead price → residual load → spark spread into ONE descriptive
# "so what" for the selected zone. Descriptive only (Posture B): we report the
# physical state + how far it sits from the series' own recent history, never a
# price forecast. Reused by the front-door situation header (and, later, the
# morning briefing).


def _series_zscore(series: list[dict], key: str) -> dict | None:
    """Trailing z-score of the latest `key` value vs the series' own prior history.

    Returns {"z", "baseline_mean", "baseline_n"} or None when there is no
    trustworthy baseline yet (too few points or zero variance).
    """
    vals = [r[key] for r in series if r.get(key) is not None]
    if len(vals) < 2:
        return None
    res = trailing_zscore(vals[-1], vals[:-1])
    if res is None:
        return None
    z, mean, _std, n = res
    return {"z": round(z, 2), "baseline_mean": round(mean, 2), "baseline_n": n}


_STATE_RANK = {"critical": 2, "warning": 1, "info": 0}
_STATE_LABEL = {2: "STRESSED", 1: "ELEVATED", 0: "CALM"}


def _worst_state(severities: list[str]) -> str:
    """Collapse a list of per-signal severities into one descriptive desk state."""
    rank = max((_STATE_RANK.get(s, 0) for s in severities), default=0)
    return _STATE_LABEL[rank]


def build_power_situation(
    zone: str,
    price_series: list[dict],
    grid_series: list[dict],
    spark_latest: dict | None,
    *,
    spark_supported: bool = True,
    grid_coverage_ok: bool = True,
    today: _date | None = None,
) -> dict:
    """Compose day-ahead price, residual load and spark spread into one descriptive
    power-situation top-line for `zone`. Pure: no DB, no network.

    price_series — ascending [{"date","close","negative_hours"}]
    grid_series  — ascending _compute_grid_row dicts (residual_mw/renewable_share/dunkelflaute)
    spark_latest — latest {"spark_spread","power_price","gas_price"} or None (DE-LU only)
    spark_supported — False for zones without a spark series (FR/NL) so the header can signpost.
    grid_coverage_ok — False when ENTSO-E generation coverage is too low to trust the
        renewable share (e.g. NL). The Dunkelflaute flag is then suppressed rather than
        raised off unreliable data.
    today — reference date for staleness assessment. When None, staleness is inert
        (`stale=False`, `age_days=None`) so existing pure call sites are unaffected.
    """
    zone_label = POWER_ZONES.get(zone, {}).get("label", zone)

    # ── price block ──
    price_latest = price_series[-1] if price_series else None
    price_z = _series_zscore(price_series, "close") if price_series else None
    neg_hours = (
        int(price_latest["negative_hours"])
        if price_latest and price_latest.get("negative_hours") is not None
        else 0
    )
    price = {
        "available": price_latest is not None,
        "close": round(price_latest["close"], 2) if price_latest else None,
        "negative_hours": neg_hours,
        "negative": neg_hours > 0,
        "z": price_z["z"] if price_z else None,
        "baseline_mean": price_z["baseline_mean"] if price_z else None,
        "baseline_n": price_z["baseline_n"] if price_z else None,
    }

    # ── grid block (residual load + Dunkelflaute) ──
    grid_latest = grid_series[-1] if grid_series else None
    resid_z = _series_zscore(grid_series, "residual_mw") if grid_series else None
    # Only trust the Dunkelflaute signal when generation coverage is high enough
    # (grid_coverage_ok). Incomplete A75 (NL) fakes a near-zero renewable share.
    raw_dunkelflaute = bool(grid_latest["dunkelflaute"]) if grid_latest else False
    dunkelflaute = raw_dunkelflaute and grid_coverage_ok
    resid_mw = grid_latest["residual_mw"] if grid_latest else None
    grid = {
        "available": grid_latest is not None,
        "residual_mw": resid_mw,
        "residual_gw": round(resid_mw / 1000.0, 2) if resid_mw is not None else None,
        "renewable_share": grid_latest["renewable_share"] if grid_latest else None,
        "renewable_share_reliable": grid_coverage_ok,
        "dunkelflaute": dunkelflaute,
        "z": resid_z["z"] if resid_z else None,
        "baseline_mean": resid_z["baseline_mean"] if resid_z else None,
        "baseline_n": resid_z["baseline_n"] if resid_z else None,
    }

    # ── spark block (DE-LU only; signpost on FR/NL) ──
    has_spark = spark_supported and spark_latest is not None
    spark = {
        "available": has_spark,
        "supported": spark_supported,
        "spark_spread": round(spark_latest["spark_spread"], 2) if has_spark else None,
        "power_price": round(spark_latest["power_price"], 2)
        if has_spark and spark_latest.get("power_price") is not None else None,
        "gas_price": round(spark_latest["gas_price"], 2)
        if has_spark and spark_latest.get("gas_price") is not None else None,
    }

    # ── flags + descriptive state ──
    severities: list[str] = []
    if price["z"] is not None:
        severities.append(severity_from_zscore(price["z"]))
    if grid["z"] is not None:
        severities.append(severity_from_zscore(grid["z"]))

    flags: list[dict] = []
    if dunkelflaute:
        flags.append({"key": "dunkelflaute", "severity": "warning",
                      "label": "Dunkelflaute — wind+solar < 15% of load"})
        severities.append("warning")
    if price["negative"]:
        flags.append({"key": "negative_prices", "severity": "warning",
                      "label": f"{neg_hours}h of negative day-ahead prices"})
        severities.append("warning")

    state = _worst_state(severities)
    available = price["available"] or grid["available"]
    date_candidates = [s[-1]["date"] for s in (price_series, grid_series) if s]
    as_of = max(date_candidates) if date_candidates else None

    # ── staleness (vs wall-clock) — never assert a confident state on days-old data ──
    age_days: int | None = None
    stale = False
    if today is not None and as_of is not None:
        try:
            age_days = (today - _date.fromisoformat(as_of)).days
            stale = age_days > SITUATION_STALE_DAYS
        except ValueError:
            age_days = None

    # ── headline (descriptive one-liner) ──
    parts: list[str] = []
    if price["available"]:
        seg = f"day-ahead €{price['close']:.0f}/MWh"
        if price["z"] is not None:
            seg += f" ({price['z']:+.1f}σ)"
        parts.append(seg)
    if grid["available"]:
        seg = f"residual {grid['residual_gw']:.0f} GW"
        if grid["z"] is not None:
            seg += f" ({grid['z']:+.1f}σ)"
        parts.append(seg)
    if spark["available"]:
        parts.append(f"spark €{spark['spark_spread']:+.0f}/MWh")
    headline = f"{zone_label} · " + " · ".join(parts) if parts else f"{zone_label} · no power data yet"

    return {
        "available": available,
        "zone": zone,
        "zone_label": zone_label,
        "zones": _ZONE_KEYS,
        "as_of": as_of,
        "stale": stale,
        "age_days": age_days,
        "state": state,
        "price": price,
        "grid": grid,
        "spark": spark,
        "flags": flags,
        "headline": headline,
    }


def load_power_situation(db: Session, zone: str) -> dict:
    """Load the DB slices for `zone` and compose the descriptive power situation.

    Extracted from the /situation route so the morning brief can reuse the exact
    same synthesis (day-ahead + residual load + Dunkelflaute + DE-LU spark →
    CALM/ELEVATED/STRESSED) without going through HTTP.
    """
    resolved_zone = _resolve_zone(zone)
    date_from, date_to = _window(120)

    price_rows = (
        db.query(PowerPriceDaily)
        .filter(
            PowerPriceDaily.zone == resolved_zone,
            PowerPriceDaily.date >= date_from,
            PowerPriceDaily.date <= date_to,
        )
        .order_by(PowerPriceDaily.date.asc())
        .all()
    )
    price_series = [
        {"date": r.date, "close": r.mean_price, "negative_hours": r.negative_hours}
        for r in price_rows
    ]

    grid_rows = (
        db.query(PowerGrid)
        .filter(
            PowerGrid.zone == resolved_zone,
            PowerGrid.date >= date_from,
            PowerGrid.date <= date_to,
        )
        .order_by(PowerGrid.date.asc())
        .all()
    )
    grid_series = [_compute_grid_row(r) for r in grid_rows]

    # Is the latest grid day's renewable share trustworthy? (NL A75 is incomplete.)
    grid_coverage_ok = True
    if grid_rows:
        latest_grid = grid_rows[-1]
        grid_coverage_ok = renewable_share_reliable(
            db, latest_grid.date, resolved_zone, latest_grid.load_mw
        )

    spark_supported = resolved_zone == DEFAULT_ZONE
    spark_latest = None
    if spark_supported:
        srow = (
            db.query(SparkSpreadHistory)
            .order_by(SparkSpreadHistory.date.desc())
            .first()
        )
        if srow is not None:
            spark_latest = {
                "spark_spread": srow.spark_spread,
                "power_price": srow.power_price,
                "gas_price": srow.gas_price,
            }

    return build_power_situation(
        resolved_zone,
        price_series,
        grid_series,
        spark_latest,
        spark_supported=spark_supported,
        grid_coverage_ok=grid_coverage_ok,
        today=datetime.utcnow().date(),
    )


@router.get("/situation")
async def get_situation(
    zone: str = Query(DEFAULT_ZONE, description="Bidding zone key: DE_LU, FR, NL"),
    db: Session = Depends(get_db),
):
    """One descriptive power-situation top-line for a bidding zone — the desk hero.

    Joins the most recent day-ahead price (with its z-context + negative-price
    flag), residual load + Dunkelflaute, and the DE-LU spark spread into a single
    `state` (CALM / ELEVATED / STRESSED) plus a `headline` and `flags`. Free tier.

    `zone` defaults to DE_LU; unknown zones fall back. Spark is DE-LU only and is
    marked `supported: false` for FR/NL so the header can signpost the gap.
    """
    return load_power_situation(db, zone)


# ─── Cross-border physical flows (free) ──────────────────────────────────────


def _zone_label(zone: str) -> str:
    """Human-readable label for a zone code.

    Zones not in POWER_ZONES (e.g. BE, CH, GB) use their code directly;
    zones in POWER_ZONES use their registered label (e.g. DE-LU).
    """
    cfg = POWER_ZONES.get(zone)
    return cfg["label"] if cfg else zone


def _border_label(from_zone: str, to_zone: str) -> str:
    """Human-readable border label, e.g. "DE-LU↔FR" or "BE↔DE-LU"."""
    return f"{_zone_label(from_zone)}↔{_zone_label(to_zone)}"


def _flow_direction(from_zone: str, to_zone: str, net_mw: float) -> str:
    """Arrow label for current net direction.

    Positive net_mw = from_zone → to_zone.
    Negative net_mw = to_zone → from_zone.
    """
    a = _zone_label(from_zone)
    b = _zone_label(to_zone)
    return f"{a}→{b}" if net_mw >= 0 else f"{b}→{a}"


@router.get("/flows")
async def get_flows(
    days: int = Query(30, ge=1, le=1500),
    db: Session = Depends(get_db),
):
    """Energy-Charts CBPF cross-border physical flows for all real borders. Free tier.

    Source: Fraunhofer ISE Energy-Charts /cbpf API (CC BY 4.0).
    Covers all real interconnectors of DE-LU, FR, and NL with their neighbours.
    The fictitious FR↔NL border (no physical interconnector) is excluded.

    Returns net daily mean MW per border (positive = from_zone→to_zone).
    `borders` — all distinct borders in the window, sorted by |net_mw| desc,
                 with the latest net_mw and direction label.
    `data`    — wide format: one row per date with one key per border arrow.
    `latest`  — most recent date values keyed by border arrow.
    `source`  — attribution string (CC BY 4.0 attribution required).
    """
    date_from, date_to = _window(days)

    rows = (
        db.query(PowerFlow)
        .filter(
            PowerFlow.date >= date_from,
            PowerFlow.date <= date_to,
        )
        .order_by(PowerFlow.date.asc())
        .all()
    )

    if not rows:
        return {
            "available": False,
            "reason": "No cross-border flow data yet — check back shortly.",
        }

    # Build wide format {date -> {border_arrow: net_mw}}
    pivot: dict[str, dict[str, float]] = {}
    for r in rows:
        arrow = f"{_zone_label(r.from_zone)}→{_zone_label(r.to_zone)}"
        pivot.setdefault(r.date, {})[arrow] = round(r.net_mw, 2)

    data = [{"date": d, **pivot[d]} for d in sorted(pivot.keys())]
    latest_date = sorted(pivot.keys())[-1]
    latest = {"date": latest_date, **pivot[latest_date]}

    # Per-border summary: discover all distinct borders in the window dynamically.
    # Get distinct (from_zone, to_zone) pairs that have data in the window
    pairs = (
        db.query(PowerFlow.from_zone, PowerFlow.to_zone)
        .filter(
            PowerFlow.date >= date_from,
            PowerFlow.date <= date_to,
        )
        .distinct()
        .all()
    )

    borders: list[dict] = []
    for from_zone, to_zone in pairs:
        border_row = (
            db.query(PowerFlow)
            .filter(
                PowerFlow.from_zone == from_zone,
                PowerFlow.to_zone == to_zone,
                PowerFlow.date >= date_from,
                PowerFlow.date <= date_to,
            )
            .order_by(PowerFlow.date.desc())
            .first()
        )
        if border_row is None:
            continue
        net = round(border_row.net_mw, 2)
        borders.append({
            "from_zone": from_zone,
            "to_zone": to_zone,
            "label": _border_label(from_zone, to_zone),
            "net_mw": net,
            "direction": _flow_direction(from_zone, to_zone, net),
        })

    # Sort by absolute net_mw descending (largest flows first)
    borders.sort(key=lambda b: abs(b["net_mw"]), reverse=True)

    return {
        "available": True,
        "unit": "MW",
        "source": ATTRIBUTION,
        "note": "net_mw > 0 = net physical flow from_zone→to_zone; Energy-Charts CBPF daily mean",
        "borders": borders,
        "latest": latest,
        "from": date_from,
        "to": date_to,
        "data": data,
    }


# ─── Generation mix (free) ────────────────────────────────────────────────────


@router.get("/generation-mix")
async def get_generation_mix(
    days: int = Query(30, ge=1, le=1500),
    zone: str = Query(DEFAULT_ZONE, description="Bidding zone key: DE_LU, FR, NL"),
    db: Session = Depends(get_db),
):
    """Full ENTSO-E A75 generation mix for a bidding zone (daily mean MW). Free tier.

    `zone` defaults to DE_LU. Unknown zones fall back to DE_LU.
    Each response includes `zone` (resolved) and `zones` (all supported zone keys).

    Returns data in wide/pivoted format: each row is one date with one key per
    production type (readable labels like "Solar", "Nuclear", "Wind Onshore").
    `types` lists all distinct production types present in the window.
    `latest` is the most recent date's breakdown plus a `total_mw` sum.
    """
    resolved_zone = _resolve_zone(zone)
    date_from, date_to = _window(days)
    rows = (
        db.query(PowerGenMix)
        .filter(
            PowerGenMix.zone == resolved_zone,
            PowerGenMix.date >= date_from,
            PowerGenMix.date <= date_to,
        )
        .order_by(PowerGenMix.date.asc(), PowerGenMix.psr_type.asc())
        .all()
    )
    if not rows:
        return {
            "available": False,
            "zone": resolved_zone,
            "zones": _ZONE_KEYS,
            "reason": f"No generation-mix data for {POWER_ZONES[resolved_zone]['label']} yet — check back shortly.",
        }

    # Pivot: {date -> {psr_type -> gen_mw}}
    pivot: dict[str, dict[str, float]] = {}
    for r in rows:
        pivot.setdefault(r.date, {})[r.psr_type] = r.gen_mw

    # Collect all distinct types (sorted for stable output)
    all_types: list[str] = sorted({r.psr_type for r in rows})

    # Build wide-format data list
    data = []
    for date_str in sorted(pivot.keys()):
        row_dict: dict = {"date": date_str}
        row_dict.update(pivot[date_str])
        data.append(row_dict)

    # Latest: most recent date + total
    latest_date = sorted(pivot.keys())[-1]
    latest_vals = pivot[latest_date]
    latest = {"date": latest_date, **latest_vals, "total_mw": round(sum(latest_vals.values()), 2)}

    return {
        "available": True,
        "zone": resolved_zone,
        "zones": _ZONE_KEYS,
        "unit": "MW",
        "types": all_types,
        "latest": latest,
        "from": date_from,
        "to": date_to,
        "data": data,
    }
