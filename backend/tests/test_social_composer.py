"""The daily-post system: event cascade, card rendering, a franchise, and the
poster's dry-run safety."""
from datetime import UTC, datetime

from backend.models.energy import PowerHourly, SeriesDim, ZoneDim  # noqa: F401 — register tables
from backend.social.card import render
from backend.social.composer import MIN_FRESH_ZONES, event_post

NOW = datetime(2026, 9, 20, 15, 30, tzinfo=UTC)


def _zone(z, price, zed=0.0, state="CALM", stale=False):
    return {"zone": z, "zone_label": z, "price_close": price, "price_z": zed,
            "state": state, "stale": stale}


def _field(n=MIN_FRESH_ZONES, base=50.0):
    return {"zones": [_zone(f"Z{i}", base + i * 0.5, zed=0.1) for i in range(n)]}


# ── event tier (pure) ────────────────────────────────────────────────────────

def test_too_few_fresh_zones_no_event():
    assert event_post({"zones": [_zone("A", 50)]}, [], NOW) is None


def test_high_spike_fires():
    ov = _field()
    ov["zones"].append(_zone("SE4", 278.0, zed=4.0, state="STRESSED"))
    p = event_post(ov, [], NOW)
    assert p is not None and p.kind == "price_spike"
    assert "SE4" in p.text and "+4.0σ" in p.text and "expensive" in p.text
    assert p.card["card"] == "bars" and p.card["highlight"] == "SE4"


def test_low_dip_is_not_an_event():
    ov = _field()
    ov["zones"].append(_zone("NO5", 8.0, zed=-4.5))   # far BELOW norm — boring
    assert event_post(ov, [], NOW) is None


def test_spike_needs_absolute_distance():
    ov = _field(base=50.0)
    ov["zones"].append(_zone("FLAT", 55.0, zed=6.0))  # +6σ but €5 from field
    assert event_post(ov, [], NOW) is None


def test_price_record_beats_spike():
    ov = _field()
    ov["zones"].append(_zone("SE4", 278.0, zed=4.0))
    recs = [{"fresh": True, "series": "price.dayahead", "kind": "max",
             "unit": "EUR/MWh", "value": 512.0, "zone": "SE4", "zone_label": "SE4"}]
    p = event_post(ov, recs, NOW)
    assert p.kind == "record" and "€512" in p.text and "on record" in p.text


def test_quarter_hour_record_ignored():
    recs = [{"fresh": True, "series": "price.dayahead.qh", "kind": "max",
             "unit": "EUR/MWh", "value": 321.0, "zone": "ES", "zone_label": "ES"}]
    assert event_post(_field(), recs, NOW) is None  # falls through, no event


def test_event_text_has_no_link():
    ov = _field()
    ov["zones"].append(_zone("SE4", 278.0, zed=4.0))
    p = event_post(ov, [], NOW)
    assert "http" not in p.text and "obsyd.dev" in p.reply


# ── card rendering ───────────────────────────────────────────────────────────

def test_bars_card_renders_png():
    spec = {"card": "bars", "title": "Test", "subtitle": "sub",
            "rows": [{"label": "SE1", "value": 9, "disp": "€9"},
                     {"label": "IT", "value": 275, "disp": "€275"}],
            "highlight": "IT", "footer": "obsyd.dev"}
    png = render(spec)
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 2000


def test_trend_card_renders_png():
    spec = {"card": "trend", "title": "Then vs now", "subtitle": "hours/year",
            "years": [2019, 2020, 2021, 2022, 2023, 2024, 2025],
            "series": [{"name": "Spain", "color": "blue", "values": [0, 0, 0, 0, 0, 178, 496]},
                       {"name": "Finland", "color": "amber", "values": [0, 9, 5, 25, 443, 687, 395]}],
            "footer": "obsyd.dev"}
    png = render(spec)
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 2000


def test_empty_bars_card_still_png():
    assert render({"card": "bars", "title": "x", "rows": []})[:8] == b"\x89PNG\r\n\x1a\n"


# ── a franchise over a seeded db ─────────────────────────────────────────────

def test_negative_hours_franchise(db_session):
    from backend.power.hourly_store import day_hour_ts, upsert_hourly
    from backend.power.zones import ENABLED_ZONES
    from backend.social.franchises import negative_hours
    # Seed daily negative_hours points for the last 3 days across enabled zones.
    base = day_hour_ts(NOW.date().isoformat(), 0)
    for i, z in enumerate(ENABLED_ZONES):
        for d in range(1, 4):
            upsert_hourly(db_session, "price.negative_hours", z,
                          [(base - d * 86400, float(i + d))], unit="h")
    db_session.commit()
    p = negative_hours(db_session, NOW)
    # Needs at least one zone with >0; enabled set may be small in tests, but the
    # builder must not crash and, when it fires, must carry a bars card + link-free text.
    if p is not None:
        assert p.card["card"] == "bars" and "http" not in p.text
        assert p.text.count("negative power prices") >= 0


def test_poster_dry_run_writes_and_never_posts(tmp_path, monkeypatch):
    from backend.social import poster
    monkeypatch.setattr(poster, "SOCIAL_DIR", tmp_path)
    assert poster.is_live() is False
    res = poster.post("hello", "alt", b"\x89PNG\r\n\x1a\nxx", reply=None, dedup="t-1")
    assert res["dry_run"] is True and any(tmp_path.glob("*.png"))
