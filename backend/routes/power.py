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
from datetime import datetime, timedelta, timezone
from datetime import datetime as _dt

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.config import settings
from backend.database import get_db
from backend.models.energy import (
    EnergyPrice,
    PowerFlow,
    PowerGenMix,
    PowerGrid,
    PowerLoadForecast,
    PowerPriceDaily,
)
from backend.power.coverage import renewable_share_reliable
from backend.power.energy_charts_flows import ATTRIBUTION
from backend.power.entsoe_grid import PSR_LABELS
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
            **_panel_freshness(data, "day_ahead"),
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
        **_panel_freshness(data, "day_ahead"),
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


def _day_ahead_qh(db: Session, zone: str, date: str | None) -> dict:
    """The raw 96-point 15-min auction curve for one delivery day, from the
    canonical store (series price.dayahead.qh)."""
    from backend.power.hourly_store import read_hourly

    if date:
        try:
            day_start = int(_dt.fromisoformat(date + "T00:00").replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            return {"available": False, "zone": zone, "zones": _ZONE_KEYS,
                    "reason": "Invalid date — expected YYYY-MM-DD."}
        points = read_hourly(db, "price.dayahead.qh", zone, day_start, day_start + 86_400)
    else:
        # Latest delivery day that has any QH data: read the series tail and
        # trim to the newest UTC day it covers.
        points = read_hourly(db, "price.dayahead.qh", zone)
        if points:
            newest_day = _dt.fromtimestamp(points[-1][0], tz=timezone.utc).strftime("%Y-%m-%d")
            day_start = int(_dt.fromisoformat(newest_day + "T00:00").replace(tzinfo=timezone.utc).timestamp())
            points = [(t, v) for t, v in points if t >= day_start]

    if not points:
        return {
            "available": False,
            "zone": zone,
            "zones": _ZONE_KEYS,
            "reason": "No 15-min day-ahead data for this day — SDAC trades 15-minute products since 2025-10-01.",
        }

    day = _dt.fromtimestamp(points[0][0], tz=timezone.utc).strftime("%Y-%m-%d")
    data = [
        {"time": _dt.fromtimestamp(t, tz=timezone.utc).strftime("%H:%M"), "price": round(v, 2)}
        for t, v in points
    ]
    return {
        "available": True,
        "zone": zone,
        "zones": _ZONE_KEYS,
        "date": day,
        "unit": "EUR/MWh",
        "resolution": "qh",
        "data": data,
    }


@router.get("/day-ahead/hourly")
async def get_day_ahead_hourly(
    zone: str = Query(DEFAULT_ZONE, description="Bidding zone key: DE_LU, FR, NL"),
    date: str = Query(None, description="YYYY-MM-DD; default = latest day with hourly data"),
    resolution: str = Query("hourly", pattern="^(hourly|qh)$",
                            description="hourly = 24 averaged points; qh = the raw 96-point 15-min auction (since 2025-10-01)"),
    db: Session = Depends(get_db),
):
    """The intraday day-ahead price shape for one day. Free tier.

    resolution=hourly (default): the 24 per-hour means — unchanged legacy shape.
    resolution=qh: the raw 96 quarter-hour auction points, exactly as traded —
    SDAC has traded 15-minute MTUs since delivery day 2025-10-01, so the hourly
    view is a smoothed picture of the real curve. Days before the switch have
    no qh series.
    """
    resolved_zone = _resolve_zone(zone)
    if resolution == "qh":
        return _day_ahead_qh(db, resolved_zone, date)
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
    zone: str = Query(DEFAULT_ZONE, description="Bidding zone key: DE_LU, FR, NL, …"),
    db: Session = Depends(get_db),
):
    """Spark spread (power − gas × heat_rate, EUR/MWh) for any zone.

    Computed live by aligning the zone's day-ahead price (EnergyPrice POWER_<zone>)
    with the TTF gas front-month on each date. The gas leg is TTF (the European
    benchmark hub) for every zone — a per-zone gas hub is a later refinement. CO₂/
    clean-spark stay null until a free EUA source is confirmed. `latest` is the most
    recent row; `data` is the ascending window for charting.
    """
    resolved_zone = _resolve_zone(zone)
    symbol = POWER_ZONES[resolved_zone]["price_symbol"]
    heat_rate = round(1.0 / settings.gas_ccgt_efficiency, 4)
    date_from, date_to = _window(days)

    def _prices(sym: str) -> dict[str, float]:
        return {
            r.date: r.close
            for r in db.query(EnergyPrice).filter(
                EnergyPrice.symbol == sym,
                EnergyPrice.date >= date_from,
                EnergyPrice.date <= date_to,
            )
        }

    power_by = _prices(symbol)
    ttf_by = _prices("TTF")
    dates = sorted(set(power_by) & set(ttf_by))
    if not dates:
        return {
            "available": False,
            "zone": resolved_zone,
            "zones": _ZONE_KEYS,
            "reason": f"Spark spread isn't available for {POWER_ZONES[resolved_zone]['label']} yet — check back shortly.",
        }

    data = [
        {
            "date": d,
            "power_price": power_by[d],
            "gas_price": ttf_by[d],
            "heat_rate": heat_rate,
            "spark_spread": round(power_by[d] - ttf_by[d] * heat_rate, 4),
            "co2_price": None,
            "clean_spark_spread": None,
        }
        for d in dates
    ]
    return {
        "available": True,
        "zone": resolved_zone,
        "zones": _ZONE_KEYS,
        "unit": "EUR/MWh",
        "heat_rate_note": "1 / CCGT_efficiency; default efficiency = 0.50",
        "gas_leg_note": "gas leg = TTF (European benchmark) for all zones; per-zone hub is a later refinement",
        "co2_note": "co2_price and clean_spark_spread are deferred (EUA ticker TBD)",
        "latest": data[-1],
        "from": date_from,
        "to": date_to,
        "data": data,
        **_panel_freshness(data, "spark"),
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
        **_panel_freshness(data, "grid"),
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
        r.date: r
        for r in db.query(PowerGrid)
        .filter(PowerGrid.zone == resolved_zone, PowerGrid.date >= date_from)
        .all()
    }

    data = []
    for r in fc_rows:
        ar = actual.get(r.date)
        a = ar.load_mw if ar else None
        err = round(a - r.forecast_mw, 2) if a is not None else None
        err_pct = round((a - r.forecast_mw) / r.forecast_mw * 100, 2) if a is not None and r.forecast_mw else None
        # Residual-load forecast = load − wind − solar (the price-driving forward quantity).
        resid_fc = None
        if r.wind_forecast_mw is not None and r.solar_forecast_mw is not None:
            resid_fc = round(r.forecast_mw - r.wind_forecast_mw - r.solar_forecast_mw, 2)
        data.append({
            "date": r.date,
            "forecast_mw": r.forecast_mw,
            "actual_mw": a,
            "error_mw": err,
            "error_pct": err_pct,
            "wind_forecast_mw": r.wind_forecast_mw,
            "solar_forecast_mw": r.solar_forecast_mw,
            "residual_forecast_mw": resid_fc,
            "residual_actual_mw": ar.residual_mw if ar else None,
        })

    forward = [d for d in data if d["actual_mw"] is None]  # forecast without actual yet = tomorrow
    errs = [abs(d["error_pct"]) for d in data if d["error_pct"] is not None]
    mape = round(sum(errs) / len(errs), 1) if errs else None

    return {
        "available": True,
        "zone": resolved_zone,
        "zones": _ZONE_KEYS,
        "unit": "MW",
        "mape_pct": mape,  # mean absolute % load-forecast error over days with an actual
        "forward": forward,
        "from": date_from,
        "data": data,
        **_panel_freshness(data, "load_forecast"),
    }


@router.get("/load-forecast/hourly")
async def get_load_forecast_hourly(
    zone: str = Query(DEFAULT_ZONE, description="Bidding zone key: DE_LU, FR, NL"),
    date: str = Query(None, description="YYYY-MM-DD; default = latest forecast day (tomorrow)"),
    db: Session = Depends(get_db),
):
    """Tomorrow's hour-by-hour residual-load forecast (load − wind − solar) — the
    price-driving forward curve behind the daily-mean forecast: the evening ramp,
    the midday solar trough, Dunkelflaute windows. Free tier. Defaults to the latest
    forecast day (tomorrow). Returns {available, zone, date, unit,
    data:[{hour, load_mw, wind_mw, solar_mw, residual_mw}]}. Descriptive, not a price call.
    """
    resolved_zone = _resolve_zone(zone)
    q = db.query(PowerLoadForecast).filter(
        PowerLoadForecast.zone == resolved_zone,
        PowerLoadForecast.hourly_forecast.isnot(None),
    )
    if date:
        q = q.filter(PowerLoadForecast.date == date)
    row = q.order_by(PowerLoadForecast.date.desc()).first()
    if row is None or not row.hourly_forecast:
        return {
            "available": False,
            "zone": resolved_zone,
            "zones": _ZONE_KEYS,
            "reason": f"No hourly load forecast for {POWER_ZONES[resolved_zone]['label']} yet — check back shortly.",
        }
    try:
        data = json.loads(row.hourly_forecast)
    except (ValueError, TypeError):
        data = []
    return {
        "available": bool(data),
        "zone": resolved_zone,
        "zones": _ZONE_KEYS,
        "date": row.date,
        "unit": "MW",
        "data": data,
    }


@router.get("/overview")
async def get_power_overview(db: Session = Depends(get_db)):
    """All bidding zones at a glance — the single-glance overview (rows = zones,
    each with its state + key metrics + vs-normal z-scores). Reuses the exact
    per-zone synthesis (load_power_situation) so the overview and the detail agree."""
    zones = []
    for zone in POWER_ZONES:
        try:
            sit = load_power_situation(db, zone)
        except Exception:
            continue
        if not sit.get("available"):
            continue
        price = sit.get("price", {})
        grid = sit.get("grid", {})
        zones.append({
            "zone": sit.get("zone"),
            "zone_label": sit.get("zone_label"),
            "state": sit.get("state", "CALM"),
            "stale": bool(sit.get("stale")),
            "as_of": sit.get("as_of"),
            "price_close": price.get("close"),
            "price_z": price.get("z"),
            "residual_gw": grid.get("residual_gw"),
            "residual_z": grid.get("z"),
            "renewable_share": grid.get("renewable_share"),
            "renewable_reliable": grid.get("renewable_share_reliable"),
            "dunkelflaute": grid.get("dunkelflaute"),
        })
    return {"available": bool(zones), "zones": zones}


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


# Panel-caption freshness thresholds. Values mirror the per-source windows in
# backend/collectors/freshness.py::SPECS (enforced by test_power_panel_freshness),
# so the UI captions and /api/health/collectors can never disagree. generation_mix
# shares the grid window (same A75 ingest); load_forecast is frontier-based.
PANEL_MAX_AGE_DAYS = {
    "day_ahead": 2,
    "grid": 3,
    "generation_mix": 3,
    "flows": 3,
    "spark": 4,  # gas leg is yfinance TTF — ~3 trading days plus weekends
    "load_forecast": 2,
}


def _freshness(as_of: str | None, today: _date | None,
               max_age_days: int = SITUATION_STALE_DAYS) -> dict:
    """as_of/age_days/stale triple for ONE component. Inert without `today`.

    Every component carries its own freshness because a fresh series must never
    mask a stale one: after the 2026-07-07 outage, prices resumed a day before
    the grid series did, and the old top-level `as_of = max(...)` presented
    days-old residual/renewables figures as current.
    """
    age_days: int | None = None
    stale = False
    if today is not None and as_of is not None:
        try:
            age_days = (today - _date.fromisoformat(as_of)).days
            stale = age_days > max_age_days
        except ValueError:
            age_days = None
    return {"as_of": as_of, "age_days": age_days, "stale": stale}


def _panel_freshness(data: list[dict], panel: str) -> dict:
    """Freshness triple for a detail endpoint from its ascending `data` rows."""
    latest = data[-1]["date"] if data else None
    return _freshness(latest, datetime.utcnow().date(), PANEL_MAX_AGE_DAYS[panel])


def build_power_situation(
    zone: str,
    price_series: list[dict],
    grid_series: list[dict],
    spark_latest: dict | None,
    *,
    spark_supported: bool = True,
    grid_coverage_ok: bool = True,
    forced_outage_mw: float | None = None,
    forced_outage_installed_mw: float | None = None,
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
        **_freshness(price_latest["date"] if price_latest else None, today),
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
        **_freshness(grid_latest["date"] if grid_latest else None, today),
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
        **_freshness(spark_latest.get("date") if has_spark else None, today),
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
    # None = the outage feed was not consulted; only a real figure can flag.
    # Severity comes from the radar detector's shared derivation (capacity-
    # relative where A68 is known, absolute fallback elsewhere) — hero and
    # radar cannot disagree.
    if forced_outage_mw is not None:
        from backend.signals.detectors.power import forced_outage_severity

        fo_sev = forced_outage_severity(forced_outage_mw, forced_outage_installed_mw)
        if fo_sev is not None:
            share_txt = (
                f" — {forced_outage_mw / forced_outage_installed_mw * 100:.0f}% of fleet"
                if forced_outage_installed_mw else ""
            )
            flags.append({"key": "forced_outages", "severity": fo_sev,
                          "label": f"{forced_outage_mw / 1000:.1f} GW forced outages{share_txt}"})
            severities.append(fo_sev)

    state = _worst_state(severities)
    available = price["available"] or grid["available"]
    date_candidates = [s[-1]["date"] for s in (price_series, grid_series) if s]
    as_of = max(date_candidates) if date_candidates else None

    # ── staleness (vs wall-clock) — never assert a confident state on days-old data ──
    # Top-level as_of stays the NEWEST component date, but stale is worst-of: any
    # available component lagging makes the whole situation stale. `max()` alone
    # would let a fresh price mask a days-old grid series.
    top = _freshness(as_of, today)
    age_days = top["age_days"]
    stale = any(c["stale"] for c in (price, grid, spark) if c["available"])

    # ── headline (descriptive one-liner) ──
    def _age_suffix(comp: dict) -> str:
        return f" ({comp['age_days']}d old)" if comp["stale"] else ""

    parts: list[str] = []
    if price["available"]:
        seg = f"day-ahead €{price['close']:.0f}/MWh"
        if price["z"] is not None:
            seg += f" ({price['z']:+.1f}σ)"
        parts.append(seg + _age_suffix(price))
    if grid["available"]:
        seg = f"residual {grid['residual_gw']:.0f} GW"
        if grid["z"] is not None:
            seg += f" ({grid['z']:+.1f}σ)"
        parts.append(seg + _age_suffix(grid))
    if spark["available"]:
        parts.append(f"spark €{spark['spark_spread']:+.0f}/MWh" + _age_suffix(spark))
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


def _latest_spark(db: Session, zone: str) -> dict | None:
    """Latest live spark for `zone` — the same derivation as /spark-spread
    (power − TTF × heat_rate on the newest day both series cover), so the
    situation hero and the panel can never disagree. None when either leg
    has no recent data."""
    symbol = POWER_ZONES[zone]["price_symbol"]
    heat_rate = round(1.0 / settings.gas_ccgt_efficiency, 4)
    date_from, date_to = _window(14)

    def _closes(sym: str) -> dict[str, float]:
        return {
            r.date: r.close
            for r in db.query(EnergyPrice).filter(
                EnergyPrice.symbol == sym,
                EnergyPrice.date >= date_from,
                EnergyPrice.date <= date_to,
            )
        }

    power_by = _closes(symbol)
    ttf_by = _closes("TTF")
    common = sorted(set(power_by) & set(ttf_by))
    if not common:
        return None
    d = common[-1]
    return {
        "date": d,
        "power_price": power_by[d],
        "gas_price": ttf_by[d],
        "spark_spread": round(power_by[d] - ttf_by[d] * heat_rate, 4),
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

    # Derive the spark live from the price series — the exact same computation as
    # /spark-spread (gas leg = TTF for every zone), so the hero and the panel below
    # it can never disagree. The old SparkSpreadHistory read was DE-only and made
    # the hero claim "DE-LU only" while the panel showed a real FR/NL spark.
    spark_latest = _latest_spark(db, resolved_zone)

    # Forced-outage aggregate via the radar detector's helpers — hero flag and
    # radar alert share one derivation (highest revision, withdrawals hidden,
    # capacity-relative severity where A68 covers the zone).
    from backend.signals.detectors.power import forced_outage_mw_now, installed_capacity_mw

    forced_mw, _ = forced_outage_mw_now(db, resolved_zone)
    installed_mw = installed_capacity_mw(db, resolved_zone)

    return build_power_situation(
        resolved_zone,
        price_series,
        grid_series,
        spark_latest,
        spark_supported=True,
        grid_coverage_ok=grid_coverage_ok,
        forced_outage_mw=forced_mw,
        forced_outage_installed_mw=installed_mw,
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
        **_panel_freshness(data, "flows"),
    }


# ─── Forecast error (free) ────────────────────────────────────────────────────

#: forecast series → the series whose SUM is the realised counterpart.
#: There is no wind.actual/solar.actual — realised wind/solar live in the
#: generation mix (B18+B19 / B16), same derivation the residual ingest uses.
_FORECAST_PAIRS: dict[str, tuple[str, list[str]]] = {
    "load": ("load.forecast", ["load.actual"]),
    "residual": ("residual.forecast", ["residual.actual"]),
    "wind": ("wind.forecast", ["gen.B18", "gen.B19"]),
    "solar": ("solar.forecast", ["gen.B16"]),
}


@router.get("/forecast-error")
async def get_forecast_error(
    zone: str = Query(DEFAULT_ZONE, description="Bidding zone key"),
    series: str = Query("load", pattern="^(load|residual|wind|solar)$"),
    days: int = Query(7, ge=1, le=90),
    db: Session = Depends(get_db),
):
    """How good was the published TSO day-ahead forecast, in numbers. Free tier.

    bias_mw = mean(actual − forecast): positive means the forecast leaned LOW
    (demand surprise / renewables over-delivered). mae_mw is the typical
    per-hour miss. Only hours where both sides exist count. Posture B: this
    describes ENTSO-E's OWN published forecast — no forecast claim of ours.
    """
    from backend.power.hourly_store import read_hourly

    resolved_zone = _resolve_zone(zone)
    fc_key, actual_keys = _FORECAST_PAIRS[series]
    start_ts = int((datetime.utcnow() - timedelta(days=days)).timestamp())

    forecast = dict(read_hourly(db, fc_key, resolved_zone, start_ts))
    actual: dict[int, float] = {}
    for key in actual_keys:
        for t, v in read_hourly(db, key, resolved_zone, start_ts):
            actual[t] = actual.get(t, 0.0) + v

    common = sorted(set(forecast) & set(actual))
    if not common:
        return {
            "available": False,
            "zone": resolved_zone,
            "zones": _ZONE_KEYS,
            "series": series,
            "reason": "No overlapping forecast/actual hours in the window yet.",
        }

    errors = [actual[t] - forecast[t] for t in common]
    return {
        "available": True,
        "zone": resolved_zone,
        "zones": _ZONE_KEYS,
        "series": series,
        "days": days,
        "n_hours": len(common),
        "bias_mw": round(sum(errors) / len(errors), 1),
        "mae_mw": round(sum(abs(e) for e in errors) / len(errors), 1),
        "note": "bias = mean(actual − forecast); describes the published TSO forecast",
    }


# ─── Records (free) ───────────────────────────────────────────────────────────

#: A record set within this many days is "the story", not archive trivia.
RECORD_FRESH_DAYS = 7


@router.get("/records")
async def get_records(
    zone: str = Query(DEFAULT_ZONE, description="Bidding zone key"),
    db: Session = Depends(get_db),
):
    """All-time extremes per series for a zone — "highest day-ahead hour on
    record", with the evidence date. Descriptive archive facts, recomputed
    nightly; `fresh` flags records set in the last week. Free tier."""
    from backend.models.energy import PowerRecord

    resolved_zone = _resolve_zone(zone)
    rows = db.query(PowerRecord).filter(PowerRecord.zone == resolved_zone).all()
    if not rows:
        return {
            "available": False,
            "zone": resolved_zone,
            "zones": _ZONE_KEYS,
            "reason": "No records computed yet — check back shortly.",
        }

    fresh_cutoff = int((datetime.utcnow() - timedelta(days=RECORD_FRESH_DAYS)).timestamp())
    records = [
        {
            "series": r.series_key,
            "kind": r.kind,
            "value": round(r.value, 2),
            "unit": r.unit,
            "date": _dt.fromtimestamp(r.ts_utc, tz=timezone.utc).strftime("%Y-%m-%d"),
            "fresh": r.ts_utc >= fresh_cutoff,
        }
        for r in sorted(rows, key=lambda r: (r.series_key, r.kind))
    ]
    return {
        "available": True,
        "zone": resolved_zone,
        "zones": _ZONE_KEYS,
        "records": records,
    }


# ─── Outages (free) ───────────────────────────────────────────────────────────

_OUTAGE_KIND = {"A53": "planned", "A54": "forced"}


@router.get("/outages")
async def get_outages(
    zone: str = Query(DEFAULT_ZONE, description="Bidding zone key"),
    horizon_days: int = Query(30, ge=1, le=400,
                              description="Include outages starting up to this many days out"),
    db: Session = Depends(get_db),
):
    """Generation unavailability (ENTSO-E A77) for a bidding zone. Free tier.

    Revision semantics: only the HIGHEST revision per message counts, and
    withdrawn messages (docStatus A09) hide the event — of 31 live documents
    sampled, 26 were withdrawn. `total_offline_mw` counts only outages running
    RIGHT NOW; the list also includes ones starting within `horizon_days`.
    Descriptive: what capacity is off and why, not a price call.
    """
    from backend.models.energy import PowerOutage

    resolved_zone = _resolve_zone(zone)
    now = datetime.utcnow()
    now_iso = now.strftime("%Y-%m-%dT%H:%MZ")
    horizon_iso = (now + timedelta(days=horizon_days)).strftime("%Y-%m-%dT%H:%MZ")

    rows = db.query(PowerOutage).filter(PowerOutage.zone == resolved_zone).all()
    if not rows:
        return {
            "available": False,
            "zone": resolved_zone,
            "zones": _ZONE_KEYS,
            "reason": f"No outage messages for {POWER_ZONES[resolved_zone]['label']} yet — check back shortly.",
        }

    # Highest revision per mRID wins; withdrawn events disappear.
    latest: dict[str, PowerOutage] = {}
    for r in rows:
        if r.mrid not in latest or r.revision > latest[r.mrid].revision:
            latest[r.mrid] = r

    outages: list[dict] = []
    total_offline = 0.0
    forced_offline = 0.0
    for r in latest.values():
        if r.status != "active":
            continue
        if r.end_utc < now_iso or r.start_utc > horizon_iso:
            continue
        offline = (
            round(r.nominal_mw - (r.available_mw or 0.0), 1)
            if r.nominal_mw is not None else None
        )
        running_now = r.start_utc <= now_iso <= r.end_utc
        if running_now and offline:
            total_offline += offline
            if r.business_type == "A54":
                forced_offline += offline
        outages.append({
            "mrid": r.mrid,
            "unit_name": r.unit_name,
            "location": r.location,
            "fuel": PSR_LABELS.get(r.psr_type, r.psr_type),
            "kind": _OUTAGE_KIND.get(r.business_type, r.business_type),
            "nominal_mw": r.nominal_mw,
            "available_mw": r.available_mw,
            "offline_mw": offline,
            "start_utc": r.start_utc,
            "end_utc": r.end_utc,
            "running_now": running_now,
        })

    outages.sort(key=lambda o: (not o["running_now"], -(o["offline_mw"] or 0.0)))
    newest_msg = max((r.created_at for r in rows if r.created_at), default=None)
    return {
        "available": True,
        "zone": resolved_zone,
        "zones": _ZONE_KEYS,
        "unit": "MW",
        "total_offline_mw": round(total_offline, 1),
        "forced_offline_mw": round(forced_offline, 1),
        "horizon_days": horizon_days,
        "outages": outages,
        **_freshness(newest_msg.strftime("%Y-%m-%d") if newest_msg else None,
                     now.date(), 2),
    }


# ─── Hydro reservoirs (free) ──────────────────────────────────────────────────

#: A72 weekly points arrive up to ~2 weeks after the week starts (verified in
#: prod: current data read as 12 days old). Flag only beyond that lag.
HYDRO_STALE_DAYS = 16


def _same_week_band(points: list[tuple[int, float]]) -> dict:
    """Compare the newest weekly filling against the SAME ISO week in prior years.

    Reservoir levels are hard seasonal — a trailing window would flag every
    spring melt as an anomaly. So the band is built from the nearest point
    within ±4 days of (newest − i·52 weeks) for each prior year i.
    """
    import bisect

    ts, value = points[-1]
    keys = [t for t, _ in points]
    band: list[float] = []
    i = 1
    while True:
        target = ts - i * 364 * 86_400
        if target < keys[0] - 4 * 86_400:
            break
        j = bisect.bisect_left(keys, target)
        best = None
        for k in (j - 1, j):
            if 0 <= k < len(keys) and abs(keys[k] - target) <= 4 * 86_400:
                if best is None or abs(keys[k] - target) < abs(keys[best] - target):
                    best = k
        if best is not None:
            band.append(points[best][1])
        i += 1

    vs_band = None
    if band:
        vs_band = "below" if value < min(band) else "above" if value > max(band) else "within"
    return {
        "band_min_twh": round(min(band) / 1e6, 2) if band else None,
        "band_max_twh": round(max(band) / 1e6, 2) if band else None,
        "band_mean_twh": round(sum(band) / len(band) / 1e6, 2) if band else None,
        "band_n": len(band),
        "vs_band": vs_band,
    }


@router.get("/hydro")
async def get_hydro(db: Session = Depends(get_db)):
    """Weekly reservoir filling (ENTSO-E A72, MWh → TWh) for the hydro zones —
    Nordics, Alps, Iberia, France — each compared against the same ISO week in
    its own prior years. Descriptive: a filling level vs its seasonal norm,
    not a price call. Free tier."""
    from backend.power.entsoe_hydro import HYDRO_ZONES
    from backend.power.hourly_store import read_hourly

    zones_out: list[dict] = []
    newest: str | None = None
    for zone in HYDRO_ZONES:
        points = read_hourly(db, "hydro.reservoir", zone)
        if not points:
            continue
        ts, mwh = points[-1]
        as_of = _dt.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        newest = max(newest, as_of) if newest else as_of
        prev = points[-2][1] if len(points) >= 2 else None
        zones_out.append({
            "zone": zone,
            "zone_label": POWER_ZONES.get(zone, {}).get("label", zone),
            "reservoir_twh": round(mwh / 1e6, 2),
            "wow_twh": round((mwh - prev) / 1e6, 2) if prev is not None else None,
            "as_of": as_of,
            **_same_week_band(points),
        })

    if not zones_out:
        return {
            "available": False,
            "reason": "No reservoir data yet — check back shortly.",
        }

    zones_out.sort(key=lambda z: z["reservoir_twh"], reverse=True)
    return {
        "available": True,
        "unit": "TWh",
        "note": "ENTSO-E A72 weekly filling; band = same ISO week across prior years",
        "zones": zones_out,
        **_freshness(newest, datetime.utcnow().date(), HYDRO_STALE_DAYS),
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
        **_panel_freshness(data, "generation_mix"),
    }
