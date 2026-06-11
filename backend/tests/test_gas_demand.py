"""Demand-model tests: calibration + daily heating/industrial split."""

from __future__ import annotations

import calendar

import numpy as np

from backend.gas import demand, weather
from backend.models.gas import GasDemandModel, GasPowerBurn, GasWeather


def test_calibrate_recovers_known_coefficients():
    # net = 50000 + 200·HDD over 8 months → OLS recovers a≈50000, b≈200.
    hdd = {f"2026-{m:02d}": 50.0 + 10 * m for m in range(1, 9)}
    net = {m: 50000.0 + 200.0 * h for m, h in hdd.items()}
    a, b, n = demand.calibrate(net, hdd)
    assert n == 8
    assert abs(a - 50000.0) < 1.0
    assert abs(b - 200.0) < 0.1


def test_calibrate_insufficient_months_is_nan():
    hdd = {"2026-01": 100.0, "2026-02": 90.0}
    net = {"2026-01": 1000.0, "2026-02": 900.0}
    a, b, n = demand.calibrate(net, hdd)
    assert n == 2 and np.isnan(a)


def _seed_weather(db, country, day_to_hdd):
    for d, h in day_to_hdd.items():
        db.add(GasWeather(date=d, country=country, t_mean=15.5 - h, hdd=h))


def test_compute_demand_end_to_end(db_session, monkeypatch):
    db = db_session
    # Single basket country so weighting is trivial.
    monkeypatch.setattr(weather, "CITY_BASKETS", {"DE": [(1.0, 1.0, 1.0)]})
    monkeypatch.setattr(demand, "CITY_BASKETS", {"DE": [(1.0, 1.0, 1.0)]})

    # 8 months, ~4 days each, HDD declining; net (eurostat-power) = 50000 + 200·HDD_month
    eurostat_pc = {"DE": {}}
    for m in range(1, 9):
        month = f"2026-{m:02d}"
        days = [f"{month}-{dd:02d}" for dd in (1, 2, 3, 4)]
        hdd_each = 20.0 - m  # per-day HDD this month
        _seed_weather(db, "DE", {d: hdd_each for d in days})
        hdd_month = hdd_each * len(days)
        net_month = 50000.0 + 200.0 * hdd_month
        # power = 3000/month, so eurostat total = net + power
        db.add(GasPowerBurn(date=f"{month}-15", gen_gwh_el=1500.0, implied_gas_gwh=3000.0, efficiency=0.5))
        eurostat_pc["DE"][month] = net_month + 3000.0
    db.commit()

    res = demand.compute_demand(db, eurostat_pc)
    assert res["has_power"] is True
    assert abs(res["b_heating_per_hdd"] - 200.0) < 1.0
    assert abs(res["a_industrial_monthly"] - 50000.0) < 5.0

    # A sample day: heating = b·HDD; industrial = a/days_in_month
    row = db.get(GasDemandModel, "2026-03-01")
    hdd_mar = 20.0 - 3
    assert abs(row.heat_gwh - 200.0 * hdd_mar) < 5.0
    assert abs(row.industrial_gwh - 50000.0 / calendar.monthrange(2026, 3)[1]) < 5.0
    assert "+power" in row.model_version


def test_compute_demand_without_power_is_flagged_preliminary(db_session, monkeypatch):
    db = db_session
    monkeypatch.setattr(demand, "CITY_BASKETS", {"DE": [(1.0, 1.0, 1.0)]})
    eurostat_pc = {"DE": {}}
    for m in range(1, 9):
        month = f"2026-{m:02d}"
        days = [f"{month}-{dd:02d}" for dd in (1, 2, 3, 4)]
        _seed_weather(db, "DE", {d: 20.0 - m for d in days})
        eurostat_pc["DE"][month] = 60000.0 + 200.0 * (20.0 - m) * 4
    db.commit()
    res = demand.compute_demand(db, eurostat_pc)  # no GasPowerBurn rows
    assert res["has_power"] is False
    row = db.query(GasDemandModel).first()
    assert "nopower(prelim)" in row.model_version
