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

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models.energy import EnergyPrice, PowerFlow, PowerGenMix, PowerGrid, PowerPriceDaily, SparkSpreadHistory
from backend.power.energy_charts_flows import ATTRIBUTION
from backend.power.zones import DEFAULT_ZONE, POWER_ZONES

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
            "reason": f"no {symbol} data yet — run power backfill (ingest_day_ahead)",
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
            "reason": (
                "no spark spread data yet — "
                "ingest POWER_DE via ingest_day_ahead, then run collect_spark_spreads"
            ),
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
            "reason": f"no grid data for zone {resolved_zone} — run power grid backfill (ingest_grid)",
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
            "reason": "no cross-border flow data yet — run power backfill (ingest_cbpf)",
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
            "reason": f"no generation-mix data for zone {resolved_zone} — run power grid backfill (ingest_grid)",
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
