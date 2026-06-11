"""Phase 4 — the residual engine. This is the product.

    Implied ΔStorage(t) = Supply(t) − Demand(t) − Exports(t)
    Residual(t)         = Actual ΔStorage(t) [AGSI] − Implied ΔStorage(t)

A persistent residual = demand destruction, unexpected flows, or demand
response the market hasn't priced. Single-day spikes are noise (EU27 aggregate
noise floor ~ low hundreds of GWh/d); only multi-day persistence is signal —
encoded in the flag logic, not prose:

    residual_7d = 7-day rolling mean
    z           = (residual_7d − mean_90d) / std_90d
    |z| ≥ 2                              → WATCH
    |z| ≥ 3 on ≥3 consecutive days       → SIGNAL

Each flagged row also records which component moved most (supply / demand /
exports), so attribution is one query away.

Component sourcing (all GWh/d):
  Supply   — pipeline imports + LNG + net UK import      (Phase 1)
  Demand   — heating + industrial + power burn-or-0      (Phase 2/3)*
  Exports  — EU→UA + net UK export                       (Phase 1)
  Actual Δ — AGSI injection − withdrawal                 (Phase 1)

* The demand model auto-consistency: in preliminary mode (no ENTSO-E token)
  heating+industrial already includes power (calibrated on total), and power
  burn is absent → demand = heat+industrial. With a token, heat+industrial =
  total−power and power is added back → demand = heat+industrial+power. Either
  way `heat + industrial + (power or 0)` is the right total.

Known v1 bias: domestic production is not yet in Supply (classification gap),
so the residual carries a roughly constant production offset; the z-score
(deviation from the trailing mean) absorbs the constant part.
"""

from __future__ import annotations

import logging

import numpy as np
from sqlalchemy.orm import Session

from backend.gas import validation
from backend.models.gas import GasBalance, GasDemandModel, GasFlow, GasPoint, GasPowerBurn, GasStorage

logger = logging.getLogger(__name__)

SMOOTH_WINDOW = 7
Z_LOOKBACK = 90
Z_MIN_HISTORY = 30
WATCH_Z = 2.0
SIGNAL_Z = 3.0
SIGNAL_RUN = 3  # consecutive days at |z| ≥ SIGNAL_Z


def _export_ua_by_day(db: Session, date_from: str, date_to: str) -> dict[str, float]:
    rows = (
        db.query(GasFlow.date, GasFlow.value_gwh)
        .join(GasPoint, GasFlow.point_id == GasPoint.point_id)
        .filter(GasFlow.date >= date_from, GasFlow.date <= date_to, GasPoint.active == 1, GasPoint.point_class == "export_ua")
        .all()
    )
    out: dict[str, float] = {}
    for d, v in rows:
        out[d] = out.get(d, 0.0) + (v or 0.0)
    return out


def compute_balance(db: Session, date_from: str, date_to: str) -> list[dict]:
    """Build the daily balance + residual + z-score + flag series."""
    supply_rows = {r["date"]: r for r in validation.compute_daily_supply(db, date_from, date_to)}
    export_ua = _export_ua_by_day(db, date_from, date_to)

    demand_h = {r.date: r for r in db.query(GasDemandModel).filter(GasDemandModel.date >= date_from, GasDemandModel.date <= date_to).all()}
    power = {r.date: (r.implied_gas_gwh or 0.0) for r in db.query(GasPowerBurn).filter(GasPowerBurn.date >= date_from, GasPowerBurn.date <= date_to).all()}
    storage = {r.date: r for r in db.query(GasStorage).filter(GasStorage.date >= date_from, GasStorage.date <= date_to).all()}

    # A day is computable only if it has supply, demand and storage.
    dates = sorted(set(supply_rows) & set(demand_h) & set(storage))
    rows: list[dict] = []
    for d in dates:
        s = supply_rows[d]
        supply = s["supply_gwh"]
        uk_net = s["uk_net_gwh"]
        exports = export_ua.get(d, 0.0) + max(0.0, -uk_net)  # EU→UA + UK net export
        dm = demand_h[d]
        demand = (dm.heat_gwh or 0.0) + (dm.industrial_gwh or 0.0) + power.get(d, 0.0)
        st = storage[d]
        actual_delta = (st.injection_gwh or 0.0) - (st.withdrawal_gwh or 0.0)
        implied_delta = supply - demand - exports
        residual = actual_delta - implied_delta
        rows.append(
            {
                "date": d,
                "supply_gwh": round(supply, 1),
                "demand_gwh": round(demand, 1),
                "exports_gwh": round(exports, 1),
                "implied_delta": round(implied_delta, 1),
                "actual_delta": round(actual_delta, 1),
                "residual": round(residual, 1),
            }
        )

    _add_smoothing_and_flags(rows)
    return rows


def _add_smoothing_and_flags(rows: list[dict]) -> None:
    """In place: residual_7d, z_score, flag (+ dominant-mover attribution)."""
    resid = np.array([r["residual"] for r in rows], dtype=float)
    n = len(rows)

    # 7-day trailing mean
    r7 = np.full(n, np.nan)
    for i in range(n):
        if i + 1 >= SMOOTH_WINDOW:
            r7[i] = resid[i - SMOOTH_WINDOW + 1 : i + 1].mean()

    # z of residual_7d vs trailing Z_LOOKBACK window (excluding the current point)
    z = np.full(n, np.nan)
    for i in range(n):
        if np.isnan(r7[i]):
            continue
        lo = max(0, i - Z_LOOKBACK)
        hist = r7[lo:i]
        hist = hist[np.isfinite(hist)]
        if len(hist) >= Z_MIN_HISTORY:
            mu, sd = hist.mean(), hist.std()
            if sd > 0:
                z[i] = (r7[i] - mu) / sd

    # supply/demand/exports trailing means for attribution
    comps = {k: np.array([r[k] for r in rows], dtype=float) for k in ("supply_gwh", "demand_gwh", "exports_gwh")}

    signal_run = 0
    for i, r in enumerate(rows):
        r["residual_7d"] = None if np.isnan(r7[i]) else round(float(r7[i]), 1)
        r["z_score"] = None if np.isnan(z[i]) else round(float(z[i]), 2)
        zi = z[i]
        if np.isnan(zi) or abs(zi) < WATCH_Z:
            signal_run = 0
            r["flag"] = None
            continue
        signal_run = signal_run + 1 if abs(zi) >= SIGNAL_Z else 0
        level = "SIGNAL" if signal_run >= SIGNAL_RUN else "WATCH"
        r["flag"] = f"{level}:{_dominant_mover(comps, i)}"


def _dominant_mover(comps: dict[str, np.ndarray], i: int) -> str:
    """Which component deviates most (in std units) from its trailing 7d mean,
    and in which direction — e.g. 'supply↑'."""
    best_name, best_dev, best_dir = "supply", 0.0, "↑"
    for key, name in (("supply_gwh", "supply"), ("demand_gwh", "demand"), ("exports_gwh", "exports")):
        hist = comps[key][max(0, i - SMOOTH_WINDOW) : i]
        if len(hist) < 3:
            continue
        sd = hist.std()
        if sd <= 0:
            continue
        diff = comps[key][i] - hist.mean()
        dev = abs(diff) / sd
        if dev > best_dev:
            best_dev, best_name, best_dir = dev, name, ("↑" if diff >= 0 else "↓")
    return f"{best_name}{best_dir}"


def persist(db: Session, rows: list[dict]) -> int:
    for r in rows:
        existing = db.get(GasBalance, r["date"])
        target = existing or GasBalance(date=r["date"])
        target.supply_gwh = r["supply_gwh"]
        target.demand_gwh = r["demand_gwh"]
        target.exports_gwh = r["exports_gwh"]
        target.implied_delta = r["implied_delta"]
        target.actual_delta = r["actual_delta"]
        target.residual = r["residual"]
        target.residual_7d = r.get("residual_7d")
        target.z_score = r.get("z_score")
        target.flag = r.get("flag")
        if existing is None:
            db.add(target)
    db.commit()
    return len(rows)


def compute_and_persist(db: Session, date_from: str = "2023-01-01", date_to: str | None = None) -> dict:
    from datetime import datetime, timezone

    date_to = date_to or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = compute_balance(db, date_from, date_to)
    n = persist(db, rows)
    flagged = sum(1 for r in rows if r.get("flag"))
    logger.info("balance.compute: %d days, %d flagged", n, flagged)
    return {"days": n, "flagged": flagged}
