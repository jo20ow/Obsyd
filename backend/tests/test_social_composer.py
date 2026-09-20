"""The daily-post composer: cascade order, gates, and template correctness."""
from __future__ import annotations

from datetime import date

from backend.social.composer import MIN_FRESH_ZONES, compose

TODAY = date(2026, 9, 20)


def _zone(z, price, zed=0.0, state="CALM", stale=False):
    return {"zone": z, "zone_label": z, "price_close": price, "price_z": zed,
            "state": state, "stale": stale}


def _overview(zones):
    return {"available": True, "zones": zones}


def _calm_field(n=MIN_FRESH_ZONES, base=50.0):
    # A spread of a few euro, no spikes — daily_map territory.
    return [_zone(f"Z{i}", base + i * 0.5, zed=0.2) for i in range(n)]


def test_too_few_fresh_zones_posts_nothing():
    assert compose(_overview(_calm_field(n=20)), today=TODAY) is None


def test_unavailable_overview_posts_nothing():
    assert compose({"available": False, "zones": []}, today=TODAY) is None


def test_daily_map_is_the_floor():
    post = compose(_overview(_calm_field()), today=TODAY)
    assert post is not None and post.kind == "daily_map"
    assert "spread" in post.text
    assert len(post.rows) == MIN_FRESH_ZONES and post.highlight is None
    assert post.rows == sorted(post.rows, key=lambda r: r["price"])  # cheapest first
    assert post.reply and "obsyd.dev" in post.reply
    assert "http" not in post.text  # link lives in the reply, never the body


def test_price_spike_beats_daily_map():
    zones = _calm_field()
    zones.append(_zone("SE4", 278.0, zed=4.0, state="STRESSED"))
    post = compose(_overview(zones), today=TODAY)
    assert post.kind == "price_spike"
    assert "SE4" in post.text and "+4.0σ" in post.text
    assert post.highlight == "SE4"  # the event zone is accented in the card
    assert "forecast" in post.text.lower()  # the honesty clause


def test_spike_needs_absolute_distance_not_just_sigma():
    # High sigma over a flat, near-median price → not a spike (micro-variance).
    zones = _calm_field(base=50.0)
    zones.append(_zone("FLAT", 51.0, zed=5.0))  # +5σ but €1 from the field
    post = compose(_overview(zones), today=TODAY)
    assert post.kind == "daily_map"


def test_record_beats_everything():
    records = [{"fresh": True, "kind": "max", "series": "price.dayahead",
                "label": "day-ahead price", "unit": "EUR/MWh", "value": 512.0,
                "zone_label": "SE4", "date": "2026-09-20"}]
    zones = _calm_field()
    zones.append(_zone("SE4", 278.0, zed=4.0, state="STRESSED"))
    post = compose(_overview(zones), records=records, today=TODAY)
    assert post.kind == "record"
    assert "highest" in post.text and "€512/MWh" in post.text
    assert post.highlight == "SE4"


def test_quarter_hour_record_never_claims_a_long_history():
    # A .qh max (15-min series, history only since 2025-10) must NOT surface as a
    # "record" — its extreme isn't the meaningful all-time high. Falls through.
    records = [{"fresh": True, "kind": "max", "series": "price.dayahead.qh",
                "label": "day-ahead price", "unit": "EUR/MWh", "value": 321.0,
                "zone_label": "ES", "date": "2026-09-14"}]
    post = compose(_overview(_calm_field()), records=records, today=TODAY)
    assert post.kind == "daily_map"


def test_stale_record_is_ignored():
    records = [{"fresh": False, "kind": "max", "series": "co2.intensity",
                "label": "CO2 intensity", "unit": "g", "value": 600.0,
                "zone_label": "DE-LU", "date": "2023-01-01"}]
    post = compose(_overview(_calm_field()), records=records, today=TODAY)
    assert post.kind == "daily_map"  # fell through to the floor


def test_dedup_key_is_date_and_kind():
    post = compose(_overview(_calm_field()), today=TODAY)
    assert post.dedup_key == "2026-09-20-daily_map"


# ── card render + poster dry-run ─────────────────────────────────────────────

def test_card_renders_png_bytes():
    from backend.social.card import render_card
    rows = [{"zone": "SE1", "price": 9.0, "state": "CALM"},
            {"zone": "DE-LU", "price": 80.0, "state": "ELEVATED"},
            {"zone": "IT-Sicilia", "price": 275.0, "state": "STRESSED"}]
    png = render_card("Test headline", rows, highlight="DE-LU")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"  # PNG magic
    assert len(png) > 2000


def test_card_handles_empty_rows():
    from backend.social.card import render_card
    png = render_card("Empty", [])
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_poster_dry_run_writes_files_and_never_posts(tmp_path, monkeypatch):
    from backend.social import poster
    monkeypatch.setattr(poster, "SOCIAL_DIR", tmp_path)
    # No keys configured in the test env → dry-run by construction.
    assert poster.is_live() is False
    res = poster.post("hello", "alt", b"\x89PNG\r\n\x1a\nxx", reply="link", dedup="t-1")
    assert res["dry_run"] is True
    assert (tmp_path / f"{res['path'].split('/')[-1]}.png").exists() or \
        any(tmp_path.glob("*.png"))
    assert any(tmp_path.glob("*.json"))
