"""ENTSO-E A44 day-ahead price parser tests — no network.

Tests the parse_day_ahead_prices() function against minimal hand-crafted XML
that mirrors the real A44 schema (Publication_MarketDocument), and exercises
the ingest_day_ahead() upsert path with a mocked fetch.
"""

from __future__ import annotations

import pytest

from backend.models.energy import EnergyPrice
from backend.power.entsoe_prices import parse_day_ahead_prices

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
        "<TimeSeries><Period>"
        "<timeInterval><start>2026-05-01T00:00Z</start><end>2026-05-02T00:00Z</end></timeInterval>"
        "<resolution>PT60M</resolution>"
        "<Point><position>1</position><quantity>5000</quantity></Point>"
        "</Period></TimeSeries>"
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
    from pydantic import SecretStr

    from backend.power import entsoe_prices

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
    from pydantic import SecretStr

    from backend.power import entsoe_prices

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
    from pydantic import SecretStr

    from backend.power import entsoe_prices

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


# ─── quarter-hourly parse + ingest (SDAC trades 15-min MTUs since 2025-10-01) ──
#
# The hourly pipeline averages PT15M points into hours — fine for the legacy
# series, but it renders a smoothed picture of a market that has traded in
# 15-minute products since 1 October 2025. The QH series keeps the raw points.


def _epoch(iso: str) -> int:
    from datetime import datetime, timezone

    return int(datetime.fromisoformat(iso).replace(tzinfo=timezone.utc).timestamp())


def test_parse_qh_returns_raw_quarter_hour_points():
    from backend.power.entsoe_prices import parse_day_ahead_quarter_hourly

    prices = [float(10 + i) for i in range(96)]
    xml = _a44(_ts("2026-05-01T00:00Z", "2026-05-02T00:00Z", prices, res="PT15M"))
    pts = parse_day_ahead_quarter_hourly(xml)

    assert len(pts) == 96
    assert pts[0] == (_epoch("2026-05-01T00:00"), 10.0)
    assert pts[1] == (_epoch("2026-05-01T00:15"), 11.0)
    assert pts[-1] == (_epoch("2026-05-01T23:45"), 105.0)


def test_parse_qh_ignores_hourly_timeseries_entirely():
    """PT60M points must never be duplicated onto four QH slots — a chart built
    from that would fake a granularity the market did not trade."""
    from backend.power.entsoe_prices import parse_day_ahead_quarter_hourly

    xml = _a44(
        _ts("2026-05-01T00:00Z", "2026-05-02T00:00Z", [50.0] * 24, res="PT60M")
        + _ts("2026-05-02T00:00Z", "2026-05-03T00:00Z", [60.0] * 96, res="PT15M")
    )
    pts = parse_day_ahead_quarter_hourly(xml)

    assert len(pts) == 96
    assert all(_epoch("2026-05-02T00:00") <= ts for ts, _ in pts)


def test_parse_qh_hourly_only_document_yields_nothing():
    """Before 2025-10-01 the auction was hourly — the QH series simply starts
    at the switch, no synthetic backfill."""
    from backend.power.entsoe_prices import parse_day_ahead_quarter_hourly

    xml = _a44(_ts("2025-05-01T00:00Z", "2025-05-02T00:00Z", [50.0] * 24, res="PT60M"))
    assert parse_day_ahead_quarter_hourly(xml) == []


def test_parse_qh_overlapping_series_are_averaged_per_timestamp():
    from backend.power.entsoe_prices import parse_day_ahead_quarter_hourly

    xml = _a44(
        _ts("2026-05-01T00:00Z", "2026-05-01T01:00Z", [40.0] * 4, res="PT15M")
        + _ts("2026-05-01T00:00Z", "2026-05-01T01:00Z", [60.0] * 4, res="PT15M")
    )
    pts = parse_day_ahead_quarter_hourly(xml)

    assert len(pts) == 4
    assert all(p == 50.0 for _, p in pts)


async def test_ingest_writes_qh_series_alongside_legacy(db_session, monkeypatch):
    from pydantic import SecretStr

    from backend.power import entsoe_prices
    from backend.power.hourly_store import read_hourly

    xml = _a44(_ts("2026-05-01T00:00Z", "2026-05-02T00:00Z",
                   [-5.0 if i < 4 else 80.0 for i in range(96)], res="PT15M"))

    async def fake_fetch(eic, month_start, *, overwrite=False):
        return xml

    monkeypatch.setattr(entsoe_prices, "_fetch_zone_month", fake_fetch)
    monkeypatch.setattr(entsoe_prices.settings, "entsoe_api_token", SecretStr("tok"))

    await entsoe_prices.ingest_day_ahead(db_session, ["2026-05-01"])

    qh = read_hourly(db_session, "price.dayahead.qh", "DE_LU")
    assert len(qh) == 96
    assert qh[0] == (_epoch("2026-05-01T00:00"), -5.0)
    assert qh[4][1] == 80.0
    # legacy hourly series keeps its averaged shape, untouched
    hourly = read_hourly(db_session, "price.dayahead", "DE_LU")
    assert len(hourly) == 24
    assert hourly[0][1] == pytest.approx(-5.0)  # 4 × −5.0 averaged


async def test_ingest_qh_respects_the_requested_days(db_session, monkeypatch):
    from pydantic import SecretStr

    from backend.power import entsoe_prices
    from backend.power.hourly_store import read_hourly

    xml = _a44(
        _ts("2026-05-01T00:00Z", "2026-05-02T00:00Z", [10.0] * 96, res="PT15M")
        + _ts("2026-05-02T00:00Z", "2026-05-03T00:00Z", [20.0] * 96, res="PT15M")
    )

    async def fake_fetch(eic, month_start, *, overwrite=False):
        return xml

    monkeypatch.setattr(entsoe_prices, "_fetch_zone_month", fake_fetch)
    monkeypatch.setattr(entsoe_prices.settings, "entsoe_api_token", SecretStr("tok"))

    await entsoe_prices.ingest_day_ahead(db_session, ["2026-05-01"])

    qh = read_hourly(db_session, "price.dayahead.qh", "DE_LU")
    assert len(qh) == 96
    assert all(v == 10.0 for _, v in qh)


# ─── coherence regression: table mean must equal the canonical hourly store ────


async def test_mean_price_matches_hourly_store_after_resolution_change(db_session, monkeypatch):
    """The bug FR exposed 2026-07-19: when ENTSO-E republishes a day as 15-min but
    the QH series covers fewer hours than the accumulated hourly store, the daily
    mean (table/situation) must still equal the mean of the canonical price.dayahead
    series — the exact number the chart, the v1 API and the client average — not the
    QH-slot snapshot. Reproduced via two ingests (hourly, then a QH subset)."""
    from pydantic import SecretStr

    from backend.models.energy import PowerPriceDaily
    from backend.power import entsoe_prices
    from backend.power.hourly_store import day_hour_ts, read_hourly

    day = "2026-05-01"
    # Ingest 1 — full hourly day: hours 0-19 @ 100, hours 20-23 @ 0 (cheap evening).
    xml1 = _a44(_ts(f"{day}T00:00Z", "2026-05-02T00:00Z", [100.0] * 20 + [0.0] * 4, res="PT60M"))
    # Ingest 2 — same day now 15-min, but only the first 20 hours (80 QH) @ 100.
    xml2 = _a44(_ts(f"{day}T00:00Z", f"{day}T20:00Z", [100.0] * 80, res="PT15M"))

    monkeypatch.setattr(entsoe_prices.settings, "entsoe_api_token", SecretStr("tok"))

    async def fetch1(eic, m, *, overwrite=False):
        return xml1

    monkeypatch.setattr(entsoe_prices, "_fetch_zone_month", fetch1)
    await entsoe_prices.ingest_day_ahead(db_session, [day])

    async def fetch2(eic, m, *, overwrite=False):
        return xml2

    monkeypatch.setattr(entsoe_prices, "_fetch_zone_month", fetch2)
    await entsoe_prices.ingest_day_ahead(db_session, [day])

    # Canonical store now holds 24h: 0-19 @ 100 (overwritten by QH), 20-23 @ 0 (kept).
    start = day_hour_ts(day, 0)
    rows = read_hourly(db_session, "price.dayahead", "DE_LU", start, start + 86400)
    assert len(rows) == 24
    hourly_mean = sum(p for _, p in rows) / len(rows)
    assert hourly_mean == pytest.approx((20 * 100 + 4 * 0) / 24)  # 83.33, NOT the 100 the QH snapshot gave

    daily = db_session.query(PowerPriceDaily).filter_by(date=day, zone="DE_LU").first()
    close = db_session.query(EnergyPrice).filter_by(date=day, symbol="POWER_DE").first().close
    assert daily.mean_price == pytest.approx(hourly_mean)
    assert close == pytest.approx(hourly_mean)
