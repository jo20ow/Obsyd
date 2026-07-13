"""An episode is an object. The radar only ever saw today.

And it could not have been taught otherwise from what it stored: `_upsert_alert` mutates the
Alert row in place, slides created_at forward and DELETES older duplicates, so a five-day
Dunkelflaute collapses into one row that claims nothing about duration. The history was never
written — episodes must be re-derived from the canonical series, exactly as records are.

The tests that matter here are the GUARD tests. An episode grouper's worst failure is not
missing an event; it is celebrating an outage of our own collector as the longest Dunkelflaute
in the record.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from backend.models.energy import PowerEpisode, PowerGenMix, PowerGrid, PowerPriceDaily
from backend.power.episodes import (
    MAX_GAP_DAYS,
    MIN_HISTORY_DAYS,
    compute_episodes,
    group_runs,
    zone_episodes,
)

TODAY = date(2026, 6, 30)


def _days(start: str, n: int) -> list[str]:
    d = date.fromisoformat(start)
    return [(d + timedelta(days=i)).isoformat() for i in range(n)]


def _lower_is_worse(a, b):
    return a < b


# ─── grouping ─────────────────────────────────────────────────────────────────


def test_a_five_day_run_is_ONE_episode_not_five_alerts():
    """The whole point. Today's alert table would show a single mutated row claiming nothing
    about duration; here it is one object that knows it lasted five days."""
    points = [(d, 0.05) for d in _days("2026-01-01", 5)]
    runs = group_runs(points, lambda _d, v: v < 0.15, deeper=_lower_is_worse)

    assert len(runs) == 1
    assert runs[0].days == 5
    assert runs[0].start == "2026-01-01" and runs[0].end == "2026-01-05"


def test_a_one_day_hole_does_not_split_a_run():
    """Feeds have holes. A run that a single missing day cuts in half is an artefact of our
    collection, not of the weather."""
    points = [(d, 0.05) for d in _days("2026-01-01", 5)]
    del points[2]                                   # 2026-01-03 simply absent

    runs = group_runs(points, lambda _d, v: v < 0.15, deeper=_lower_is_worse)

    assert len(runs) == 1
    assert runs[0].days == 5, "the SPAN, counted honestly across the bridged hole"
    assert runs[0].populated == pytest.approx(4 / 5)


def test_a_hole_longer_than_the_tolerance_ends_the_run():
    """A three-day hole is not a gap in an episode. It is two episodes with a gap between them,
    and pretending otherwise would manufacture the longest Dunkelflaute in the record."""
    points = ([(d, 0.05) for d in _days("2026-01-01", 3)]
              + [(d, 0.05) for d in _days("2026-01-07", 3)])

    runs = group_runs(points, lambda _d, v: v < 0.15, deeper=_lower_is_worse)

    assert len(runs) == 2
    assert MAX_GAP_DAYS == 1


def test_the_depth_points_at_the_day_it_was_worst():
    """records.py's discipline: an extreme without its evidence is an assertion."""
    points = list(zip(_days("2026-01-01", 4), [0.10, 0.03, 0.08, 0.12]))
    run = group_runs(points, lambda _d, v: v < 0.15, deeper=_lower_is_worse)[0]

    assert run.depth == 0.03
    assert run.depth_date == "2026-01-02"


def test_a_single_qualifying_day_is_a_day_not_an_episode():
    points = [("2026-01-01", 0.05), ("2026-01-02", 0.50)]
    assert group_runs(points, lambda _d, v: v < 0.15, deeper=_lower_is_worse) == []


# ─── the guard: the test this module lives or dies by ─────────────────────────


def _seed_grid(db, zone, days, share, *, coverage=1.0, load=60_000.0):
    """PowerGrid + a matching generation mix. `coverage` scales the reported generation."""
    for d in days:
        db.add(PowerGrid(date=d, zone=zone, load_mw=load,
                         wind_mw=load * share * 0.6, solar_mw=load * share * 0.4))
        db.add(PowerGenMix(date=d, zone=zone, psr_type="Fossil Gas",
                           gen_mw=load * coverage))


def _january_history(exclude: list[str]) -> list[str]:
    """Six Januaries of normal days — the same-month record the tail is measured against.

    The LENGTH is load-bearing, and for a reason worth knowing: an episode's own days are part
    of the record it is judged against. With 79 days of history, the 2nd-percentile rank falls
    INSIDE a four-day event, and the event cannot be below its own cutoff — it self-suppresses.
    With six Januaries (186 days) the cutoff lands on a historical day, which is the regime real
    data is in (DE-LU has 230 January days on record).

    The property is real, not a fixture artefact: a run that is very long RELATIVE TO ITS OWN
    RECORD is reported truncated to its darkest days. That is what "the bottom 2%" means, and it
    is the honest reading — but it is worth knowing before someone reports a bug.

    Excluding the event window also matters: seeding a day twice is not a fixture, it is a
    UNIQUE constraint waiting to fire.
    """
    days = [d for year in range(2020, 2026) for d in _days(f"{year}-01-01", 31)]
    days += _days("2026-01-20", 12)
    return [d for d in days if d not in exclude]


def test_a_collector_outage_is_not_a_dunkelflaute(db_session):
    """THE guard test. When a zone's A75 feed drops, wind reads 0 and solar reads 0 — and the
    renewable share reads 0. Without the coverage guard the engine finds its longest Dunkelflaute
    ever inside an outage of our own collector.

    The fixture MUST seed a coverage failure, not a genuinely dark period. A fixture that seeds
    real low wind cannot express this bug at all."""
    outage = _days("2026-01-05", 10)
    _seed_grid(db_session, "DE_LU", _january_history(outage), 0.40)  # a real fleet, a real record

    # Ten days where generation reporting collapses to a fifth of load: share reads ~0.
    _seed_grid(db_session, "DE_LU", outage, 0.0, coverage=0.2)
    db_session.commit()

    compute_episodes(db_session, today=TODAY)

    episodes = db_session.query(PowerEpisode).filter_by(kind="dunkelflaute").all()
    assert episodes == [], "a broken feed is not weather"


def test_a_genuine_dunkelflaute_survives_the_guard(db_session):
    """The guard must not be a mute button: the same shape, with the generation reporting
    intact, has to come through."""
    event = _days("2026-01-05", 4)
    _seed_grid(db_session, "DE_LU", _january_history(event), 0.40)
    _seed_grid(db_session, "DE_LU", event, 0.03)   # dark, but fully reported
    db_session.commit()

    compute_episodes(db_session, today=TODAY)

    episodes = db_session.query(PowerEpisode).filter_by(kind="dunkelflaute").all()
    assert len(episodes) == 1
    assert episodes[0].duration_days == 4


# ─── the recompute ────────────────────────────────────────────────────────────


def test_the_recompute_is_idempotent(db_session):
    event = _days("2026-01-05", 3)
    _seed_grid(db_session, "DE_LU", _january_history(event), 0.40)
    _seed_grid(db_session, "DE_LU", event, 0.03)
    db_session.commit()

    first = compute_episodes(db_session, today=TODAY)
    second = compute_episodes(db_session, today=TODAY)

    assert first["dunkelflaute"] == second["dunkelflaute"] == 1
    assert db_session.query(PowerEpisode).count() == 1


def test_an_episode_the_data_no_longer_supports_is_RETRACTED(db_session):
    """A full recompute must also un-say things. Without the retraction the archive fills up
    with episodes a later revision of the data no longer supports — and they would be ranked
    against, forever."""
    db_session.add(PowerEpisode(
        kind="dunkelflaute", zone="DE_LU", start_date="2019-01-01", end_date="2019-01-09",
        duration_days=9, depth=0.01, depth_date="2019-01-04", mean_value=0.02,
        status="resolved",
    ))
    db_session.commit()

    out = compute_episodes(db_session, today=TODAY)

    assert out["removed"] == 1
    assert db_session.query(PowerEpisode).count() == 0


# ─── reading them back ────────────────────────────────────────────────────────


def _seed_negative_run(db, zone="DE_LU", *, history_days=800):
    for d in _days("2024-01-01", history_days):
        db.add(PowerPriceDaily(date=d, zone=zone, mean_price=60.0, min_price=10.0,
                               max_price=90.0, negative_hours=0))
    for d in _days("2026-06-27", 4):        # runs up to TODAY − 1 → active
        db.add(PowerPriceDaily(date=d, zone=zone, mean_price=5.0, min_price=-20.0,
                               max_price=30.0, negative_hours=8))
    db.commit()


def test_the_running_episode_is_ranked_against_the_zones_own_record(db_session):
    """The sentence the radar could never say: not "there is a run", but "and it is the longest
    one we have"."""
    _seed_negative_run(db_session)
    compute_episodes(db_session, today=TODAY)

    out = zone_episodes(db_session, "DE_LU", "negative_prices")

    assert out["available"] is True
    assert out["active"]["duration_days"] == 4
    assert out["rank"]["position"] == 1
    assert out["rank"]["longest_days"] == 4


def test_a_rank_is_withheld_below_a_year_of_history(db_session):
    """A rank over three months of data is a statement about our coverage, not about the grid —
    records.py's rule, and the same number."""
    _seed_negative_run(db_session, history_days=100)
    compute_episodes(db_session, today=TODAY)

    out = zone_episodes(db_session, "DE_LU", "negative_prices")

    assert out["rank"]["position"] is None
    assert "coverage" in out["rank"]["reason"]
    assert MIN_HISTORY_DAYS == 365


def test_a_zone_with_no_episodes_says_so(db_session):
    out = zone_episodes(db_session, "FR", "dunkelflaute")
    assert out["available"] is False
    assert "No dunkelflaute episodes" in out["reason"]


def test_the_detector_names_the_rank_and_never_forecasts(db_session):
    """Posture B: past tense, the zone's own record, a sample size. "Running" means the episode
    reaches the newest day we hold — not that it will continue."""
    from backend.signals.detectors.power import detect_episode_rank

    _seed_negative_run(db_session)
    compute_episodes(db_session, today=TODAY)

    results = detect_episode_rank(db_session)
    assert len(results) == 1
    r = results[0]

    assert r.rule == "episode_rank" and r.vertical == "power"
    assert "1st-longest" in r.title
    assert "longest:" in r.detail
    for forbidden in ("because", "will", "expect", "forecast", "caused"):
        assert forbidden not in (r.title + r.detail).lower()
