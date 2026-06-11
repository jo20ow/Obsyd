"""EU gas demand model (Phase 3): heating + industrial.

Total gas demand = power burn (measured, Phase 2) + heating (HDD-driven) +
industrial (flat). We calibrate the heating/industrial split on Eurostat
monthly consumption:

    net_month = Eurostat_total_month − power_burn_month
    regress   net_month = a + b · HDD_month   (EU aggregate, OLS)
    heating_day    = b · HDD_day              (integrates to b·HDD_month)
    industrial_day = a / days_in_month        (flat, temperature-independent)

Industrial is explicitly the weakest component — a constant monthly residual.
When the residual signal moves, this assumption is usually what breaks (real
industrial demand destruction/recovery shows up here first); that is intended.

Calibration needs power burn (ENTSO-E, Phase 2). Without a token, power is
absent → net = total, which conflates power into heating/industrial: the model
runs but the split is PRELIMINARY (flagged in model_version).
"""

from __future__ import annotations

import calendar
import logging

import numpy as np
from sqlalchemy.orm import Session

from backend.gas import eurostat
from backend.gas.weather import CITY_BASKETS
from backend.models.gas import GasDemandModel, GasPowerBurn, GasWeather

logger = logging.getLogger(__name__)

MIN_CALIBRATION_MONTHS = 6


def _month(day: str) -> str:
    return day[:7]


def eu_daily_hdd(db: Session, country_weights: dict[str, float]) -> dict[str, float]:
    """Consumption-weighted mean HDD across basket countries, per day."""
    rows = db.query(GasWeather.date, GasWeather.country, GasWeather.hdd).all()
    acc: dict[str, list[tuple[float, float]]] = {}
    for d, country, hdd in rows:
        w = country_weights.get(country)
        if w and hdd is not None:
            acc.setdefault(d, []).append((hdd, w))
    out: dict[str, float] = {}
    for d, pairs in acc.items():
        wsum = sum(w for _, w in pairs)
        if wsum:
            out[d] = sum(h * w for h, w in pairs) / wsum
    return out


def _power_monthly(db: Session) -> dict[str, float]:
    out: dict[str, float] = {}
    for date_, implied in db.query(GasPowerBurn.date, GasPowerBurn.implied_gas_gwh).all():
        if implied is not None:
            out[_month(date_)] = out.get(_month(date_), 0.0) + implied
    return out


def calibrate(net_monthly: dict[str, float], hdd_monthly: dict[str, float]) -> tuple[float, float, int]:
    """OLS net = a + b·HDD over months present in both. Returns (a, b, n)."""
    months = sorted(set(net_monthly) & set(hdd_monthly))
    if len(months) < MIN_CALIBRATION_MONTHS:
        return (float("nan"), float("nan"), len(months))
    x = np.array([hdd_monthly[m] for m in months], dtype=float)
    y = np.array([net_monthly[m] for m in months], dtype=float)
    A = np.vstack([np.ones_like(x), x]).T
    (a, b), *_ = np.linalg.lstsq(A, y, rcond=None)
    return (float(a), float(b), len(months))


def compute_demand(db: Session, eurostat_per_country: dict[str, dict[str, float]]) -> dict:
    """Calibrate and write daily gas_demand_model for every day with HDD."""
    # country weight = its annual Eurostat consumption (basket countries only)
    weights = {
        c: sum(eurostat_per_country.get(c, {}).values())
        for c in CITY_BASKETS
        if eurostat_per_country.get(c)
    }
    if not weights:
        return {"written": 0, "note": "no eurostat data for basket countries"}

    daily_hdd = eu_daily_hdd(db, weights)
    if not daily_hdd:
        return {"written": 0, "note": "no weather/HDD data — run weather ingest"}

    hdd_monthly: dict[str, float] = {}
    for d, h in daily_hdd.items():
        hdd_monthly[_month(d)] = hdd_monthly.get(_month(d), 0.0) + h

    eu_total = eurostat.eu_monthly_total(eurostat_per_country)
    power = _power_monthly(db)
    has_power = bool(power)
    net_monthly = {m: eu_total[m] - power.get(m, 0.0) for m in eu_total}

    a, b, n = calibrate(net_monthly, hdd_monthly)
    if not np.isfinite(a):
        return {"written": 0, "note": f"insufficient calibration data (n={n}, need {MIN_CALIBRATION_MONTHS})"}

    version = f"v1{'+power' if has_power else '+nopower(prelim)'};a={a:.0f};b={b:.1f};n={n}"
    days_in = {}
    written = 0
    for d, hdd in sorted(daily_hdd.items()):
        y, mo = int(d[:4]), int(d[5:7])
        dim = days_in.setdefault((y, mo), calendar.monthrange(y, mo)[1])
        heat = max(0.0, b * hdd)
        industrial = max(0.0, a / dim)
        _upsert(db, d, round(heat, 1), round(industrial, 1), version)
        written += 1
    db.commit()
    logger.info("demand.compute: %d days, a=%.0f b=%.1f n=%d power=%s", written, a, b, n, has_power)
    return {"written": written, "a_industrial_monthly": round(a, 1), "b_heating_per_hdd": round(b, 1), "calibration_months": n, "has_power": has_power}


def _upsert(db: Session, day: str, heat: float, industrial: float, version: str) -> None:
    existing = db.get(GasDemandModel, day)
    if existing:
        existing.heat_gwh = heat
        existing.industrial_gwh = industrial
        existing.model_version = version
    else:
        db.add(GasDemandModel(date=day, heat_gwh=heat, industrial_gwh=industrial, model_version=version))


async def compute_demand_model(db: Session, *, since: str = "2023-01") -> dict:
    """Load Eurostat then calibrate + write. Entry point for backfill/scheduler."""
    eurostat_per_country = await eurostat.load_monthly_consumption(since=since)
    if not eurostat_per_country:
        return {"written": 0, "note": "eurostat unavailable"}
    return compute_demand(db, eurostat_per_country)
