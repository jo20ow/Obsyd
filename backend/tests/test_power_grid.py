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


def _gen_ts(psr: str, start: str, mw: float, n: int = 24, res: str = "PT60M") -> str:
    pts = "".join(
        f"<Point><position>{i + 1}</position><quantity>{mw}</quantity></Point>"
        for i in range(n)
    )
    return (
        f"<TimeSeries>"
        f"<MktPSRType><psrType>{psr}</psrType></MktPSRType>"
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
