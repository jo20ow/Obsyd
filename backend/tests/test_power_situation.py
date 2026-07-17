"""Tests for the power-desk situation synthesis (GET /api/power/situation).

`build_power_situation` is a pure function that joins day-ahead price, residual
load and the (DE-LU-only) spark spread into one descriptive top-line for the
selected bidding zone — the coherence keystone of the power desk. It must stay
descriptive (Posture B): it reports the physical state + how far it deviates
from the series' own history, never a forecast.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.models.energy import EnergyPrice, PowerGenMix, PowerGrid, PowerPriceDaily
from backend.routes.power import build_power_situation


@pytest.fixture(autouse=True)
def _clear_dependency_overrides():
    yield
    from backend.main import app

    app.dependency_overrides.clear()


# ─── helpers ─────────────────────────────────────────────────────────────────

# UTC, not local: the routes bucket on datetime.utcnow().date(). With a local
# date.today() these tests fail for the two hours between local and UTC midnight
# (CEST is UTC+2) — a flake that only ever fires in the middle of the night.
_TODAY = datetime.now(timezone.utc).date()


def _price_series(closes: list[float], neg_hours: list[int] | None = None) -> list[dict]:
    """Build an ascending price series ending today."""
    n = len(closes)
    neg = neg_hours or [0] * n
    return [
        {
            "date": (_TODAY - timedelta(days=n - 1 - i)).isoformat(),
            "close": closes[i],
            "negative_hours": neg[i],
        }
        for i in range(n)
    ]


def _grid_row(d: str, residual_mw: float, renewable_share: float, dunkelflaute: bool) -> dict:
    return {
        "date": d,
        "residual_mw": residual_mw,
        "renewable_share": renewable_share,
        "dunkelflaute": dunkelflaute,
    }


def _flat_grid(residuals: list[float], dunkel_last: bool = False) -> list[dict]:
    n = len(residuals)
    rows = []
    for i, r in enumerate(residuals):
        d = (_TODAY - timedelta(days=n - 1 - i)).isoformat()
        last = i == n - 1
        rows.append(_grid_row(d, r, 0.25, dunkel_last and last))
    return rows


# ─── unit tests: build_power_situation ───────────────────────────────────────


def test_empty_series_unavailable():
    s = build_power_situation("DE_LU", [], [], None)
    assert s["available"] is False
    assert s["price"]["available"] is False
    assert s["grid"]["available"] is False
    assert s["zone_label"] == "DE-LU"


def test_calm_state_no_flags():
    # ~flat price + flat residual, plenty of history, no dunkelflaute/negative.
    closes = [50.0 + (i % 2) for i in range(20)]  # 50/51 alternating → tiny variance
    price = _price_series(closes)
    grid = _flat_grid([45_000.0 + (i % 2) * 100 for i in range(20)])
    s = build_power_situation("DE_LU", price, grid, {"spark_spread": 8.0, "power_price": 60.0, "gas_price": 30.0})
    assert s["available"] is True
    assert s["state"] == "CALM"
    assert s["flags"] == []
    assert abs(s["price"]["z"]) < 2.0
    assert s["spark"]["available"] is True
    assert s["spark"]["dirty_spark_spread"] == pytest.approx(8.0)
    # It is DIRTY, and the hero says so: a positive spread is only a margin below
    # this carbon price. 8.0 / 0.404 = 19.8 EUR/t.
    assert s["spark"]["breakeven_eua_eur_t"] == pytest.approx(19.8, abs=0.1)
    assert "gas_price" not in s["spark"], "licensed exchange data is not republished"


def test_dunkelflaute_elevates_and_flags():
    price = _price_series([50.0, 51.0, 50.0])  # short history → no z
    grid = _flat_grid([45_000.0, 46_000.0, 47_000.0], dunkel_last=True)
    s = build_power_situation("DE_LU", price, grid, None)
    assert s["grid"]["dunkelflaute"] is True
    assert s["state"] == "ELEVATED"
    assert any(f["key"] == "dunkelflaute" for f in s["flags"])


def test_negative_prices_flag():
    price = _price_series([50.0, 40.0, -5.0], neg_hours=[0, 0, 6])
    grid = _flat_grid([45_000.0, 46_000.0, 47_000.0])
    s = build_power_situation("DE_LU", price, grid, None)
    assert s["price"]["negative"] is True
    assert s["price"]["negative_hours"] == 6
    assert any(f["key"] == "negative_prices" for f in s["flags"])
    assert s["state"] == "ELEVATED"


def test_price_spike_is_stressed():
    closes = [50.0 + (i % 2) for i in range(19)] + [120.0]  # last day far above baseline
    price = _price_series(closes)
    grid = _flat_grid([45_000.0 + (i % 2) * 100 for i in range(20)])
    s = build_power_situation("DE_LU", price, grid, None)
    assert s["price"]["z"] is not None and s["price"]["z"] >= 3.0
    assert s["state"] == "STRESSED"


def test_spark_unsupported_for_non_de_zone():
    price = _price_series([50.0, 51.0, 52.0])
    grid = _flat_grid([30_000.0, 31_000.0, 32_000.0])
    s = build_power_situation("FR", price, grid, None, spark_supported=False)
    assert s["spark"]["supported"] is False
    assert s["spark"]["available"] is False
    assert s["zone_label"] == "FR"


def test_short_history_has_no_zscore_but_is_available():
    price = _price_series([50.0, 51.0, 52.0])  # < MIN_BASELINE_N
    grid = _flat_grid([45_000.0, 46_000.0, 47_000.0])
    s = build_power_situation("DE_LU", price, grid, None)
    assert s["price"]["available"] is True
    assert s["price"]["z"] is None
    assert s["price"]["close"] == pytest.approx(52.0)


def test_headline_describes_zone_and_metrics():
    price = _price_series([50.0, 51.0, 52.0])
    grid = _flat_grid([45_000.0, 46_000.0, 47_000.0])
    s = build_power_situation("DE_LU", price, grid, {"spark_spread": 5.0, "power_price": 52.0, "gas_price": 30.0})
    assert "DE-LU" in s["headline"]
    assert "day-ahead" in s["headline"]
    assert s["as_of"] == price[-1]["date"]


# ─── staleness: the hero must not assert a confident state on days-old data ────


def _series_ending(end: date, n: int, key_vals):
    """[{date, close/residual...}] of length n ending on `end` (ascending)."""
    return [
        {"date": (end - timedelta(days=n - 1 - i)).isoformat(), **key_vals(i)}
        for i in range(n)
    ]


def test_situation_flags_stale_data():
    today = date(2026, 7, 2)
    end = date(2026, 6, 27)  # 5 days behind → stale
    price = _series_ending(end, 5, lambda i: {"close": 50.0 + (i % 2), "negative_hours": 0})
    grid = _series_ending(
        end, 5, lambda i: {"residual_mw": 40_000.0, "renewable_share": 0.25, "dunkelflaute": False}
    )
    s = build_power_situation("DE_LU", price, grid, None, today=today)
    assert s["as_of"] == "2026-06-27"
    assert s["stale"] is True
    assert s["age_days"] == 5


def test_situation_fresh_data_not_stale():
    price = _price_series([50.0, 51.0, 52.0])  # ends _TODAY
    grid = _flat_grid([45_000.0, 46_000.0, 47_000.0])
    s = build_power_situation("DE_LU", price, grid, None, today=_TODAY)
    assert s["stale"] is False
    assert s["age_days"] == 0


def test_situation_staleness_defaults_off_without_today():
    # Existing call sites pass no `today`; staleness assessment is then inert.
    price = _price_series([50.0, 51.0, 52.0])
    grid = _flat_grid([45_000.0, 46_000.0, 47_000.0])
    s = build_power_situation("DE_LU", price, grid, None)
    assert s["stale"] is False
    assert s["age_days"] is None


# ─── per-component staleness: one fresh series must not mask a stale one ──────
#
# The old top-level `as_of = max(price, grid)` let a fresh day-ahead price make
# 5-day-old residual/renewables/Dunkelflaute figures look current — exactly the
# failure mode of the 2026-07-07 outage aftermath, when prices resumed before
# the grid series did.


def test_stale_grid_behind_fresh_price_is_flagged():
    today = date(2026, 7, 11)
    price = _series_ending(today, 5, lambda i: {"close": 50.0 + (i % 2), "negative_hours": 0})
    grid = _series_ending(
        date(2026, 7, 6), 5,
        lambda i: {"residual_mw": 40_000.0, "renewable_share": 0.25, "dunkelflaute": False},
    )
    s = build_power_situation("DE_LU", price, grid, None, today=today)

    assert s["price"]["stale"] is False
    assert s["price"]["age_days"] == 0
    assert s["grid"]["stale"] is True
    assert s["grid"]["age_days"] == 5
    # top level: newest date stays as_of, but staleness is worst-of, not max-of
    assert s["as_of"] == "2026-07-11"
    assert s["stale"] is True


def test_component_freshness_fields_on_fresh_data():
    price = _price_series([50.0, 51.0, 52.0])
    grid = _flat_grid([45_000.0, 46_000.0, 47_000.0])
    spark = {"spark_spread": 5.0, "power_price": 52.0, "gas_price": 30.0,
             "date": _TODAY.isoformat()}
    s = build_power_situation("DE_LU", price, grid, spark, today=_TODAY)

    for comp in ("price", "grid", "spark"):
        assert s[comp]["as_of"] is not None
        assert s[comp]["age_days"] == 0
        assert s[comp]["stale"] is False
    assert s["stale"] is False


def test_stale_spark_is_flagged_from_its_own_date():
    today = date(2026, 7, 11)
    price = _series_ending(today, 5, lambda i: {"close": 50.0, "negative_hours": 0})
    grid = _series_ending(
        today, 5,
        lambda i: {"residual_mw": 40_000.0, "renewable_share": 0.25, "dunkelflaute": False},
    )
    spark = {"spark_spread": 5.0, "power_price": 52.0, "gas_price": 30.0, "date": "2026-07-04"}
    s = build_power_situation("DE_LU", price, grid, spark, today=today)

    assert s["spark"]["stale"] is True
    assert s["spark"]["age_days"] == 7
    assert s["stale"] is True, "a stale component must surface at the top level"


def test_headline_marks_stale_components():
    today = date(2026, 7, 11)
    price = _series_ending(today, 5, lambda i: {"close": 50.0, "negative_hours": 0})
    grid = _series_ending(
        date(2026, 7, 6), 5,
        lambda i: {"residual_mw": 40_000.0, "renewable_share": 0.25, "dunkelflaute": False},
    )
    s = build_power_situation("DE_LU", price, grid, None, today=today)
    assert "5d old" in s["headline"], s["headline"]
    # the fresh price segment must NOT carry an age marker
    price_seg = [p for p in s["headline"].split("·") if "day-ahead" in p][0]
    assert "old" not in price_seg


def test_component_staleness_inert_without_today():
    price = _price_series([50.0, 51.0, 52.0])
    grid = _flat_grid([45_000.0, 46_000.0, 47_000.0])
    s = build_power_situation("DE_LU", price, grid, None)
    for comp in ("price", "grid"):
        assert s[comp]["stale"] is False
        assert s[comp]["age_days"] is None


# ─── coverage: an unreliable renewable share must not flag Dunkelflaute ────────


def test_situation_coverage_suppresses_dunkelflaute_flag():
    price = _price_series([50.0, 51.0, 50.0])  # short history → no z → no price severity
    grid = _flat_grid([45_000.0, 46_000.0, 47_000.0], dunkel_last=True)
    s = build_power_situation("DE_LU", price, grid, None, grid_coverage_ok=False)
    assert all(f["key"] != "dunkelflaute" for f in s["flags"])
    assert s["grid"]["dunkelflaute"] is False
    assert s["grid"]["renewable_share_reliable"] is False
    assert s["state"] == "CALM"


def test_situation_coverage_ok_keeps_dunkelflaute_flag():
    price = _price_series([50.0, 51.0, 50.0])
    grid = _flat_grid([45_000.0, 46_000.0, 47_000.0], dunkel_last=True)
    s = build_power_situation("DE_LU", price, grid, None, grid_coverage_ok=True)
    assert any(f["key"] == "dunkelflaute" for f in s["flags"])
    assert s["grid"]["renewable_share_reliable"] is True


def test_route_suppresses_dunkelflaute_on_incomplete_coverage(db_session):
    # NL-style: near-zero renewable share but generation mix covers <60% of load →
    # the situation hero must not raise a Dunkelflaute flag off unreliable data.
    for i in range(3):
        d = (_TODAY - timedelta(days=2 - i)).isoformat()
        db_session.add(PowerPriceDaily(date=d, zone="NL", mean_price=60.0, min_price=20.0, max_price=90.0, negative_hours=0))
        db_session.add(PowerGrid(date=d, zone="NL", load_mw=10_000.0, wind_mw=70.0, solar_mw=60.0))  # ~1.3%
        db_session.add(PowerGenMix(date=d, zone="NL", psr_type="Fossil Gas", gen_mw=3_400.0))
        db_session.add(PowerGenMix(date=d, zone="NL", psr_type="Hard Coal", gen_mw=1_360.0))
    db_session.commit()
    client = _make_client(db_session)
    body = client.get("/api/power/situation?zone=NL").json()
    assert body["available"] is True
    assert all(f["key"] != "dunkelflaute" for f in body["flags"])
    assert body["grid"]["renewable_share_reliable"] is False


# ─── route integration ───────────────────────────────────────────────────────


def _make_client(db: Session) -> TestClient:
    from backend.database import get_db
    from backend.main import app

    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app, raise_server_exceptions=True)


def _seed_de_lu(db: Session) -> None:
    for i in range(3):
        d = (_TODAY - timedelta(days=2 - i)).isoformat()
        db.add(PowerPriceDaily(date=d, zone="DE_LU", mean_price=50.0 + i, min_price=10.0, max_price=90.0, negative_hours=0))
        db.add(PowerGrid(date=d, zone="DE_LU", load_mw=50_000.0, wind_mw=8_000.0, solar_mw=4_000.0))
    # The hero derives the spark live from the price series (same as /spark-spread)
    for i in range(3):
        d = (_TODAY - timedelta(days=2 - i)).isoformat()
        db.add(EnergyPrice(date=d, symbol="POWER_DE", close=52.0))
        db.add(EnergyPrice(date=d, symbol="TTF", close=30.0))
    db.commit()


def test_route_de_lu_available(db_session):
    _seed_de_lu(db_session)
    client = _make_client(db_session)
    resp = client.get("/api/power/situation?zone=DE_LU")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["zone"] == "DE_LU"
    assert body["price"]["available"] is True
    assert body["grid"]["available"] is True
    assert body["spark"]["available"] is True
    assert "DE_LU" in body["zones"]


def test_route_fr_spark_matches_the_panel(db_session):
    """The hero and the /spark-spread panel must never disagree: the route computes
    a live TTF-leg spark for EVERY zone, so the hero saying "DE-LU only" while the
    panel below shows an FR spark was a contradiction. The hero now derives the
    spark the same way the panel does."""
    for i in range(3):
        d = (_TODAY - timedelta(days=2 - i)).isoformat()
        db_session.add(PowerPriceDaily(date=d, zone="FR", mean_price=40.0 + i, min_price=10.0, max_price=80.0, negative_hours=0))
        db_session.add(PowerGrid(date=d, zone="FR", load_mw=45_000.0, wind_mw=5_000.0, solar_mw=3_000.0))
        db_session.add(EnergyPrice(date=d, symbol="POWER_FR", close=100.0))
        db_session.add(EnergyPrice(date=d, symbol="TTF", close=30.0))
    db_session.commit()
    client = _make_client(db_session)
    body = client.get("/api/power/situation?zone=FR").json()
    assert body["available"] is True
    assert body["spark"]["supported"] is True
    assert body["spark"]["available"] is True
    # heat_rate = 1/0.50 → dirty spark = 100 − 30·2 = 40
    assert body["spark"]["dirty_spark_spread"] == pytest.approx(40.0)
    assert body["spark"]["as_of"] == _TODAY.isoformat()

    panel = client.get("/api/power/spark-spread?zone=FR&days=7").json()
    assert panel["latest"]["dirty_spark_spread"] == pytest.approx(body["spark"]["dirty_spark_spread"])
    # The break-even carbon price must agree too — the hero and the panel computing the same
    # number two ways is exactly how they came to disagree in the first place.
    assert panel["latest"]["breakeven_eua_eur_t"] == pytest.approx(
        body["spark"]["breakeven_eua_eur_t"])


def test_route_fr_spark_without_prices_is_signposted_not_pretended(db_session):
    for i in range(3):
        d = (_TODAY - timedelta(days=2 - i)).isoformat()
        db_session.add(PowerPriceDaily(date=d, zone="FR", mean_price=40.0 + i, min_price=10.0, max_price=80.0, negative_hours=0))
        db_session.add(PowerGrid(date=d, zone="FR", load_mw=45_000.0, wind_mw=5_000.0, solar_mw=3_000.0))
    db_session.commit()
    client = _make_client(db_session)
    body = client.get("/api/power/situation?zone=FR").json()
    assert body["spark"]["supported"] is True
    assert body["spark"]["available"] is False
    assert body["spark"]["dirty_spark_spread"] is None
    assert body["spark"]["breakeven_eua_eur_t"] is None


def test_route_empty_unavailable(db_session):
    client = _make_client(db_session)
    resp = client.get("/api/power/situation?zone=DE_LU")
    assert resp.status_code == 200
    assert resp.json()["available"] is False


def test_power_empty_states_are_user_facing(db_session):
    # Empty-DB "reason" strings are surfaced to visitors — they must not leak the
    # internal collector function names (ingest_*/backfill/run …).
    client = _make_client(db_session)
    for path in (
        "/api/power/day-ahead?zone=DE_LU",
        "/api/power/grid?zone=DE_LU",
        "/api/power/generation-mix?zone=DE_LU",
        "/api/power/flows",
        "/api/power/spark-spread",
    ):
        body = client.get(path).json()
        assert body["available"] is False, path
        reason = (body.get("reason") or "").lower()
        assert "ingest" not in reason and "backfill" not in reason and "run " not in reason, (path, reason)


# ─── forced-outage flag: the flagship surfaces in the hero ────────────────────


def test_large_forced_outages_flag_the_situation():
    price = _price_series([50.0, 51.0, 52.0])
    grid = _flat_grid([45_000.0, 46_000.0, 47_000.0])
    s = build_power_situation("DE_LU", price, grid, None, forced_outage_mw=2_400.0)

    flag = next((f for f in s["flags"] if f["key"] == "forced_outages"), None)
    assert flag is not None
    assert "2.4 GW" in flag["label"]
    assert s["state"] == "ELEVATED"


def test_small_forced_outages_do_not_flag():
    price = _price_series([50.0, 51.0, 52.0])
    grid = _flat_grid([45_000.0, 46_000.0, 47_000.0])
    s = build_power_situation("DE_LU", price, grid, None, forced_outage_mw=300.0)
    assert all(f["key"] != "forced_outages" for f in s["flags"])
    assert s["state"] == "CALM"


def test_forced_outage_default_is_absent_not_zero():
    """Call sites without outage data must not imply '0 MW forced' — absence
    of the feed and a calm grid are different statements."""
    price = _price_series([50.0, 51.0, 52.0])
    grid = _flat_grid([45_000.0, 46_000.0, 47_000.0])
    s = build_power_situation("DE_LU", price, grid, None)
    assert all(f["key"] != "forced_outages" for f in s["flags"])


def test_forced_outage_flag_is_capacity_relative_when_installed_known():
    """With A68 coverage, 2.4 GW against a 200 GW fleet (1.2%) is not a flag;
    against a 25 GW fleet (9.6%) it escalates to critical with the share shown."""
    price = _price_series([50.0, 51.0, 52.0])
    grid = _flat_grid([45_000.0, 46_000.0, 47_000.0])

    big_fleet = build_power_situation(
        "DE_LU", price, grid, None,
        forced_outage_mw=2_400.0, forced_outage_installed_mw=200_000.0,
    )
    assert all(f["key"] != "forced_outages" for f in big_fleet["flags"])
    assert big_fleet["state"] == "CALM"

    small_fleet = build_power_situation(
        "DE_LU", price, grid, None,
        forced_outage_mw=2_400.0, forced_outage_installed_mw=25_000.0,
    )
    flag = next((f for f in small_fleet["flags"] if f["key"] == "forced_outages"), None)
    assert flag is not None and flag["severity"] == "critical"
    assert "10% of fleet" in flag["label"]
    assert small_fleet["state"] == "STRESSED"


def test_weekend_old_spark_does_not_stale_the_situation():
    """TTF doesn't trade on weekends: a 2-day-old spark leg (Friday close seen
    on Sunday) is NORMAL and must not flag every zone's situation stale via the
    worst-of rule. Spark is judged by its own window (PANEL_MAX_AGE_DAYS
    'spark' = 4), same as the spark panel caption."""
    today = date(2026, 7, 12)  # a Sunday
    price = _series_ending(today, 5, lambda i: {"close": 50.0, "negative_hours": 0})
    grid = _series_ending(
        today, 5,
        lambda i: {"residual_mw": 40_000.0, "renewable_share": 0.25, "dunkelflaute": False},
    )
    spark = {"spark_spread": 5.0, "power_price": 52.0, "gas_price": 30.0, "date": "2026-07-10"}
    s = build_power_situation("DE_LU", price, grid, spark, today=today)

    assert s["spark"]["age_days"] == 2
    assert s["spark"]["stale"] is False
    assert s["stale"] is False


def test_situation_ships_the_baseline_window_it_actually_used():
    """The UI must be able to STATE the window instead of guessing it. It guessed
    '~90-day' for a 120-day window until 2026-07-12; the number now travels with
    the data."""
    from backend.routes.power import SITUATION_BASELINE_DAYS

    price = _price_series([50.0, 51.0, 52.0])
    grid = _flat_grid([45_000.0, 46_000.0, 47_000.0])
    s = build_power_situation("DE_LU", price, grid, None)
    assert s["baseline_days"] == SITUATION_BASELINE_DAYS


def test_no_frontend_panel_restates_the_baseline_window():
    """Regression guard for the class of bug, not the instance: a hardcoded
    '90-day norm' in a power panel silently contradicted the code. Panels must
    render `baseline_days` / `baseline_n` from the response instead."""
    from pathlib import Path

    components = Path(__file__).resolve().parents[2] / "frontend" / "src" / "components"
    offenders = []
    for f in components.glob("*.jsx"):
        text = f.read_text()
        # dormant non-power verticals are out of scope (crypto/equities glossaries)
        if f.name in {"CryptoPanel.jsx", "RelatedEquitiesPanel.jsx"}:
            continue
        for claim in ("90-day norm", "~90-day norm", "90d norm"):
            if claim in text:
                offenders.append(f"{f.name}: {claim!r}")
    assert not offenders, f"panels restating a baseline window: {offenders}"


def test_zone_with_generation_but_no_load_does_not_crash_the_headline():
    """IE-SEM publishes wind but stopped publishing A65 load on 2025-10-23. The
    honest residual is None — and formatting None into the headline 500'd
    /api/power/situation in production for exactly that zone."""
    today = date(2026, 7, 12)
    price = _series_ending(today, 3, lambda i: {"close": 50.0, "negative_hours": 0})
    grid = _series_ending(
        today, 3,
        lambda i: {"residual_mw": None, "renewable_share": None, "dunkelflaute": False},
    )
    s = build_power_situation("IE_SEM", price, grid, None, today=today)

    assert s["grid"]["residual_gw"] is None
    assert "no published load" in s["headline"]
    assert s["state"] in ("CALM", "ELEVATED", "STRESSED")
