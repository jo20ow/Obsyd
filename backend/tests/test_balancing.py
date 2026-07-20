"""ENTSO-E activated balancing energy (A83 volumes / A84 prices) → hourly series
`balancing.<afrr|mfrr>.<price|vol>.<up|down>`. Mirrors test_imbalance.py's shape."""
from __future__ import annotations

import pytest

from backend.models.energy import PowerHourly, SeriesDim, ZoneDim  # noqa: F401 — register tables
from backend.power import entsoe_balancing as bal
from backend.power.entsoe_balancing import (
    control_area_eic,
    parse_balancing_prices,
    parse_balancing_volumes,
)
from backend.power.hourly_store import read_hourly

NS = "urn:iec62325.351:tc57wg16:451-6:balancingdocument:4:1"


def _balancing_doc(series: list[dict], start="2026-06-01T00:00Z", end="2026-06-02T00:00Z") -> str:
    """Build a Balancing_MarketDocument with one TimeSeries per `series` entry.

    Each entry: {"business_type": "A96", "direction": "A01", "amounts": [...],
    "tag": "activation_Price.amount", "res": "PT15M"}.
    """
    ts_blocks = []
    for s in series:
        pts = "".join(
            f"<Point><position>{i + 1}</position><{s['tag']}>{v}</{s['tag']}></Point>"
            for i, v in enumerate(s["amounts"])
        )
        ts_blocks.append(
            f"<TimeSeries><mRID>1</mRID><businessType>{s['business_type']}</businessType>"
            f"<flowDirection.direction>{s['direction']}</flowDirection.direction>"
            f"<curveType>A03</curveType><Period>"
            f"<timeInterval><start>{start}</start><end>{end}</end></timeInterval>"
            f"<resolution>{s.get('res', 'PT15M')}</resolution>{pts}</Period></TimeSeries>"
        )
    return (
        f'<?xml version="1.0"?><Balancing_MarketDocument xmlns="{NS}">'
        + "".join(ts_blocks)
        + "</Balancing_MarketDocument>"
    )


def _afrr_up_prices(amounts, **kw):
    return _balancing_doc(
        [{"business_type": "A96", "direction": "A01", "amounts": amounts,
          "tag": "activation_Price.amount", **kw}]
    )


# ─── control-area domain resolution ───────────────────────────────────────────


def test_control_area_eic_de_uses_tennet_not_country_code():
    """Spiked 2026-07-20 live: unlike A85's reBAP, DE aFRR/mFRR prices are NOT published at
    the country EIC (10Y1001A1001A83F) or the DE_LU bidding-zone EIC — both answer a clean
    'No matching data found'. TenneT's control area (10YDE-EON------1) carries real data."""
    assert control_area_eic("DE_LU") == "10YDE-EON------1"
    assert control_area_eic("FR") == "10YFR-RTE------C"
    assert control_area_eic("NL") == "10YNL----------L"


# ─── parse_balancing_prices ────────────────────────────────────────────────────


def test_parse_prices_hourly_mean_afrr_up():
    xml = _afrr_up_prices([100.0 + i for i in range(96)])  # 96 PT15M -> 24 hourly means
    out = parse_balancing_prices(xml)
    assert set(out.keys()) == {("afrr", "up")}
    day = out[("afrr", "up")]["2026-06-01"]
    assert len(day) == 24
    # hour 0 = mean of positions 1-4 -> 100,101,102,103 -> mean 101.5
    assert day[0] == pytest.approx(101.5)


def test_parse_prices_splits_by_direction_and_product():
    xml = _balancing_doc([
        {"business_type": "A96", "direction": "A01", "amounts": [150.0] * 4, "tag": "activation_Price.amount"},
        {"business_type": "A96", "direction": "A02", "amounts": [50.0] * 4, "tag": "activation_Price.amount"},
        {"business_type": "A97", "direction": "A01", "amounts": [200.0] * 4, "tag": "activation_Price.amount"},
    ])
    out = parse_balancing_prices(xml)
    assert out[("afrr", "up")]["2026-06-01"][0] == 150.0
    assert out[("afrr", "down")]["2026-06-01"][0] == 50.0
    assert out[("mfrr", "up")]["2026-06-01"][0] == 200.0
    assert ("mfrr", "down") not in out


def test_parse_prices_ignores_fcr_and_rr():
    """FCR (A95) and RR (A98) are out of scope for this desk (aFRR/mFRR only)."""
    xml = _balancing_doc([
        {"business_type": "A95", "direction": "A01", "amounts": [999.0] * 4, "tag": "activation_Price.amount"},
        {"business_type": "A98", "direction": "A01", "amounts": [888.0] * 4, "tag": "activation_Price.amount"},
    ])
    assert parse_balancing_prices(xml) == {}


def test_parse_prices_gap_between_periods_is_not_filled():
    """curveType A03 is declared but does NOT behave like A09's step function here: a gap
    between two Periods means genuinely no activation happened, not a value to hold forward
    (spiked live 2026-07-20 — see module docstring). A second Period starting after a gap
    must not smuggle in the first period's last value for the missing slot."""
    xml = (
        f'<?xml version="1.0"?><Balancing_MarketDocument xmlns="{NS}"><TimeSeries>'
        "<businessType>A96</businessType><flowDirection.direction>A01</flowDirection.direction>"
        "<curveType>A03</curveType>"
        '<Period><timeInterval><start>2026-06-01T00:00Z</start><end>2026-06-01T01:00Z</end></timeInterval>'
        "<resolution>PT15M</resolution>"
        "<Point><position>1</position><activation_Price.amount>10.0</activation_Price.amount></Point>"
        "<Point><position>2</position><activation_Price.amount>10.0</activation_Price.amount></Point>"
        "<Point><position>3</position><activation_Price.amount>10.0</activation_Price.amount></Point>"
        "<Point><position>4</position><activation_Price.amount>10.0</activation_Price.amount></Point>"
        "</Period>"
        # gap: 01:00-01:30 has no Period at all
        '<Period><timeInterval><start>2026-06-01T01:30Z</start><end>2026-06-01T02:00Z</end></timeInterval>'
        "<resolution>PT15M</resolution>"
        "<Point><position>1</position><activation_Price.amount>20.0</activation_Price.amount></Point>"
        "<Point><position>2</position><activation_Price.amount>20.0</activation_Price.amount></Point>"
        "</Period></TimeSeries></Balancing_MarketDocument>"
    )
    out = parse_balancing_prices(xml)[("afrr", "up")]["2026-06-01"]
    assert len(out) == 2  # only hours 0 and 1 exist; nothing invented for the gap
    assert out[0] == 10.0
    # hour 1 only has the 01:30/01:45 quarter-hours (2 points), not 4 — no fill-forward
    assert out[1] == 20.0


def test_parse_acknowledgement_is_empty():
    ack = '<?xml version="1.0"?><Acknowledgement_MarketDocument><mRID>x</mRID></Acknowledgement_MarketDocument>'
    assert parse_balancing_prices(ack) == {}
    assert parse_balancing_volumes(ack) == {}


def test_parse_malformed_raises():
    with pytest.raises(ValueError):
        parse_balancing_prices("<not-xml")
    with pytest.raises(ValueError):
        parse_balancing_volumes("<not-xml")


# ─── parse_balancing_volumes ────────────────────────────────────────────────────


def test_parse_volumes_hourly_sum_not_mean():
    """Volumes (MWh) are SUMMED per hour — 4 quarter-hour MWh amounts add up to the hour's
    total energy, unlike prices which are averaged."""
    xml = _balancing_doc([
        {"business_type": "A96", "direction": "A01", "amounts": [10.0, 20.0, 30.0, 40.0], "tag": "quantity"},
    ])
    out = parse_balancing_volumes(xml)[("afrr", "up")]["2026-06-01"]
    assert out[0] == 100.0  # sum, not mean (25.0)


def test_parse_volumes_splits_by_direction_and_product():
    xml = _balancing_doc([
        {"business_type": "A97", "direction": "A02", "amounts": [5.0] * 4, "tag": "quantity"},
    ])
    out = parse_balancing_volumes(xml)
    assert set(out.keys()) == {("mfrr", "down")}
    assert out[("mfrr", "down")]["2026-06-01"][0] == 20.0


# ─── ZIP unwrap: multiple inner XML documents merge ────────────────────────────


def test_parse_multi_xml_zip_members_merge(tmp_path, monkeypatch):
    """A83/A84 ZIP archives can hold MULTIPLE inner .xml documents — every member must be
    parsed and merged, not just the first (unlike A85's single-doc ZIP)."""
    import asyncio
    import io
    import zipfile

    doc1 = _balancing_doc([{"business_type": "A96", "direction": "A01", "amounts": [111.0] * 4,
                             "tag": "activation_Price.amount"}])
    doc2 = _balancing_doc([{"business_type": "A96", "direction": "A02", "amounts": [222.0] * 4,
                             "tag": "activation_Price.amount"}])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("doc1.xml", doc1)
        zf.writestr("doc2.xml", doc2)
    zip_bytes = buf.getvalue()

    class _FakeResp:
        status_code = 200
        content = zip_bytes
        text = ""

        def raise_for_status(self):
            pass

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **kw):
            return _FakeResp()

    monkeypatch.setattr(bal.httpx, "AsyncClient", _FakeClient)
    monkeypatch.setattr(bal, "_token", lambda: "tok")

    from datetime import date

    docs = asyncio.run(
        bal._fetch_balancing_month("A84", "entsoe_a84_test", "10YDE-EON------1", date(2026, 6, 1))
    )
    merged: dict = {}
    for xml in docs:
        for key, days in parse_balancing_prices(xml).items():
            merged.setdefault(key, {}).update(days)
    assert merged[("afrr", "up")]["2026-06-01"][0] == 111.0
    assert merged[("afrr", "down")]["2026-06-01"][0] == 222.0


# ─── ingest_balancing ───────────────────────────────────────────────────────────


async def test_ingest_no_token_skips(db_session, monkeypatch):
    monkeypatch.setattr(bal.settings, "entsoe_api_token", None)
    r = await bal.ingest_balancing(db_session, ["2026-06-01"], zone="FR")
    assert r == {"skipped": "no token"}


async def test_ingest_no_days_returns_zero(db_session, monkeypatch):
    monkeypatch.setattr(bal.settings, "entsoe_api_token", "x")
    r = await bal.ingest_balancing(db_session, [], zone="FR")
    assert r["days"] == 0


async def test_ingest_writes_eight_series(db_session, monkeypatch):
    """One month of both aFRR+mFRR, up+down, price+volume -> all 8 canonical series land."""
    monkeypatch.setattr(bal.settings, "entsoe_api_token", "x")

    price_xml = _balancing_doc([
        {"business_type": "A96", "direction": "A01", "amounts": [150.0] * 4, "tag": "activation_Price.amount"},
        {"business_type": "A96", "direction": "A02", "amounts": [50.0] * 4, "tag": "activation_Price.amount"},
        {"business_type": "A97", "direction": "A01", "amounts": [200.0] * 4, "tag": "activation_Price.amount"},
        {"business_type": "A97", "direction": "A02", "amounts": [80.0] * 4, "tag": "activation_Price.amount"},
    ])
    vol_xml = _balancing_doc([
        {"business_type": "A96", "direction": "A01", "amounts": [10.0] * 4, "tag": "quantity"},
        {"business_type": "A96", "direction": "A02", "amounts": [5.0] * 4, "tag": "quantity"},
        {"business_type": "A97", "direction": "A01", "amounts": [20.0] * 4, "tag": "quantity"},
        {"business_type": "A97", "direction": "A02", "amounts": [8.0] * 4, "tag": "quantity"},
    ])

    async def fake_fetch(document_type, cache_source, eic, month_start, *, overwrite=False):
        return [price_xml] if document_type == "A84" else [vol_xml]

    monkeypatch.setattr(bal, "_fetch_balancing_month", fake_fetch)
    r = await bal.ingest_balancing(db_session, ["2026-06-01"], zone="FR", overwrite=True)
    assert r["written"] > 0

    for product, price in (("afrr", 150.0), ("mfrr", 200.0)):
        series = read_hourly(db_session, f"balancing.{product}.price.up", "FR")
        assert series[0][1] == price
    for product, price in (("afrr", 50.0), ("mfrr", 80.0)):
        series = read_hourly(db_session, f"balancing.{product}.price.down", "FR")
        assert series[0][1] == price
    for product, vol in (("afrr", 40.0), ("mfrr", 80.0)):  # sum of 4 quarter-hours, not the mean
        series = read_hourly(db_session, f"balancing.{product}.vol.up", "FR")
        assert series[0][1] == vol
    for product, vol in (("afrr", 20.0), ("mfrr", 32.0)):
        series = read_hourly(db_session, f"balancing.{product}.vol.down", "FR")
        assert series[0][1] == vol


async def test_ingest_volumes_structurally_rejected_is_graceful(db_session, monkeypatch):
    """Spiked live 2026-07-20: A83 answers a structural 400 ('combination not valid') for
    every zone/param combination tried — treated exactly like the codebase's existing
    'clean no-data ACK' convention (entsoe_exchange.py's A09/A25 fetchers): ingestion must
    stay green, prices still write, volumes just come back empty."""
    monkeypatch.setattr(bal.settings, "entsoe_api_token", "x")
    price_xml = _afrr_up_prices([150.0] * 4)

    async def fake_fetch(document_type, cache_source, eic, month_start, *, overwrite=False):
        return [price_xml] if document_type == "A84" else [""]

    monkeypatch.setattr(bal, "_fetch_balancing_month", fake_fetch)
    r = await bal.ingest_balancing(db_session, ["2026-06-01"], zone="FR", overwrite=True)
    assert r["written"] > 0
    assert read_hourly(db_session, "balancing.afrr.price.up", "FR")
    assert read_hourly(db_session, "balancing.afrr.vol.up", "FR") == []


async def test_ingest_fetch_error_is_isolated_per_doctype(db_session, monkeypatch):
    """A price-fetch failure must not stop volumes (and vice versa) — each documentType is
    fetched and handled independently, matching the rest of this vertical's isolate-and-log
    convention."""
    import httpx

    monkeypatch.setattr(bal.settings, "entsoe_api_token", "x")
    vol_xml = _balancing_doc([
        {"business_type": "A96", "direction": "A01", "amounts": [10.0] * 4, "tag": "quantity"},
    ])

    async def fake_fetch(document_type, cache_source, eic, month_start, *, overwrite=False):
        if document_type == "A84":
            raise httpx.HTTPError("boom")
        return [vol_xml]

    monkeypatch.setattr(bal, "_fetch_balancing_month", fake_fetch)
    r = await bal.ingest_balancing(db_session, ["2026-06-01"], zone="FR", overwrite=True)
    assert read_hourly(db_session, "balancing.afrr.vol.up", "FR")
    assert read_hourly(db_session, "balancing.afrr.price.up", "FR") == []
    assert r["written"] > 0


# ─── GET /api/power/balancing ───────────────────────────────────────────────────


def _client(db):
    from fastapi.testclient import TestClient

    from backend.database import get_db
    from backend.main import app

    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    from backend.main import app
    app.dependency_overrides.clear()


def _seed_balancing(db, zone="DE_LU", product="afrr"):
    import time as _time

    from backend.power.hourly_store import upsert_hourly

    now = (int(_time.time()) // 3600) * 3600
    up_prices = [(now - i * 3600, 100.0 + i) for i in range(24, 0, -1)]
    down_prices = [(now - i * 3600, -50.0 - i) for i in range(24, 0, -1)]
    up_vols = [(now - i * 3600, 5.0 + i) for i in range(24, 0, -1)]
    down_vols = [(now - i * 3600, 2.0 + i) for i in range(24, 0, -1)]
    upsert_hourly(db, f"balancing.{product}.price.up", zone, up_prices, unit="EUR/MWh")
    upsert_hourly(db, f"balancing.{product}.price.down", zone, down_prices, unit="EUR/MWh")
    upsert_hourly(db, f"balancing.{product}.vol.up", zone, up_vols, unit="MWh")
    upsert_hourly(db, f"balancing.{product}.vol.down", zone, down_vols, unit="MWh")


def test_route_happy_path(db_session):
    _seed_balancing(db_session)
    body = _client(db_session).get("/api/power/balancing?zone=DE_LU&product=afrr").json()
    assert body["available"] is True
    assert body["zone"] == "DE_LU"
    assert body["product"] == "afrr"
    assert body["unit"] == "EUR/MWh"
    assert len(body["up"]) >= 24
    assert len(body["down"]) >= 24
    assert body["latest"] is not None
    assert body["peak"] is not None
    assert body["as_of"] is not None
    assert "stale" in body and "age_days" in body


def test_route_defaults_to_afrr_and_30_days(db_session):
    _seed_balancing(db_session, product="afrr")
    body = _client(db_session).get("/api/power/balancing?zone=DE_LU").json()
    assert body["product"] == "afrr"
    assert body["days"] == 30


def test_route_mfrr_product(db_session):
    _seed_balancing(db_session, product="mfrr")
    body = _client(db_session).get("/api/power/balancing?zone=DE_LU&product=mfrr").json()
    assert body["available"] is True
    assert body["product"] == "mfrr"


def test_route_invalid_product_is_rejected(db_session):
    resp = _client(db_session).get("/api/power/balancing?zone=DE_LU&product=bogus")
    assert resp.status_code == 422


def test_route_days_clamped(db_session):
    resp_low = _client(db_session).get("/api/power/balancing?zone=DE_LU&days=0")
    resp_high = _client(db_session).get("/api/power/balancing?zone=DE_LU&days=91")
    assert resp_low.status_code == 422
    assert resp_high.status_code == 422


def test_route_unknown_zone_falls_back(db_session):
    """Mirrors get_imbalance / _resolve_zone: an unknown zone falls back to DEFAULT_ZONE
    rather than 400ing (the `zones` list in the response tells the caller what's valid)."""
    _seed_balancing(db_session, zone="DE_LU")
    body = _client(db_session).get("/api/power/balancing?zone=NOPE").json()
    assert body["zone"] == "DE_LU"


def test_route_no_data_is_honest(db_session):
    body = _client(db_session).get("/api/power/balancing?zone=NL&product=mfrr").json()
    assert body["available"] is False
    assert "reason" in body
    assert body["zone"] == "NL"


def test_route_zones_list_present(db_session):
    _seed_balancing(db_session)
    body = _client(db_session).get("/api/power/balancing?zone=DE_LU").json()
    assert "zones" in body and "DE_LU" in body["zones"]


# ─── freshness spec ─────────────────────────────────────────────────────────────


def test_freshness_spec_registered():
    from backend.collectors.freshness import SPECS

    spec = next((s for s in SPECS if s.key == "balancing_energy"), None)
    assert spec is not None
    assert spec.hourly_series == "balancing.afrr.price.up"
    assert spec.max_age.days == 2


# ─── backfill registration ──────────────────────────────────────────────────────


def test_backfill_source_registered():
    from backend.scripts import power_backfill as pb

    assert "balancing" in pb.ALL_SOURCES
