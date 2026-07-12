"""power_backfill CLI: window/daterange/zone helpers + dry-run + per-zone-month dispatch."""
from __future__ import annotations

from datetime import date

from backend.scripts import power_backfill as pb


def test_month_windows_span_partial_first_and_last():
    w = pb._month_windows(date(2026, 1, 15), date(2026, 3, 10))
    assert w == [
        (date(2026, 1, 15), date(2026, 1, 31)),
        (date(2026, 2, 1), date(2026, 2, 28)),
        (date(2026, 3, 1), date(2026, 3, 10)),
    ]


def test_month_windows_single_month():
    assert pb._month_windows(date(2026, 6, 1), date(2026, 6, 30)) == [
        (date(2026, 6, 1), date(2026, 6, 30))
    ]


def test_daterange_inclusive():
    assert pb._daterange(date(2026, 1, 1), date(2026, 1, 3)) == [
        "2026-01-01", "2026-01-02", "2026-01-03"
    ]


def test_resolve_zones_default_and_filter():
    assert set(pb._resolve_zones(None)) == {"DE_LU", "FR", "NL"}
    assert pb._resolve_zones("DE_LU,FR") == ["DE_LU", "FR"]
    assert pb._resolve_zones("DE_LU,BOGUS") == ["DE_LU"]  # drops unknown


async def test_dry_run_counts_plan_without_fetching(monkeypatch):
    async def _boom(*a, **k):  # any ingest call in dry-run is a bug
        raise AssertionError("ingest called during dry run")

    monkeypatch.setattr(pb, "ingest_day_ahead", _boom)
    monkeypatch.setattr(pb, "ingest_grid", _boom)
    monkeypatch.setattr(pb, "ingest_load_forecast", _boom)

    res = await pb.run_backfill(
        db=None, start=date(2026, 1, 1), end=date(2026, 3, 31),
        zones=["DE_LU", "FR"], sources={"price", "grid", "forecast"},
        overwrite=False, dry_run=True, throttle=0,
    )
    assert res["zone_months"] == 6  # 2 zones × 3 months
    assert res["months"] == 3


async def test_run_dispatches_each_source_per_zone_month(monkeypatch):
    calls = {"price": 0, "grid": 0, "forecast": 0}

    async def _price(*a, **k):
        calls["price"] += 1

    async def _grid(*a, **k):
        calls["grid"] += 1

    async def _forecast(*a, **k):
        calls["forecast"] += 1

    monkeypatch.setattr(pb, "ingest_day_ahead", _price)
    monkeypatch.setattr(pb, "ingest_grid", _grid)
    monkeypatch.setattr(pb, "ingest_load_forecast", _forecast)

    await pb.run_backfill(
        db=None, start=date(2026, 1, 1), end=date(2026, 2, 28),
        zones=["DE_LU"], sources={"price", "grid"},  # forecast excluded
        overwrite=True, dry_run=False, throttle=0,
    )
    assert calls["price"] == 2   # 1 zone × 2 months
    assert calls["grid"] == 2
    assert calls["forecast"] == 0  # not requested


async def test_flows_source_runs_once_per_month_not_per_zone(monkeypatch):
    """Flows are zone-independent: one cached /cbpf sweep per month, however
    many zones the backfill targets."""
    flow_calls = []

    async def _flows(db, days, **kwargs):
        flow_calls.append((days[0], days[-1], kwargs))

    async def _noop(*a, **k):
        pass

    monkeypatch.setattr(pb, "ingest_cbpf", _flows)
    monkeypatch.setattr(pb, "ingest_day_ahead", _noop)

    res = await pb.run_backfill(
        db=None, start=date(2026, 1, 1), end=date(2026, 2, 28),
        zones=["DE_LU", "FR"], sources={"price", "flows"},
        overwrite=False, dry_run=False, throttle=0,
    )
    assert res["flow_months"] == 2
    assert [(c[0], c[1]) for c in flow_calls] == [
        ("2026-01-01", "2026-01-31"), ("2026-02-01", "2026-02-28"),
    ]
    assert all(c[2].get("use_cache") is True for c in flow_calls)


async def test_flows_dry_run_counts_without_fetching(monkeypatch):
    async def _boom(*a, **k):
        raise AssertionError("ingest called during dry run")

    monkeypatch.setattr(pb, "ingest_cbpf", _boom)
    res = await pb.run_backfill(
        db=None, start=date(2026, 1, 1), end=date(2026, 3, 31),
        zones=["DE_LU"], sources={"flows"},
        overwrite=False, dry_run=True, throttle=0,
    )
    assert res["flow_months"] == 3
