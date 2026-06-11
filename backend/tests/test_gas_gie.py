"""GIE AGSI/ALSI ingestion tests — mock the day fetch, exercise the DB path."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.gas import gie
from backend.models.gas import GasLng, GasStorage

FIXTURES = Path(__file__).parent / "fixtures" / "gas"


def _patch_fetch(monkeypatch, by_day: dict):
    async def fake(base, source, day, *, overwrite=False):
        return by_day.get((source, day), {"data": []})

    monkeypatch.setattr(gie, "_fetch_day", fake)


async def test_storage_parses_eu_row(db_session, monkeypatch):
    payload = {
        "data": [
            {"code": "ne", "gasInStorage": "1.0"},  # Non-EU — must be ignored
            {"code": "eu", "gasInStorage": "892.5677", "injection": "108.27", "withdrawal": "7935.5", "full": "78.21"},
        ]
    }
    _patch_fetch(monkeypatch, {("agsi", "2024-01-15"): payload})
    await gie.ingest_storage(db_session, ["2024-01-15"])
    row = db_session.get(GasStorage, "2024-01-15")
    assert row.stock_twh == 892.5677
    assert row.injection_gwh == 108.27
    assert row.withdrawal_gwh == 7935.5
    assert row.fill_pct == 78.21


async def test_lng_inventory_dict_to_twh(db_session, monkeypatch):
    payload = {"data": [{"code": "eu", "sendOut": "3542.2", "inventory": {"lng": "9321.97", "gwh": "62148.18"}}]}
    _patch_fetch(monkeypatch, {("alsi", "2026-06-09"): payload})
    await gie.ingest_lng(db_session, ["2026-06-09"])
    row = db_session.get(GasLng, "2026-06-09")
    assert row.send_out_gwh == 3542.2
    assert abs(row.inventory_twh - 62.14818) < 1e-9  # 62148.18 GWh → TWh


async def test_gie_null_markers_become_none(db_session, monkeypatch):
    payload = {"data": [{"code": "eu", "sendOut": "-", "inventory": {"lng": "-", "gwh": "-"}}]}
    _patch_fetch(monkeypatch, {("alsi", "2026-06-05"): payload})
    await gie.ingest_lng(db_session, ["2026-06-05"])
    row = db_session.get(GasLng, "2026-06-05")
    assert row.send_out_gwh is None
    assert row.inventory_twh is None


async def test_missing_eu_row_is_skipped(db_session, monkeypatch):
    _patch_fetch(monkeypatch, {("agsi", "2024-01-15"): {"data": [{"code": "ne"}]}})
    await gie.ingest_storage(db_session, ["2024-01-15"])
    assert db_session.query(GasStorage).count() == 0  # no EU row → no silent fill


async def test_reingest_is_idempotent(db_session, monkeypatch):
    payload = {"data": [{"code": "eu", "gasInStorage": "500.0", "injection": "10", "withdrawal": "0", "full": "44"}]}
    _patch_fetch(monkeypatch, {("agsi", "2026-06-09"): payload})
    await gie.ingest_storage(db_session, ["2026-06-09"])
    await gie.ingest_storage(db_session, ["2026-06-09"])
    assert db_session.query(GasStorage).count() == 1


async def test_real_fixtures_parse(db_session, monkeypatch):
    """Smoke-test against the captured live responses."""
    agsi = json.loads((FIXTURES / "agsi_eu.json").read_text())
    alsi = json.loads((FIXTURES / "alsi_eu.json").read_text())
    _patch_fetch(monkeypatch, {("agsi", "2026-06-09"): agsi, ("alsi", "2026-06-05"): alsi})
    await gie.ingest_storage(db_session, ["2026-06-09"])
    await gie.ingest_lng(db_session, ["2026-06-05"])
    s = db_session.get(GasStorage, "2026-06-09")
    lng = db_session.get(GasLng, "2026-06-05")
    assert s is not None and s.stock_twh and s.stock_twh > 0
    assert lng is not None and lng.send_out_gwh and lng.send_out_gwh > 0


def test_daterange_inclusive():
    from datetime import date

    assert gie.daterange(date(2026, 6, 1), date(2026, 6, 3)) == ["2026-06-01", "2026-06-02", "2026-06-03"]
    assert gie.daterange(date(2026, 6, 1), date(2026, 6, 1)) == ["2026-06-01"]


def test_gie_headers_requires_key(monkeypatch):
    monkeypatch.setattr(gie.settings, "gie_api_key", None)
    with pytest.raises(RuntimeError):
        gie._gie_headers()
