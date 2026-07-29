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


# ─── source registry: what a default (unfiltered) run may and may not pull ─────


def test_all_sources_membership():
    """"balancing" IS a default source: 2 requests per zone-month (one A84 + one A83,
    with empty months cached by the collector) — the same order as the 1 request per
    zone-month of price/grid/forecast/imbalance. "capacity" (A15, ~200+ paginated
    requests per DAY) and "units_gen" (A73, per-CTA drill-down) are explicit opt-ins
    and must never ride along in an unfiltered run — see the module docstring."""
    assert "balancing" in pb.ALL_SOURCES
    assert "capacity" not in pb.ALL_SOURCES
    assert "units_gen" not in pb.ALL_SOURCES


def test_sources_cli_default_round_trips():
    """main() joins ALL_SOURCES into the --sources default and splits it back on commas —
    every token must survive that round trip (no commas/blanks inside a token)."""
    tokens = {s.strip() for s in ",".join(pb.ALL_SOURCES).split(",") if s.strip()}
    assert tokens == set(pb.ALL_SOURCES)


def test_main_rejects_unknown_source_tokens(monkeypatch):
    """A typo like "balancin" must exit 2 BEFORE touching the DB — run_backfill skips
    unrecognised sources, so without the guard a typo'd run sleeps through the plan,
    exits 0 and logs "complete" while fetching nothing."""
    def _boom():
        raise AssertionError("DB opened despite an invalid --sources token")

    monkeypatch.setattr(pb, "SessionLocal", _boom)
    assert pb.main(["power_backfill", "--sources", "balancin", "--dry-run"]) == 2
    assert pb.main(["power_backfill", "--sources", "price,balancin", "--dry-run"]) == 2


def test_main_accepts_opt_in_source_tokens(monkeypatch):
    """"capacity" and "units_gen" are valid tokens (explicit opt-ins), just not defaults —
    the unknown-token guard must not reject them."""
    class _Session:
        def close(self):
            pass

    monkeypatch.setattr(pb, "SessionLocal", lambda: _Session())
    assert pb.main(["power_backfill", "--sources", "capacity,units_gen", "--dry-run"]) == 0


# ─── balancing: per-zone, but its own post-zone-loop sweep ──────────────────────


async def test_balancing_dispatches_per_zone_month_outside_zone_loop(monkeypatch):
    """balancing runs AFTER the main zone loop with its own counter: a balancing-only
    run must not walk the price/grid/forecast/imbalance machinery, and each call gets
    the month's day list plus the zone (ingest_balancing groups the days into
    control-area month fetches itself)."""
    calls = []

    async def _balancing(db, days, **kwargs):
        calls.append((days[0], days[-1], kwargs))

    async def _boom(*a, **k):
        raise AssertionError("zone-loop source called during a balancing-only run")

    monkeypatch.setattr(pb, "ingest_balancing", _balancing)
    monkeypatch.setattr(pb, "ingest_day_ahead", _boom)
    monkeypatch.setattr(pb, "ingest_grid", _boom)

    res = await pb.run_backfill(
        db=None, start=date(2026, 1, 1), end=date(2026, 2, 28),
        zones=["DE_LU", "FR"], sources={"balancing"},
        overwrite=True, dry_run=False, throttle=0,
    )
    assert res["balancing_months"] == 4  # 2 zones × 2 months
    assert res["zone_months"] == 0       # the main zone loop must not have run
    assert [(c[0], c[1]) for c in calls] == [
        ("2026-01-01", "2026-01-31"), ("2026-02-01", "2026-02-28"),
        ("2026-01-01", "2026-01-31"), ("2026-02-01", "2026-02-28"),
    ]
    assert [c[2]["zone"] for c in calls] == ["DE_LU", "DE_LU", "FR", "FR"]
    assert all(c[2]["overwrite"] is True for c in calls)


async def test_balancing_dry_run_counts_zone_months_without_fetching(monkeypatch):
    async def _boom(*a, **k):
        raise AssertionError("ingest called during dry run")

    monkeypatch.setattr(pb, "ingest_balancing", _boom)
    res = await pb.run_backfill(
        db=None, start=date(2026, 1, 1), end=date(2026, 3, 31),
        zones=["DE_LU", "FR"], sources={"balancing"},
        overwrite=False, dry_run=True, throttle=0,
    )
    assert res["balancing_months"] == 6  # 2 zones × 3 months


# ─── capacity: opt-in only, dry-run still plans it ──────────────────────────────


async def test_capacity_dry_run_counts_months_without_fetching(monkeypatch):
    """The dedicated block still dispatches when asked for explicitly — and a dry run
    plans its months without touching the network. Patched on the SOURCE module because
    run_backfill imports ingest_capacity_prices locally (see test_capacity_prices.py's
    dispatch test for the same reason)."""
    from backend.power import entsoe_reserves as cap

    async def _boom(*a, **k):
        raise AssertionError("ingest called during dry run")

    monkeypatch.setattr(cap, "ingest_capacity_prices", _boom)
    res = await pb.run_backfill(
        db=None, start=date(2026, 1, 1), end=date(2026, 3, 31),
        zones=["DE_LU"], sources={"capacity"},
        overwrite=False, dry_run=True, throttle=0,
    )
    assert res["capacity_months"] == 3
