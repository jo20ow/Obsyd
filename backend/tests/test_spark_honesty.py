"""The desk showed +€25, in green, and called it the CCGT margin. It was not.

A gas plant has to buy carbon. At the EUA price the EU ETS was trading at, a CCGT at this desk's
own heat rate owes about €32/MWh for it — enough to turn Germany's, France's and Spain's
"margins" negative. The number was wrong in the direction that flatters us, and it takes an
analyst ten seconds to check.

These tests pin the honest version: the spread is DIRTY, it says so, and it publishes the carbon
price at which it becomes a zero margin — which needs no EUA data at all, and is therefore
publishable while the licence question is still open.
"""
from __future__ import annotations

import pytest

from backend.models.energy import EnergyPrice  # registers the table before conftest creates it
from backend.power.spark import (
    NATURAL_GAS_CO2_T_PER_MWH_TH,
    breakeven_eua,
    clean_spark,
    co2_intensity,
)

HEAT_RATE = 2.0  # 1 / 0.50, this desk's default CCGT efficiency


def test_the_carbon_a_ccgt_owes_per_mwh_of_electricity():
    """0.202 tCO2 per MWh of GAS burned (IPCC's default factor for natural gas, the one EU ETS
    monitoring uses) × 2 MWh of gas per MWh of power = 0.404 tCO2 per MWh of electricity."""
    assert NATURAL_GAS_CO2_T_PER_MWH_TH == 0.202
    assert co2_intensity(HEAT_RATE) == pytest.approx(0.404)


def test_the_breakeven_carbon_price_is_where_the_margin_hits_zero():
    """THE number this module exists to publish. Germany on 2026-07-10: a dirty spread of €25.50.
    Divide by the carbon intensity and you get the EUA price at which the plant earns nothing.

    €63/t. The EU ETS was at €78. The German "margin" was negative, and the desk was showing it
    in green."""
    assert breakeven_eua(25.50, HEAT_RATE) == pytest.approx(63.1, abs=0.1)

    # …and the arithmetic is consistent: at exactly that carbon price, the clean spread is zero.
    assert clean_spark(25.50, HEAT_RATE, 63.1) == pytest.approx(0.0, abs=0.05)


def test_the_carbon_price_that_was_actually_trading_flips_the_sign():
    """Not a rounding error. A sign error."""
    assert clean_spark(25.50, HEAT_RATE, 78.0) < 0, "DE-LU"
    assert clean_spark(21.60, HEAT_RATE, 78.0) < 0, "FR"
    assert clean_spark(19.00, HEAT_RATE, 78.0) < 0, "ES"
    assert clean_spark(50.90, HEAT_RATE, 78.0) > 0, "IT-Nord — the one that really was in profit"


def test_a_spread_that_is_already_negative_has_no_breakeven():
    """A plant under water BEFORE carbon is not saved by any carbon price. Quoting a negative
    break-even would invite the reader to take it for one."""
    assert breakeven_eua(-69.1, HEAT_RATE) is None      # SE1, on real data
    assert breakeven_eua(0.0, HEAT_RATE) is None


def test_the_clean_spread_stays_None_without_a_carbon_price():
    """It is not computed from a guess. There is no confirmed free, redistributable daily EUA
    series (docs/findings/2026-06-24-eua-coal-data-source.md), and nothing enters the free core
    until there is. The arithmetic is written down and tested anyway, so the day the licence
    clears it is not reinvented under time pressure."""
    assert clean_spark(25.50, HEAT_RATE, None) is None


# ─── what the desk must no longer say ─────────────────────────────────────────


def test_the_api_does_not_republish_the_gas_leg(db_session):
    """yfinance's TTF is Yahoo's copy of the ICE Endex front-month — licensed exchange data,
    which CLAUDE.md says this project does not redistribute. The raw close is gone from the
    response; only the derived spread is served.

    A MITIGATION, not a cure, and the docstring says so: the heat rate is published and the power
    price is public, so the spread can be inverted back to the gas leg by anyone who cares. The
    real fix is a free redistributable European gas benchmark, and there is not one."""
    from datetime import date, timedelta

    from fastapi.testclient import TestClient

    from backend.database import get_db
    from backend.main import app

    d = (date.today() - timedelta(days=3)).isoformat()
    db_session.add(EnergyPrice(date=d, symbol="POWER_DE", close=122.81))
    db_session.add(EnergyPrice(date=d, symbol="TTF", close=48.655))
    db_session.commit()

    app.dependency_overrides[get_db] = lambda: db_session
    try:
        body = TestClient(app).get("/api/power/spark-spread?zone=DE_LU&days=30").json()
        hero = TestClient(app).get("/api/power/situation?zone=DE_LU").json()
    finally:
        app.dependency_overrides.clear()

    assert "gas_price" not in body["latest"]
    assert "gas_price" not in hero["spark"]

    # The real DE-LU numbers of 2026-07-10, end to end.
    assert body["latest"]["dirty_spark_spread"] == pytest.approx(25.5, abs=0.01)
    assert body["latest"]["breakeven_eua_eur_t"] == pytest.approx(63.1, abs=0.1)


def test_the_headline_never_calls_a_dirty_spread_a_margin(db_session):
    """The exact words that were wrong: the hero rendered "CCGT margin", in green, for a number
    that excludes the cost of carbon. It must say what it is, and carry the carbon price that
    makes the omission legible."""
    from backend.routes.power import build_power_situation

    price = [{"date": "2026-07-10", "close": 122.81, "negative_hours": 0}]
    grid = [{"date": "2026-07-10", "residual_mw": 30_000.0, "renewable_share": 0.4,
             "dunkelflaute": False}]
    spark_latest = {"spark_spread": 25.5, "power_price": 122.81, "gas_price": 48.655}

    s = build_power_situation("DE_LU", price, grid, spark_latest)

    assert s["spark"]["dirty_spark_spread"] == pytest.approx(25.5)
    assert s["spark"]["breakeven_eua_eur_t"] == pytest.approx(63.1, abs=0.1)

    headline = s["headline"].lower()
    assert "dirty spark" in headline
    assert "margin" not in headline, "it is not a margin until the carbon is paid for"
    assert "/t co₂" in headline or "/t co2" in headline, "the break-even travels with the number"
