"""The data-dictionary layer (backend/power/series_meta.py): every catalogued
series family must carry its full contract — description, source, cadence,
licence — and the catalog endpoint must serve it. The completeness sweep is
the lückenlos rule made mechanical: add a series without its contract and CI
fails here."""
from __future__ import annotations

from backend.power.series_catalog import SERIES_LABELS
from backend.power.series_meta import series_meta

#: One representative per pattern family (the static keys are swept in full).
FAMILY_EXAMPLES = [
    "gen.B04", "gen.B16", "consumption.B10",
    "flow.FR", "sched.DK1", "ntc.CH",
    "capture.B16.price", "capture.B16.factor", "capture.B16.price_floor0",
    "conv.full.FR", "conv.low.FR", "conv.hours.FR", "conv.spread.FR",
    "balancing.afrr.price.up", "balancing.mfrr.vol.down",
    "capacity.fcr.price", "capacity.afrr.price.pos",
]


def test_every_catalogued_series_carries_its_contract():
    missing = []
    for key in list(SERIES_LABELS) + FAMILY_EXAMPLES:
        m = series_meta(key)
        for field in ("description", "source", "cadence", "licence"):
            if not m.get(field) or "No description recorded" in str(m.get(field)):
                missing.append(f"{key}.{field}")
        assert "caveat" in m, key
    assert not missing, f"series without a full metadata contract: {missing}"


def test_unknown_key_gets_honest_placeholders_not_a_raise():
    m = series_meta("totally.unknown.series")
    assert "No description recorded" in m["description"]


def test_meta_sources_use_the_shared_spellings():
    """One spelling per source/licence everywhere — the catalog must not grow
    three variants of the ENTSO-E attribution."""
    seen_licences = {series_meta(k)["licence"] for k in list(SERIES_LABELS) + FAMILY_EXAMPLES}
    assert len(seen_licences) <= 6, seen_licences


def test_catalog_endpoint_serves_the_contract(db_session):
    import pytest
    from fastapi.testclient import TestClient

    from backend.database import get_db
    from backend.main import app
    from backend.power.hourly_store import upsert_hourly

    upsert_hourly(db_session, "price.dayahead", "DE_LU", [(1_700_000_400, 50.0)],
                  unit="EUR/MWh")
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        body = TestClient(app).get("/api/v1/series/catalog").json()
    finally:
        app.dependency_overrides.clear()
    entry = next(s for s in body["series"] if s["key"] == "price.dayahead")
    assert entry["description"].startswith("Day-ahead auction clearing price")
    assert "ENTSO-E" in entry["source"]
    assert entry["cadence"] and entry["licence"]
    assert "caveat" in entry
    _ = pytest
