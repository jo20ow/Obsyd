"""Grid stress is an episode, and the radar only ever saw today.

A Dunkelflaute runs for days. A negative-price stretch runs for a weekend. The desk could say
"DE-LU is in a Dunkelflaute" and never "this is the fourth-longest one in our record" — which is
the sentence an analyst actually wants, because it is the one that says whether to care.

WHY THIS CANNOT BE MINED FROM THE ALERTS
----------------------------------------
It looks like the history is already there: the radar has been writing Alert rows for months.
It is not. `_upsert_alert` (signals/rules.py, DEDUP_HOURS=24) MUTATES the existing row in place,
slides `created_at` forward and DELETES older duplicates — so a five-day Dunkelflaute collapses
into a single row whose timestamp keeps moving and which claims nothing about duration. The
radar is stateless by construction, and re-firing actively destroys the record an episode would
be built from.

So episodes are RE-DERIVED from the canonical series, nightly, in full. That is exactly the
doctrine records.py already follows: no incremental state means no state to corrupt, and a
recompute is always correct.

THE GUARD IS THE PART THAT DECIDES WHETHER THIS SHIPS OR EMBARRASSES
--------------------------------------------------------------------
records.py's `_bounds` exists because a 0 MW ENTSO-E gap once produced a bogus all-time low for
SI "that the radar dutifully celebrated". An episode grouper has the same failure mode,
amplified: if a zone's A75 feed drops for two days, wind reads 0 and solar reads 0, the renewable
share reads 0 — and out comes a two-day Dunkelflaute that never happened. A run of them would be
worse: the engine would find its longest episode ever in an outage of our own collector.

Hence: an hour the coverage guard rejects is a HOLE, not an episode day, and a hole longer than
the gap tolerance ENDS the episode rather than bridging it.

A PROPERTY WORTH KNOWING BEFORE SOMEONE REPORTS IT AS A BUG
-----------------------------------------------------------
The predicates are relative — "the bottom 2% of this zone's own record for this month" — and an
episode's own days are PART of that record. So a run that is very long relative to the record it
is judged against is reported TRUNCATED to its darkest days: with 230 January days on file, the
cutoff is the 6th-darkest, and a hypothetical ten-day Dunkelflaute would surface as its five
worst days rather than all ten. That is exactly what "the bottom 2%" means, and it is the honest
reading — but it is not obvious, and it is the reason the tests seed six years of history rather
than two.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from backend.models.energy import PowerEpisode, PowerGrid, PowerPriceDaily
from backend.power.coverage import reliable_days
from backend.power.dunkelflaute import is_dunkelflaute, zone_thresholds

logger = logging.getLogger(__name__)

#: A single missing day must not split a five-day run — feeds have holes. A three-day hole is
#: not a gap in an episode, it is two episodes with a gap between them.
MAX_GAP_DAYS = 1

#: One qualifying day is a day, not an episode. Two consecutive is the shortest thing worth
#: calling a run.
MIN_DURATION_DAYS = 2

#: A window less than this fraction populated is not an episode, it is a coverage hole with a
#: few readings in it.
MIN_POPULATED = 0.8

#: Below a year of history for a zone, a rank is a statement about our coverage, not about the
#: grid. records.py's RECORD_MIN_COVERAGE_DAYS, and the same argument.
MIN_HISTORY_DAYS = 365

#: A negative-price day: the existing detector's own floor, so the desk has one definition.
NEGATIVE_HOURS_MIN = 3


@dataclass(frozen=True)
class Run:
    """A maximal stretch of qualifying days. Pure — no DB, no dates beyond what it was given."""

    start: str
    end: str
    days: int
    depth: float
    depth_date: str
    mean: float
    populated: float


def group_runs(
    points: list[tuple[str, float]],
    predicate,
    *,
    holes: set[str] | None = None,
    deeper,
    min_days: int = MIN_DURATION_DAYS,
    max_gap_days: int = MAX_GAP_DAYS,
) -> list[Run]:
    """Consecutive qualifying days → runs. Pure.

    `points` is an ascending [(YYYY-MM-DD, value)] list. `holes` are days we do not trust (the
    coverage guard rejected them, or they are simply absent). A hole does NOT qualify and does
    not extend a run — but a run survives up to `max_gap_days` of them, because a feed with a
    one-day hole in it is still one episode, and a feed with a three-day hole is two.

    `deeper(a, b) -> bool` says which of two values is the worse one, so the same grouper serves
    a minimum (Dunkelflaute) and a maximum (a price spike) without knowing which it is.
    """
    by_date = dict(points)
    if not by_date:
        return []
    holes = holes or set()

    all_days = _day_range(min(by_date), max(by_date))
    runs: list[Run] = []
    current: list[str] = []
    gap = 0

    for day in all_days:
        value = by_date.get(day)
        qualifies = day not in holes and value is not None and predicate(day, value)

        if qualifies:
            current.append(day)
            gap = 0
            continue
        if not current:
            continue
        gap += 1
        if gap > max_gap_days:
            runs.append(_close(current, by_date, deeper))
            current, gap = [], 0

    if current:
        runs.append(_close(current, by_date, deeper))

    return [r for r in runs if r.days >= min_days and r.populated >= MIN_POPULATED]


def _close(days: list[str], by_date: dict[str, float], deeper) -> Run:
    span = _day_range(days[0], days[-1])
    values = [by_date[d] for d in days]
    depth_date = days[0]
    for d in days:
        if deeper(by_date[d], by_date[depth_date]):
            depth_date = d
    return Run(
        start=days[0],
        end=days[-1],
        days=len(span),                      # the SPAN, so a bridged hole is counted honestly
        depth=by_date[depth_date],
        depth_date=depth_date,
        mean=sum(values) / len(values),
        populated=len(days) / len(span),
    )


def _day_range(start: str, end: str) -> list[str]:
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    return [(a + timedelta(days=i)).isoformat() for i in range((b - a).days + 1)]


# ─── the kinds ────────────────────────────────────────────────────────────────

KINDS = ("dunkelflaute", "negative_prices", "price_spike")

#: A price spike is judged the way a Dunkelflaute is: against the zone's OWN record for the SAME
#: calendar month. A flat EUR threshold would call every 2022 day a spike and no 2020 day one.
SPIKE_TAIL = 0.02


def compute_episodes(db: Session, *, today: date | None = None) -> dict:
    """Full recompute of every episode, every zone. Idempotent. Returns a counter."""
    today = today or datetime.now(timezone.utc).date()
    reliable = reliable_days(db)          # ONE query for the whole coverage guard

    counts = {k: 0 for k in KINDS}
    seen: set[tuple[str, str, str]] = set()

    for kind in KINDS:
        for zone, runs in _runs_for_kind(db, kind, reliable, today).items():
            for run in runs:
                _upsert(db, kind, zone, run, today)
                seen.add((kind, zone, run.start))
                counts[kind] += 1

    # A full recompute must also RETRACT: a run that a later revision of the data no longer
    # supports has to disappear, or the archive slowly fills with episodes that never happened.
    removed = 0
    for row in db.query(PowerEpisode).all():
        if (row.kind, row.zone, row.start_date) not in seen:
            db.delete(row)
            removed += 1

    db.commit()
    return {**counts, "removed": removed}


def _runs_for_kind(db: Session, kind: str, reliable: set, today: date) -> dict[str, list[Run]]:
    if kind == "dunkelflaute":
        return _dunkelflaute_runs(db, reliable)
    if kind == "negative_prices":
        return _price_runs(db, "negative_hours")
    if kind == "price_spike":
        return _price_runs(db, "mean_price")
    raise ValueError(f"unknown episode kind {kind}")


def _dunkelflaute_runs(db: Session, reliable: set) -> dict[str, list[Run]]:
    """Runs of days the calibrated Dunkelflaute predicate fires on (see dunkelflaute.py)."""
    rows = (
        db.query(PowerGrid.zone, PowerGrid.date, PowerGrid.load_mw,
                 PowerGrid.wind_mw, PowerGrid.solar_mw)
        .order_by(PowerGrid.zone, PowerGrid.date)
        .all()
    )
    by_zone: dict[str, list[tuple[str, float]]] = {}
    holes: dict[str, set[str]] = {}
    for zone, day, load, wind, solar in rows:
        if not load or load <= 0:
            holes.setdefault(zone, set()).add(day)
            continue
        share = ((wind or 0.0) + (solar or 0.0)) / load
        by_zone.setdefault(zone, []).append((day, share))
        # THE guard. A zone whose A75 feed dropped reads wind=0, solar=0, share=0 — and without
        # this every collector outage becomes the longest Dunkelflaute in the record.
        if (day, zone) not in reliable:
            holes.setdefault(zone, set()).add(day)

    thresholds: dict[str, dict] = {}
    out: dict[str, list[Run]] = {}
    for zone, points in by_zone.items():
        def _predicate(day: str, share: float, _zone=zone) -> bool:
            month = day[5:7]
            if month not in thresholds:
                thresholds[month] = zone_thresholds(db, month)
            return is_dunkelflaute(share, thresholds[month].get(_zone, {}))

        runs = group_runs(points, _predicate, holes=holes.get(zone), deeper=lambda a, b: a < b)
        if runs:
            out[zone] = runs
    return out


def _price_runs(db: Session, column: str) -> dict[str, list[Run]]:
    rows = (
        db.query(PowerPriceDaily.zone, PowerPriceDaily.date,
                 PowerPriceDaily.negative_hours, PowerPriceDaily.mean_price)
        .order_by(PowerPriceDaily.zone, PowerPriceDaily.date)
        .all()
    )
    by_zone: dict[str, list[tuple[str, float]]] = {}
    for zone, day, neg, mean in rows:
        value = neg if column == "negative_hours" else mean
        if value is None:
            continue
        by_zone.setdefault(zone, []).append((day, float(value)))

    out: dict[str, list[Run]] = {}
    for zone, points in by_zone.items():
        if column == "negative_hours":
            runs = group_runs(
                points,
                lambda _d, v: v >= NEGATIVE_HOURS_MIN,
                deeper=lambda a, b: a > b,     # the deepest day is the one with the MOST hours
            )
        else:
            # Same discipline as the Dunkelflaute: unusual FOR THIS ZONE, IN THIS MONTH. A flat
            # EUR threshold would make every day of 2022 a spike and no day of 2020 one.
            cutoffs = _monthly_cutoffs(points, SPIKE_TAIL)
            if not cutoffs:
                continue
            runs = group_runs(
                points,
                lambda d, v: d[5:7] in cutoffs and v >= cutoffs[d[5:7]],
                deeper=lambda a, b: a > b,
            )
        if runs:
            out[zone] = runs
    return out


def _monthly_cutoffs(points: list[tuple[str, float]], tail: float) -> dict[str, float]:
    """{month: value at the (1 - tail) percentile of that month's own record}."""
    from backend.power.borders import percentile
    from backend.power.dunkelflaute import MIN_MONTH_HISTORY

    by_month: dict[str, list[float]] = {}
    for day, value in points:
        by_month.setdefault(day[5:7], []).append(value)
    return {
        month: percentile(values, 1.0 - tail)
        for month, values in by_month.items()
        if len(values) >= MIN_MONTH_HISTORY
    }


def _upsert(db: Session, kind: str, zone: str, run: Run, today: date) -> None:
    # "Active" means the run reaches the newest day we have. It is not a claim that it continues
    # tomorrow — only that it has not been seen to end.
    status = "active" if run.end >= (today - timedelta(days=1)).isoformat() else "resolved"
    existing = (
        db.query(PowerEpisode)
        .filter(PowerEpisode.kind == kind, PowerEpisode.zone == zone,
                PowerEpisode.start_date == run.start)
        .one_or_none()
    )
    values = {
        "end_date": run.end,
        "duration_days": run.days,
        "depth": round(run.depth, 4),
        "depth_date": run.depth_date,
        "mean_value": round(run.mean, 4),
        "status": status,
    }
    if existing:
        for k, v in values.items():
            setattr(existing, k, v)
    else:
        db.add(PowerEpisode(kind=kind, zone=zone, start_date=run.start, **values))
    db.flush()


# ─── reading them back ────────────────────────────────────────────────────────


def zone_episodes(db: Session, zone: str, kind: str) -> dict:
    """Every episode of one kind in one zone, with the running one ranked against the rest."""
    rows = (
        db.query(PowerEpisode)
        .filter(PowerEpisode.kind == kind, PowerEpisode.zone == zone)
        .order_by(PowerEpisode.start_date.desc())
        .all()
    )
    if not rows:
        return {"available": False, "zone": zone, "kind": kind,
                "reason": f"No {kind.replace('_', ' ')} episodes on record for {zone}."}

    history_days = _history_days(db, kind, zone)
    by_length = sorted(rows, key=lambda r: -r.duration_days)
    active = next((r for r in rows if r.status == "active"), None)

    rank = None
    if active is not None:
        if history_days < MIN_HISTORY_DAYS:
            rank = {"position": None, "of": len(rows),
                    "reason": (f"Only {history_days} days of {zone} history — a rank would be a "
                               "statement about our coverage, not about the grid.")}
        else:
            rank = {
                "position": by_length.index(active) + 1,
                "of": len(rows),
                "by": "duration_days",
                "longest_days": by_length[0].duration_days,
                "longest_start": by_length[0].start_date,
            }

    return {
        "available": True,
        "zone": zone,
        "kind": kind,
        "history_days": history_days,
        "active": _row(active) if active else None,
        "rank": rank,
        "episodes": [_row(r) for r in by_length[:20]],
        "count": len(rows),
        "note": (
            "An episode is a run of consecutive qualifying days, re-derived nightly from the "
            "published record — not an alert log. Ranked by duration against this zone's own "
            "history. Descriptive: what has happened, never what happens next."
        ),
    }


def _row(r: PowerEpisode) -> dict:
    return {
        "start_date": r.start_date, "end_date": r.end_date,
        "duration_days": r.duration_days,
        "depth": r.depth, "depth_date": r.depth_date,
        "mean_value": r.mean_value, "status": r.status,
    }


def _history_days(db: Session, kind: str, zone: str) -> int:
    from sqlalchemy import func

    model = PowerGrid if kind == "dunkelflaute" else PowerPriceDaily
    return int(
        db.query(func.count(model.date)).filter(model.zone == zone).scalar() or 0
    )
