"""Embeddable SVG badges: GET /api/v1/badge/{zone}/{metric}.svg (Task P10).

Covers: content-type/cache headers, the shared v1 rate-limit dependency, price/load
happy paths, unknown zone/metric and no-data degrading to a grey 200 (never a broken
image in a README), well-formed SVG (parses as XML), and zone-label correctness.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from backend.api_guard import _reset_coverage_cache
from backend.auth.ratelimit import reset_limits
from backend.database import get_db
from backend.main import app
from backend.models.energy import PowerHourly, PowerPriceDaily, SeriesDim, ZoneDim  # noqa: F401
from backend.power.hourly_store import upsert_hourly

_BASE = int(datetime(2026, 6, 1, tzinfo=UTC).timestamp())
_H = 3600


@pytest.fixture(autouse=True)
def _isolate():
    reset_limits()
    _reset_coverage_cache()
    yield
    app.dependency_overrides.clear()
    reset_limits()
    _reset_coverage_cache()


def _client(db):
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def _seed_price(db, zone="DE_LU", date="2026-06-01", mean=87.4):
    db.add(PowerPriceDaily(
        date=date, zone=zone, mean_price=mean, min_price=mean - 10,
        max_price=mean + 10, negative_hours=0,
    ))
    db.commit()


def _seed_load(db, zone="DE_LU", n=6):
    # The badge route only looks back 6h from "now" (a live-status read, not a
    # historical one) — seed near the current hour, not the fixed 2026-06-01 base
    # the other v1 tests use for their (unrelated) window-math fixtures.
    now_hour = int(datetime.now(UTC).timestamp()) // _H * _H
    start = now_hour - (n - 1) * _H
    upsert_hourly(db, "load.actual", zone,
                  [(start + i * _H, 54_200.0 + i * 10) for i in range(n)], unit="MW")


def _parse_svg(text: str) -> ET.Element:
    return ET.fromstring(text)


def test_price_badge_happy_path(db_session):
    _seed_price(db_session)
    resp = _client(db_session).get("/api/v1/badge/DE_LU/price.svg")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/svg+xml")
    assert resp.headers["cache-control"] == "public, max-age=900"
    root = _parse_svg(resp.text)
    assert root.tag.endswith("svg")
    text_el = root.find("{http://www.w3.org/2000/svg}text")
    assert text_el is not None
    assert "DE-LU" in text_el.text
    assert "day-ahead" in text_el.text
    assert "87" in text_el.text
    assert "2026-06-01" in text_el.text


def test_load_badge_happy_path(db_session):
    _seed_load(db_session)
    resp = _client(db_session).get("/api/v1/badge/DE_LU/load.svg")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/svg+xml")
    root = _parse_svg(resp.text)
    text_el = root.find("{http://www.w3.org/2000/svg}text")
    assert "DE-LU" in text_el.text
    assert "load" in text_el.text
    assert "GW" in text_el.text
    assert "UTC" in text_el.text


def test_load_badge_uses_latest_point(db_session):
    _seed_load(db_session, n=6)
    resp = _client(db_session).get("/api/v1/badge/DE_LU/load.svg")
    root = _parse_svg(resp.text)
    text_el = root.find("{http://www.w3.org/2000/svg}text")
    # last point: 54_200 + 5*10 = 54_250 -> 54.25 GW rounds to 54.2 or 54.3
    assert "54.2" in text_el.text or "54.3" in text_el.text


def test_zone_label_correctness_for_fr(db_session):
    _seed_price(db_session, zone="FR", date="2026-06-02", mean=101.0)
    resp = _client(db_session).get("/api/v1/badge/FR/price.svg")
    root = _parse_svg(resp.text)
    text_el = root.find("{http://www.w3.org/2000/svg}text")
    assert text_el.text.startswith("FR ")
    assert "101" in text_el.text


def test_unknown_zone_returns_grey_200(db_session):
    resp = _client(db_session).get("/api/v1/badge/NOPE/price.svg")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/svg+xml")
    root = _parse_svg(resp.text)
    fill = root.find("{http://www.w3.org/2000/svg}rect").get("fill")
    assert fill == "#1a1c22"  # the grey "no data" background, not the ok-badge dark
    text_el = root.find("{http://www.w3.org/2000/svg}text")
    assert "no data" in text_el.text


def test_unknown_zone_quote_injection_is_neutralized(db_session):
    """Regression for a reflected-XSS finding: an unknown zone reaches `_no_data_badge`
    with the RAW URL path segment as `text`, which `_badge_svg` places both in an
    element body (<title>/<text>, safe with plain & < > escaping) and in an ATTRIBUTE
    value (`aria-label="..."`). A `"` in the zone must not be able to close that
    attribute early and inject a new one (e.g. `onload=`) — `_badge_svg` must escape
    quotes specifically for the attribute sink."""
    # No "/" in the payload — a URL-encoded %2F is decoded back to a literal "/" by
    # the ASGI path before routing, which would split it into extra path segments
    # (a routing quirk, not part of what this regression is about) rather than
    # reach the handler as a single {zone} value.
    payload = 'x" onload="alert(1)'
    resp = _client(db_session).get(f"/api/v1/badge/{quote(payload, safe='')}/price.svg")
    assert resp.status_code == 200
    root = _parse_svg(resp.text)  # raises ET.ParseError if the injected attribute broke the XML
    assert root.tag.endswith("svg")
    # No element anywhere in the document may have picked up an "onload" attribute —
    # the whole point of the injection would be a real, parseable onload="...".
    for el in root.iter():
        assert "onload" not in el.attrib
    aria_label = root.get("aria-label")
    assert aria_label is not None
    assert '"' in aria_label  # the literal quote survived — safely, as &quot; on the wire
    assert "onload" in aria_label  # present as inert TEXT inside the attribute value, not a new attribute


def test_unknown_metric_returns_grey_200(db_session):
    _seed_price(db_session)
    resp = _client(db_session).get("/api/v1/badge/DE_LU/genmix.svg")
    assert resp.status_code == 200
    root = _parse_svg(resp.text)
    text_el = root.find("{http://www.w3.org/2000/svg}text")
    assert "no data" in text_el.text


def test_no_data_yet_returns_grey_200(db_session):
    # Known zone, known metric, but nothing seeded — must still be a 200 grey pill.
    resp = _client(db_session).get("/api/v1/badge/DE_LU/price.svg")
    assert resp.status_code == 200
    root = _parse_svg(resp.text)
    fill = root.find("{http://www.w3.org/2000/svg}rect").get("fill")
    assert fill == "#1a1c22"
    text_el = root.find("{http://www.w3.org/2000/svg}text")
    assert "no data" in text_el.text


def test_svg_is_well_formed_for_ok_and_no_data_badges(db_session):
    _seed_price(db_session)
    for url in (
        "/api/v1/badge/DE_LU/price.svg",
        "/api/v1/badge/DE_LU/load.svg",       # no load data -> grey
        "/api/v1/badge/NOPE/price.svg",       # unknown zone -> grey
        "/api/v1/badge/DE_LU/bogus.svg",      # unknown metric -> grey
    ):
        resp = _client(db_session).get(url)
        assert resp.status_code == 200
        root = _parse_svg(resp.text)  # raises if malformed
        assert root.tag.endswith("svg")
        title_el = root.find("{http://www.w3.org/2000/svg}title")
        assert title_el is not None and title_el.text


def test_rate_limit_dependency_present(db_session, monkeypatch):
    """Badges share the v1 per-IP budget (backend.routes.api_v1._rate_limit) —
    patching its RATE_PER_MIN throttles the badge route exactly like /series."""
    import backend.routes.api_v1 as v1

    monkeypatch.setattr(v1, "RATE_PER_MIN", 2)
    _seed_price(db_session)
    c = _client(db_session)
    url = "/api/v1/badge/DE_LU/price.svg"
    assert c.get(url).status_code == 200
    assert c.get(url).status_code == 200
    assert c.get(url).status_code == 429  # third within the window


def test_price_badge_attribution_in_title(db_session):
    _seed_price(db_session)
    resp = _client(db_session).get("/api/v1/badge/DE_LU/price.svg")
    root = _parse_svg(resp.text)
    title_el = root.find("{http://www.w3.org/2000/svg}title")
    assert "ENTSO-E" in title_el.text
