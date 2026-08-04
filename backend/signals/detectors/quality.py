"""Data-quality incidents on the anomaly radar — Honest Record slice A4.

The A1/A2 transparency tables (revision ledger, nightly quality aggregates)
record what the source published; these two detectors surface the days that
record turns NEWSWORTHY, on the same Alert backbone as every other radar rule —
zero extra delivery code, same feed, same RSS.

* ``quality_completeness_drop`` — yesterday (the newest finished UTC day) a
  charter series' published hours collapsed below half, in a zone+series that
  is normally near-complete. The trailing norm is the guard rail twice over: a
  chronically thin series is not news, and a freshly deployed / newly enabled
  zone has no norm yet, so the detector stays silent instead of judging a day
  against a handful of rows.
* ``quality_major_restatement`` — the source re-published materially different
  values for several SETTLED hours of one series within the last 24 h. "Settled"
  reuses the read-side maturity threshold shared with /api/v1/quality/revisions
  (REVISION_MATURITY_S, canonical home backend/power/quality.py): restatements
  inside the routine provisional fill-in window are not events. "Materially" is
  two floors deep: the change must clear an absolute per-series-family floor in
  the series' own unit BEFORE its percentage is computed — the ledger's write
  epsilon is only 0.5 units, and a percentage alone would let three 1-MW
  re-publishes page someone.

Sibling contract (base.py): DB reads only, cheap enough for the 5-minute
runner, template text, descriptive never predictive — a low-completeness day
is hours the source has not (yet) published, a restatement is the source
revising its own publication; neither says "wrong data" and neither judges the
market. Both rules FOLD multiple offending series of one zone into a single
result (worst offender leads the title, every one is named in the detail) —
the alert backbone dedups on (rule, zone), so per-series results would
overwrite each other and a warning could mask a critical (the
interconnector_saturated precedent).
"""

from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone

from backend.models.energy import PowerRevision, QualityDaily, SeriesDim, ZoneDim
from backend.power.quality import QUALITY_SERIES, REVISION_MATURITY_S
from backend.power.zones import POWER_ZONES
from backend.signals.detectors.base import MIN_BASELINE_N, DetectorResult

# ── quality_completeness_drop ─────────────────────────────────────────────────
#: Yesterday's hours_present/hours_expected below this → a drop worth a look.
COMPLETENESS_DROP_RATIO = 0.5
#: … but only where the zone+series is NORMALLY near-complete: trailing mean
#: completeness (days with quality rows, yesterday excluded) must clear this.
COMPLETENESS_NORM_MIN = 0.9
#: Trailing window for that norm, and the minimum days-with-rows before the
#: norm is trustworthy (MIN_BASELINE_N — the radar's shared "no baseline, no
#: judgement" floor; this is what keeps the first days after deploy quiet).
COMPLETENESS_NORM_DAYS = 30
COMPLETENESS_MIN_NORM_DAYS = MIN_BASELINE_N


def detect_completeness_drops(db) -> list[DetectorResult]:
    """Zones where a normally-complete charter series lost most of yesterday.

    Judges only YESTERDAY (UTC) — today is partial by construction while the
    day runs. A missing yesterday row is silence, not a drop: either the
    nightly quality job has not covered the day yet or the series is inactive
    there, and absence is never turned into a claim (no data → no alert).
    """
    yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
    y_iso = yesterday.isoformat()
    cut = (yesterday - timedelta(days=COMPLETENESS_NORM_DAYS)).isoformat()

    # One indexed date-range scan for all zones; QUALITY_SERIES is the charter
    # list and never contains the reserved "_zone" pseudo-series (its rows carry
    # hours_expected == 0 and describe zone-level flags, not completeness).
    rows = (
        db.query(
            QualityDaily.zone,
            QualityDaily.series_key,
            QualityDaily.date,
            QualityDaily.hours_present,
            QualityDaily.hours_expected,
        )
        .filter(
            QualityDaily.date >= cut,
            QualityDaily.date <= y_iso,
            QualityDaily.series_key.in_(QUALITY_SERIES),
        )
        .all()
    )

    cells: dict[tuple[str, str], dict] = {}
    for zone, skey, day, present, expected in rows:
        if zone not in POWER_ZONES or expected <= 0:
            continue  # disabled zone (radar speaks only about served zones)
        c = cells.setdefault((zone, skey), {"today": None, "norm": []})
        if day == y_iso:
            c["today"] = (present, expected)
        else:
            c["norm"].append(present / expected)

    by_zone: dict[str, list[dict]] = {}
    for (zone, skey), c in cells.items():
        if c["today"] is None:
            continue
        present, expected = c["today"]
        if present / expected >= COMPLETENESS_DROP_RATIO:
            continue
        norm = c["norm"]
        if len(norm) < COMPLETENESS_MIN_NORM_DAYS:
            continue  # no trustworthy norm yet (fresh deploy / new zone)
        norm_mean = sum(norm) / len(norm)
        if norm_mean < COMPLETENESS_NORM_MIN:
            continue  # chronically thin series — its gaps are not news
        by_zone.setdefault(zone, []).append({
            "series": skey,
            "present": present,
            "expected": expected,
            "norm_pct": 100.0 * norm_mean,
        })

    results: list[DetectorResult] = []
    for zone, entries in sorted(by_zone.items()):
        # Worst first: fully-missing series lead, then the lowest ratio.
        entries.sort(key=lambda e: (e["present"] > 0, e["present"] / e["expected"]))
        worst = entries[0]
        parts = [
            f"{e['series']}: {e['present']} of {e['expected']} expected hours "
            f"on {y_iso} vs a {e['norm_pct']:.0f}% {COMPLETENESS_NORM_DAYS}-day norm"
            for e in entries
        ]
        results.append(
            DetectorResult(
                rule="quality_completeness_drop",
                zone=zone,
                vertical="power",
                severity=(
                    "critical" if any(e["present"] == 0 for e in entries) else "warning"
                ),
                title=(
                    f"{zone}: {worst['series']} — source published "
                    f"{worst['present']} of {worst['expected']} expected hours"
                    + (f" (+{len(entries) - 1} more series)" if len(entries) > 1 else "")
                ),
                detail=(
                    "; ".join(parts) + ". Completeness describes the published "
                    "record for the day — the hours may still arrive with the "
                    "source's later publications. Descriptive, not a verdict."
                ),
                as_of=y_iso,
            )
        )
    return results


# ── quality_major_restatement ─────────────────────────────────────────────────
#: Radar window: restatements OBSERVED within the last 24 h (the feed reports
#: what changed recently; the full history lives in /api/v1/quality/revisions).
RESTATE_WINDOW_S = 24 * 3600
#: An hour counts when |new − old| / max(|old|, floor) exceeds this fraction.
RESTATE_MIN_FRACTION = 0.20
#: The floor keeps a previous value near zero (a price crossing 0) from
#: manufacturing an unbounded percentage; 1.0 in the series' own unit.
RESTATE_DENOM_FLOOR = 1.0
#: Materiality floor on the CHANGE ITSELF, per series family (longest story
#: first): the ledger's write epsilon is only 0.5 units (hourly_store.
#: REVISION_FLOOR), so a 2→3 MW re-publish is ledgered — and against the
#: percentage machinery above it scores 50% (0→1.5 MW scores 150% via the
#: denominator floor). Three such hours would page someone about nothing. A
#: restatement must first be material in the series' OWN unit before its
#: percentage is worth saying: 50 MW for MW-scale series (well under one
#: mid-size unit, far above meter jitter), 5 EUR for prices (a sub-5-EUR move
#: on a settled hour is rounding, not news), 10 units for anything unlisted
#: (ntc.*, sched.*, imbalance.* …). STEP_JUMP_FLOORS' per-series-floor pattern
#: (backend/power/quality.py), keyed by prefix because the family is what sets
#: the unit scale.
RESTATE_ABS_FLOORS: dict[str, float] = {
    "load.": 50.0,
    "gen.": 50.0,
    "flow.": 50.0,
    "price.": 5.0,
}
RESTATE_ABS_FLOOR_DEFAULT = 10.0
#: ≥3 distinct hours of ONE series → warning; ≥12 hours or a ≥50% median
#: change → critical.
RESTATE_MIN_HOURS = 3
RESTATE_CRIT_HOURS = 12
RESTATE_CRIT_MEDIAN_PCT = 50.0
#: Detail cap: name the top offenders, fold the rest. A backfill burst can
#: restate dozens of series in one zone at once; the title already folds, and
#: an unbounded detail would push kilobytes into every feed/RSS render.
RESTATE_DETAIL_MAX_SERIES = 6


def _abs_floor(series_key: str) -> float:
    for prefix, floor in RESTATE_ABS_FLOORS.items():
        if series_key.startswith(prefix):
            return floor
    return RESTATE_ABS_FLOOR_DEFAULT


def _recent_revisions(db, cutoff_epoch: int) -> list:
    """The ledger rows observed at/after `cutoff_epoch`, WITHOUT scanning the
    (never-pruned) ledger: power_revision is append-only and `observed_at` is
    the write-time wall clock, so it is nondecreasing in the autoincrement PK.
    Walking the PK tail backwards and stopping at the first row older than the
    cutoff reads O(recent rows).

    Why not the (series_id, zone_id, observed_at) index the API reads ride:
    observed_at is its THIRD column, so it only seeks for a KNOWN series+zone
    pair (routes/quality.py always has one). This detector cuts across ALL
    pairs, and a bare observed_at filter would scan the whole table on every
    5-minute run — the PK tail is the one access path that makes "recent
    first" cheap without enumerating hundreds of candidate pairs. yield_per
    keeps the walk streaming (without it the ORM would buffer the full result
    before the first row)."""
    out = []
    q = (
        db.query(
            PowerRevision.series_id,
            PowerRevision.zone_id,
            PowerRevision.ts_utc,
            PowerRevision.old_value,
            PowerRevision.new_value,
            PowerRevision.observed_at,
        )
        .order_by(PowerRevision.id.desc())
        .yield_per(1000)
    )
    for row in q:
        if row.observed_at < cutoff_epoch:
            break
        out.append(row)
    return out


def detect_major_restatements(db) -> list[DetectorResult]:
    """Zones where the source restated several settled hours of one series in
    the last 24 h. Distinct hours, not revision rows — an hour revised twice in
    the window is one restated hour, at its largest observed change."""
    now = int(datetime.now(timezone.utc).timestamp())
    recent = _recent_revisions(db, now - RESTATE_WINDOW_S)
    if not recent:
        return []

    sid_key = dict(
        db.query(SeriesDim.id, SeriesDim.key)
        .filter(SeriesDim.id.in_({r.series_id for r in recent}))
        .all()
    )
    zid_key = dict(
        db.query(ZoneDim.id, ZoneDim.key)
        .filter(ZoneDim.id.in_({r.zone_id for r in recent}))
        .all()
    )

    # (zone, series) → {hour ts → largest change fraction}; plus the newest
    # observation per pair for the as_of stamp.
    hours: dict[tuple[str, str], dict[int, float]] = {}
    seen_at: dict[tuple[str, str], int] = {}
    for r in recent:
        if r.observed_at - r.ts_utc <= REVISION_MATURITY_S:
            continue  # routine provisional fill-in, not a restatement of settled data
        zone, skey = zid_key.get(r.zone_id), sid_key.get(r.series_id)
        if zone is None or skey is None or zone not in POWER_ZONES:
            continue
        delta = abs(r.new_value - r.old_value)
        if delta < _abs_floor(skey):
            continue  # immaterial in the series' own unit — no percentage worth saying
        frac = delta / max(abs(r.old_value), RESTATE_DENOM_FLOOR)
        if frac <= RESTATE_MIN_FRACTION:
            continue
        pair = (zone, skey)
        per_hour = hours.setdefault(pair, {})
        per_hour[r.ts_utc] = max(per_hour.get(r.ts_utc, 0.0), frac)
        seen_at[pair] = max(seen_at.get(pair, 0), r.observed_at)

    by_zone: dict[str, list[dict]] = {}
    for (zone, skey), per_hour in hours.items():
        if len(per_hour) < RESTATE_MIN_HOURS:
            continue
        pcts = [100.0 * f for f in per_hour.values()]
        median = statistics.median(pcts)
        by_zone.setdefault(zone, []).append({
            "series": skey,
            "n_hours": len(per_hour),
            "median_pct": median,
            "largest_pct": max(pcts),
            "critical": len(per_hour) >= RESTATE_CRIT_HOURS
            or median >= RESTATE_CRIT_MEDIAN_PCT,
            "observed_at": seen_at[(zone, skey)],
        })

    results: list[DetectorResult] = []
    for zone, entries in sorted(by_zone.items()):
        entries.sort(
            key=lambda e: (e["critical"], e["n_hours"], e["median_pct"]), reverse=True
        )
        worst = entries[0]
        newest = max(e["observed_at"] for e in entries)
        shown = entries[:RESTATE_DETAIL_MAX_SERIES]
        parts = [
            f"{e['n_hours']} hours of {e['series']} restated by a median of "
            f"{e['median_pct']:.0f}% (largest {e['largest_pct']:.0f}%)"
            for e in shown
        ]
        if len(entries) > len(shown):
            parts.append(f"+{len(entries) - len(shown)} more series")
        results.append(
            DetectorResult(
                rule="quality_major_restatement",
                zone=zone,
                vertical="power",
                severity="critical" if any(e["critical"] for e in entries) else "warning",
                title=(
                    f"{zone}: {worst['n_hours']} settled hours of {worst['series']} "
                    f"restated (median {worst['median_pct']:.0f}%)"
                    + (f" (+{len(entries) - 1} more series)" if len(entries) > 1 else "")
                ),
                detail=(
                    f"The source re-published different values for hours settled "
                    f"more than {REVISION_MATURITY_S // 3600} h earlier, observed "
                    f"in the last {RESTATE_WINDOW_S // 3600} h: "
                    + "; ".join(parts)
                    + ". A restatement is the source revising its own "
                    "publication — described here, not judged."
                ),
                as_of=datetime.fromtimestamp(newest, tz=timezone.utc).strftime("%Y-%m-%d"),
            )
        )
    return results
