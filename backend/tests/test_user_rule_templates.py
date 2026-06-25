"""
Tests for the cross-vertical user alert-rule templates (Slice 2):
  - negative_prices (power) and gas_balance (gas) evaluators trigger on a
    seeded fixture and return None when they shouldn't
  - both are registered in TEMPLATES and validate_params accepts/rejects
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from backend.models.energy import PowerPriceDaily
from backend.models.gas import GasBalance
from backend.signals import user_alert_rules

NOW = datetime(2026, 6, 25, 12, 0, 0)


def test_new_templates_registered():
    assert "negative_prices" in user_alert_rules.TEMPLATES
    assert "gas_balance" in user_alert_rules.TEMPLATES
    # validate_params on the enum + the no-param template
    ok, _ = user_alert_rules.validate_params("negative_prices", {"zone": "DE_LU"})
    assert ok
    ok, err = user_alert_rules.validate_params("negative_prices", {"zone": "XX"})
    assert not ok and err
    ok, _ = user_alert_rules.validate_params("gas_balance", {})
    assert ok


def test_negative_prices_triggers(db_session):
    anchor = date(2026, 6, 24)
    # Trailing norm: a low, varying negative-hour count (variance > 0 so z is defined).
    for o in range(1, 31):
        d = (anchor - timedelta(days=o)).isoformat()
        db_session.add(
            PowerPriceDaily(date=d, zone="DE_LU", mean_price=30, min_price=-5, max_price=70, negative_hours=o % 3)
        )
    # Today: a spike well above the norm.
    db_session.add(
        PowerPriceDaily(date=anchor.isoformat(), zone="DE_LU", mean_price=8, min_price=-60, max_price=55, negative_hours=14)
    )
    db_session.commit()

    res = user_alert_rules.evaluate_negative_prices(db_session, {"zone": "DE_LU"}, now=NOW)
    assert res is not None
    assert "DE_LU" in res.title
    assert res.payload["negative_hours"] == 14


def test_negative_prices_quiet_zone_no_trigger(db_session):
    anchor = date(2026, 6, 24)
    for o in range(0, 20):
        d = (anchor - timedelta(days=o)).isoformat()
        db_session.add(
            PowerPriceDaily(date=d, zone="FR", mean_price=40, min_price=5, max_price=80, negative_hours=0)
        )
    db_session.commit()
    assert user_alert_rules.evaluate_negative_prices(db_session, {"zone": "FR"}, now=NOW) is None


def test_gas_balance_triggers(db_session):
    db_session.add(GasBalance(date="2026-06-24", flag="SIGNAL:supply↑", z_score=3.2, residual_7d=-120.0))
    db_session.commit()
    res = user_alert_rules.evaluate_gas_balance(db_session, {}, now=NOW)
    assert res is not None
    assert "signal" in res.title.lower()
    assert res.payload["level"] == "SIGNAL"


def test_gas_balance_no_flag_no_trigger(db_session):
    db_session.add(GasBalance(date="2026-06-24", flag=None, z_score=0.4, residual_7d=5.0))
    db_session.commit()
    assert user_alert_rules.evaluate_gas_balance(db_session, {}, now=NOW) is None
