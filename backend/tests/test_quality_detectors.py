"""Honest-Record slice A4 — data-quality incidents on the anomaly-radar backbone.

Two curated detectors (backend/signals/detectors/quality.py) surface what the
A1/A2 transparency tables recorded, as radar alerts:

* quality_completeness_drop — yesterday's published hours collapsed for a
  series that is normally near-complete (quality_daily),
* quality_major_restatement — the source restated several settled hours of one
  series in the last 24 h (power_revision ledger).

Same contract as every sibling in test_anomaly_detectors.py: DB reads only,
descriptive template text, one alert per (rule, zone) — multiple offending
series in a zone FOLD into one result (the interconnector_saturated precedent),
and no data → no alert, never a fabricated calm. All clocks UTC (#111).
"""

from datetime import datetime, timedelta, timezone

from backend.models.alerts import Alert
from backend.models.energy import PowerRevision, QualityDaily
from backend.power.hourly_store import resolve_series_id, resolve_zone_id
from backend.signals.detectors import DETECTORS, run_all_detectors
from backend.signals.detectors.quality import (
    detect_completeness_drops,
    detect_major_restatements,
)


def _utc_yesterday():
    return datetime.now(timezone.utc).date() - timedelta(days=1)


def _seed_quality(
    db,
    zone="DE_LU",
    series="load.actual",
    *,
    yesterday_present=4,
    expected=24,
    norm_days=30,
    norm_present=24,
    days_ago=1,
):
    """`norm_days` full prior days then the anchor day (`days_ago` before today,
    default yesterday) with `yesterday_present` hours. yesterday_present=None
    seeds only the norm (no row for the anchor day)."""
    y = datetime.now(timezone.utc).date() - timedelta(days=days_ago)
    for o in range(1, norm_days + 1):
        db.add(QualityDaily(
            zone=zone, series_key=series, date=(y - timedelta(days=o)).isoformat(),
            hours_present=norm_present, hours_expected=expected, flags="[]",
        ))
    if yesterday_present is not None:
        db.add(QualityDaily(
            zone=zone, series_key=series, date=y.isoformat(),
            hours_present=yesterday_present, hours_expected=expected, flags="[]",
        ))
    db.commit()


def _seed_revisions(
    db,
    zone="DE_LU",
    series="load.actual",
    *,
    hours=3,
    pct=30.0,
    observed_ago_h=1,
    hour_age_h=120,
    old=1000.0,
):
    """`hours` distinct restated hours of one series+zone: each hour is
    `hour_age_h` old (mature at 120 h > the 48 h threshold), observed
    `observed_ago_h` ago, moved by `pct` percent of the old value. Anchored to
    the top of the hour so two seeding calls hit the SAME hour timestamps."""
    sid = resolve_series_id(db, series)
    zid = resolve_zone_id(db, zone)
    now = int(
        datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0).timestamp()
    )
    observed = now - observed_ago_h * 3600
    for i in range(hours):
        db.add(PowerRevision(
            series_id=sid, zone_id=zid,
            ts_utc=now - hour_age_h * 3600 - i * 3600,
            old_value=old, new_value=old * (1.0 + pct / 100.0),
            observed_at=observed,
        ))
    db.commit()


# ─── quality_completeness_drop ────────────────────────────────────────────────


def test_completeness_drop_fires(db_session):
    _seed_quality(db_session, yesterday_present=4)
    results = detect_completeness_drops(db_session)
    assert len(results) == 1
    r = results[0]
    assert r.rule == "quality_completeness_drop"
    assert r.zone == "DE_LU" and r.vertical == "power" and r.severity == "warning"
    assert "load.actual" in r.title
    assert "4 of 24" in r.title, "yesterday's count vs the expectation, in the headline"
    assert "100%" in r.detail, "the 30-day norm travels in the detail"
    assert r.as_of == _utc_yesterday().isoformat()


def test_completeness_critical_at_zero_hours(db_session):
    _seed_quality(db_session, yesterday_present=0)
    r = detect_completeness_drops(db_session)[0]
    assert r.severity == "critical"
    assert "0 of 24" in r.title


def test_completeness_weak_norm_suppressed(db_session):
    # A series that is chronically half-complete dropping further is not news —
    # the ≥90% trailing norm is the whole point of the rule.
    _seed_quality(db_session, yesterday_present=4, norm_present=12)
    assert detect_completeness_drops(db_session) == []


def test_completeness_new_zone_guard(db_session):
    # 3 prior perfect days are not a norm (MIN_BASELINE_N discipline): right
    # after a deploy / a newly enabled zone the detector stays silent instead of
    # judging yesterday against a handful of days.
    _seed_quality(db_session, yesterday_present=2, norm_days=3)
    assert detect_completeness_drops(db_session) == []


def test_completeness_above_threshold_suppressed(db_session):
    _seed_quality(db_session, yesterday_present=13)  # 54% — above the 50% bar
    assert detect_completeness_drops(db_session) == []


def test_completeness_missing_yesterday_row_is_silence(db_session):
    # No quality row for yesterday (nightly job not run yet): the detector
    # anchors on the newest recorded day instead — here that day is fully
    # complete, so silence. Absence is never turned into a claim.
    _seed_quality(db_session, yesterday_present=None)
    assert detect_completeness_drops(db_session) == []


def test_completeness_fires_without_a_yesterday_row(db_session):
    """The race the anchor exists for: the nightly quality job (23:55 UTC on
    day D) writes day D−1's rows minutes before "yesterday" rolls over to D —
    for almost all of day D the literal calendar yesterday has no row yet (and
    a job overrun past midnight would skip it entirely). The newest recorded
    day ≤ yesterday is what gets judged, with an honest as_of."""
    two_days_ago = (datetime.now(timezone.utc).date() - timedelta(days=2)).isoformat()
    _seed_quality(db_session, yesterday_present=4, days_ago=2)  # no yesterday row at all
    results = detect_completeness_drops(db_session)
    assert len(results) == 1
    r = results[0]
    assert "4 of 24" in r.title
    assert r.as_of == two_days_ago, "as_of names the day actually judged"


def test_completeness_no_data_no_alerts(db_session):
    assert detect_completeness_drops(db_session) == []


def test_completeness_exactly_half_suppressed(db_session):
    # 12/24 is exactly the 0.5 ratio — the bar is strictly below.
    _seed_quality(db_session, yesterday_present=12)
    assert detect_completeness_drops(db_session) == []


def test_completeness_norm_exactly_090_fires(db_session):
    # 9/10 on every prior day → the trailing mean sits exactly at the 0.90
    # threshold, which is inclusive (a 90%-complete series IS the near-complete
    # case the rule exists for).
    _seed_quality(db_session, yesterday_present=4, expected=10, norm_present=9)
    assert len(detect_completeness_drops(db_session)) == 1


def test_completeness_13_norm_days_silent(db_session):
    # One day short of MIN_BASELINE_N — not a norm yet.
    _seed_quality(db_session, yesterday_present=4, norm_days=13)
    assert detect_completeness_drops(db_session) == []


def test_completeness_14_norm_days_fires(db_session):
    # Exactly MIN_BASELINE_N days of rows — the norm becomes trustworthy.
    _seed_quality(db_session, yesterday_present=4, norm_days=14)
    assert len(detect_completeness_drops(db_session)) == 1


def test_completeness_folds_series_per_zone(db_session):
    """The backbone dedups on (rule, zone): two dropped series in one zone must
    come back as ONE result — worst offender leads the title, both are named."""
    _seed_quality(db_session, series="load.actual", yesterday_present=0)   # critical
    _seed_quality(db_session, series="gen.B16", yesterday_present=5)       # warning
    results = detect_completeness_drops(db_session)
    assert len(results) == 1, "one result per zone — the backbone's dedup key"
    r = results[0]
    assert r.severity == "critical", "the worst series sets the zone's severity"
    assert "load.actual" in r.title and "+1 more" in r.title
    assert "load.actual" in r.detail and "gen.B16" in r.detail


def test_completeness_two_zones_two_results(db_session):
    _seed_quality(db_session, zone="DE_LU", yesterday_present=4)
    _seed_quality(db_session, zone="FR", yesterday_present=0)
    results = detect_completeness_drops(db_session)
    assert {r.zone for r in results} == {"DE_LU", "FR"}


def test_completeness_disabled_zone_suppressed(db_session):
    # ES is in the registry but not in the test env's enabled zones — the radar
    # only speaks about zones the product serves.
    _seed_quality(db_session, zone="ES", yesterday_present=0)
    assert detect_completeness_drops(db_session) == []


def test_completeness_ignores_unfinished_today(db_session):
    # TODAY is always partial while the day runs; only YESTERDAY (the newest
    # finished UTC day) is ever judged.
    y = _utc_yesterday()
    _seed_quality(db_session, yesterday_present=24)
    db_session.add(QualityDaily(
        zone="DE_LU", series_key="load.actual", date=(y + timedelta(days=1)).isoformat(),
        hours_present=6, hours_expected=24, flags="[]",
    ))
    db_session.commit()
    assert detect_completeness_drops(db_session) == []


# ─── quality_major_restatement ────────────────────────────────────────────────


def test_restatement_fires_at_three_hours(db_session):
    _seed_revisions(db_session, hours=3, pct=30.0)
    results = detect_major_restatements(db_session)
    assert len(results) == 1
    r = results[0]
    assert r.rule == "quality_major_restatement"
    assert r.zone == "DE_LU" and r.vertical == "power" and r.severity == "warning"
    assert "load.actual" in r.title and "3" in r.title
    assert "30%" in r.detail, "the median change travels"


def test_restatement_two_hours_silent(db_session):
    _seed_revisions(db_session, hours=2, pct=30.0)
    assert detect_major_restatements(db_session) == []


def test_restatement_small_changes_silent(db_session):
    _seed_revisions(db_session, hours=6, pct=10.0)  # below the 20% bar
    assert detect_major_restatements(db_session) == []


def test_restatement_critical_at_twelve_hours(db_session):
    _seed_revisions(db_session, hours=12, pct=30.0)
    assert detect_major_restatements(db_session)[0].severity == "critical"


def test_restatement_critical_at_median_50pct(db_session):
    _seed_revisions(db_session, hours=3, pct=60.0)
    assert detect_major_restatements(db_session)[0].severity == "critical"


def test_restatement_immature_fill_in_silent(db_session):
    # Restated hours only 12h old — the normal provisional fill-in window
    # (REVISION_MATURITY_S read-side threshold, backend/power/quality.py), not news.
    _seed_revisions(db_session, hours=6, pct=30.0, hour_age_h=12)
    assert detect_major_restatements(db_session) == []


def test_restatement_old_observation_silent(db_session):
    # Observed three days ago — outside the 24h radar window (the on-site feed
    # is "what changed recently", not the ledger's history).
    _seed_revisions(db_session, hours=6, pct=30.0, observed_ago_h=72, hour_age_h=240)
    assert detect_major_restatements(db_session) == []


def test_restatement_counts_distinct_hours_not_rows(db_session):
    # Two revisions of the SAME hour are one restated hour, not two.
    _seed_revisions(db_session, hours=2, pct=30.0)
    _seed_revisions(db_session, hours=2, pct=40.0)  # same two hours, revised again
    assert detect_major_restatements(db_session) == []


def test_restatement_folds_series_per_zone(db_session):
    _seed_revisions(db_session, series="load.actual", hours=12, pct=30.0)  # critical
    _seed_revisions(db_session, series="gen.B16", hours=3, pct=25.0)       # warning
    results = detect_major_restatements(db_session)
    assert len(results) == 1, "one result per zone — the backbone's dedup key"
    r = results[0]
    assert r.severity == "critical"
    assert "load.actual" in r.title and "+1 more" in r.title
    assert "load.actual" in r.detail and "gen.B16" in r.detail


def test_restatement_disabled_zone_suppressed(db_session):
    _seed_revisions(db_session, zone="ES", hours=6, pct=30.0)
    assert detect_major_restatements(db_session) == []


def test_restatement_no_data_no_alerts(db_session):
    assert detect_major_restatements(db_session) == []


def test_restatement_maturity_boundary_is_strict(db_session):
    """Exactly 48h between the hour and its observation is still fill-in (the
    /revisions API's strict >, mirrored); one second beyond is settled data."""
    from backend.power.quality import REVISION_MATURITY_S

    sid = resolve_series_id(db_session, "load.actual")
    zid = resolve_zone_id(db_session, "DE_LU")
    now = int(datetime.now(timezone.utc).timestamp())
    for i in range(3):
        ts = now - REVISION_MATURITY_S - 2 * 3600 - i * 3600
        db_session.add(PowerRevision(
            series_id=sid, zone_id=zid, ts_utc=ts,
            old_value=1000.0, new_value=1300.0,
            observed_at=ts + REVISION_MATURITY_S,  # exactly at the threshold
        ))
    db_session.commit()
    assert detect_major_restatements(db_session) == []

    for r in db_session.query(PowerRevision).all():
        r.observed_at += 1  # one second beyond — now mature
    db_session.commit()
    assert len(detect_major_restatements(db_session)) == 1


def test_restatement_fraction_boundary_is_strict(db_session):
    # Exactly 20% of the old value (1000→1200, well past the materiality
    # floor) — the bar is "exceeds", not "meets".
    _seed_revisions(db_session, hours=3, pct=20.0)
    assert detect_major_restatements(db_session) == []


def test_restatement_detail_caps_series_list(db_session):
    # A backfill burst can restate many series of one zone at once; the detail
    # names the top offenders and folds the rest instead of growing unbounded.
    for i in range(8):
        _seed_revisions(db_session, series=f"gen.B{i:02d}", hours=3 + i, pct=30.0)
    r = detect_major_restatements(db_session)[0]
    assert r.detail.count("hours of gen.B") == 6, "top offenders only"
    assert "+2 more series" in r.detail
    assert "gen.B07" in r.title, "the most-restated series leads"


def test_restatement_zero_old_value_uses_floor(db_session):
    # The percentage is measured against max(|old|, floor) — an old value of
    # exactly 0 must not divide-by-zero or manufacture an infinite percentage.
    # new=200 clears the load.* materiality floor (50 MW), so only the
    # denominator is under test here.
    _seed_revisions(db_session, hours=3, pct=0.0, old=0.0)
    for r in db_session.query(PowerRevision).all():
        r.new_value = 200.0
    db_session.commit()
    results = detect_major_restatements(db_session)
    assert len(results) == 1  # 200 against the 1.0 denominator floor — finite, fires


def test_restatement_downward_fires(db_session):
    # Downward corrections are the COMMON real case (provisional generation
    # revised down); the magnitude must be signless.
    _seed_revisions(db_session, hours=3, pct=-30.0)
    r = detect_major_restatements(db_session)[0]
    assert r.severity == "warning" and "30%" in r.detail


def test_restatement_immaterial_absolute_change_silent(db_session):
    # 2→3 MW is 50% against the denominator floor but one megawatt of movement
    # — the ledger's 0.5-unit write epsilon lets it through, the load.* 50 MW
    # materiality floor must not. Percentages only get a voice once the change
    # is material in the series' own unit.
    _seed_revisions(db_session, hours=3, pct=50.0, old=2.0)
    assert detect_major_restatements(db_session) == []


def test_restatement_immaterial_price_change_silent(db_session):
    # 2→3 EUR is 50% but one euro — below the price.* 5 EUR floor.
    _seed_revisions(db_session, series="price.dayahead", hours=3, pct=50.0, old=2.0)
    assert detect_major_restatements(db_session) == []


def test_restatement_material_price_change_fires(db_session):
    # 40→52 EUR: 12 EUR clears the price.* floor, 30% clears the fraction bar.
    _seed_revisions(db_session, series="price.dayahead", hours=3, pct=30.0, old=40.0)
    r = detect_major_restatements(db_session)[0]
    assert r.severity == "warning" and "price.dayahead" in r.title


# ─── registry + runner integration ────────────────────────────────────────────


def test_quality_detectors_are_registered(db_session):
    assert detect_completeness_drops in DETECTORS
    assert detect_major_restatements in DETECTORS


def test_runner_surfaces_quality_alerts(db_session):
    _seed_quality(db_session, yesterday_present=4)
    _seed_revisions(db_session, hours=3, pct=30.0)
    run_all_detectors(db_session)
    rules = {a.rule: a for a in db_session.query(Alert).all()}
    assert "quality_completeness_drop" in rules
    assert "quality_major_restatement" in rules
    assert rules["quality_completeness_drop"].vertical == "power"
    assert rules["quality_completeness_drop"].zone == "DE_LU"
