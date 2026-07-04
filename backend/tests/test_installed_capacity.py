"""Installed generation capacity (ENTSO-E A68): parse → ingest per zone-year."""
from __future__ import annotations

from backend.models.energy import InstalledCapacity  # noqa: F401 — register table
from backend.power import entsoe_capacity as cap
from backend.power.entsoe_capacity import ingest_installed_capacity, parse_installed_capacity
from backend.tests.test_power_grid import _a75_gen, _gen_ts


def test_parse_installed_capacity():
    xml = _a75_gen(
        _gen_ts("B16", "2025-01-01T00:00Z", 50_000.0, n=1)
        + _gen_ts("B19", "2025-01-01T00:00Z", 60_000.0, n=1)
    )
    caps = parse_installed_capacity(xml)
    assert caps["B16"] == 50_000.0
    assert caps["B19"] == 60_000.0


def test_parse_installed_capacity_malformed():
    import pytest
    with pytest.raises(ValueError):
        parse_installed_capacity("<not-xml")


async def test_ingest_installed_capacity(db_session, monkeypatch):
    monkeypatch.setattr(cap.settings, "entsoe_api_token", "x")

    async def fake_fetch(eic, year, **kw):
        return _a75_gen(_gen_ts("B16", "2025-01-01T00:00Z", 50_000.0, n=1))

    monkeypatch.setattr(cap, "_fetch_capacity_year", fake_fetch)
    r = await ingest_installed_capacity(db_session, 2025, eic="X", zone="DE_LU", overwrite=True)
    assert r["written"] == 1
    row = db_session.query(InstalledCapacity).filter_by(zone="DE_LU", year=2025).first()
    assert row.psr_type == "Solar"  # PSR_LABELS[B16]
    assert row.capacity_mw == 50_000.0


async def test_ingest_skips_without_token(db_session, monkeypatch):
    monkeypatch.setattr(cap.settings, "entsoe_api_token", None)
    r = await ingest_installed_capacity(db_session, 2025, eic="X", zone="FR")
    assert r == {"skipped": "no token"}
