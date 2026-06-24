"""Tests for USGS copper supply parser and /api/metals/copper route."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.metals.usgs_copper import _clean_value, parse_mis_xlsx
from backend.models.energy import EnergyPrice
from backend.models.metals import CopperSupply

# ─── fixture path ─────────────────────────────────────────────────────────────

FIXTURE = Path(__file__).parent / "fixtures" / "copper" / "mis-202501-coppe.xlsx"


@pytest.fixture(autouse=True)
def _clear_dependency_overrides():
    """Ensure dependency overrides are cleaned up after each test."""
    yield
    from backend.main import app

    app.dependency_overrides.clear()


# ─── _clean_value unit tests ──────────────────────────────────────────────────


def test_clean_value_int():
    assert _clean_value(92900) == pytest.approx(92900.0)


def test_clean_value_float():
    assert _clean_value(92900.0) == pytest.approx(92900.0)


def test_clean_value_string_with_commas():
    assert _clean_value("36,000 e") == pytest.approx(36000.0)


def test_clean_value_string_multi_footnote():
    assert _clean_value("2,360 r, e") == pytest.approx(2360.0)


def test_clean_value_string_r_footnote():
    assert _clean_value("103,000 r") == pytest.approx(103000.0)


def test_clean_value_none():
    assert _clean_value(None) is None


def test_clean_value_nan():

    assert _clean_value(float("nan")) is None


def test_clean_value_empty_string():
    assert _clean_value("") is None


# ─── parse_mis_xlsx tests ─────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def fixture_bytes() -> bytes:
    if not FIXTURE.exists():
        pytest.skip(f"USGS fixture not found at {FIXTURE}")
    return FIXTURE.read_bytes()


@pytest.fixture(scope="module")
def parsed(fixture_bytes) -> dict:
    return parse_mis_xlsx(fixture_bytes)


def test_parsed_returns_dict(parsed):
    assert isinstance(parsed, dict)
    assert len(parsed) > 0


def test_parsed_all_three_metrics_present(parsed):
    """Every row should have all three metric keys."""
    for date_str, row in parsed.items():
        assert "us_mine_production" in row, f"Missing us_mine_production for {date_str}"
        assert "us_refined_production" in row, f"Missing us_refined_production for {date_str}"
        assert "us_refined_stocks" in row, f"Missing us_refined_stocks for {date_str}"


def test_parsed_date_format(parsed):
    """All dates should be in YYYY-MM-01 format."""
    for d in parsed:
        assert len(d) == 10, f"Bad date format: {d}"
        assert d.endswith("-01"), f"Date does not end with -01: {d}"
        # Ensure it's a valid date
        date.fromisoformat(d)


def test_parsed_jan_2024_mine_production(parsed):
    """January 2024 mine production should be ~92,900 t (T2 Total col)."""
    row = parsed.get("2024-01-01")
    assert row is not None, "2024-01-01 not found in parsed output"
    assert row["us_mine_production"] == pytest.approx(92900.0)


def test_parsed_jan_2024_refined_production(parsed):
    """January 2024 Total refined = 78,300 t (T4)."""
    row = parsed.get("2024-01-01")
    assert row is not None
    assert row["us_refined_production"] == pytest.approx(78300.0)


def test_parsed_jan_2024_refined_stocks(parsed):
    """January 2024 Total refined stocks = 103,000 t (T10, footnote-stripped)."""
    row = parsed.get("2024-01-01")
    assert row is not None
    assert row["us_refined_stocks"] == pytest.approx(103000.0)


def test_parsed_mine_production_plausible_range(parsed):
    """All mine production values should be in the 75k–120k t/month range."""
    for d, row in parsed.items():
        v = row["us_mine_production"]
        if v is not None:
            assert 50_000 <= v <= 150_000, f"Mine production out of range for {d}: {v}"


def test_parsed_jan_2025_mine_production(parsed):
    """January 2025 mine production should be ~87,800 t."""
    row = parsed.get("2025-01-01")
    assert row is not None, "2025-01-01 not found in parsed output"
    assert row["us_mine_production"] == pytest.approx(87800.0)


def test_parsed_bad_xlsx_does_not_crash():
    """parse_mis_xlsx should return empty dict on garbage input, not raise."""
    result = parse_mis_xlsx(b"not an xlsx")
    assert result == {}


# ─── route integration tests ──────────────────────────────────────────────────


_TODAY = date.today()
_D1 = (_TODAY - timedelta(days=60)).strftime("%Y-%m-01")   # ~2 months ago
_D2 = (_TODAY - timedelta(days=30)).strftime("%Y-%m-01")   # ~1 month ago


def _seed_copper(db, rows: list[dict]) -> None:
    for r in rows:
        db.add(
            CopperSupply(
                date=r["date"],
                us_mine_production=r.get("us_mine_production"),
                us_refined_production=r.get("us_refined_production"),
                us_refined_stocks=r.get("us_refined_stocks"),
            )
        )
    db.commit()


def _seed_price(db, rows: list[dict]) -> None:
    for r in rows:
        db.add(EnergyPrice(date=r["date"], symbol="COPPER", close=r["close"]))
    db.commit()


def _make_client(db) -> TestClient:
    from backend.database import get_db
    from backend.main import app

    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app, raise_server_exceptions=True)


def test_route_available_true(db_session):
    """Seeded rows → available=True with data + latest + price arrays."""
    _seed_copper(db_session, [
        {"date": _D1, "us_mine_production": 90000.0, "us_refined_production": 78000.0, "us_refined_stocks": 100000.0},
        {"date": _D2, "us_mine_production": 88000.0, "us_refined_production": 76000.0, "us_refined_stocks": 95000.0},
    ])
    _seed_price(db_session, [
        {"date": "2025-01-02", "close": 4.25},
        {"date": "2025-01-03", "close": 4.30},
    ])

    client = _make_client(db_session)
    resp = client.get("/api/metals/copper?months=36")
    assert resp.status_code == 200
    body = resp.json()

    assert body["available"] is True
    assert body["source"] == "USGS Mineral Industry Surveys (public domain)"
    assert body["unit"] == "metric tons"
    assert len(body["data"]) == 2
    assert body["latest"] is not None
    assert "us_mine_production" in body["latest"]
    assert "us_refined_stocks" in body["latest"]
    assert len(body["price"]) == 2
    assert body["price"][0]["close"] == pytest.approx(4.25)


def test_route_available_false_when_empty(db_session):
    """No CopperSupply rows → available=False."""
    client = _make_client(db_session)
    resp = client.get("/api/metals/copper?months=36")
    assert resp.status_code == 200
    assert resp.json()["available"] is False


def test_route_no_price_data_still_works(db_session):
    """CopperSupply rows without EnergyPrice COPPER → price=[] not an error."""
    _seed_copper(db_session, [
        {"date": _D1, "us_mine_production": 90000.0, "us_refined_production": 78000.0, "us_refined_stocks": 100000.0},
    ])
    client = _make_client(db_session)
    resp = client.get("/api/metals/copper?months=36")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["price"] == []


def test_route_data_sorted_ascending(db_session):
    """data array should be sorted by date ascending (chart-friendly)."""
    _seed_copper(db_session, [
        {"date": _D2, "us_mine_production": 88000.0, "us_refined_production": 76000.0, "us_refined_stocks": 95000.0},
        {"date": _D1, "us_mine_production": 90000.0, "us_refined_production": 78000.0, "us_refined_stocks": 100000.0},
    ])
    client = _make_client(db_session)
    resp = client.get("/api/metals/copper?months=36")
    body = resp.json()
    dates = [r["date"] for r in body["data"]]
    assert dates == sorted(dates)


def test_route_latest_is_most_recent(db_session):
    """latest should reflect the most-recent date row."""
    _seed_copper(db_session, [
        {"date": _D1, "us_mine_production": 90000.0, "us_refined_production": 78000.0, "us_refined_stocks": 100000.0},
        {"date": _D2, "us_mine_production": 88000.0, "us_refined_production": 76000.0, "us_refined_stocks": 95000.0},
    ])
    client = _make_client(db_session)
    resp = client.get("/api/metals/copper?months=36")
    body = resp.json()
    # D2 is more recent (30 days ago vs 60 days ago)
    assert body["latest"]["date"] == _D2
    assert body["latest"]["us_mine_production"] == pytest.approx(88000.0)
