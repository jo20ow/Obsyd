"""ENTSO-E power-burn tests — XML parse + ingestion (mock fetch).

Built against the documented A75/B04 schema; re-verify the parser against a
live response once an ENTSO-E token is available.
"""

from __future__ import annotations

from pathlib import Path

from backend.gas import entsoe
from backend.models.gas import GasPowerBurn

FIXTURES = Path(__file__).parent / "fixtures" / "gas"


def _a75(ts_blocks: str, ns: str = "urn:iec62325.351:tc57wg16:451-6:generationloaddocument:3:0") -> str:
    return f'<?xml version="1.0"?><GL_MarketDocument xmlns="{ns}"><type>A75</type>{ts_blocks}</GL_MarketDocument>'


def _ts(start, end, mw, n=24, res="PT60M", psr="B04"):
    pts = "".join(f"<Point><position>{i + 1}</position><quantity>{mw}</quantity></Point>" for i in range(n))
    return (
        f"<TimeSeries><MktPSRType><psrType>{psr}</psrType></MktPSRType>"
        f"<Period><timeInterval><start>{start}</start><end>{end}</end></timeInterval>"
        f"<resolution>{res}</resolution>{pts}</Period></TimeSeries>"
    )


def test_parse_hourly_mw_to_daily_gwh():
    xml = _a75(_ts("2026-04-01T00:00Z", "2026-04-02T00:00Z", 5000))  # 24×5000 MWh = 120 GWh
    assert entsoe.parse_generation(xml) == {"2026-04-01": 120.0}


def test_parse_buckets_multiple_days():
    xml = _a75(
        _ts("2026-04-01T00:00Z", "2026-04-02T00:00Z", 5000)
        + _ts("2026-04-02T00:00Z", "2026-04-03T00:00Z", 4000)
    )
    out = entsoe.parse_generation(xml)
    assert out == {"2026-04-01": 120.0, "2026-04-02": 96.0}


def test_parse_quarter_hourly_resolution():
    # 96 × 15-min points of 4000 MW → 4000 × 0.25 × 96 = 96 GWh
    xml = _a75(_ts("2026-04-01T00:00Z", "2026-04-02T00:00Z", 4000, n=96, res="PT15M"))
    assert entsoe.parse_generation(xml) == {"2026-04-01": 96.0}


def test_parse_ignores_non_gas_psr():
    xml = _a75(_ts("2026-04-01T00:00Z", "2026-04-02T00:00Z", 5000, psr="B14"))  # nuclear
    assert entsoe.parse_generation(xml) == {}


def test_parse_real_shape_fixture():
    xml = (FIXTURES / "entsoe_a75_b04.xml").read_text()
    out = entsoe.parse_generation(xml)
    assert out["2026-04-01"] == 120.0
    assert out["2026-04-02"] == 96.0


def test_parse_malformed_raises():
    import pytest

    with pytest.raises(ValueError):
        entsoe.parse_generation("<not-xml")


async def test_ingest_aggregates_zones_and_applies_efficiency(db_session, monkeypatch):
    # Two zones each report 120 GWh on 2026-04-01 → 240 GWh total gen,
    # implied gas = 240 / 0.5 = 480 GWh.
    xml = _a75(_ts("2026-04-01T00:00Z", "2026-04-02T00:00Z", 5000))
    calls = {"n": 0}

    async def fake_fetch(eic, month_start, *, overwrite=False):
        calls["n"] += 1
        # Only the first two zones return data; the rest are empty.
        return xml if calls["n"] <= 2 else ""

    from pydantic import SecretStr

    monkeypatch.setattr(entsoe, "_fetch_zone_month", fake_fetch)
    monkeypatch.setattr(entsoe.settings, "gas_ccgt_efficiency", 0.5)
    monkeypatch.setattr(entsoe.settings, "entsoe_api_token", SecretStr("test-token"))

    res = await entsoe.ingest_power_burn(db_session, ["2026-04-01"])
    assert res["written"] == 1
    row = db_session.get(GasPowerBurn, "2026-04-01")
    assert row.gen_gwh_el == 240.0
    assert row.implied_gas_gwh == 480.0
    assert row.efficiency == 0.5


async def test_ingest_filters_to_requested_days(db_session, monkeypatch):
    xml = _a75(
        _ts("2026-04-01T00:00Z", "2026-04-02T00:00Z", 5000)
        + _ts("2026-04-02T00:00Z", "2026-04-03T00:00Z", 4000)
    )

    async def fake_fetch(eic, month_start, *, overwrite=False):
        return xml if eic == entsoe.EU27_BIDDING_ZONES["DE-LU"] else ""

    from pydantic import SecretStr

    monkeypatch.setattr(entsoe, "_fetch_zone_month", fake_fetch)
    monkeypatch.setattr(entsoe.settings, "entsoe_api_token", SecretStr("test-token"))
    await entsoe.ingest_power_burn(db_session, ["2026-04-01"])  # only 04-01 requested
    assert db_session.get(GasPowerBurn, "2026-04-01") is not None
    assert db_session.get(GasPowerBurn, "2026-04-02") is None  # 04-02 not requested


def test_token_required(monkeypatch):
    import pytest

    monkeypatch.setattr(entsoe.settings, "entsoe_api_token", None)
    with pytest.raises(RuntimeError):
        entsoe._token()
