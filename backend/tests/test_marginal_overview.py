"""Bulk marginal overview: one read colours the whole map, honestly.

/api/power/marginal/overview returns the LATEST price-setting attribution per
enabled zone for a map fill. These tests pin the honesty rules the map
inherits from the single-zone endpoint: a zone with no attributable hour is
`missing` (no-data), never painted with an invented value; a "tension" hour
crosses into the bulk payload unreclassified; the method/note strings are the
SAME objects both endpoints serve (no drift); and the 30-minute payload cache
never freezes freshness (derived per request) and is never mutated by the
per-request stale stamping.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import backend.power.marginal as marginal_mod
from backend.api_guard import _reset_coverage_cache, cached_value
from backend.power.hourly_store import upsert_hourly
from backend.power.marginal import (
    METHOD,
    NOTE,
    compute_marginal,
    compute_marginal_overview,
)
from backend.power.zones import POWER_ZONES, ZONE_REGISTRY


@pytest.fixture(autouse=True)
def _isolate_cache():
    _reset_coverage_cache()  # process-global keyed cache; would leak the overview between tests
    yield
    _reset_coverage_cache()


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    from backend.main import app

    app.dependency_overrides.clear()


def _client(db):
    from backend.database import get_db
    from backend.main import app

    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


#: Fixed anchor at top of hour — the window math must never depend on the wall clock.
_NOW = datetime(2026, 3, 10, 12, tzinfo=timezone.utc)


def _ts(hours_ago: int) -> int:
    return int((_NOW - timedelta(hours=hours_ago)).timestamp())


def _seed_hour(db, hours_ago: int, price: float | None, gens: dict[str, float],
               zone: str = "DE_LU") -> None:
    """One zone-hour: an optional day-ahead price plus MW per gen.<Bxx> series."""
    t = _ts(hours_ago)
    if price is not None:
        upsert_hourly(db, "price.dayahead", zone, [(t, price)], unit="EUR/MWh")
    for code, mw in gens.items():
        upsert_hourly(db, f"gen.{code}", zone, [(t, mw)], unit="MW")


def _seed_days_ago(db, days: int, zone: str = "DE_LU") -> None:
    """A gas-marginal hour exactly `days` whole days before the CURRENT
    top-of-hour: the route reads the real clock, and a whole-day offset keeps
    the seeded DATE exactly `days` behind today at any wall-clock time (the
    UTC-midnight flake fixed repo-wide in #111)."""
    t = int(datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
            .timestamp()) - days * 86400
    upsert_hourly(db, "price.dayahead", zone, [(t, 80.0)], unit="EUR/MWh")
    upsert_hourly(db, "gen.B19", zone, [(t, 10_000.0)], unit="MW")
    upsert_hourly(db, "gen.B04", zone, [(t, 5_000.0)], unit="MW")


# ─── the bulk compute ─────────────────────────────────────────────────────────


def test_empty_db_route_answers_available_false_with_all_zones_missing(db_session):
    r = _client(db_session).get("/api/power/marginal/overview")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert body["zones"] == []
    assert body["missing"] == list(POWER_ZONES)
    # No freshness triple on an unavailable body — matching /marginal's shape.
    assert "as_of" not in body and "age_days" not in body and "stale" not in body


def test_one_entry_per_seeded_zone_and_the_latest_hour_wins(db_session, monkeypatch):
    # NO2 is not in the test env's ENABLED_ZONES (DE_LU/FR/NL) — widen the
    # module's zone view so the Nordic hydro_flex case runs under its real key.
    monkeypatch.setattr(marginal_mod, "POWER_ZONES",
                        {**POWER_ZONES, "NO2": ZONE_REGISTRY["NO2"]})
    # DE_LU: an older lignite hour then a newer gas hour — the map shows hourly[-1].
    _seed_hour(db_session, 5, 60.0, {"B19": 4_000, "B02": 4_000})
    _seed_hour(db_session, 2, 80.0, {"B19": 10_000, "B04": 5_000})
    # NO2: the Nordic reservoir hour — hydro_flex, never "must-run".
    _seed_hour(db_session, 2, 45.0, {"B12": 9_500, "B11": 300, "B19": 200},
               zone="NO2")

    out = compute_marginal_overview(db_session, now=_NOW)
    assert out["available"] is True
    assert out["hours"] == 72
    by_zone = {z["zone"]: z for z in out["zones"]}
    assert set(by_zone) == {"DE_LU", "NO2"}
    de = by_zone["DE_LU"]
    assert de["tech"] == "gas" and de["tech_label"] == "Gas"
    assert de["price"] == 80.0
    assert de["mw"] == 5_000.0
    assert de["share_pct"] == pytest.approx(33.3)
    assert de["consistency"] == "ok"
    assert de["zone_label"] == "DE-LU"
    assert de["ts_utc"] == datetime.fromtimestamp(_ts(2), tz=timezone.utc).isoformat()
    no2 = by_zone["NO2"]
    assert no2["tech"] == "hydro_flex"
    assert no2["zone_label"] == "NO2"
    assert out["missing"] == ["FR", "NL"]


def test_tension_crosses_into_the_bulk_payload_unreclassified(db_session):
    """Lignite at €300 contradicts the assumed order; the map payload SAYS so —
    reclassifying in the bulk view would hide exactly what the canary shows."""
    _seed_hour(db_session, 2, 300.0, {"B19": 4_000, "B02": 4_000})
    out = compute_marginal_overview(db_session, now=_NOW)
    (entry,) = out["zones"]
    assert entry["tech"] == "lignite"
    assert entry["consistency"] == "tension"


def test_method_and_note_are_shared_with_the_single_zone_compute(db_session):
    """One pair of strings, two endpoints: the module constants ARE the values
    both computes serve, so the honesty wording can never drift apart."""
    _seed_hour(db_session, 2, 80.0, {"B19": 10_000, "B04": 5_000})
    single = compute_marginal(db_session, "DE_LU", hours=72, now=_NOW)
    out = compute_marginal_overview(db_session, now=_NOW)
    assert out["method"] is METHOD and single["method"] is METHOD
    assert out["note"] is NOTE and single["note"] is NOTE
    assert "estimate" in METHOD and "not a forecast" in NOTE


# ─── route: freshness outside the cache, cache never mutated ──────────────────


def test_stale_seed_flags_zone_and_top_level(db_session, monkeypatch):
    # A 5-day-old hour sits OUTSIDE the default 72 h compute window, so widen
    # the window the route computes with: present-but-old (`stale`), not
    # absent (`missing`), is what this test pins. Threshold is
    # PANEL_MAX_AGE_DAYS["marginal"] = 3.
    real = compute_marginal_overview
    monkeypatch.setattr(marginal_mod, "compute_marginal_overview",
                        lambda db, **kw: real(db, hours=240, **kw))
    _seed_days_ago(db_session, 5)
    body = _client(db_session).get("/api/power/marginal/overview").json()
    assert body["available"] is True
    (entry,) = body["zones"]
    assert entry["stale"] is True
    assert body["stale"] is True and body["age_days"] == 5
    assert body["as_of"] == entry["ts_utc"][:10]


def test_fresh_seed_is_not_stale(db_session):
    _seed_days_ago(db_session, 0)
    body = _client(db_session).get("/api/power/marginal/overview").json()
    (entry,) = body["zones"]
    assert entry["stale"] is False
    assert body["stale"] is False and body["age_days"] == 0
    assert body["as_of"] == datetime.now(timezone.utc).date().isoformat()


def test_route_computes_once_and_serves_the_warm_cache(db_session, monkeypatch):
    calls = {"n": 0}
    real = compute_marginal_overview

    def counting(db, **kw):
        calls["n"] += 1
        return real(db, **kw)

    monkeypatch.setattr(marginal_mod, "compute_marginal_overview", counting)
    _seed_days_ago(db_session, 0)
    client = _client(db_session)
    first = client.get("/api/power/marginal/overview").json()
    second = client.get("/api/power/marginal/overview").json()
    assert calls["n"] == 1, "the second request must be served from the warm cache"
    assert first == second


def test_response_is_built_around_the_cache_never_from_it(db_session):
    """The stale/freshness stamps are per-request; stamping the cached dict
    would freeze the first request's clock for every reader after it."""
    from backend.routes.power import get_marginal_overview

    _seed_days_ago(db_session, 0)
    body = get_marginal_overview(db=db_session, _guard=None)
    cached = cached_value("marginal_overview",
                          lambda: pytest.fail("cache must be warm here"))
    assert body is not cached
    assert body["zones"] is not cached["zones"]
    # The per-request stamps must never have leaked INTO the cached payload.
    assert "as_of" not in cached and "age_days" not in cached and "stale" not in cached
    assert all("stale" not in z for z in cached["zones"])
    assert all(z["stale"] is False for z in body["zones"])
