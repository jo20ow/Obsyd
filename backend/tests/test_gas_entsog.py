"""ENTSOG ingestion tests — mock the fetch functions, exercise the DB path."""

from __future__ import annotations

from datetime import date

import pytest

from backend.gas import entsog
from backend.models.gas import GasFlow, GasPoint


def _reg_row(operator, point, direction, tso, adj, label, xb="Cross-Border EU|Non-EU"):
    return {
        "operatorKey": operator, "pointKey": point, "directionKey": direction,
        "tSOCountry": tso, "adjacentCountry": adj, "pointLabel": label,
        "operatorLabel": operator, "crossBorderPointType": xb,
    }


@pytest.fixture
def seeded_points(db_session, monkeypatch):
    """Sync a tiny registry: one Norway import (EU side), its supplier-side
    duplicate (must be inactive), and an in-country transit (out of scope)."""
    registry = [
        _reg_row("DE-TSO", "EMD", "entry", "DE", "NO", "Emden (EPT1)"),       # import
        _reg_row("NO-TSO", "EMD", "entry", "NO", "DE", "Emden (EPT1)"),       # supplier side → inactive
        _reg_row("DE-TSO", "VTP", "entry", "DE", "DE", "Transit", xb="In-country EU"),  # out of scope
    ]

    async def fake_registry(*, overwrite=False):
        return registry

    monkeypatch.setattr(entsog, "fetch_point_registry", fake_registry)
    return db_session


async def test_sync_points_marks_only_eu_import_active(seeded_points):
    db = seeded_points
    res = await entsog.sync_points(db)
    assert res["by_class"] == {"import_pipeline": 1}
    active = db.query(GasPoint).filter(GasPoint.active == 1).all()
    assert len(active) == 1
    assert active[0].point_class == "import_pipeline"
    # supplier-side + transit stored but inactive
    assert db.query(GasPoint).count() == 3


async def test_ingest_converts_kwh_to_gwh_and_filters(seeded_points, monkeypatch):
    db = seeded_points
    await entsog.sync_points(db)
    imp_id = "DE-TSO|EMD|entry"

    flows = {
        "2026-06-01": [
            {"operatorKey": "DE-TSO", "pointKey": "EMD", "directionKey": "entry", "unit": "kWh/d", "value": "2500000000"},  # 2500 GWh/d
            {"operatorKey": "NO-TSO", "pointKey": "EMD", "directionKey": "entry", "unit": "kWh/d", "value": "2500000000"},  # supplier side, must be ignored
            {"operatorKey": "DE-TSO", "pointKey": "VTP", "directionKey": "entry", "unit": "kWh/d", "value": "9999"},        # out of scope
        ],
    }

    async def fake_day(day, *, overwrite=False):
        return flows.get(day, [])

    monkeypatch.setattr(entsog, "fetch_flows_day", fake_day)
    await entsog.ingest_flows(db, ["2026-06-01"], reference=date(2026, 6, 10))

    rows = db.query(GasFlow).all()
    assert len(rows) == 1  # only the EU import point
    assert rows[0].point_id == imp_id
    assert rows[0].value_gwh == 2500.0
    assert rows[0].interpolated == 0
    assert rows[0].provisional == 0  # 2026-06-01 is >2 days before reference 2026-06-10


async def test_empty_value_leaves_gap(seeded_points, monkeypatch):
    db = seeded_points
    await entsog.sync_points(db)

    async def fake_day(day, *, overwrite=False):
        return [{"operatorKey": "DE-TSO", "pointKey": "EMD", "directionKey": "entry", "unit": "kWh/d", "value": ""}]

    monkeypatch.setattr(entsog, "fetch_flows_day", fake_day)
    await entsog.ingest_flows(db, ["2026-06-01"], reference=date(2026, 6, 10))
    assert db.query(GasFlow).count() == 0  # "" → no silent zero


async def test_forward_fill_two_days_then_gap(seeded_points, monkeypatch):
    db = seeded_points
    await entsog.sync_points(db)

    # real value only on day 1; days 2-3 missing → fill, day 4 missing → also fill?
    # max_gap=2 means fill up to 2 days after last real obs; day 4 (gap 3) stays empty.
    data = {"2026-06-01": "1000000000"}  # 1000 GWh/d

    async def fake_day(day, *, overwrite=False):
        v = data.get(day)
        if v is None:
            return []
        return [{"operatorKey": "DE-TSO", "pointKey": "EMD", "directionKey": "entry", "unit": "kWh/d", "value": v}]

    monkeypatch.setattr(entsog, "fetch_flows_day", fake_day)
    days = ["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04"]
    await entsog.ingest_flows(db, days, reference=date(2026, 6, 30))

    by_day = {f.date: f for f in db.query(GasFlow).all()}
    assert by_day["2026-06-01"].interpolated == 0 and by_day["2026-06-01"].value_gwh == 1000.0
    assert by_day["2026-06-02"].interpolated == 1 and by_day["2026-06-02"].value_gwh == 1000.0
    assert by_day["2026-06-03"].interpolated == 1
    assert "2026-06-04" not in by_day  # gap of 3 days → not filled


async def test_reingest_is_idempotent(seeded_points, monkeypatch):
    db = seeded_points
    await entsog.sync_points(db)

    async def fake_day(day, *, overwrite=False):
        return [{"operatorKey": "DE-TSO", "pointKey": "EMD", "directionKey": "entry", "unit": "kWh/d", "value": "1000000000"}]

    monkeypatch.setattr(entsog, "fetch_flows_day", fake_day)
    await entsog.ingest_flows(db, ["2026-06-01"], reference=date(2026, 6, 10))
    await entsog.ingest_flows(db, ["2026-06-01"], reference=date(2026, 6, 10))
    assert db.query(GasFlow).count() == 1  # upsert, not duplicate
