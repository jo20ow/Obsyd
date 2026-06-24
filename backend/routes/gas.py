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

from backend.database import get_db
from backend.gas import validation
from backend.models.gas import GasBalance, GasDemandModel, GasLng, GasPowerBurn, GasStorage

router = APIRouter(prefix="/api/gas", tags=["gas"])

# The operator drops the refreshed Bruegel weekly CSV here (gitignored data/).
BRUEGEL_CSV = Path("data/gas/bruegel_weekly.csv")


def _window(days: int) -> tuple[str, str]:
    end = datetime.utcnow().date()
    start = end - timedelta(days=days)
    return start.isoformat(), end.isoformat()


@router.get("/supply")
async def get_supply(days: int = Query(90, ge=1, le=1500), db: Session = Depends(get_db)):
    """Daily supply (GWh/d) decomposed into pipeline imports, LNG, net UK."""
    date_from, date_to = _window(days)
    rows = validation.compute_daily_supply(db, date_from, date_to)
    if not rows:
        return {"available": False, "reason": "no flow/lng data yet — run gas_backfill"}
    return {"available": True, "from": date_from, "to": date_to, "data": rows}


@router.get("/storage")
async def get_storage(days: int = Query(90, ge=1, le=1500), db: Session = Depends(get_db)):
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
    }


@router.get("/lng")
async def get_lng(days: int = Query(90, ge=1, le=1500), db: Session = Depends(get_db)):
    date_from, date_to = _window(days)
    rows = (
        db.query(GasLng)
        .filter(GasLng.date >= date_from, GasLng.date <= date_to)
        .order_by(GasLng.date.asc())
        .all()
    )
    if not rows:
        return {"available": False, "reason": "no ALSI data yet"}
    return {"available": True, "data": [{"date": r.date, "send_out_gwh": r.send_out_gwh, "inventory_twh": r.inventory_twh} for r in rows]}


@router.get("/power-burn")
async def get_power_burn(days: int = Query(90, ge=1, le=1500), db: Session = Depends(get_db)):
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
    }


@router.get("/demand")
async def get_demand(days: int = Query(90, ge=1, le=1500), db: Session = Depends(get_db)):
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
    }


@router.get("/balance")
async def get_balance(days: int = Query(120, ge=1, le=1500), db: Session = Depends(get_db)):
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
        "latest": {"date": latest.date, "residual_7d": latest.residual_7d, "z_score": latest.z_score, "flag": latest.flag},
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
    }


@router.get("/validation")
async def get_validation(
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
