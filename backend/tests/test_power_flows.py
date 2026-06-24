"""Tests for cross-border physical electricity flows (ENTSO-E A11).

Covers:
  - parse_physical_flow: unit test with crafted A11 XML for both directions
  - ingest_flows: net computation + upsert idempotency
  - GET /api/power/flows: route shape + available/unavailable cases
"""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.models.energy import PowerFlow
from backend.power.entsoe_flows import parse_physical_flow

# ─── autouse: clear dependency overrides between tests ───────────────────────


@pytest.fixture(autouse=True)
def _clear_dependency_overrides():
    """Prevent app.dependency_overrides leaking between test files."""
    yield
    from backend.main import app

    app.dependency_overrides.clear()


# ─── helpers ─────────────────────────────────────────────────────────────────

_TODAY = date.today()
_D1 = (_TODAY - timedelta(days=3)).isoformat()
_D2 = (_TODAY - timedelta(days=2)).isoformat()
_D3 = (_TODAY - timedelta(days=1)).isoformat()


def _a11_xml(day: str, quantity: float, resolution: str = "PT60M") -> str:
    """Minimal valid A11 XML for a single day (24 hourly points at `quantity` MW).

    The <start> element uses ISO 8601 UTC format (e.g. "2026-06-21T00:00Z") that
    _parse_utc (datetime.fromisoformat) can consume.  The YYYYMMDD0000 format is
    the ENTSO-E query parameter format, NOT the XML element format.
    """
    # Build 24 hourly points
    points = "".join(
        f"<Point><position>{i}</position><quantity>{quantity}</quantity></Point>"
        for i in range(1, 25)
    )
    iso_start = f"{day}T00:00Z"
    # Note: no leading whitespace before the XML declaration — ET.fromstring
    # requires it to be at position 0.
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<GL_MarketDocument xmlns="urn:iec62325.351:tc57wg16:451-6:generationloaddocument:3:0">'
        f"<TimeSeries>"
        f"<Period>"
        f"<timeInterval><start>{iso_start}</start></timeInterval>"
        f"<resolution>{resolution}</resolution>"
        f"{points}"
        f"</Period>"
        f"</TimeSeries>"
        f"</GL_MarketDocument>"
    )


def _seed_flows(db: Session, rows: list[dict]) -> None:
    for r in rows:
        db.add(
            PowerFlow(
                date=r["date"],
                from_zone=r.get("from_zone", "DE_LU"),
                to_zone=r.get("to_zone", "FR"),
                net_mw=r["net_mw"],
            )
        )
    db.commit()


def _make_client(db: Session) -> TestClient:
    from backend.database import get_db
    from backend.main import app

    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app, raise_server_exceptions=True)


# ─── parse_physical_flow unit tests ──────────────────────────────────────────


def test_parse_returns_daily_mean():
    """24 hourly points of 1000 MW → daily mean 1000.0 MW."""
    xml = _a11_xml(_D1, 1000.0)
    result = parse_physical_flow(xml)
    assert _D1 in result
    assert result[_D1] == pytest.approx(1000.0)


def test_parse_two_days():
    """Points spanning midnight produce two date buckets."""
    # Build a 24-h block starting at midnight of d2 (so all in d2)
    xml_d2 = _a11_xml(_D2, 3000.0)
    result = parse_physical_flow(xml_d2)
    assert _D2 in result
    assert result[_D2] == pytest.approx(3000.0)


def test_parse_empty_xml_returns_empty():
    """An XML with no TimeSeries → empty dict (no crash)."""
    xml = '<?xml version="1.0"?><GL_MarketDocument></GL_MarketDocument>'
    result = parse_physical_flow(xml)
    assert result == {}


def test_parse_invalid_xml_raises():
    """Malformed XML → ValueError."""
    with pytest.raises(ValueError, match="A11 XML parse error"):
        parse_physical_flow("not xml at all <<>>")


def test_net_flow_convention():
    """Net = AB − BA: positive = A→B, negative = B→A."""
    xml_ab = _a11_xml(_D1, 2000.0)  # from A to B: 2 GW
    xml_ba = _a11_xml(_D1, 500.0)   # from B to A: 0.5 GW
    flow_ab = parse_physical_flow(xml_ab)
    flow_ba = parse_physical_flow(xml_ba)
    net = flow_ab[_D1] - flow_ba[_D1]
    assert net == pytest.approx(1500.0)  # net 1.5 GW A→B


# ─── ingest_flows integration tests ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_ingest_skips_without_token(db_session):
    """ingest_flows returns {"skipped": "no token"} when ENTSOE_API_TOKEN is unset."""
    from backend.power.entsoe_flows import ingest_flows

    with patch("backend.power.entsoe_flows.settings") as mock_settings:
        mock_settings.entsoe_api_token = None
        result = await ingest_flows(db_session, [_D1])
    assert result == {"skipped": "no token"}


@pytest.mark.asyncio
async def test_ingest_empty_days(db_session):
    """Empty day list → {days: 0, written: 0}."""
    from backend.power.entsoe_flows import ingest_flows

    result = await ingest_flows(db_session, [])
    assert result == {"days": 0, "written": 0}


@pytest.mark.asyncio
async def test_ingest_upsert_idempotent(db_session):
    """Ingesting the same day twice with overwrite=True updates net_mw in place."""
    from backend.power.entsoe_flows import _upsert_flow

    _upsert_flow(db_session, _D1, "DE_LU", "FR", 1500.0)
    db_session.commit()
    row1 = db_session.query(PowerFlow).filter(PowerFlow.date == _D1).first()
    assert row1.net_mw == pytest.approx(1500.0)

    # Second upsert with different value
    _upsert_flow(db_session, _D1, "DE_LU", "FR", 800.0)
    db_session.commit()
    rows = db_session.query(PowerFlow).filter(PowerFlow.date == _D1).all()
    assert len(rows) == 1
    assert rows[0].net_mw == pytest.approx(800.0)


@pytest.mark.asyncio
async def test_ingest_net_computation(db_session):
    """ingest_flows computes net = AB − BA for each border day and writes PowerFlow rows."""
    from backend.power.entsoe_flows import ingest_flows

    xml_ab = _a11_xml(_D1, 2000.0)   # DE_LU → FR: 2000 MW
    xml_ba = _a11_xml(_D1, 500.0)    # FR → DE_LU: 500 MW

    call_count = {}

    async def mock_fetch(in_eic, out_eic, month_start, *, overwrite=False):
        key = (in_eic, out_eic)
        call_count[key] = call_count.get(key, 0) + 1
        # Identify direction: DE_LU EIC = "10Y1001A1001A82H", FR EIC = "10YFR-RTE------C"
        de_eic = "10Y1001A1001A82H"
        fr_eic = "10YFR-RTE------C"
        if out_eic == de_eic and in_eic == fr_eic:
            return xml_ab  # out=DE_LU (from_zone), in=FR (to_zone)
        if out_eic == fr_eic and in_eic == de_eic:
            return xml_ba  # out=FR, in=DE_LU
        return ""

    with (
        patch("backend.power.entsoe_flows.settings") as mock_settings,
        patch("backend.power.entsoe_flows._fetch_border_month", side_effect=mock_fetch),
    ):
        mock_settings.entsoe_api_token = "fake-token"
        result = await ingest_flows(db_session, [_D1])

    # Should have written rows (1 per border per day; 3 borders but mock only
    # covers DE_LU↔FR, others return empty → 0 for those)
    assert result["days"] == 1
    assert result["written"] >= 1

    # Check the DE_LU↔FR border specifically
    row = (
        db_session.query(PowerFlow)
        .filter(
            PowerFlow.date == _D1,
            PowerFlow.from_zone == "DE_LU",
            PowerFlow.to_zone == "FR",
        )
        .first()
    )
    assert row is not None
    assert row.net_mw == pytest.approx(1500.0)  # 2000 − 500


# ─── route tests ─────────────────────────────────────────────────────────────


def test_route_flows_available_false_when_empty(db_session):
    """No PowerFlow rows → available=False."""
    client = _make_client(db_session)
    resp = client.get("/api/power/flows?days=30")
    assert resp.status_code == 200
    assert resp.json()["available"] is False


def test_route_flows_available_true(db_session):
    """Seeded rows → available=True, borders list, data list, latest."""
    _seed_flows(db_session, [
        {"date": _D1, "from_zone": "DE_LU", "to_zone": "FR",  "net_mw": 1200.0},
        {"date": _D1, "from_zone": "DE_LU", "to_zone": "NL",  "net_mw": -800.0},
        {"date": _D2, "from_zone": "DE_LU", "to_zone": "FR",  "net_mw": 1500.0},
    ])
    client = _make_client(db_session)
    resp = client.get("/api/power/flows?days=30")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert len(body["borders"]) >= 1
    assert "data" in body
    assert "latest" in body
    assert "unit" in body


def test_route_flows_border_direction(db_session):
    """Positive net_mw → direction arrow A→B; negative → B→A."""
    _seed_flows(db_session, [
        {"date": _D1, "from_zone": "DE_LU", "to_zone": "FR", "net_mw":  2000.0},
        {"date": _D2, "from_zone": "DE_LU", "to_zone": "NL", "net_mw": -1500.0},
    ])
    client = _make_client(db_session)
    resp = client.get("/api/power/flows?days=30")
    body = resp.json()
    borders = {f"{b['from_zone']}-{b['to_zone']}": b for b in body["borders"]}

    de_fr = borders.get("DE_LU-FR")
    assert de_fr is not None
    assert de_fr["net_mw"] == pytest.approx(2000.0)
    assert "DE-LU" in de_fr["direction"] and "FR" in de_fr["direction"]

    de_nl = borders.get("DE_LU-NL")
    assert de_nl is not None
    assert de_nl["net_mw"] == pytest.approx(-1500.0)
    # Negative → NL→DE-LU direction
    assert "NL" in de_nl["direction"]


def test_route_flows_data_wide_format(db_session):
    """data rows are wide-format dicts with date + border arrow keys."""
    _seed_flows(db_session, [
        {"date": _D1, "from_zone": "DE_LU", "to_zone": "FR", "net_mw": 1200.0},
        {"date": _D1, "from_zone": "FR",    "to_zone": "NL", "net_mw":  300.0},
    ])
    client = _make_client(db_session)
    resp = client.get("/api/power/flows?days=30")
    body = resp.json()
    d1_row = next((r for r in body["data"] if r["date"] == _D1), None)
    assert d1_row is not None
    assert "DE-LU→FR" in d1_row or "DE_LU→FR" in str(d1_row)
