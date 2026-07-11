"""ENTSO-E imbalance prices (A85): parse (ZIP-inner XML) → hourly series imbalance.price."""
from __future__ import annotations

import pytest

from backend.models.energy import PowerHourly, SeriesDim, ZoneDim  # noqa: F401 — register tables
from backend.power import entsoe_imbalance as imb
from backend.power.entsoe_imbalance import control_area_eic, parse_imbalance_prices
from backend.power.hourly_store import read_hourly

NS = "urn:iec62325.351:tc57wg16:451-6:balancingdocument:4:0"


def _a85(amounts, start="2026-06-01T00:00Z", res="PT60M", financial=False):
    pts = ""
    for i, v in enumerate(amounts):
        fin = "<Financial_Price><imbalance_Price.amount>999.0</imbalance_Price.amount></Financial_Price>" if financial else ""
        pts += (f"<Point><position>{i + 1}</position>"
                f"<imbalance_Price.amount>{v}</imbalance_Price.amount>"
                f"<imbalance_Price.category>A04</imbalance_Price.category>{fin}</Point>")
    return (f'<?xml version="1.0"?><Balancing_MarketDocument xmlns="{NS}"><TimeSeries><Period>'
            f"<timeInterval><start>{start}</start><end>2026-06-02T00:00Z</end></timeInterval>"
            f"<resolution>{res}</resolution>{pts}</Period></TimeSeries></Balancing_MarketDocument>")


def test_parse_hourly():
    xml = _a85([50.0 + i for i in range(24)])
    out = parse_imbalance_prices(xml)["2026-06-01"]
    assert len(out) == 24
    assert out[0] == 50.0
    assert out[23] == 73.0


def test_parse_pt15m_averaged_to_hourly():
    xml = _a85([80.0] * 96, res="PT15M")  # 96 quarter-hours → 24 hourly means
    out = parse_imbalance_prices(xml)["2026-06-01"]
    assert len(out) == 24
    assert out[0] == 80.0


def test_parse_ignores_nested_financial_price():
    # Point-level amount is taken; the nested Financial_Price (999) is ignored.
    xml = _a85([120.0] * 24, financial=True)
    out = parse_imbalance_prices(xml)["2026-06-01"]
    assert out[0] == 120.0


def test_parse_acknowledgement_is_empty():
    ack = '<?xml version="1.0"?><Acknowledgement_MarketDocument><mRID>x</mRID></Acknowledgement_MarketDocument>'
    assert parse_imbalance_prices(ack) == {}


def test_parse_malformed_raises():
    with pytest.raises(ValueError):
        parse_imbalance_prices("<not-xml")


def test_control_area_eic_skips_de():
    assert control_area_eic("DE_LU") is None  # 4 control areas, combined reBAP not exposed
    assert control_area_eic("FR") == "10YFR-RTE------C"
    assert control_area_eic("BE") == "10YBE----------2"


async def test_ingest_writes_series(db_session, monkeypatch):
    monkeypatch.setattr(imb.settings, "entsoe_api_token", "x")

    async def fake_fetch(ca_eic, month_start, **kw):
        return _a85([40.0 + i for i in range(24)])

    monkeypatch.setattr(imb, "_fetch_imbalance_month", fake_fetch)
    r = await imb.ingest_imbalance(db_session, ["2026-06-01"], zone="FR", overwrite=True)
    assert r["written"] == 24
    series = read_hourly(db_session, "imbalance.price", "FR")
    assert len(series) == 24
    assert series[0][1] == 40.0


async def test_ingest_de_skipped(db_session, monkeypatch):
    monkeypatch.setattr(imb.settings, "entsoe_api_token", "x")
    r = await imb.ingest_imbalance(db_session, ["2026-06-01"], zone="DE_LU")
    assert r == {"skipped": "no control area (DE has 4)"}


# ─── quarter-hourly: imbalance settles in 15-min periods — that IS the native truth ─
#
# The hourly mean erases exactly what imbalance watchers care about: single
# ±1000 €/MWh quarter-hours vanish into a bland average.


def _epoch(iso: str) -> int:
    from datetime import datetime, timezone

    return int(datetime.fromisoformat(iso).replace(tzinfo=timezone.utc).timestamp())


def test_parse_qh_keeps_raw_quarter_hours():
    from backend.power.entsoe_imbalance import parse_imbalance_quarter_hourly

    amounts = [50.0] * 95 + [-990.0]  # one extreme settlement period
    xml = _a85(amounts, res="PT15M")
    pts = parse_imbalance_quarter_hourly(xml)

    assert len(pts) == 96
    assert pts[0] == (_epoch("2026-06-01T00:00"), 50.0)
    assert pts[-1] == (_epoch("2026-06-01T23:45"), -990.0)


def test_parse_qh_ignores_hourly_documents():
    from backend.power.entsoe_imbalance import parse_imbalance_quarter_hourly

    assert parse_imbalance_quarter_hourly(_a85([50.0] * 24, res="PT60M")) == []


def test_parse_qh_takes_point_amount_not_financial(db_session):
    from backend.power.entsoe_imbalance import parse_imbalance_quarter_hourly

    pts = parse_imbalance_quarter_hourly(_a85([70.0] * 96, res="PT15M", financial=True))
    assert len(pts) == 96
    assert all(v == 70.0 for _, v in pts)


async def test_ingest_writes_qh_series_alongside_hourly(db_session, monkeypatch):
    from pydantic import SecretStr

    amounts = [50.0] * 95 + [-990.0]
    xml = _a85(amounts, res="PT15M")

    async def fake_fetch(ca_eic, month_start, *, overwrite=False):
        return xml

    monkeypatch.setattr(imb, "_fetch_imbalance_month", fake_fetch)
    monkeypatch.setattr(imb.settings, "entsoe_api_token", SecretStr("tok"))

    await imb.ingest_imbalance(db_session, ["2026-06-01"], zone="FR")

    qh = read_hourly(db_session, "imbalance.price.qh", "FR")
    assert len(qh) == 96
    assert qh[-1][1] == -990.0
    # hourly stays the averaged view: the extreme quarter-hour is diluted there
    hourly = read_hourly(db_session, "imbalance.price", "FR")
    assert len(hourly) == 24
    assert hourly[-1][1] == pytest.approx((50.0 * 3 - 990.0) / 4)
