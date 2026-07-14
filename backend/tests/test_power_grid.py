"""ENTSO-E grid load + generation mix: parse + ingest tests."""
from __future__ import annotations

import backend.power.entsoe_grid as grid_mod
from backend.models.energy import PowerGrid


def test_power_grid_model_importable():
    """Smoke-test: model class exists with the expected columns."""
    cols = {c.name for c in PowerGrid.__table__.columns}
    assert {"date", "zone", "load_mw", "wind_mw", "solar_mw", "created_at"} <= cols


# ── A65 XML helpers ───────────────────────────────────────────────────────────


def _a65(
    ts_blocks: str,
    ns: str = "urn:iec62325.351:tc57wg16:451-6:generationloaddocument:3:0",
) -> str:
    return (
        f'<?xml version="1.0"?>'
        f'<GL_MarketDocument xmlns="{ns}"><type>A65</type>'
        f"{ts_blocks}"
        f"</GL_MarketDocument>"
    )


def _load_ts(start: str, mw_per_hour: float, n: int = 24, res: str = "PT60M") -> str:
    pts = "".join(
        f"<Point><position>{i + 1}</position><quantity>{mw_per_hour}</quantity></Point>"
        for i in range(n)
    )
    return (
        f"<TimeSeries>"
        f"<Period>"
        f"<timeInterval><start>{start}</start></timeInterval>"
        f"<resolution>{res}</resolution>"
        f"{pts}"
        f"</Period>"
        f"</TimeSeries>"
    )


# ── parse_load tests ──────────────────────────────────────────────────────────


def test_parse_load_daily_mean():
    """24 hourly points of 50_000 MW → mean = 50_000 MW for that UTC day."""
    xml = _a65(_load_ts("2026-04-01T00:00Z", 50_000.0, n=24))
    result = grid_mod.parse_load(xml)
    assert result == {"2026-04-01": 50_000.0}


def test_parse_load_two_days():
    """Two separate 24-point periods → two daily means."""
    xml = _a65(
        _load_ts("2026-04-01T00:00Z", 60_000.0)
        + _load_ts("2026-04-02T00:00Z", 40_000.0)
    )
    result = grid_mod.parse_load(xml)
    assert result == {"2026-04-01": 60_000.0, "2026-04-02": 40_000.0}


def test_parse_load_mixed_values():
    """Mean of 23 hours at 50_000 and 1 hour at 70_000 → correct mean."""
    pts = "".join(
        f"<Point><position>{i + 1}</position><quantity>50000.0</quantity></Point>"
        for i in range(23)
    ) + "<Point><position>24</position><quantity>70000.0</quantity></Point>"
    ts = (
        f"<TimeSeries>"
        f"<Period>"
        f"<timeInterval><start>2026-04-01T00:00Z</start></timeInterval>"
        f"<resolution>PT60M</resolution>"
        f"{pts}"
        f"</Period>"
        f"</TimeSeries>"
    )
    xml = _a65(ts)
    result = grid_mod.parse_load(xml)
    expected = (23 * 50_000 + 70_000) / 24
    assert abs(result["2026-04-01"] - expected) < 0.01


def test_parse_load_malformed_raises():
    import pytest

    with pytest.raises(ValueError):
        grid_mod.parse_load("<not-xml")


# ── A75 XML helpers ───────────────────────────────────────────────────────────


def _a75_gen(
    ts_blocks: str,
    ns: str = "urn:iec62325.351:tc57wg16:451-6:generationloaddocument:3:0",
) -> str:
    return (
        f'<?xml version="1.0"?>'
        f'<GL_MarketDocument xmlns="{ns}"><type>A75</type>'
        f"{ts_blocks}"
        f"</GL_MarketDocument>"
    )


def _gen_ts(psr: str, start: str, mw: float, n: int = 24, res: str = "PT60M",
            direction: str = "in") -> str:
    """One A75 TimeSeries. `direction="out"` marks it as CONSUMPTION
    (outBiddingZone_Domain) — how ENTSO-E publishes pumped-storage pumping."""
    pts = "".join(
        f"<Point><position>{i + 1}</position><quantity>{mw}</quantity></Point>"
        for i in range(n)
    )
    domain = (
        "<outBiddingZone_Domain.mRID>10Y1001A1001A82H</outBiddingZone_Domain.mRID>"
        if direction == "out"
        else "<inBiddingZone_Domain.mRID>10Y1001A1001A82H</inBiddingZone_Domain.mRID>"
    )
    return (
        f"<TimeSeries>"
        f"<MktPSRType><psrType>{psr}</psrType></MktPSRType>"
        f"{domain}"
        f"<Period>"
        f"<timeInterval><start>{start}</start></timeInterval>"
        f"<resolution>{res}</resolution>"
        f"{pts}"
        f"</Period>"
        f"</TimeSeries>"
    )


# ── parse_generation_by_type tests ───────────────────────────────────────────


def test_parse_genmix_single_type():
    """24 points of 8000 MW for B16 (solar) → mean = 8000.0 MW."""
    xml = _a75_gen(_gen_ts("B16", "2026-04-01T00:00Z", 8_000.0))
    result = grid_mod.parse_generation_by_type(xml)
    assert result == {"2026-04-01": {"B16": 8_000.0}}


def test_parse_genmix_multiple_types_same_day():
    """B16 + B18 + B19 on same day all parsed independently."""
    xml = _a75_gen(
        _gen_ts("B16", "2026-04-01T00:00Z", 5_000.0)   # solar
        + _gen_ts("B18", "2026-04-01T00:00Z", 2_000.0)  # wind offshore
        + _gen_ts("B19", "2026-04-01T00:00Z", 15_000.0)  # wind onshore
    )
    result = grid_mod.parse_generation_by_type(xml)
    day = result["2026-04-01"]
    assert day["B16"] == 5_000.0
    assert day["B18"] == 2_000.0
    assert day["B19"] == 15_000.0


def test_parse_genmix_two_days():
    """B19 across two separate 24-point periods → two day entries."""
    xml = _a75_gen(
        _gen_ts("B19", "2026-04-01T00:00Z", 12_000.0)
        + _gen_ts("B19", "2026-04-02T00:00Z", 9_000.0)
    )
    result = grid_mod.parse_generation_by_type(xml)
    assert result["2026-04-01"]["B19"] == 12_000.0
    assert result["2026-04-02"]["B19"] == 9_000.0


def test_parse_genmix_ignores_unknown_types():
    """Unknown psrTypes (e.g. B04 gas, B14 nuclear) are included as-is — no filter."""
    xml = _a75_gen(
        _gen_ts("B04", "2026-04-01T00:00Z", 3_000.0)
        + _gen_ts("B16", "2026-04-01T00:00Z", 7_000.0)
    )
    result = grid_mod.parse_generation_by_type(xml)
    # B04 is collected too (caller decides which types to use)
    assert "B04" in result["2026-04-01"]
    assert result["2026-04-01"]["B16"] == 7_000.0


def test_parse_genmix_malformed_raises():
    import pytest

    with pytest.raises(ValueError):
        grid_mod.parse_generation_by_type("<not-xml")


# ── ingest_grid tests ─────────────────────────────────────────────────────────


async def test_ingest_grid_upserts_load_wind_solar(db_session, monkeypatch):
    """ingest_grid fetches A65 + A75 and upserts PowerGrid rows correctly."""
    from pydantic import SecretStr

    load_xml = _a65(_load_ts("2026-04-01T00:00Z", 55_000.0))  # 55 GW load
    gen_xml = _a75_gen(
        _gen_ts("B16", "2026-04-01T00:00Z", 8_000.0)   # solar
        + _gen_ts("B18", "2026-04-01T00:00Z", 3_000.0)  # wind offshore
        + _gen_ts("B19", "2026-04-01T00:00Z", 18_000.0)  # wind onshore
    )

    async def fake_fetch_zone_month(eic, month_start, doctype, extra_params, *, overwrite=False):
        if doctype == "A65":
            return load_xml
        if doctype == "A75":
            return gen_xml
        return ""

    monkeypatch.setattr(grid_mod, "_fetch_zone_month", fake_fetch_zone_month)
    monkeypatch.setattr(grid_mod.settings, "entsoe_api_token", SecretStr("test-token"))

    result = await grid_mod.ingest_grid(db_session, ["2026-04-01"])
    assert result["written"] == 1
    assert result["days"] == 1

    row = (
        db_session.query(PowerGrid)
        .filter_by(date="2026-04-01", zone="DE_LU")
        .first()
    )
    assert row is not None
    assert row.load_mw == 55_000.0
    assert row.solar_mw == 8_000.0
    assert row.wind_mw == 21_000.0  # B18(3000) + B19(18000)


async def test_ingest_grid_upserts_existing_row(db_session, monkeypatch):
    """Second ingest of same date updates values in place (no duplicate row)."""
    from pydantic import SecretStr

    load_xml_v1 = _a65(_load_ts("2026-04-01T00:00Z", 50_000.0))
    load_xml_v2 = _a65(_load_ts("2026-04-01T00:00Z", 52_000.0))
    gen_xml = _a75_gen(_gen_ts("B16", "2026-04-01T00:00Z", 5_000.0))

    call_count = {"n": 0}

    async def fake_fetch(eic, month_start, doctype, extra_params, *, overwrite=False):
        if doctype == "A65":
            call_count["n"] += 1
            return load_xml_v1 if call_count["n"] == 1 else load_xml_v2
        return gen_xml

    monkeypatch.setattr(grid_mod, "_fetch_zone_month", fake_fetch)
    monkeypatch.setattr(grid_mod.settings, "entsoe_api_token", SecretStr("test-token"))

    await grid_mod.ingest_grid(db_session, ["2026-04-01"])
    await grid_mod.ingest_grid(db_session, ["2026-04-01"])

    rows = db_session.query(PowerGrid).filter_by(date="2026-04-01", zone="DE_LU").all()
    assert len(rows) == 1  # no duplicate
    assert rows[0].load_mw == 52_000.0  # updated value


async def test_ingest_grid_skips_without_token(db_session, monkeypatch):
    """Returns {"skipped": "no token"} when ENTSOE_API_TOKEN is not configured."""
    monkeypatch.setattr(grid_mod.settings, "entsoe_api_token", None)
    result = await grid_mod.ingest_grid(db_session, ["2026-04-01"])
    assert result == {"skipped": "no token"}
    assert db_session.query(PowerGrid).count() == 0


async def test_ingest_grid_empty_days(db_session, monkeypatch):
    """Empty days list returns immediately with zero counts."""
    from pydantic import SecretStr

    monkeypatch.setattr(grid_mod.settings, "entsoe_api_token", SecretStr("test-token"))
    result = await grid_mod.ingest_grid(db_session, [])
    assert result == {"days": 0, "written": 0}


# ── pumped storage: generation vs consumption must never be merged ────────────
# ENTSO-E publishes B10 TWICE in one A75 document — inBiddingZone (generating)
# and outBiddingZone (pumping). Keying only on psrType averaged them into one
# meaningless number. Measured on the real cached DE-LU document (2026-06):
# generation 1253 MW, pumping 1579 MW → the store held 1416 MW.


def test_parse_genmix_splits_pumped_storage_generation_from_pumping():
    xml = _a75_gen(
        _gen_ts("B10", "2026-04-01T00:00Z", 1_253.0, direction="in")
        + _gen_ts("B10", "2026-04-01T00:00Z", 1_579.0, direction="out")
        + _gen_ts("B16", "2026-04-01T00:00Z", 8_000.0)
    )
    result = grid_mod.parse_generation_by_type(xml)["2026-04-01"]
    assert result["B10"] == 1_253.0, "generation leg"
    assert result["B10_CONS"] == 1_579.0, "pumping leg, kept separate"
    assert result["B16"] == 8_000.0
    assert 1_416.0 not in result.values(), "the old averaged-together value must be gone"


def test_parse_generation_hourly_splits_pumped_storage():
    xml = _a75_gen(
        _gen_ts("B10", "2026-04-01T00:00Z", 1_253.0, direction="in")
        + _gen_ts("B10", "2026-04-01T00:00Z", 1_579.0, direction="out")
    )
    day = grid_mod.parse_generation_hourly(xml)["2026-04-01"]
    assert day["B10"][0] == 1_253.0
    assert day["B10_CONS"][0] == 1_579.0


def test_consumption_key_helpers():
    assert grid_mod.is_consumption_key("B10_CONS") is True
    assert grid_mod.is_consumption_key("B10") is False
    assert grid_mod.base_psr("B10_CONS") == "B10"
    assert grid_mod.base_psr("B16") == "B16"


async def test_ingest_keeps_pumping_out_of_the_generation_mix(db_session, monkeypatch):
    """Pumping is load on the grid, not generation. It must land in its own
    series and must NOT enter PowerGenMix — counting it as generation inflated
    the mix AND the generation total that coverage.py divides by load, making
    the A75 coverage guard too generous."""
    from pydantic import SecretStr

    from backend.models.energy import PowerGenMix
    from backend.power.coverage import generation_total_mw
    from backend.power.hourly_store import read_hourly

    load_xml = _a65(_load_ts("2026-04-01T00:00Z", 55_000.0))
    gen_xml = _a75_gen(
        _gen_ts("B16", "2026-04-01T00:00Z", 8_000.0)
        + _gen_ts("B10", "2026-04-01T00:00Z", 1_253.0, direction="in")    # generating
        + _gen_ts("B10", "2026-04-01T00:00Z", 1_579.0, direction="out")   # pumping
    )

    async def fake_fetch(eic, month_start, doctype, extra_params, *, overwrite=False):
        return {"A65": load_xml, "A75": gen_xml}.get(doctype, "")

    monkeypatch.setattr(grid_mod, "_fetch_zone_month", fake_fetch)
    monkeypatch.setattr(grid_mod.settings, "entsoe_api_token", SecretStr("test-token"))
    await grid_mod.ingest_grid(db_session, ["2026-04-01"])

    mix = {
        r.psr_type: r.gen_mw
        for r in db_session.query(PowerGenMix).filter_by(date="2026-04-01", zone="DE_LU")
    }
    assert mix["Hydro Pumped Storage"] == 1_253.0, "generation leg only"
    assert not any("_CONS" in k or "consumption" in k.lower() for k in mix), \
        "pumping must not appear in the mix"
    # Generation total (the coverage-guard numerator) counts 8000 + 1253, not the pumping.
    assert generation_total_mw(db_session, "2026-04-01", "DE_LU") == 9_253.0

    assert read_hourly(db_session, "gen.B10", "DE_LU")[0][1] == 1_253.0
    assert read_hourly(db_session, "consumption.B10", "DE_LU")[0][1] == 1_579.0


# ── the daily tables are derived from the hourly shape (power/daily.py) ───────


async def test_a_day_that_is_not_over_is_not_a_day(db_session, monkeypatch):
    """The radar read the newest PowerGrid row and announced "PT: Dunkelflaute — renewables 11%
    of load" at breakfast; by the afternoon PT was at 22%, because the sun had come up. The row
    was the mean of the nine hours that had been published. A day that is not finished is not a
    day, and the ingest no longer writes one — which fixes every consumer of "the latest row" at
    once, without any of them having to know."""
    from datetime import UTC, datetime

    from pydantic import SecretStr

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    yesterday_dt = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday = (yesterday_dt.toordinal() - 1)
    yesterday = datetime.fromordinal(yesterday).strftime("%Y-%m-%d")

    load_xml = _a65(_load_ts(f"{yesterday}T00:00Z", 50_000.0) + _load_ts(f"{today}T00:00Z", 51_000.0, n=9))
    gen_xml = _a75_gen(_gen_ts("B16", f"{yesterday}T00:00Z", 5_000.0) + _gen_ts("B16", f"{today}T00:00Z", 1_000.0, n=9))

    async def fake_fetch(eic, month_start, doctype, extra_params, *, overwrite=False):
        return load_xml if doctype == "A65" else gen_xml if doctype == "A75" else ""

    monkeypatch.setattr(grid_mod, "_fetch_zone_month", fake_fetch)
    monkeypatch.setattr(grid_mod.settings, "entsoe_api_token", SecretStr("test-token"))

    await grid_mod.ingest_grid(db_session, [yesterday, today])

    days = {r.date for r in db_session.query(PowerGrid).filter_by(zone="DE_LU").all()}
    assert yesterday in days
    assert today not in days, "the current day is a running total, not a daily mean"


async def test_a_fuel_that_is_only_published_by_day_is_averaged_over_the_whole_day(db_session, monkeypatch):
    """PT publishes solar for 18 hours and nothing at night. The old daily parse divided by 18 and
    stored the mean of the DAYLIGHT hours as the day's — a third too high, on a settled day, right
    through the history. The hours it does not send are zeros: the sun is down."""
    from pydantic import SecretStr

    day = "2026-04-01"
    # Solar: 3000 MW for 18 hours, nothing published for the other six.
    gen_xml = _a75_gen(
        _gen_ts("B16", f"{day}T05:00Z", 3_000.0, n=18)
        + _gen_ts("B19", f"{day}T00:00Z", 1_000.0)     # wind reports all night → the day IS covered
    )
    load_xml = _a65(_load_ts(f"{day}T00:00Z", 6_000.0))

    async def fake_fetch(eic, month_start, doctype, extra_params, *, overwrite=False):
        return load_xml if doctype == "A65" else gen_xml if doctype == "A75" else ""

    monkeypatch.setattr(grid_mod, "_fetch_zone_month", fake_fetch)
    monkeypatch.setattr(grid_mod.settings, "entsoe_api_token", SecretStr("test-token"))

    await grid_mod.ingest_grid(db_session, [day])

    row = db_session.query(PowerGrid).filter_by(date=day, zone="DE_LU").first()
    assert row.solar_mw == 3_000.0 * 18 / 24 == 2_250.0, "divided by the day, not by the points"
    assert row.load_hours == 24
    assert row.gen_hours == 24, "wind reports through the night — the day has no hole in it"
