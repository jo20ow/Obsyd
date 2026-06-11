"""Residual-engine tests: balance arithmetic + smoothing/z-score/flags."""

from __future__ import annotations

from backend.gas import balance
from backend.models.gas import GasDemandModel, GasFlow, GasPoint, GasPowerBurn, GasStorage


def test_balance_arithmetic(db_session):
    db = db_session
    db.add(GasPoint(point_id="IMP|P|entry", name="Imp", operator="o", point_class="import_pipeline", counterparty="N", active=1))
    db.add(GasPoint(point_id="UA|P|exit", name="UA", operator="o", point_class="export_ua", counterparty="UA", active=1))
    db.add(GasFlow(date="2026-04-01", point_id="IMP|P|entry", direction="entry", value_gwh=8000.0, provisional=0, interpolated=0))
    db.add(GasFlow(date="2026-04-01", point_id="UA|P|exit", direction="exit", value_gwh=200.0, provisional=0, interpolated=0))
    db.add(GasDemandModel(date="2026-04-01", heat_gwh=3000.0, industrial_gwh=2500.0, model_version="v1+power"))
    db.add(GasPowerBurn(date="2026-04-01", gen_gwh_el=900.0, implied_gas_gwh=1800.0, efficiency=0.5))
    db.add(GasStorage(date="2026-04-01", stock_twh=500.0, injection_gwh=600.0, withdrawal_gwh=50.0, fill_pct=45.0))
    db.commit()

    rows = balance.compute_balance(db, "2026-04-01", "2026-04-01")
    assert len(rows) == 1
    r = rows[0]
    # supply = 8000 (import); no UK → uk_net 0; exports = export_ua 200 + UK export 0
    assert r["supply_gwh"] == 8000.0
    assert r["exports_gwh"] == 200.0
    # demand = heat 3000 + industrial 2500 + power 1800 = 7300
    assert r["demand_gwh"] == 7300.0
    # implied Δ = 8000 - 7300 - 200 = 500 ; actual Δ = 600 - 50 = 550 ; residual = 50
    assert r["implied_delta"] == 500.0
    assert r["actual_delta"] == 550.0
    assert r["residual"] == 50.0


def test_day_skipped_without_all_layers(db_session):
    db = db_session
    # supply only, no demand/storage → no balance row
    db.add(GasPoint(point_id="IMP|P|entry", name="Imp", operator="o", point_class="import_pipeline", counterparty="N", active=1))
    db.add(GasFlow(date="2026-04-01", point_id="IMP|P|entry", direction="entry", value_gwh=8000.0, provisional=0, interpolated=0))
    db.commit()
    assert balance.compute_balance(db, "2026-04-01", "2026-04-01") == []


def _series(residuals, supply_spike_at=None):
    rows = []
    for i, resid in enumerate(residuals):
        supply = 8000.0 + (2000.0 if (supply_spike_at is not None and i >= supply_spike_at) else 0.0)
        rows.append(
            {"date": f"day{i:03d}", "residual": resid, "supply_gwh": supply, "demand_gwh": 7000.0, "exports_gwh": 200.0}
        )
    return rows


def test_smoothing_is_7day_trailing_mean():
    rows = _series([float(i) for i in range(10)])
    balance._add_smoothing_and_flags(rows)
    # day 6 (0-indexed) = mean(0..6) = 3.0
    assert rows[6]["residual_7d"] == 3.0
    assert rows[5]["residual_7d"] is None  # < 7 days


def test_quiet_series_has_no_signal():
    # Pure noise: occasional WATCH (~5% of days cross |z|≥2 by construction of a
    # z-score) is expected, but a sustained SIGNAL must NOT fire on noise.
    rng = __import__("numpy").random.default_rng(0)
    rows = _series(list(rng.normal(0, 10, size=200)))
    balance._add_smoothing_and_flags(rows)
    assert not any(r["flag"] and r["flag"].startswith("SIGNAL") for r in rows)


def test_sustained_shift_triggers_watch_then_signal():
    import numpy as np

    rng = np.random.default_rng(1)
    quiet = list(rng.normal(0, 10, size=110))
    spike = [2000.0] * 10  # large sustained residual
    rows = _series(quiet + spike, supply_spike_at=110)
    balance._add_smoothing_and_flags(rows)

    flags = [r["flag"] for r in rows]
    assert any(f and f.startswith("WATCH") for f in flags)
    signals = [f for f in flags if f and f.startswith("SIGNAL")]
    assert signals, "a multi-day |z|≥3 run should escalate to SIGNAL"
    # attribution points at supply (which spiked alongside the residual)
    assert any("supply" in f for f in signals)


def test_z_score_needs_history_before_flagging():
    import numpy as np

    rng = np.random.default_rng(3)
    rows = _series(list(rng.normal(0, 10, size=200)))
    balance._add_smoothing_and_flags(rows)
    # No z-score (hence no flag) until enough trailing 7d-mean history exists.
    assert rows[10]["z_score"] is None
    assert rows[10]["flag"] is None
    assert any(r["z_score"] is not None for r in rows[60:])
