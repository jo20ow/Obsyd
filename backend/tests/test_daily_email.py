"""
Tests for the personalised daily-brief watch block (Slice 3).

`_build_watch_block` renders a per-user "YOUR WATCH" fragment from the user's
watchlist: supply concentration for watched materials, latest radar anomaly for
watched zones. Empty string when the user watches nothing (global brief intact).
"""

from __future__ import annotations

from backend.models.alerts import Alert
from backend.models.atlas import CountryResource
from backend.models.watchlist import WatchlistItem
from backend.notifications.daily_email import _build_watch_block


def test_watch_block_empty_for_no_items(db_session):
    assert _build_watch_block(db_session, "nobody@obsyd.dev") == ""


def test_watch_block_material(db_session):
    email = "w@obsyd.dev"
    db_session.add_all(
        [
            CountryResource(
                iso3="COD", country_name="Congo", commodity="cobalt",
                period="2024", value=220000.0, unit="t",
            ),
            CountryResource(
                iso3="IDN", country_name="Indonesia", commodity="cobalt",
                period="2024", value=28000.0, unit="t",
            ),
            WatchlistItem(email=email, kind="material", key="cobalt", label="Cobalt"),
        ]
    )
    db_session.commit()

    html = _build_watch_block(db_session, email)
    assert "YOUR WATCH" in html
    assert "Cobalt" in html
    assert "COD" in html  # top producer
    assert "HHI" in html


def test_watch_block_zone(db_session):
    email = "z@obsyd.dev"
    db_session.add_all(
        [
            Alert(
                rule="chokepoint_anomaly", zone="hormuz", vertical="oil",
                severity="critical", title="Strait of Hormuz: -96% drop", detail="x",
            ),
            WatchlistItem(email=email, kind="zone", key="hormuz", label="Strait of Hormuz"),
        ]
    )
    db_session.commit()

    html = _build_watch_block(db_session, email)
    assert "Strait of Hormuz" in html
    assert "-96% drop" in html


def test_watch_block_zone_no_anomaly(db_session):
    email = "z2@obsyd.dev"
    db_session.add(WatchlistItem(email=email, kind="zone", key="malacca", label="Strait of Malacca"))
    db_session.commit()

    html = _build_watch_block(db_session, email)
    assert "Strait of Malacca" in html
    assert "no anomaly flagged" in html
