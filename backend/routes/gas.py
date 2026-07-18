"""EU gas balance read endpoints (Phase 1).

GET /api/gas/supply      — daily supply decomposition (imports + LNG + net UK)
GET /api/gas/storage     — AGSI storage series
GET /api/gas/lng         — ALSI LNG series
GET /api/gas/validation  — Bruegel ±5% comparison (the Phase-1 milestone)
"""

from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.collectors.freshness import freshness_meta
from backend.database import get_db
from backend.gas import validation
from backend.models.gas import (
    GasBalance,
    GasDemandModel,
    GasLng,
    GasPowerBurn,
    GasStorage,
    GasStorageCountry,
)

router = APIRouter(prefix="/api/gas", tags=["gas"])

# The operator drops the refreshed Bruegel weekly CSV here (gitignored data/).
BRUEGEL_CSV = Path("data/gas/bruegel_weekly.csv")


def _window(days: int) -> tuple[str, str]:
    end = datetime.utcnow().date()
    start = end - timedelta(days=days)
    return start.isoformat(), end.isoformat()


# Gas confirms 1-2 days late (ENTSOG provisional window, AGSI evening publish),
# so 3 days is a hung feed, not a normal lag. Matches the gas_balance SPECS entry.
GAS_STALE_DAYS = 3


def _panel_freshness(rows) -> dict:
    """as_of/age_days/stale from the newest row's delivery date. `rows` are
    ascending model rows or dicts with a `date` field."""
    if not rows:
        return freshness_meta(None, None, GAS_STALE_DAYS)
    last = rows[-1]
    as_of = last["date"] if isinstance(last, dict) else last.date
    return freshness_meta(as_of, datetime.utcnow().date(), GAS_STALE_DAYS)


@router.get("/supply")
def get_supply(days: int = Query(90, ge=1, le=1500), db: Session = Depends(get_db)):
    """Daily supply (GWh/d) decomposed into pipeline imports, LNG, net UK."""
    date_from, date_to = _window(days)
    rows = validation.compute_daily_supply(db, date_from, date_to)
    if not rows:
        return {"available": False, "reason": "no flow/lng data yet — run gas_backfill"}
    return {"available": True, "from": date_from, "to": date_to, "data": rows, **_panel_freshness(rows)}


@router.get("/storage/countries")
def get_storage_countries(
    days: int = Query(90, ge=1, le=1500),
    country: str | None = Query(None, description="ISO code, e.g. DE, UA, GB*"),
    db: Session = Depends(get_db),
):
    """Storage per country — the level a power desk can actually use.

    "EU storage is 51% full" averages a full Germany with an empty Ukraine, and gas does not
    flow freely across those borders. The per-country rows were in every payload we fetched
    since 2023 and were discarded at read time.

    NOTE ON TOTALS: this deliberately returns no cross-country sum. Coverage is a property of
    who reports to GIE, so an "all countries" TWh figure would be an absolute value we cannot
    completely capture — the desk's oldest data rule. The EU aggregate at /api/gas/storage IS
    that total, computed by GIE, and it is the only honest one. Fill % is a ratio inside a
    single complete country row, and is safe to compare across them.
    """
    date_from, date_to = _window(days)
    q = (
        db.query(GasStorageCountry)
        .filter(GasStorageCountry.date >= date_from, GasStorageCountry.date <= date_to)
    )
    if country:
        q = q.filter(GasStorageCountry.country == country)
    rows = q.order_by(GasStorageCountry.date.asc(), GasStorageCountry.country.asc()).all()
    if not rows:
        return {
            "available": False,
            "reason": (
                f"no per-country AGSI rows for {country} yet"
                if country else
                "no per-country AGSI data yet — run backfill_gie_countries (cache-only)"
            ),
        }

    latest_date = rows[-1].date
    latest = sorted(
        (r for r in rows if r.date == latest_date),
        key=lambda r: (r.fill_pct is None, -(r.fill_pct or 0.0)),
    )
    return {
        "available": True,
        "data": [
            {
                "date": r.date, "country": r.country, "region": r.region, "name": r.name,
                "stock_twh": r.stock_twh, "fill_pct": r.fill_pct,
                "injection_gwh": r.injection_gwh, "withdrawal_gwh": r.withdrawal_gwh,
                "working_gas_twh": r.working_gas_twh,
                "withdrawal_capacity_gwh": r.withdrawal_capacity_gwh,
            }
            for r in rows
        ],
        "latest": [
            {
                "country": r.country, "region": r.region, "name": r.name,
                "fill_pct": r.fill_pct, "stock_twh": r.stock_twh,
                "working_gas_twh": r.working_gas_twh,
                "withdrawal_capacity_gwh": r.withdrawal_capacity_gwh,
            }
            for r in latest
        ],
        "note": (
            "Per-country fill from GIE AGSI. Fill % is a ratio within one country and is "
            "comparable across them; TWh is only meaningful beside that country's own working "
            "gas volume. No cross-country total is given — the EU aggregate (/api/gas/storage) "
            "is GIE's own, and the only complete one. `ne` marks non-EU reporters (UA, GB*)."
        ),
        **_panel_freshness(rows),
    }


@router.get("/storage")
def get_storage(days: int = Query(90, ge=1, le=1500), db: Session = Depends(get_db)):
    date_from, date_to = _window(days)
    rows = (
        db.query(GasStorage)
        .filter(GasStorage.date >= date_from, GasStorage.date <= date_to)
        .order_by(GasStorage.date.asc())
        .all()
    )
    if not rows:
        return {"available": False, "reason": "no AGSI data yet"}
    return {
        "available": True,
        "data": [
            {"date": r.date, "stock_twh": r.stock_twh, "injection_gwh": r.injection_gwh, "withdrawal_gwh": r.withdrawal_gwh, "fill_pct": r.fill_pct}
            for r in rows
        ],
        **_panel_freshness(rows),
    }


@router.get("/lng")
def get_lng(days: int = Query(90, ge=1, le=1500), db: Session = Depends(get_db)):
    date_from, date_to = _window(days)
    rows = (
        db.query(GasLng)
        .filter(GasLng.date >= date_from, GasLng.date <= date_to)
        .order_by(GasLng.date.asc())
        .all()
    )
    if not rows:
        return {"available": False, "reason": "no ALSI data yet"}
    return {"available": True, "data": [{"date": r.date, "send_out_gwh": r.send_out_gwh, "inventory_twh": r.inventory_twh} for r in rows], **_panel_freshness(rows)}


@router.get("/power-burn")
def get_power_burn(days: int = Query(90, ge=1, le=1500), db: Session = Depends(get_db)):
    """Gas-fired power generation (measured) + implied gas demand (Phase 2)."""
    date_from, date_to = _window(days)
    rows = (
        db.query(GasPowerBurn)
        .filter(GasPowerBurn.date >= date_from, GasPowerBurn.date <= date_to)
        .order_by(GasPowerBurn.date.asc())
        .all()
    )
    if not rows:
        return {"available": False, "reason": "no power-burn data yet (needs ENTSOE_API_TOKEN)"}
    return {
        "available": True,
        "note": "implied_gas_gwh = gen_gwh_el / efficiency; efficiency ~0.50 carries ~±5% systematic error",
        "data": [
            {"date": r.date, "gen_gwh_el": r.gen_gwh_el, "implied_gas_gwh": r.implied_gas_gwh, "efficiency": r.efficiency}
            for r in rows
        ],
        **_panel_freshness(rows),
    }


@router.get("/demand")
def get_demand(days: int = Query(90, ge=1, le=1500), db: Session = Depends(get_db)):
    """Modeled gas demand: HDD-driven heating + flat industrial baseline (Phase 3)."""
    date_from, date_to = _window(days)
    rows = (
        db.query(GasDemandModel)
        .filter(GasDemandModel.date >= date_from, GasDemandModel.date <= date_to)
        .order_by(GasDemandModel.date.asc())
        .all()
    )
    if not rows:
        return {"available": False, "reason": "no demand model yet — run gas_backfill --sources weather,demand"}
    has_power = "power" in (rows[-1].model_version or "")
    note = (
        "demand = HDD-driven heating + flat industrial baseline; power burn is modeled "
        "separately (see /power-burn) — see model_version"
        if has_power
        else "industrial baseline is flat/month and (without ENTSO-E power burn) absorbs power — see model_version"
    )
    return {
        "available": True,
        "note": note,
        "data": [
            {"date": r.date, "heat_gwh": r.heat_gwh, "industrial_gwh": r.industrial_gwh, "model_version": r.model_version}
            for r in rows
        ],
        **_panel_freshness(rows),
    }


@router.get("/balance")
def get_balance(days: int = Query(120, ge=1, le=1500), db: Session = Depends(get_db)):
    """The residual signal (Phase 4): implied vs actual ΔStorage, 7d-smoothed,
    z-scored, flagged. The residual is the product — persistent deviation =
    demand destruction / unexpected flows the market hasn't priced."""
    date_from, date_to = _window(days)
    rows = (
        db.query(GasBalance)
        .filter(GasBalance.date >= date_from, GasBalance.date <= date_to)
        .order_by(GasBalance.date.asc())
        .all()
    )
    if not rows:
        return {"available": False, "reason": "no balance yet — run gas_backfill"}
    latest = rows[-1]
    return {
        "available": True,
        # The panel's subline reads supply and demand off `latest` — they belong in it. Omitting
        # them rendered "supply — demand — GWh" on the hero of the tab, every day, while the
        # numbers sat in `data` right below.
        "latest": {
            "date": latest.date,
            "supply_gwh": latest.supply_gwh,
            "demand_gwh": latest.demand_gwh,
            "residual_7d": latest.residual_7d,
            "z_score": latest.z_score,
            "flag": latest.flag,
        },
        "active_flags": [{"date": r.date, "z_score": r.z_score, "flag": r.flag} for r in rows if r.flag],
        "data": [
            {
                "date": r.date,
                "supply_gwh": r.supply_gwh,
                "demand_gwh": r.demand_gwh,
                "exports_gwh": r.exports_gwh,
                "implied_delta": r.implied_delta,
                "actual_delta": r.actual_delta,
                "residual": r.residual,
                "residual_7d": r.residual_7d,
                "z_score": r.z_score,
                "flag": r.flag,
            }
            for r in rows
        ],
        **_panel_freshness(rows),
    }


@router.get("/validation")
def get_validation(
    date_from: str = Query(None, description="YYYY-MM-DD; defaults to the Bruegel CSV's span"),
    date_to: str = Query(None),
    db: Session = Depends(get_db),
):
    """Phase-1 milestone: modeled supply vs Bruegel weekly imports (±5%)."""
    if not BRUEGEL_CSV.exists():
        return {"available": False, "reason": f"drop the Bruegel weekly CSV at {BRUEGEL_CSV}"}
    # Default the window wide enough to cover all model data; the comparison
    # only scores weeks present in both the model and the Bruegel CSV.
    date_from = date_from or "2023-01-01"
    date_to = date_to or datetime.utcnow().date().isoformat()
    result = validation.validate(db, date_from, date_to, BRUEGEL_CSV)
    return {"available": True, **result}
