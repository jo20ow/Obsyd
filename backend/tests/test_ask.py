"""The question box (backend/power/ask.py): the parser never guesses, the
aggregation follows each metric's declared rule, coverage gaps are named, and
the endpoint is premium. The owner's original question — in German — is the
canonical fixture."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from backend.power.ask import answer, parse_query
from backend.power.hourly_store import upsert_hourly

# ─── parsing ─────────────────────────────────────────────────────────────────


def test_the_owners_question_parses_in_german():
    p = parse_query("vergleiche negative stunden in Finnland von 2019 bis 2025")
    assert p["ok"] is True
    assert p["metric"]["id"] == "negative_hours"
    assert p["zones"] == ["FI"]
    assert (p["year_from"], p["year_to"]) == (2019, 2025)


def test_english_variants_and_since():
    p = parse_query("compare negative hours in Finland from 2019 to 2025")
    assert p["metric"]["id"] == "negative_hours" and p["zones"] == ["FI"]
    p = parse_query("solar capture factor Germany since 2022")
    assert p["metric"]["id"] == "capture_solar" and p["zones"] == ["DE_LU"]
    assert p["year_from"] == 2022 and p["year_to"] == datetime.now(UTC).year


def test_longest_phrase_wins_over_substrings():
    # "negative hours" must not resolve to the bare "price" metric, and
    # "residual load" must not resolve to "load".
    assert parse_query("negative hours Spain 2024")["metric"]["id"] == "negative_hours"
    assert parse_query("residual load France 2024")["metric"]["id"] == "residual"


def test_country_with_many_zones_becomes_a_comparison():
    p = parse_query("price Norway 2024")
    assert p["zones"] == ["NO1", "NO2", "NO3", "NO4", "NO5"]


def test_unparseable_fails_visibly_with_suggestions():
    p = parse_query("what happens if the wind stops")
    # "wind" matches a metric, but no zone follows → the zone problem is named.
    assert p["ok"] is False and p["problem"] == "zone"
    p2 = parse_query("frobnicate Bavaria")
    assert p2["ok"] is False and p2["problem"] == "metric"
    assert p2["examples"]


# ─── answering ────────────────────────────────────────────────────────────────


def _seed_neg_hours(db, zone="FI"):
    for year, total_days, per_day in ((2023, 10, 2.0), (2024, 10, 5.0)):
        pts = [(int(datetime(year, 3, 1 + d, tzinfo=UTC).timestamp()), per_day)
               for d in range(total_days)]
        upsert_hourly(db, "price.negative_hours", zone, pts, unit="h")


def test_sum_metric_totals_per_year_and_names_coverage(db_session):
    _seed_neg_hours(db_session)
    out = answer(db_session, "negative stunden Finnland 2019 bis 2024",
                 now=datetime(2026, 9, 13, tzinfo=UTC))
    assert out["available"] is True
    assert out["interpreted"]["aggregation"] == "total per period"
    by_period = {r["period"]: r.get("FI") for r in out["rows"]}
    assert by_period == {"2023": 20.0, "2024": 50.0}
    # 2019-2022 were asked for but predate the record — named, not trimmed.
    assert any("2019" in c and "not on record" in c for c in out["coverage"])
    assert "FI: 50 h in 2024" in out["sentence"]


def test_interior_year_gap_is_named(db_session):
    """A year absent BETWEEN record start and the window end must be said out
    loud — the QA walkthrough had DE-LU's mix jump 2015 → 2018 with 2016–2017
    silently missing from the chart."""
    for year in (2021, 2024):  # 2022–2023 missing mid-record
        pts = [(int(datetime(year, 3, 1 + d, tzinfo=UTC).timestamp()), 3.0)
               for d in range(5)]
        upsert_hourly(db_session, "price.negative_hours", "FI", pts, unit="h")
    out = answer(db_session, "negative stunden Finnland 2021 bis 2024",
                 now=datetime(2026, 9, 14, tzinfo=UTC))
    assert out["available"] is True
    assert any("2022–2023" in c and "no data on record" in c for c in out["coverage"])


def test_mean_metric_averages_and_single_year_goes_monthly(db_session):
    base = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp())
    upsert_hourly(db_session, "price.dayahead", "FR",
                  [(base + i * 3600, 100.0 + (i % 2) * 50) for i in range(48)],
                  unit="EUR/MWh")
    out = answer(db_session, "price France 2024", now=datetime(2026, 1, 1, tzinfo=UTC))
    assert out["interpreted"]["grain"] == "monthly"
    assert out["rows"][0]["period"] == "2024-01"
    assert out["rows"][0]["FR"] == 125.0  # mean, not sum


def test_composite_wind_combines_both_series(db_session):
    base = int(datetime(2024, 6, 1, tzinfo=UTC).timestamp())
    upsert_hourly(db_session, "gen.B18", "DE_LU", [(base, 1000.0)], unit="MW")
    upsert_hourly(db_session, "gen.B19", "DE_LU", [(base, 9000.0)], unit="MW")
    out = answer(db_session, "wind Germany 2023 to 2024",
                 now=datetime(2026, 1, 1, tzinfo=UTC))
    assert {r["period"]: r.get("DE_LU") for r in out["rows"]} == {"2024": 10000.0}
    assert "Composite" in out["note"]


def test_partial_period_is_flagged(db_session):
    """Yearly grain: the running year is flagged as partial (a fragment
    wearing a period's label); a complete past month under monthly grain
    is NOT flagged."""
    now = datetime(2026, 9, 13, tzinfo=UTC)
    upsert_hourly(db_session, "price.negative_hours", "ES",
                  [(int(datetime(2026, 2, 1, tzinfo=UTC).timestamp()), 3.0)], unit="h")
    out = answer(db_session, "negative hours Spain 2024 to 2026", now=now)
    assert out["partial_period"] == "2026"
    monthly = answer(db_session, "negative hours Spain 2026", now=now)
    assert monthly["partial_period"] is None  # Feb 2026 is complete


def test_first_partial_year_is_named(db_session):
    """A record starting mid-year must not wear a full year's label silently
    (the DE 2018 bar was really Oct–Dec 2018)."""
    pts = [(int(datetime(2023, 10, 1, tzinfo=UTC).timestamp()) + d * 86400, 2.0)
           for d in range(20)]
    upsert_hourly(db_session, "price.negative_hours", "FR", pts, unit="h")
    out = answer(db_session, "negative hours France 2022 to 2024",
                 now=datetime(2026, 1, 1, tzinfo=UTC))
    assert any("2023 is a partial year" in c for c in out["coverage"])
    assert any("2022–2022 not on record" in c for c in out["coverage"])


def test_yearly_capture_factor_is_generation_weighted(db_session):
    """Owner audit of the numbers: the yearly capture factor must be the
    GENERATION-WEIGHTED figure from raw hours — an unweighted mean of monthly
    factors overweights winter and reads too high. Seed: baseload 100 all
    year, solar produces only in 50-price hours → weighted factor exactly
    0.50 (the unweighted-monthly reading would sit above it whenever volumes
    vary)."""
    from backend.power.ask import answer_structured

    prices, gen = [], []
    for day in range(300):
        base = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp()) + day * 86400
        for h in range(24):
            prices.append((base + h * 3600, 150.0 if h % 2 else 50.0))
        for h in (0, 2, 4, 6):  # 4 solar hours/day, all in the 50-price slots
            gen.append((base + h * 3600, 1000.0))
    upsert_hourly(db_session, "price.dayahead", "ES", prices, unit="EUR/MWh")
    upsert_hourly(db_session, "gen.B16", "ES", gen, unit="MW")

    out = answer_structured(db_session, "capture_solar", ["ES"], 2023, 2024,
                            now=datetime(2026, 1, 1, tzinfo=UTC))
    assert out["available"] is True
    assert {r["period"]: r.get("ES") for r in out["rows"]} == {"2024": 0.5}
    assert "Generation-weighted" in out["note"]

    # Monthly grain still reads the stored exact monthly factors (none seeded
    # here → honest no-data, not a silently recomputed number).
    monthly = answer_structured(db_session, "capture_solar", ["ES"], 2024, 2024,
                                now=datetime(2026, 1, 1, tzinfo=UTC))
    assert monthly["available"] is False


# ─── endpoint / premium gate ─────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    from backend.main import app

    app.dependency_overrides.clear()


def _client(db, *, pro=False) -> TestClient:
    from backend.auth.dependencies import require_pro
    from backend.database import get_db
    from backend.main import app

    app.dependency_overrides[get_db] = lambda: db
    if pro:
        app.dependency_overrides[require_pro] = lambda: {"email": "owner@test"}
    return TestClient(app, raise_server_exceptions=True)


def test_ask_endpoint_is_premium(db_session):
    assert _client(db_session).get("/api/v1/ask?q=price+France+2024").status_code == 401
    _seed_neg_hours(db_session)
    body = _client(db_session, pro=True).get(
        "/api/v1/ask", params={"q": "negative hours Finland 2023 to 2024"}).json()
    assert body["available"] is True and body["rows"]
    # Bare call = the filter OPTIONS (the UI's single source), never data.
    probe = _client(db_session, pro=True).get("/api/v1/ask").json()
    assert probe["available"] is False
    assert {m["id"] for m in probe["filters"]["metrics"]} >= {"negative_hours", "mix"}
    assert any(p["label"] == "Norway" and len(p["zones"]) == 5
               for p in probe["filters"]["places"])


def test_structured_params_reach_the_same_answerer(db_session):
    _seed_neg_hours(db_session)
    body = _client(db_session, pro=True).get(
        "/api/v1/ask",
        params={"metric": "negative_hours", "zones": "FI", "from": 2019, "to": 2024},
    ).json()
    assert body["available"] is True
    assert body["column_kind"] == "zones"
    assert {r["period"]: r.get("FI") for r in body["rows"]} == {"2023": 20.0, "2024": 50.0}
    assert any("2019" in c for c in body["coverage"])


def test_mix_metric_answers_with_fuel_columns(db_session):
    base = int(datetime(2024, 6, 1, tzinfo=UTC).timestamp())
    upsert_hourly(db_session, "gen.B16", "DE_LU", [(base, 5000.0)], unit="MW")
    upsert_hourly(db_session, "gen.B19", "DE_LU", [(base, 15000.0)], unit="MW")
    from backend.power.ask import answer_structured

    out = answer_structured(db_session, "mix", ["DE_LU", "FR"], 2023, 2024,
                            now=datetime(2026, 1, 1, tzinfo=UTC))
    assert out["available"] is True and out["column_kind"] == "fuels"
    assert set(out["columns"]) == {"B16", "B19"}
    assert out["rows"] == [{"period": "2024", "B16": 5000.0, "B19": 15000.0}]
    # One place at a time — the second zone is named as dropped, not silently eaten.
    assert any("one place at a time" in c for c in out["coverage"])
    # The leader is named with its label, not its PSR code.
    assert "Wind Onshore" in out["sentence"] or "B19" not in out["sentence"]
