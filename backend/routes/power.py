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
from sqlalchemy import func
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
from backend.power.baseline import BASELINE_DAYS
from backend.power.coverage import renewable_share_reliable
from backend.power.daily import share_is_claimable
from backend.power.dunkelflaute import ABSOLUTE_THRESHOLD, TAIL_PERCENTILE
from backend.power.energy_charts_flows import ATTRIBUTION
from backend.power.entsoe_grid import PSR_LABELS
from backend.power.zones import DEFAULT_ZONE, POWER_ZONES
from backend.signals.detectors.base import severity_from_zscore, trailing_zscore

#: The situation hero is stale when its newest data lags wall-clock by more than
#: this many days. Day-ahead prices and realised grid data are ~daily, so a gap
#: beyond a day signals a frozen collector rather than normal publication lag.
SITUATION_STALE_DAYS = 1

#: Trailing window the hero's and the overview's z-scores are measured against.
#: One definition of "normal" for the whole desk — see backend/power/baseline.py for
#: what the window length actually does to the claims (it was 120 days, and a 120-day
#: window in March was mostly reporting that it is March).
#: Shipped in every situation/overview response as `baseline_days` — the UI
#: MUST render that number rather than restate it, because restating it is
#: exactly how the product ended up telling users "~90-day norm" while the code
#: had been computing 120 (caught 2026-07-12).
SITUATION_BASELINE_DAYS = BASELINE_DAYS

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
def get_day_ahead(
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
def get_day_ahead_hourly(
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
def get_spark_spread(
    days: int = Query(120, ge=7, le=1500),
    zone: str = Query(DEFAULT_ZONE, description="Bidding zone key: DE_LU, FR, NL, …"),
    efficiency: float | None = Query(
        None, ge=0.30, le=0.65,
        description="Override the CCGT electrical efficiency (0.30–0.65) used for this "
        "request's heat rate. Defaults to settings.gas_ccgt_efficiency. A PPA/asset-modelling "
        "knob: the desk's own fleet-average assumption may not match a specific plant.",
    ),
    db: Session = Depends(get_db),
):
    """DIRTY spark spread (power − gas × heat_rate, EUR/MWh) for any zone — and the carbon
    price at which it becomes a zero margin.

    It is DIRTY, and the name is not a hedge: this is the spread BEFORE carbon. A gas plant has
    to buy EUAs, and at 2026 prices that is around EUR 30/MWh — enough to flip the sign in most
    of Europe. This endpoint used to call the number a "CCGT margin" and the hero painted it
    green. It is not a margin. See backend/power/spark.py.

    `breakeven_eua_eur_t` is what the desk publishes instead of a clean spread it cannot compute:
    the EUA price at which this zone's gas fleet reaches zero. Pure arithmetic on our own record
    plus a published emission factor — no carbon price needed, so no licence question, and more
    useful than the clean spread anyway.

    The gas leg is TTF for every zone (a per-zone hub is a later refinement) and its raw close is
    NOT returned: yfinance's TTF is Yahoo's copy of the ICE Endex front-month, licensed exchange
    data this project does not redistribute. That is a mitigation, not a cure — see spark.py.

    `efficiency`, when given, overrides settings.gas_ccgt_efficiency for THIS request only — the
    heat rate and everything derived from it (the spread, the break-even carbon price, the carbon
    intensity) are recomputed at the requested efficiency. The raw gas price stays unexposed
    regardless: the override changes the arithmetic applied to it, not what leaves the response.
    The efficiency actually used (requested or defaulted) is always echoed back as `efficiency`.
    """
    resolved_zone = _resolve_zone(zone)
    symbol = POWER_ZONES[resolved_zone]["price_symbol"]
    eff = efficiency if efficiency is not None else settings.gas_ccgt_efficiency
    heat_rate = round(1.0 / eff, 4)
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
            "efficiency": eff,
            "reason": f"Spark spread isn't available for {POWER_ZONES[resolved_zone]['label']} yet — check back shortly.",
        }

    from backend.power.spark import breakeven_eua, co2_intensity

    data = []
    for d in dates:
        dirty = round(power_by[d] - ttf_by[d] * heat_rate, 4)
        data.append({
            "date": d,
            "power_price": power_by[d],
            # gas_price (the raw TTF close) is deliberately NOT returned — see the docstring.
            "heat_rate": heat_rate,
            "dirty_spark_spread": dirty,
            "breakeven_eua_eur_t": breakeven_eua(dirty, heat_rate),
            "co2_price": None,
            "clean_spark_spread": None,
        })
    return {
        "available": True,
        "zone": resolved_zone,
        "zones": _ZONE_KEYS,
        "unit": "EUR/MWh",
        "efficiency": eff,
        "heat_rate_note": (
            "1 / CCGT_efficiency; default efficiency = "
            f"{settings.gas_ccgt_efficiency}, this request used {eff}"
            if efficiency is not None
            else f"1 / CCGT_efficiency; default efficiency = {settings.gas_ccgt_efficiency}"
        ),
        "co2_intensity_t_per_mwh": round(co2_intensity(heat_rate), 4),
        "gas_leg_note": (
            "Gas leg = TTF front-month for every zone (a per-zone hub is a later refinement). Its "
            "raw close is not returned: yfinance's TTF is Yahoo's copy of the ICE Endex contract, "
            "licensed exchange data this project does not redistribute."
        ),
        "co2_note": (
            "This spread is DIRTY — it excludes the cost of carbon, which at 2026 EUA prices is "
            f"around EUR 30/MWh for a CCGT ({round(co2_intensity(heat_rate), 3)} tCO2 per MWh of "
            "electricity at this heat rate). `breakeven_eua_eur_t` is the carbon price at which "
            "the margin reaches zero: above it, the plant loses money on the day-ahead. The clean "
            "spread itself stays null until a free, redistributable daily EUA series is confirmed "
            "(docs/findings/2026-06-24-eua-coal-data-source.md)."
        ),
        "latest": data[-1],
        "from": date_from,
        "to": date_to,
        "data": data,
        **_panel_freshness(data, "spark"),
    }


# ─── Grid load + renewables (free) ───────────────────────────────────────────


def _grid_row_values(
    date: str,
    load_mw: float | None,
    wind_mw: float | None,
    solar_mw: float | None,
    load_hours: int | None = None,
    gen_hours: int | None = None,
) -> dict:
    """Derive residual_mw and renewable_share for one row.

    Residual load is DEMAND minus renewables. Without a load there is no demand,
    so there is no residual and no renewable share — they are None, not zero.
    Coercing a missing load to 0.0 made the desk render `residual = −(wind+solar)`
    and a 0% renewable share out of nothing: IE-SEM stopped publishing A65 load on
    2025-10-23 and the desk has been showing it a NEGATIVE residual load ever since.

    None wind_mw / solar_mw ARE treated as 0 — those are genuinely near-zero or
    absent generation, which is a real physical statement, unlike a missing load.
    Value-based so the bulk overview loader can feed column tuples without
    hydrating ORM entities.

    The Dunkelflaute flag is NOT set here: it is a judgment against the zone's own history
    (power/dunkelflaute.py), which a single row cannot make. This function used to answer it
    with a flat `share < 15%` — the predicate the radar was cured of — and that is how the
    hero came to flag thirteen zones on a day the radar flagged three. `_flag_dunkelflaute`
    below fills it in from the record.
    """
    wind = wind_mw or 0.0
    solar = solar_mw or 0.0
    has_load = load_mw is not None and load_mw > 0
    # A whole-day A75 blackout leaves wind AND solar None (unknown) with gen_hours 0.
    # Reading those as 0 would invent residual = full load and a 0% renewable share
    # (the mirror of the missing-load bug). So generation counts as present only if
    # a renewable leg exists OR gen_hours says the feed ran — an all-thermal day
    # (no wind/solar, gen_hours > 0) is a REAL 0% share, not a blackout.
    has_gen = wind_mw is not None or solar_mw is not None or (gen_hours is not None and gen_hours > 0)

    residual_mw = round(load_mw - wind - solar, 2) if has_load and has_gen else None
    renewable_share = round((wind + solar) / load_mw, 4) if has_load and has_gen else None

    return {
        "date": date,
        "load_mw": load_mw,
        "wind_mw": wind_mw,
        "solar_mw": solar_mw,
        "residual_mw": residual_mw,
        "renewable_share": renewable_share,
        # How much of the day these means stand on (power/daily.py). Shipped, not hidden: a share
        # is only a share when the day is whole, and a reader is entitled to check that.
        "load_hours": load_hours,
        "gen_hours": gen_hours,
        "dunkelflaute": False,
    }


def _compute_grid_row(r: PowerGrid) -> dict:
    return _grid_row_values(r.date, r.load_mw, r.wind_mw, r.solar_mw, r.load_hours, r.gen_hours)


def _flag_dunkelflaute(db: Session, zone: str, rows: list[dict]) -> None:
    """Set `dunkelflaute` on ascending `_grid_row_values` rows of ONE zone — the calibrated
    predicate + the coverage guard, i.e. exactly what the radar asks. Mutates in place."""
    if not rows:
        return
    from backend.power.coverage import reliable_days
    from backend.power.dunkelflaute import flag_days

    reliable = {
        d for d, _z in reliable_days(
            db, zone=zone, date_from=rows[0]["date"], date_to=rows[-1]["date"]
        )
    }
    # A share is a claim about a whole day: a day of load, and generation reported in every hour
    # of it. A day with a hole in its feed reads as zeros, and zeros make a Dunkelflaute out of an
    # outage — so days that cannot carry the claim never reach the predicate.
    shares = {
        r["date"]: (r["renewable_share"] if share_is_claimable(r["load_hours"], r["gen_hours"]) else None)
        for r in rows
    }
    verdicts = flag_days(db, zone, shares, reliable)
    for r in rows:
        r["dunkelflaute"] = verdicts.get(r["date"], False)


@router.get("/grid")
def get_grid(
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
    _flag_dunkelflaute(db, resolved_zone, data)
    latest = data[-1]
    dunkelflaute_days = sum(1 for d in data if d["dunkelflaute"])

    return {
        "available": True,
        "zone": resolved_zone,
        "zones": _ZONE_KEYS,
        "unit": "MW",
        "threshold_note": (
            f"dunkelflaute = wind+solar below {ABSOLUTE_THRESHOLD:.0%} of load AND in the bottom "
            f"{TAIL_PERCENTILE:.0%} of this zone's own same-month history "
            "(zones with no wind/solar fleet cannot be in one)"
        ),
        "latest": latest,
        "dunkelflaute_days": dunkelflaute_days,
        "from": date_from,
        "to": date_to,
        "data": data,
        **_panel_freshness(data, "grid"),
    }


@router.get("/load-forecast")
def get_load_forecast(
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
def get_load_forecast_hourly(
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
def get_power_overview(db: Session = Depends(get_db)):
    """All bidding zones at a glance — the single-glance overview (rows = zones,
    each with its state + key metrics + vs-normal z-scores). Uses the batched
    loader (load_power_situations_bulk) — same synthesis as the per-zone detail,
    ~7 queries instead of 37 × ~6. Sync def: FastAPI runs it in the threadpool,
    so the (still synchronous) DB work no longer blocks the event loop."""
    zones = []
    for sit in load_power_situations_bulk(db).values():
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
    return {
        "available": bool(zones),
        "zones": zones,
        "baseline_days": SITUATION_BASELINE_DAYS,  # the window the z columns use
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


# Panel-caption freshness thresholds. Values mirror the per-source windows in
# backend/collectors/freshness.py::SPECS (enforced by test_power_panel_freshness),
# so the UI captions and /api/health/collectors can never disagree. generation_mix
# shares the grid window (same A75 ingest); load_forecast is frontier-based.
PANEL_MAX_AGE_DAYS = {
    "day_ahead": 2,
    "grid": 3,
    "generation_mix": 3,
    "flows": 3,
    "flows_hourly": 3,  # mirrors SPECS "flows_hourly"
    "imbalance": 4,     # mirrors SPECS "imbalance_qh" — reBAP settles late
    "spark": 4,  # gas leg is yfinance TTF — ~3 trading days plus weekends
    "load_forecast": 2,
    "balancing": 2,  # mirrors SPECS "balancing_energy"
}

#: /api/power/live's own freshness threshold — day granularity like every
#: other panel caption above (the endpoint's own `lag_minutes` field carries
#: the real-time precision). Kept in sync with the "live_load" FreshnessSpec
#: (backend/collectors/freshness.py) by
#: test_power_live.py::test_route_max_age_matches_live_load_freshness_spec,
#: the same pattern PANEL_MAX_AGE_DAYS uses against SPECS above
#: (test_power_panel_freshness.py::test_panel_thresholds_match_health_specs).
LIVE_MAX_AGE_DAYS = 1


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
    spark_latest — latest {"spark_spread","power_price","gas_price"} or None. The DIRTY spread;
        the block it produces renames it and adds the break-even carbon price (power/spark.py).
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

    # ── spark block ──
    #
    # DIRTY, and the hero must say so. It used to render this as "CCGT margin", in green when
    # positive — for a number that excludes the cost of carbon, which is around EUR 30/MWh and
    # flips the sign in most of Europe. The break-even carbon price goes out with it, because it
    # is the number that makes the omission legible: above that EUA price, the margin is negative.
    from backend.power.spark import breakeven_eua

    has_spark = spark_supported and spark_latest is not None
    _heat_rate = round(1.0 / settings.gas_ccgt_efficiency, 4)
    _dirty = round(spark_latest["spark_spread"], 2) if has_spark else None
    spark = {
        "available": has_spark,
        "supported": spark_supported,
        "dirty_spark_spread": _dirty,
        "breakeven_eua_eur_t": breakeven_eua(_dirty, _heat_rate) if _dirty is not None else None,
        "power_price": round(spark_latest["power_price"], 2)
        if has_spark and spark_latest.get("power_price") is not None else None,
        # The raw TTF close is NOT exposed: licensed exchange data (see power/spark.py).
        # Spark's gas leg is yfinance TTF (~3 trading days + weekends) — judge it
        # by the SAME window as the spark panel caption. With the 1-day default,
        # every weekend flagged EVERY zone's situation stale (worst-of semantics),
        # because TTF simply doesn't trade on Saturdays.
        **_freshness(spark_latest.get("date") if has_spark else None, today,
                     PANEL_MAX_AGE_DAYS["spark"]),
    }

    # ── flags + descriptive state ──
    severities: list[str] = []
    if price["z"] is not None:
        severities.append(severity_from_zscore(price["z"]))
    if grid["z"] is not None:
        severities.append(severity_from_zscore(grid["z"]))

    flags: list[dict] = []
    if dunkelflaute:
        share = grid["renewable_share"]
        share_txt = f" — renewables {share * 100:.0f}% of load" if share is not None else ""
        flags.append({"key": "dunkelflaute", "severity": "warning",
                      "label": f"Dunkelflaute{share_txt}"})
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
        # A zone can have generation but no published load (IE-SEM since
        # 2025-10-23) — there is then no residual to state, and saying so is the
        # whole point. Formatting it anyway is what 500'd the endpoint.
        if grid["residual_gw"] is not None:
            seg = f"residual {grid['residual_gw']:.0f} GW"
            if grid["z"] is not None:
                seg += f" ({grid['z']:+.1f}σ)"
            parts.append(seg + _age_suffix(grid))
        else:
            parts.append("residual n/a — no published load for this zone")
    if spark["available"]:
        seg = f"dirty spark €{spark['dirty_spark_spread']:+.0f}/MWh"
        if spark["breakeven_eua_eur_t"] is not None:
            # The whole point of the sentence: the spread is only a margin below this carbon price.
            seg += f" (zero at €{spark['breakeven_eua_eur_t']:.0f}/t CO₂)"
        parts.append(seg + _age_suffix(spark))
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
        # The window every z below was measured against. Shipped so the UI can
        # STATE it instead of guessing it (it guessed "~90" for a 120-day window).
        "baseline_days": SITUATION_BASELINE_DAYS,
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
    date_from, date_to = _window(SITUATION_BASELINE_DAYS)

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
    _flag_dunkelflaute(db, resolved_zone, grid_series)

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


def load_power_situations_bulk(db: Session) -> dict[str, dict]:
    """Every zone's situation with a FIXED number of queries (~7) instead of the
    per-zone path's 37 × ~6 — /overview is the default EUROPE tab, i.e. the most
    trafficked endpoint, and the N+1 grew with zones × history. Feeds the SAME
    pure build_power_situation as load_power_situation, so overview and detail
    cannot disagree (pinned by a parity test)."""
    from backend.models.energy import InstalledCapacity
    from backend.power.coverage import coverage_min_ratio
    from backend.signals.detectors.power import forced_outage_totals_now

    date_from, date_to = _window(SITUATION_BASELINE_DAYS)
    today = datetime.utcnow().date()

    # 1. Day-ahead price stats, all zones in one scan.
    price_by_zone: dict[str, list[dict]] = {z: [] for z in POWER_ZONES}
    price_rows = (
        db.query(PowerPriceDaily.zone, PowerPriceDaily.date,
                 PowerPriceDaily.mean_price, PowerPriceDaily.negative_hours)
        .filter(PowerPriceDaily.date >= date_from, PowerPriceDaily.date <= date_to)
        .order_by(PowerPriceDaily.date.asc())
        .all()
    )
    for z, d, mean, neg in price_rows:
        if z in price_by_zone:
            price_by_zone[z].append({"date": d, "close": mean, "negative_hours": neg})

    # 2. Grid rows (load/wind/solar), all zones in one scan. Column tuples, not
    #    ORM entities — hydrating ~4.5k PowerGrid objects cost 0.17s on prod.
    grid_by_zone: dict[str, list[dict]] = {z: [] for z in POWER_ZONES}
    latest_grid: dict[str, tuple[str, float | None]] = {}  # zone -> (date, load_mw)
    for zone_key, d, load, wind, solar, lh, gh in (
        db.query(PowerGrid.zone, PowerGrid.date, PowerGrid.load_mw,
                 PowerGrid.wind_mw, PowerGrid.solar_mw,
                 PowerGrid.load_hours, PowerGrid.gen_hours)
        .filter(PowerGrid.date >= date_from, PowerGrid.date <= date_to)
        .order_by(PowerGrid.date.asc())
        .all()
    ):
        if zone_key in grid_by_zone:
            grid_by_zone[zone_key].append(_grid_row_values(d, load, wind, solar, lh, gh))
            latest_grid[zone_key] = (d, load)

    # 3. Coverage guard for each zone's LATEST grid day. Query by the small set
    #    of distinct dates (usually 1-2: yesterday/today) — the date index makes
    #    this a few hundred row visits, while a (zone, date) row-value IN made
    #    SQLite scan all ~640k genmix rows (measured 0.15s on prod). Extra
    #    (zone, date) combos in the result are harmless — lookups use exact pairs.
    #    Same semantics as renewable_share_reliable: no genmix row → untrusted.
    dates = sorted({d for d, _ in latest_grid.values()})
    gen_totals: dict[tuple[str, str], float] = {}
    if dates:
        for z, d, total in (
            db.query(PowerGenMix.zone, PowerGenMix.date, func.sum(PowerGenMix.gen_mw))
            .filter(PowerGenMix.date.in_(dates))
            .group_by(PowerGenMix.zone, PowerGenMix.date)
            .all()
        ):
            gen_totals[(z, d)] = float(total) if total is not None else None

    def _coverage_ok(zone: str) -> bool:
        lg = latest_grid.get(zone)
        if lg is None:
            return True  # no grid rows at all — matches the per-zone path
        d, load_mw = lg
        if not load_mw or load_mw <= 0:
            return False
        total = gen_totals.get((zone, d))
        if total is None:
            return False
        return total >= coverage_min_ratio(zone) * load_mw

    # 3b. The Dunkelflaute verdict for each zone's LATEST day — the only one the hero and the
    #     matrix read. The calibrated predicate (power/dunkelflaute.py), i.e. the radar's, so the
    #     front door cannot flag thirteen zones on a day the radar flags three. Thresholds are
    #     memoised per (month, record), so the 37 calls cost one window-function scan per month.
    from backend.power.dunkelflaute import flag_days

    for zone_key, series in grid_by_zone.items():
        if not series:
            continue
        last = series[-1]
        reliable = {last["date"]} if _coverage_ok(zone_key) else set()
        share = (
            last["renewable_share"]
            if share_is_claimable(last["load_hours"], last["gen_hours"])
            else None
        )
        last["dunkelflaute"] = flag_days(
            db, zone_key, {last["date"]: share}, reliable
        )[last["date"]]

    # 4. Spark legs: every zone's power symbol + TTF. Query by DATE RANGE only —
    #    the (date, symbol) unique index turns 14 days × ~50 symbols into a few
    #    hundred row visits, while `symbol IN (38)` made SQLite walk each
    #    symbol's full multi-year history (measured 0.29s on prod). The symbol
    #    filter happens in Python on the handful of returned rows.
    spark_from, spark_to = _window(14)
    symbols = {cfg["price_symbol"] for cfg in POWER_ZONES.values()} | {"TTF"}
    closes: dict[str, dict[str, float]] = {s: {} for s in symbols}
    for sym, d, close in (
        db.query(EnergyPrice.symbol, EnergyPrice.date, EnergyPrice.close)
        .filter(EnergyPrice.date >= spark_from, EnergyPrice.date <= spark_to)
        .all()
    ):
        if sym in symbols:
            closes[sym][d] = close
    heat_rate = round(1.0 / settings.gas_ccgt_efficiency, 4)

    def _spark(zone: str) -> dict | None:
        power_by = closes.get(POWER_ZONES[zone]["price_symbol"], {})
        ttf_by = closes.get("TTF", {})
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

    # 5. Forced outages per zone (SQL revision-dedupe, one query).
    forced_by_zone = forced_outage_totals_now(db)

    # 6. Installed capacity (latest A68 year) per zone, one query.
    latest_year = (
        db.query(InstalledCapacity.zone, func.max(InstalledCapacity.year).label("y"))
        .group_by(InstalledCapacity.zone)
        .subquery()
    )
    installed: dict[str, float] = {}
    for z, total in (
        db.query(InstalledCapacity.zone, func.sum(InstalledCapacity.capacity_mw))
        .join(latest_year, (InstalledCapacity.zone == latest_year.c.zone)
              & (InstalledCapacity.year == latest_year.c.y))
        .group_by(InstalledCapacity.zone)
        .all()
    ):
        if total:
            installed[z] = float(total)

    situations: dict[str, dict] = {}
    for zone in POWER_ZONES:
        try:
            situations[zone] = build_power_situation(
                zone,
                price_by_zone[zone],
                grid_by_zone[zone],
                _spark(zone),
                spark_supported=True,
                grid_coverage_ok=_coverage_ok(zone),
                forced_outage_mw=forced_by_zone.get(zone, 0.0),
                forced_outage_installed_mw=installed.get(zone),
                today=today,
            )
        except Exception:  # one broken zone must not blank the whole overview
            continue
    return situations


@router.get("/situation")
def get_situation(
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
def get_flows(
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
def get_forecast_error(
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


# ─── Hourly cross-border flows (free) ─────────────────────────────────────────


@router.get("/flows/hourly")
def get_flows_hourly(
    zone: str = Query(DEFAULT_ZONE, description="Bidding zone key"),
    hours: int = Query(72, ge=6, le=720, description="Lookback window in hours"),
    db: Session = Depends(get_db),
):
    """Hourly cross-border flows for one zone's borders (Energy-Charts CBPF,
    CC BY 4.0). Free tier.

    Reads the canonical store (series ``flow.<TO>`` under zone ``<FROM>``,
    canonical sorted border) and normalises every border to the SELECTED zone's
    perspective: net_mw > 0 = the selected zone EXPORTS to that neighbour.
    Country-level source — sub-zones without an Energy-Charts country (Italian
    sub-zones, DK1/DK2 …) return available:false with the reason, not a blank.
    """
    from backend.power.hourly_store import iter_border_points

    resolved_zone = _resolve_zone(zone)
    start_ts = int((datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp())

    # Sign convention + storing-side/counterparty-side handling now lives in ONE
    # place (iter_border_points) shared with backend/power/live.py — see its
    # docstring for the Case A/B mechanics this used to duplicate here.
    borders: dict[str, list[tuple[int, float]]] = {}
    for neighbor, ts, v in iter_border_points(db, resolved_zone, start_ts=start_ts):
        borders.setdefault(neighbor, []).append((ts, v))

    if not borders:
        return {
            "available": False,
            "zone": resolved_zone,
            "zones": _ZONE_KEYS,
            "reason": (
                f"No hourly flow series for {POWER_ZONES[resolved_zone]['label']} — "
                "Energy-Charts flows are country-level, so sub-zones (Italian zones, "
                "DK1/DK2, Nordic sub-zones) have no border series of their own."
            ),
        }

    newest_ts = max(pts[-1][0] for pts in borders.values())
    as_of = _dt.fromtimestamp(newest_ts, tz=timezone.utc).strftime("%Y-%m-%d")
    out = []
    for neighbor, pts in borders.items():
        latest_mw = pts[-1][1]
        out.append({
            "neighbor": neighbor,
            "neighbor_label": _zone_label(neighbor),
            "latest_mw": round(latest_mw, 1),
            "direction": "export" if latest_mw >= 0 else "import",
            "data": [
                {"ts_utc": _dt.fromtimestamp(t, tz=timezone.utc).isoformat(), "net_mw": round(v, 1)}
                for t, v in pts
            ],
        })
    out.sort(key=lambda b: -abs(b["latest_mw"]))
    return {
        "available": True,
        "zone": resolved_zone,
        "zones": _ZONE_KEYS,
        "unit": "MW",
        "hours": hours,
        "note": f"net_mw > 0 = {POWER_ZONES[resolved_zone]['label']} exports to the neighbour; hourly means",
        "source": ATTRIBUTION,
        "borders": out,
        **_freshness(as_of, datetime.utcnow().date(), PANEL_MAX_AGE_DAYS["flows_hourly"]),
    }


# ─── Products: Base / Peak / Off-peak, in market time ─────────────────────────


@router.get("/products")
def get_products(
    zone: str = Query(DEFAULT_ZONE, description="Bidding zone key"),
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
):
    """Base, Peak and Off-peak per CET delivery day — the products Europe actually
    trades. The desk could show a daily mean (which is Base) and a raw 24h shape,
    but not the peak price a trader reads first. Free tier, descriptive.

    Computed on the CET delivery day, not the UTC calendar day: the products are
    defined in CET (EPEX Peak = 08:00–20:00 CET, Mon–Fri). See
    backend/power/products.py.
    """
    from backend.power.products import compute_products

    return compute_products(db, _resolve_zone(zone), days=days)


@router.get("/capture")
def get_capture(
    zone: str = Query(DEFAULT_ZONE, description="Bidding zone key"),
    months: int = Query(24, ge=1, le=120),
    db: Session = Depends(get_db),
):
    """What a solar (or wind, or gas) MWh actually earned: the generation-weighted
    capture price per fuel per month, and the value factor against baseload.

    The metric the European power market argues about most, and which no free EU
    tool publishes per bidding zone. Realised arithmetic on published auction
    results — not a model. See backend/power/capture.py.
    """
    from backend.power.capture import compute_capture

    return compute_capture(db, _resolve_zone(zone), months=months)


@router.get("/episodes")
def get_episodes(
    zone: str = Query(DEFAULT_ZONE, description="Bidding zone key"),
    kind: str = Query("dunkelflaute", description="dunkelflaute | negative_prices | price_spike"),
    db: Session = Depends(get_db),
):
    """Grid stress as EPISODES — runs of consecutive days, ranked against the zone's own record.

    The radar only ever saw today: "DE-LU is in a Dunkelflaute". It could not say "and this is
    the fourth-longest in five years", which is the sentence that decides whether to care. And it
    could not have learned to — the alert table mutates its rows in place, so the history was
    never written. Episodes are re-derived nightly from the published record.

    Descriptive (Posture B): what has happened and how it compares to what happened before. An
    "active" episode is one that reaches the newest day we hold — not a claim that it continues.
    """
    from backend.power.episodes import KINDS, zone_episodes

    if kind not in KINDS:
        return {"available": False, "reason": f"Unknown episode kind {kind}.",
                "kinds": list(KINDS)}
    return zone_episodes(db, _resolve_zone(zone), kind)


# ─── Drivers: why is this zone expensive today? ───────────────────────────────


@router.get("/drivers")
def get_drivers(
    zone: str = Query(DEFAULT_ZONE, description="Bidding zone key"),
    db: Session = Depends(get_db),
):
    """The conditions co-occurring with today's price in one zone, ranked by how
    far each sits from its own norm — plus what physically similar days cleared.

    Descriptive (Posture B): the wording is co-occurrence ("price €142 WHILE wind
    is 2.8σ below norm"), never causation, and the analogs report what similar
    days DID clear, never what tomorrow will. See backend/power/drivers.py.
    """
    from backend.power.drivers import compute_drivers

    return compute_drivers(db, _resolve_zone(zone))


# ─── Borders: where the price series and the flow series finally meet ─────────


@router.get("/borders")
def get_borders(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
):
    """Every border with a day-ahead price on BOTH sides: how often the two zones
    cleared together, how far apart they cleared, how often the physical flow sat
    at this border's own historical rail, and how often power ran from the
    expensive zone to the cheap one. Free tier.

    Descriptive statistics on published records. A price spread is NOT a claim
    that this interconnector was the binding constraint — the Core region clears
    flow-based, where the constraint is a network element, not the border.
    See backend/power/borders.py.
    """
    from backend.power.borders import compute_borders

    return compute_borders(db, days=days)


@router.get("/spread")
def get_spread(
    a: str = Query(..., description="Zone A, e.g. DE_LU"),
    b: str = Query(..., description="Zone B, e.g. FR"),
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
):
    """One border, hour by hour: both day-ahead prices, their spread, and the
    physical flow across it. Free tier, descriptive."""
    from backend.power.borders import compute_spread

    return compute_spread(db, a, b, days=days)


# ─── Live (near-real-time TODAY) ──────────────────────────────────────────────


@router.get("/live")
def get_live(
    zone: str = Query(DEFAULT_ZONE, description="Bidding zone key"),
    db: Session = Depends(get_db),
):
    """Near-real-time read of TODAY, hour by hour. Free tier.

    The situation hero and every daily panel read the DAILY rollup tables, which
    only ever hold COMPLETE days — so the desk has no view of today until the
    nightly job closes it out, even though the canonical hourly store already
    fills in every ~30 minutes as ENTSO-E publishes. This is that missing read:
    published actual load/residual/per-fuel generation/net cross-border flow
    alongside the day-ahead forecast/price for the SAME hours, straight from
    backend/power/live.py::compute_live. Descriptive (Posture B): actuals are
    compared against ENTSO-E's/the auction's own published day-ahead figures,
    never predicted.

    `zone` defaults to DE_LU; unknown zones fall back (same convention as every
    other endpoint on this router — see `_resolve_zone`; `zones` in the response
    lists every valid key, exactly like its siblings). Shortly after UTC
    midnight, before today's first actual has published, falls back to
    yesterday's complete day (`showing: "yesterday"`) — in that mode
    `summary.price_now` is null until today's first actual has landed, because
    only the shown (yesterday's) day-ahead prices are loaded.
    """
    from backend.power.live import compute_live

    resolved_zone = _resolve_zone(zone)
    result = compute_live(db, resolved_zone)
    if not result.get("available"):
        return {**result, "zones": _ZONE_KEYS}

    as_of_date = result["latest_actual_ts"][:10] if result.get("latest_actual_ts") else None
    return {
        **result,
        "zones": _ZONE_KEYS,
        **_freshness(as_of_date, datetime.now(timezone.utc).date(), max_age_days=LIVE_MAX_AGE_DAYS),
    }


# ─── Imbalance prices (free) ──────────────────────────────────────────────────


@router.get("/imbalance")
def get_imbalance(
    zone: str = Query(DEFAULT_ZONE, description="Bidding zone key"),
    days: int = Query(7, ge=1, le=90),
    resolution: str = Query("hourly", pattern="^(hourly|qh)$"),
    db: Session = Depends(get_db),
):
    """Imbalance prices (ENTSO-E A85; reBAP for DE-LU via the country EIC) for a
    zone — what being out of balance actually costs, the intraday stress gauge
    that day-ahead means smooth away. `resolution=qh` returns the raw 15-min
    settlement points where they exist. Free tier, descriptive.
    """
    from backend.power.hourly_store import read_hourly

    resolved_zone = _resolve_zone(zone)
    series_key = "imbalance.price" if resolution == "hourly" else "imbalance.price.qh"
    start_ts = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    points = read_hourly(db, series_key, resolved_zone, start_ts=start_ts)
    if not points:
        return {
            "available": False,
            "zone": resolved_zone,
            "zones": _ZONE_KEYS,
            "reason": (
                f"No imbalance-price series for {POWER_ZONES[resolved_zone]['label']} — "
                "A85 coverage varies by zone (and 15-min settlement only exists where "
                "the market settles in quarter-hours)."
            ),
        }

    values = [v for _, v in points]
    peak = max(points, key=lambda p: abs(p[1]))
    as_of = _dt.fromtimestamp(points[-1][0], tz=timezone.utc).strftime("%Y-%m-%d")
    return {
        "available": True,
        "zone": resolved_zone,
        "zones": _ZONE_KEYS,
        "unit": "EUR/MWh",
        "resolution": resolution,
        "days": days,
        "latest": round(values[-1], 2),
        "peak": {
            "price": round(peak[1], 2),
            "ts_utc": _dt.fromtimestamp(peak[0], tz=timezone.utc).isoformat(),
        },
        "data": [
            {"ts_utc": _dt.fromtimestamp(t, tz=timezone.utc).isoformat(), "price": round(v, 2)}
            for t, v in points
        ],
        **_freshness(as_of, datetime.utcnow().date(), PANEL_MAX_AGE_DAYS["imbalance"]),
    }


# ─── Activated balancing energy (free) ────────────────────────────────────────


@router.get("/balancing")
def get_balancing(
    zone: str = Query(DEFAULT_ZONE, description="Bidding zone key"),
    days: int = Query(30, ge=1, le=90),
    product: str = Query("afrr", pattern="^(afrr|mfrr)$"),
    db: Session = Depends(get_db),
):
    """Activated balancing energy (ENTSO-E A84 prices EUR/MWh + A83 volumes MWh) for aFRR or
    mFRR, split by direction — what the TSO actually called on beyond the day-ahead price, in
    real time. Free tier, descriptive (Posture B: context, not a forecast).

    Coverage varies by zone/product — see backend/power/entsoe_balancing.py's module
    docstring for the live-spiked specifics: DE_LU is TenneT's control area only (one of four
    German TSOs, not the national total), and activation VOLUMES (A83) are not currently
    served by the public API at all for any zone (prices still populate).
    """
    from backend.power.entsoe_balancing import coverage_caveat
    from backend.power.hourly_store import read_hourly

    resolved_zone = _resolve_zone(zone)
    caveat = coverage_caveat(resolved_zone)
    start_ts = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())

    def _direction_rows(direction: str) -> list[tuple[int, float | None, float | None]]:
        prices = dict(read_hourly(db, f"balancing.{product}.price.{direction}", resolved_zone, start_ts=start_ts))
        vols = dict(read_hourly(db, f"balancing.{product}.vol.{direction}", resolved_zone, start_ts=start_ts))
        return [(t, prices.get(t), vols.get(t)) for t in sorted(set(prices) | set(vols))]

    up_rows = _direction_rows("up")
    down_rows = _direction_rows("down")
    if not up_rows and not down_rows:
        return {
            "available": False,
            "zone": resolved_zone,
            "zone_label": POWER_ZONES[resolved_zone]["label"],
            "zones": _ZONE_KEYS,
            "product": product,
            "reason": (
                f"No {product} activated-balancing-energy series for "
                f"{POWER_ZONES[resolved_zone]['label']} — A83/A84 coverage varies by zone "
                "(and activation volumes aren't currently served by the public API at all; "
                "see backend/power/entsoe_balancing.py)."
            ),
            "coverage": caveat,
        }

    def _fmt(rows: list[tuple[int, float | None, float | None]]) -> list[dict]:
        return [
            {
                "t": _dt.fromtimestamp(t, tz=timezone.utc).isoformat(),
                "price": round(p, 2) if p is not None else None,
                "vol": round(v, 2) if v is not None else None,
            }
            for t, p, v in rows
        ]

    # Tag each row with its direction before merging so latest/peak can report which half
    # of the market they came from — a bare number doesn't say whether the TSO was paying
    # for upward or downward regulation.
    all_rows = [(t, p, v, "up") for t, p, v in up_rows] + [(t, p, v, "down") for t, p, v in down_rows]
    priced_rows = [(t, p, d) for t, p, _, d in all_rows if p is not None]
    latest_t, latest_p, latest_v, latest_dir = max(all_rows, key=lambda r: r[0])
    peak_row = max(priced_rows, key=lambda r: abs(r[1])) if priced_rows else None
    as_of = _dt.fromtimestamp(max(r[0] for r in all_rows), tz=timezone.utc).strftime("%Y-%m-%d")

    return {
        "available": True,
        "zone": resolved_zone,
        "zone_label": POWER_ZONES[resolved_zone]["label"],
        "zones": _ZONE_KEYS,
        "product": product,
        "unit": "EUR/MWh",
        "days": days,
        "up": _fmt(up_rows),
        "down": _fmt(down_rows),
        "latest": {
            "t": _dt.fromtimestamp(latest_t, tz=timezone.utc).isoformat(),
            "price": round(latest_p, 2) if latest_p is not None else None,
            "vol": round(latest_v, 2) if latest_v is not None else None,
            "direction": latest_dir,
        },
        "peak": (
            {
                "t": _dt.fromtimestamp(peak_row[0], tz=timezone.utc).isoformat(),
                "price": round(peak_row[1], 2),
                "direction": peak_row[2],
            }
            if peak_row is not None else None
        ),
        "note": (
            "Activated balancing energy — what the TSO actually called on to keep the grid "
            "balanced, beyond the day-ahead auction. Descriptive context, not a forecast "
            "(Posture B)."
        ),
        "coverage": caveat,
        **_freshness(as_of, datetime.utcnow().date(), PANEL_MAX_AGE_DAYS["balancing"]),
    }


# ─── Records (free) ───────────────────────────────────────────────────────────

#: A record set within this many days is "the story", not archive trivia.
RECORD_FRESH_DAYS = 7


@router.get("/records")
def get_records(
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


def _unit_names_for(db: Session, eics: list[str]) -> dict[str, str]:
    """{unit_eic: name} for the EICs on this board, in one query.

    The registry keeps a row per (unit_eic, year); the newest year wins, because a plant that
    was renamed should show its current name against an outage filed last month.
    """
    from backend.models.energy import ProductionUnit

    if not eics:
        return {}
    rows = (
        db.query(ProductionUnit.unit_eic, ProductionUnit.name, ProductionUnit.year)
        .filter(ProductionUnit.unit_eic.in_(set(eics)), ProductionUnit.name.isnot(None))
        .order_by(ProductionUnit.year.asc())
        .all()
    )
    return {eic: name for eic, name, _year in rows}   # later years overwrite earlier ones


@router.get("/outages")
def get_outages(
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
    from backend.signals.detectors.power import latest_outage_revisions

    resolved_zone = _resolve_zone(zone)
    now = datetime.utcnow()
    now_iso = now.strftime("%Y-%m-%dT%H:%MZ")
    horizon_iso = (now + timedelta(days=horizon_days)).strftime("%Y-%m-%dT%H:%MZ")

    # Highest revision per mRID wins; withdrawn events disappear. The dedupe
    # runs in SQL (shared with the radar detector) instead of loading every
    # revision row into Python.
    latest_rows = latest_outage_revisions(db, resolved_zone)
    if not latest_rows:
        return {
            "available": False,
            "zone": resolved_zone,
            "zones": _ZONE_KEYS,
            "reason": f"No outage messages for {POWER_ZONES[resolved_zone]['label']} yet — check back shortly.",
        }

    outages: list[dict] = []
    total_offline = 0.0
    forced_offline = 0.0
    # Names for the EICs, in ONE query. PowerOutage.unit_eic has been written since the outage
    # ingest was built and read by nothing; the unit registry (A71/A33) is what it was waiting
    # for. Hydrating per row would repeat the 0.25 s / 8.5k-entity mistake the outage board has
    # already made once.
    unit_names = _unit_names_for(db, [r.unit_eic for r in latest_rows if r.unit_eic])

    for r in latest_rows:
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
            # The message's own name if it carries one, else the registry's. Many A77 messages
            # carry no name at all — which is why the board used to print raw EICs.
            "unit_name": r.unit_name or unit_names.get(r.unit_eic),
            "unit_eic": r.unit_eic,
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
    # Freshness = newest message we EVER ingested for the zone (any revision) —
    # a superseded revision still proves the collector is alive.
    newest_msg = (
        db.query(func.max(PowerOutage.created_at))
        .filter(PowerOutage.zone == resolved_zone)
        .scalar()
    )
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


# same_week_band moved to backend/power/entsoe_hydro.py so the hydro_deviation
# radar detector and this route share one derivation.


@router.get("/hydro")
def get_hydro(db: Session = Depends(get_db)):
    """Weekly reservoir filling (ENTSO-E A72, MWh → TWh) for the hydro zones —
    Nordics, Alps, Iberia, France — each compared against the same ISO week in
    its own prior years. Descriptive: a filling level vs its seasonal norm,
    not a price call. Free tier."""
    from backend.power.entsoe_hydro import HYDRO_ZONES, same_week_band
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
            **same_week_band(points),
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
def get_generation_mix(
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
