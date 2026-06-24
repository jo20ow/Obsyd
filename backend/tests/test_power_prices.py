"""ENTSO-E A44 day-ahead price parser tests — no network.

Tests the parse_day_ahead_prices() function against minimal hand-crafted XML
that mirrors the real A44 schema (Publication_MarketDocument), and exercises
the ingest_day_ahead() upsert path with a mocked fetch.
"""

from __future__ import annotations

import pytest

from backend.power.entsoe_prices import parse_day_ahead_prices
from backend.models.energy import EnergyPrice


# ─── XML helpers ─────────────────────────────────────────────────────────────

_NS = "urn:iec62325.351:tc57wg16:451-6:publicationdocument:7:0"


def _a44(ts_blocks: str, ns: str = _NS) -> str:
    return (
        f'<?xml version="1.0"?>'
        f'<Publication_MarketDocument xmlns="{ns}">'
        f"<type>A44</type>"
        f"{ts_blocks}"
        f"</Publication_MarketDocument>"
    )


def _ts(start: str, end: str, prices: list[float], res: str = "PT60M") -> str:
    """Build a single TimeSeries/Period block with the given hourly prices."""
    pts = "".join(
        f"<Point><position>{i + 1}</position><price.amount>{p}</price.amount></Point>"
        for i, p in enumerate(prices)
    )
    return (
        f"<TimeSeries>"
        f"<Period>"
        f"<timeInterval><start>{start}</start><end>{end}</end></timeInterval>"
        f"<resolution>{res}</resolution>"
        f"{pts}"
        f"</Period>"
        f"</TimeSeries>"
    )


# ─── parse tests ─────────────────────────────────────────────────────────────


def test_parse_single_day_exact_mean():
    """24 identical hourly prices → daily mean equals that price."""
    prices = [50.0] * 24
    xml = _a44(_ts("2026-05-01T00:00Z", "2026-05-02T00:00Z", prices))
    result = parse_day_ahead_prices(xml)
    assert result == {"2026-05-01": 50.0}


def test_parse_single_day_varying_prices():
    """Daily mean of [40, 60] over 2 hours = 50.0."""
    prices = [40.0, 60.0]
    xml = _a44(_ts("2026-05-01T00:00Z", "2026-05-01T02:00Z", prices))
    result = parse_day_ahead_prices(xml)
    assert result == {"2026-05-01": 50.0}


def test_parse_two_days_separate():
    """Two separate Period blocks on consecutive days → two dict entries."""
    xml = _a44(
        _ts("2026-05-01T00:00Z", "2026-05-02T00:00Z", [100.0] * 24)
        + _ts("2026-05-02T00:00Z", "2026-05-03T00:00Z", [200.0] * 24)
    )
    result = parse_day_ahead_prices(xml)
    assert result == {"2026-05-01": 100.0, "2026-05-02": 200.0}


def test_parse_two_days_in_one_period():
    """A 48-hour Period spanning two days; hours bucket to UTC day correctly."""
    # 24 hours at 30 EUR + 24 hours at 70 EUR → two days with distinct means
    prices = [30.0] * 24 + [70.0] * 24
    xml = _a44(_ts("2026-05-01T00:00Z", "2026-05-03T00:00Z", prices))
    result = parse_day_ahead_prices(xml)
    assert result == {"2026-05-01": 30.0, "2026-05-02": 70.0}


def test_parse_no_price_amount_returns_empty():
    """A Point that uses <quantity> (A75-style) instead of <price.amount> is skipped."""
    xml = _a44(
        f"<TimeSeries><Period>"
        f"<timeInterval><start>2026-05-01T00:00Z</start><end>2026-05-02T00:00Z</end></timeInterval>"
        f"<resolution>PT60M</resolution>"
        f"<Point><position>1</position><quantity>5000</quantity></Point>"
        f"</Period></TimeSeries>"
    )
    result = parse_day_ahead_prices(xml)
    assert result == {}


def test_parse_half_hourly_resolution():
    """PT30M: 48 half-hour slots of 80 EUR → mean = 80.0."""
    prices = [80.0] * 48
    xml = _a44(_ts("2026-05-01T00:00Z", "2026-05-02T00:00Z", prices, res="PT30M"))
    result = parse_day_ahead_prices(xml)
    assert result == {"2026-05-01": 80.0}


def test_parse_malformed_xml_raises():
    with pytest.raises(ValueError):
        parse_day_ahead_prices("<not-xml")


def test_parse_empty_document():
    xml = _a44("")
    assert parse_day_ahead_prices(xml) == {}


def test_parse_namespace_agnostic():
    """Parser must work even with a different (or no) XML namespace."""
    prices = [55.5] * 24
    xml = _a44(_ts("2026-05-01T00:00Z", "2026-05-02T00:00Z", prices), ns="some:other:ns")
    result = parse_day_ahead_prices(xml)
    assert result == {"2026-05-01": 55.5}


# ─── ingest tests ─────────────────────────────────────────────────────────────


async def test_ingest_upserts_daily_mean(db_session, monkeypatch):
    """ingest_day_ahead writes a POWER_DE row for each requested day."""
    from backend.power import entsoe_prices
    from pydantic import SecretStr

    prices = [100.0] * 24
    xml = _a44(_ts("2026-05-01T00:00Z", "2026-05-02T00:00Z", prices))

    async def fake_fetch(eic, month_start, *, overwrite=False):
        return xml

    monkeypatch.setattr(entsoe_prices, "_fetch_zone_month", fake_fetch)
    monkeypatch.setattr(entsoe_prices.settings, "entsoe_api_token", SecretStr("tok"))

    result = await entsoe_prices.ingest_day_ahead(db_session, ["2026-05-01"])
    assert result == {"days": 1, "written": 1}

    row = (
        db_session.query(EnergyPrice)
        .filter_by(date="2026-05-01", symbol="POWER_DE")
        .first()
    )
    assert row is not None
    assert row.close == 100.0


async def test_ingest_skips_when_no_token(db_session, monkeypatch):
    from backend.power import entsoe_prices

    monkeypatch.setattr(entsoe_prices.settings, "entsoe_api_token", None)
    result = await entsoe_prices.ingest_day_ahead(db_session, ["2026-05-01"])
    assert result == {"skipped": "no token"}
    assert db_session.query(EnergyPrice).count() == 0


async def test_ingest_idempotent_upsert(db_session, monkeypatch):
    """Running ingest twice doesn't duplicate rows; close is updated."""
    from backend.power import entsoe_prices
    from pydantic import SecretStr

    xml_v1 = _a44(_ts("2026-05-01T00:00Z", "2026-05-02T00:00Z", [100.0] * 24))
    xml_v2 = _a44(_ts("2026-05-01T00:00Z", "2026-05-02T00:00Z", [120.0] * 24))
    fetch_results = [xml_v1, xml_v2]

    call_count = {"n": 0}

    async def fake_fetch(eic, month_start, *, overwrite=False):
        idx = call_count["n"]
        call_count["n"] += 1
        return fetch_results[min(idx, len(fetch_results) - 1)]

    monkeypatch.setattr(entsoe_prices, "_fetch_zone_month", fake_fetch)
    monkeypatch.setattr(entsoe_prices.settings, "entsoe_api_token", SecretStr("tok"))

    await entsoe_prices.ingest_day_ahead(db_session, ["2026-05-01"])
    await entsoe_prices.ingest_day_ahead(db_session, ["2026-05-01"], overwrite=True)

    rows = db_session.query(EnergyPrice).filter_by(symbol="POWER_DE").all()
    assert len(rows) == 1
    assert rows[0].close == 120.0


async def test_ingest_filters_to_requested_days(db_session, monkeypatch):
    """Days outside `days` list are not written even if present in the XML."""
    from backend.power import entsoe_prices
    from pydantic import SecretStr

    xml = _a44(
        _ts("2026-05-01T00:00Z", "2026-05-02T00:00Z", [100.0] * 24)
        + _ts("2026-05-02T00:00Z", "2026-05-03T00:00Z", [200.0] * 24)
    )

    async def fake_fetch(eic, month_start, *, overwrite=False):
        return xml

    monkeypatch.setattr(entsoe_prices, "_fetch_zone_month", fake_fetch)
    monkeypatch.setattr(entsoe_prices.settings, "entsoe_api_token", SecretStr("tok"))

    result = await entsoe_prices.ingest_day_ahead(db_session, ["2026-05-01"])
    assert result == {"days": 1, "written": 1}
    assert db_session.query(EnergyPrice).filter_by(date="2026-05-02").first() is None
