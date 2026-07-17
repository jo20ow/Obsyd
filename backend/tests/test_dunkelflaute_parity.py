"""The hero said "Dunkelflaute flagged in AT, PT, CZ". The radar, two tabs away, said PT, ES, HU.

Same day, same database, one word — two answers. The radar's answer is the calibrated one
(backend/power/dunkelflaute.py): a zone must HAVE a wind/solar fleet worth the word, and today
must be in the bottom 2% of that zone's own same-month history. The desk — /grid, /overview and
the situation hero — never got that fix and kept asking the flat question the radar was cured of:
is wind+solar under 15% of load? On prod that made the front door flag thirteen zones, five of
them Norwegian hydro, which is not a Dunkelflaute; it is a description of their fleet.

One predicate. These tests pin the desk to the radar's, and vice versa.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.models.energy import PowerGenMix, PowerGrid

# UTC, not local — the episode/radar code buckets on datetime.utcnow().date()
# (same fix as test_power_situation.py).
TODAY = datetime.now(timezone.utc).date()

# The predicate is data-driven, not name-driven: a zone is "hydro" to it because its record says
# so, not because it is called NO5. The test env enables DE_LU/FR/NL, so the fleets are seeded
# onto those three — FR plays the hydro zone (no wind, no solar, ever), which is what NO5 IS.
FLEET = "DE_LU"      # median 40% renewable share — a Dunkelflaute is a thing that can happen here
NO_FLEET = "FR"      # 0% every day of its life — the flat test flagged it every day of its life
ORDINARY = "NL"      # a fleet, having a perfectly normal day


@pytest.fixture(autouse=True)
def _clear_dependency_overrides():
    yield
    from backend.main import app

    app.dependency_overrides.clear()


def _make_client(db: Session) -> TestClient:
    from backend.database import get_db
    from backend.main import app

    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app, raise_server_exceptions=True)


def _seed(
    db: Session,
    zone: str,
    shares: list[float],
    *,
    dates: list[str],
    load: float = 50_000.0,
    coverage: bool = True,
    gen_hours: int = 24,
) -> None:
    """One grid day per share, with matching A75 generation so the coverage guard is satisfied.

    Without generation rows the renewable share is untrusted (fail safe) and nothing can be
    flagged — that guard is the detector's, and the desk must honour it too.
    """
    for day, share in zip(dates, shares, strict=True):
        db.add(PowerGrid(date=day, zone=zone, load_mw=load,
                         wind_mw=load * share * 0.6, solar_mw=load * share * 0.4,
                         load_hours=24, gen_hours=gen_hours))
        if coverage:
            # Reported generation ≈ load → coverage ratio 1.0, comfortably over the 0.6 floor.
            db.add(PowerGenMix(date=day, zone=zone, psr_type="Fossil Gas", gen_mw=load))
    db.commit()


def _same_month_history(n: int) -> list[str]:
    """`n` dates in TODAY's calendar month, walking back through previous years.

    The month tail is measured per calendar month, so history for "this month" has to BE in this
    month — in some other year is exactly how a real record looks in July.
    """
    out: list[str] = []
    year = TODAY.year
    while len(out) < n:
        for dom in range(1, 29):
            d = date(year, TODAY.month, dom)
            if d >= TODAY:          # nothing from the future, and never today itself
                continue
            out.append(d.isoformat())
            if len(out) == n:
                break
        year -= 1
    return out


def _dark_day() -> str:
    return TODAY.isoformat()


def test_a_zone_without_a_fleet_is_never_in_a_dunkelflaute_on_the_desk(db_session):
    """A hydro zone has no wind and no solar. Its renewable share is 0% every day of its life, so
    the flat test flagged it every day of its life — 100% of the record. /grid must stay silent."""
    hist = _same_month_history(70)
    _seed(db_session, NO_FLEET, [0.0] * len(hist), dates=hist)
    _seed(db_session, NO_FLEET, [0.0], dates=[_dark_day()])

    body = _make_client(db_session).get(f"/api/power/grid?zone={NO_FLEET}&days=120").json()

    assert body["available"] is True
    assert body["latest"]["dunkelflaute"] is False, "its normal is not its emergency"
    assert body["dunkelflaute_days"] == 0


def test_a_real_dunkelflaute_still_fires(db_session):
    """The fix must not simply switch the flag off: DE-LU has a fleet (median 40%), and a day at
    5% of load is both unusual for its own month and dark in absolute terms."""
    hist = _same_month_history(70)
    _seed(db_session, FLEET, [0.40] * len(hist), dates=hist)
    _seed(db_session, FLEET, [0.05], dates=[_dark_day()])

    body = _make_client(db_session).get(f"/api/power/grid?zone={FLEET}&days=120").json()

    assert body["latest"]["dunkelflaute"] is True
    assert body["dunkelflaute_days"] == 1


def test_an_unusually_dark_day_without_generation_coverage_is_not_claimed(db_session):
    """The coverage guard, honoured by the radar since NL's A75 was broken: a zone whose reported
    generation cannot account for its load has no trustworthy renewable share, so no claim."""
    hist = _same_month_history(70)
    _seed(db_session, FLEET, [0.40] * len(hist), dates=hist)
    _seed(db_session, FLEET, [0.05], dates=[_dark_day()], coverage=False)

    body = _make_client(db_session).get(f"/api/power/grid?zone={FLEET}&days=120").json()

    assert body["latest"]["dunkelflaute"] is False


def test_the_desk_and_the_radar_cannot_disagree(db_session):
    """The parity that was missing. One seeded record, two surfaces: the zones the radar flags
    and the zones /api/power/overview flags must be the same set."""
    from backend.signals.detectors.power import detect_dunkelflaute

    hist = _same_month_history(70)
    # A real fleet having a genuinely dark day.
    _seed(db_session, FLEET, [0.40] * len(hist), dates=hist)
    _seed(db_session, FLEET, [0.05], dates=[_dark_day()])
    # Hydro. Dark by construction, every day, forever.
    _seed(db_session, NO_FLEET, [0.0] * len(hist), dates=hist)
    _seed(db_session, NO_FLEET, [0.0], dates=[_dark_day()])
    # A fleet, but an ordinary day.
    _seed(db_session, ORDINARY, [0.30] * len(hist), dates=hist)
    _seed(db_session, ORDINARY, [0.28], dates=[_dark_day()])

    radar = {r.zone for r in detect_dunkelflaute(db_session)}
    overview = _make_client(db_session).get("/api/power/overview").json()
    desk = {z["zone"] for z in overview["zones"] if z["dunkelflaute"]}

    assert radar == {FLEET}
    assert desk == radar, "the front door must not tell a story the radar knows is false"


def test_the_situation_hero_agrees_with_the_radar(db_session):
    """The hero flag is what the EUROPE narrative reads ("Dunkelflaute flagged in AT, PT, CZ")."""
    hist = _same_month_history(70)
    _seed(db_session, NO_FLEET, [0.0] * len(hist), dates=hist)
    _seed(db_session, NO_FLEET, [0.0], dates=[_dark_day()])

    body = _make_client(db_session).get(f"/api/power/situation?zone={NO_FLEET}").json()

    assert body["grid"]["dunkelflaute"] is False
    assert [f for f in body["flags"] if f["key"] == "dunkelflaute"] == []


def test_a_day_with_a_hole_in_its_generation_feed_is_not_judged(db_session):
    """A settled day can still be a broken one. If a zone's generation feed stops for six hours,
    those hours are unaccounted for — and a daily mean that reads them as zeros manufactures a
    dark day out of an outage. Both surfaces must stay silent, not agree on a fiction."""
    from backend.signals.detectors.power import detect_dunkelflaute

    hist = _same_month_history(70)
    _seed(db_session, FLEET, [0.40] * len(hist), dates=hist)
    _seed(db_session, FLEET, [0.05], dates=[_dark_day()], gen_hours=18)

    body = _make_client(db_session).get(f"/api/power/grid?zone={FLEET}&days=120").json()

    assert body["latest"]["dunkelflaute"] is False
    assert body["latest"]["gen_hours"] == 18, "the hole is visible in the row, not hidden"
    assert {r.zone for r in detect_dunkelflaute(db_session)} == set(), "the radar keeps quiet too"
