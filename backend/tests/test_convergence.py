"""Convergence bands (backend/power/convergence.py): the ACER band arithmetic,
the exact-counts storage doctrine (counts, not shares — DST days must aggregate
without rounding drift), the non-SDAC flagging, and the wiring."""
from __future__ import annotations

from datetime import date, datetime, timezone

from backend.power.convergence import (
    FULL_EUR,
    LOW_EUR,
    band_counts,
    compute_convergence,
    store_convergence,
)
from backend.power.hourly_store import read_hourly, upsert_hourly

_DAY = date(2026, 9, 1)
_DAY_TS = int(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp())


def _seed_prices(db, zone: str, values: list[float], base_ts: int = _DAY_TS):
    upsert_hourly(db, "price.dayahead", zone,
                  [(base_ts + i * 3600, v) for i, v in enumerate(values)],
                  unit="EUR/MWh")


def test_band_edges_match_acer_exactly():
    """Full is INCLUSIVE at 1.00 (ACER: ≤1); low EXCLUSIVE at 10.00 (ACER's
    legend leaves exactly 10 unassigned — we count it moderate, documented)."""
    full, low = band_counts([0.0, 1.0, 1.01, 10.0, 10.01])
    assert (full, low) == (2, 1)
    assert FULL_EUR == 1.0 and LOW_EUR == 10.0


def test_store_writes_counts_and_exact_mean(db_session):
    # DE_LU–FR: spreads 0, 0.5, 5, 20 → full=2, low=1, hours=4, mean=6.375
    _seed_prices(db_session, "DE_LU", [50.0, 50.5, 55.0, 70.0])
    _seed_prices(db_session, "FR", [50.0, 50.0, 50.0, 50.0])
    n = store_convergence(db_session, _DAY, _DAY)
    assert n == 4  # one point per metric series
    assert read_hourly(db_session, "conv.full.FR", "DE_LU") == [(_DAY_TS, 2.0)]
    assert read_hourly(db_session, "conv.low.FR", "DE_LU") == [(_DAY_TS, 1.0)]
    assert read_hourly(db_session, "conv.hours.FR", "DE_LU") == [(_DAY_TS, 4.0)]
    assert read_hourly(db_session, "conv.spread.FR", "DE_LU") == [(_DAY_TS, 6.375)]


def test_only_overlapping_hours_count(db_session):
    """An hour priced on one side only is ABSENT, never an invented 0-spread
    hour — the denominator is overlapping priced hours."""
    _seed_prices(db_session, "DE_LU", [50.0, 60.0, 70.0])
    _seed_prices(db_session, "FR", [50.0])  # only hour 0 overlaps
    store_convergence(db_session, _DAY, _DAY)
    assert read_hourly(db_session, "conv.hours.FR", "DE_LU") == [(_DAY_TS, 1.0)]
    assert read_hourly(db_session, "conv.full.FR", "DE_LU") == [(_DAY_TS, 1.0)]


def test_compute_aggregates_exactly_and_flags_non_sdac(db_session):
    # Two days DE_LU–FR + one day CH–FR (CH = outside SDAC).
    day2 = int(datetime(2026, 9, 2, tzinfo=timezone.utc).timestamp())
    _seed_prices(db_session, "DE_LU", [50.0, 62.0])
    _seed_prices(db_session, "DE_LU", [50.0, 50.0], base_ts=day2)
    _seed_prices(db_session, "FR", [50.0, 50.0])
    _seed_prices(db_session, "FR", [50.0, 50.5], base_ts=day2)
    _seed_prices(db_session, "CH", [90.0, 90.0])
    store_convergence(db_session, _DAY, date(2026, 9, 2))

    out = compute_convergence(db_session, days=3650)
    assert out["available"] is True
    by_pair = {(b["zone_a"], b["zone_b"]): b for b in out["borders"]}

    defr = by_pair[("DE_LU", "FR")]
    # 4 hours: spreads 0, 12, 0, 0.5 → full=3, low=1, moderate=0
    assert defr["hours"] == 4 and defr["sdac"] is True
    assert defr["full_pct"] == 75.0 and defr["low_pct"] == 25.0 and defr["moderate_pct"] == 0.0
    # Hour-weighted window mean: (mean(0,12)*2 + mean(0,0.5)*2) / 4 = 3.125,
    # rounded half-even by round() → 3.12
    assert defr["mean_abs_spread"] == 3.12

    chfr = by_pair[("CH", "FR")]
    assert chfr["sdac"] is False
    # Headline excludes the CH border: 4 SDAC border-hours, not 6.
    assert out["overall_sdac"]["hours"] == 4
    assert out["overall_sdac"]["full_pct"] == 75.0
    assert out["thresholds"] == {"full_eur": 1.0, "low_eur": 10.0}
    assert "not an objective" in out["note"]


def test_no_data_is_reported_not_invented(db_session):
    out = compute_convergence(db_session, days=365)
    assert out["available"] is False
    assert "reason" in out


def test_wiring():
    """Freshness spec, catalog label + group, revision-ledger exclusion, and
    the route's heavy-query slot — the integration checklist as assertions."""
    from backend.api_guard import heavy_query_guard
    from backend.collectors.freshness import SPECS
    from backend.main import app
    from backend.power.hourly_store import REVISION_EXCLUDED_PREFIXES
    from backend.power.series_catalog import GROUP_LABELS, series_label

    assert any(s.key == "convergence_series" for s in SPECS)
    assert "Convergence" in series_label("conv.full.FR")
    assert "conv" in GROUP_LABELS
    assert any("conv.full.FR".startswith(p) for p in REVISION_EXCLUDED_PREFIXES)
    route = next(r for r in app.routes
                 if getattr(r, "path", None) == "/api/power/convergence")
    assert heavy_query_guard in [d.call for d in route.dependant.dependencies]
