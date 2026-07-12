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


def test_control_area_eic_de_uses_country_code():
    """Spike 2026-07-11 against the live API: the German reBAP is NOT published
    under the four control-area EICs (Acknowledgement 999 for TenneT/Amprion)
    nor under the DE_LU bidding-zone EIC — but the COUNTRY EIC returns the full
    96-slot uniform reBAP. So DE_LU maps to the country code."""
    assert control_area_eic("DE_LU") == "10Y1001A1001A83F"
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


async def test_ingest_de_writes_rebap(db_session, monkeypatch):
    """DE_LU was the only enabled zone without an imbalance series."""
    monkeypatch.setattr(imb.settings, "entsoe_api_token", "x")
    seen = {}

    async def fake_fetch(ca_eic, month_start, **kw):
        seen["eic"] = ca_eic
        return _a85([40.0 + i for i in range(24)])

    monkeypatch.setattr(imb, "_fetch_imbalance_month", fake_fetch)
    r = await imb.ingest_imbalance(db_session, ["2026-06-01"], zone="DE_LU", overwrite=True)
    assert r["written"] == 24
    assert seen["eic"] == "10Y1001A1001A83F", "must query the country EIC, not a control area"
    assert len(read_hourly(db_session, "imbalance.price", "DE_LU")) == 24


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


# ─── GET /api/power/imbalance ─────────────────────────────────────────────────


def _client(db):
    from fastapi.testclient import TestClient

    from backend.database import get_db
    from backend.main import app

    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def _seed_imbalance_series(db, zone="DE_LU", resolution="hourly"):
    import time as _time

    from backend.power.hourly_store import upsert_hourly

    key = "imbalance.price" if resolution == "hourly" else "imbalance.price.qh"
    now = (int(_time.time()) // 3600) * 3600
    step = 3600 if resolution == "hourly" else 900
    points = [(now - i * step, 80.0 + i) for i in range(48, 0, -1)]
    points.append((now, -740.0))  # the spike that makes the panel worth reading
    upsert_hourly(db, key, zone, points, unit="EUR/MWh")


def test_imbalance_route_hourly(db_session):
    _seed_imbalance_series(db_session)
    body = _client(db_session).get("/api/power/imbalance?zone=DE_LU&days=7").json()
    assert body["available"] is True
    assert body["unit"] == "EUR/MWh" and body["resolution"] == "hourly"
    assert body["latest"] == -740.0
    assert body["peak"]["price"] == -740.0
    assert body["stale"] is False and body["as_of"] is not None
    assert len(body["data"]) >= 48


def test_imbalance_route_qh(db_session):
    _seed_imbalance_series(db_session, resolution="qh")
    body = _client(db_session).get("/api/power/imbalance?zone=DE_LU&resolution=qh").json()
    assert body["available"] is True and body["resolution"] == "qh"


def test_imbalance_route_zone_without_series_is_honest(db_session):
    _seed_imbalance_series(db_session, zone="DE_LU")
    body = _client(db_session).get("/api/power/imbalance?zone=NL").json()
    assert body["available"] is False
    assert "A85 coverage" in body["reason"]
    from backend.main import app
    app.dependency_overrides.clear()
