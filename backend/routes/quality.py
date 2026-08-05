"""Honest-Record read API (/api/v1/quality/*) — slice A3.

Public, versioned endpoints over the A1/A2 transparency tables:

* `quality_daily` (backend/power/quality.py) — nightly completeness + rule
  flags per (zone, series, UTC day),
* `power_revision` — one row per REAL value restatement observed at the single
  hourly write path (backend/power/hourly_store.py),
* `ingest_arrival` — one row per fetch batch (arrival cadence + frontier lag).

Posture B throughout: every field DESCRIBES what the SOURCE published, when it
arrived, and how it was later restated. Nothing here says "wrong data" — a
restatement is the source revising its own publication, a low completeness day
is hours the source has not (yet) published.

Conventions (matching the rest of /api/v1):
* every handler is `def`, never `async def` — sync-DB routes must run in
  Starlette's threadpool (repo rule, PR #116);
* the shared v1 per-IP budget applies (`_rate_limit` from routes/api_v1);
* timestamps in JSON are ISO 8601 UTC strings, like /api/v1/series'
  `datetime_utc` — the store's epoch seconds are converted at the edge;
* every response carries `as_of`/`age_days`/`stale` (freshness_meta);
* a valid-but-empty combination is HTTP 200 with `available: false` + empty
  lists, never a 404; unknown series/zone keys are HTTP 400 with a `detail`
  that lists (or points at) the valid values.

Freshness sources, decided + documented per endpoint:
* quality endpoints (`/summary`, `/series`): `as_of` = the newest quality_daily
  DATE in scope; the stale window is the `quality_daily` freshness spec's
  (backend/collectors/freshness.py::SPECS), looked up by key so retuning the
  spec retunes these endpoints.
* `/revisions`: the ledger is forward-only and a quiet series is a FEATURE, so
  pretending a per-spec window exists would be false precision. Its honest
  freshness signal is the newest `ingest_arrival.observed_at` for the
  series+zone — the last time the source was polled and could have restated
  something — exposed as `as_of`, stale after ARRIVAL_STALE_DAYS.
"""
from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.api_guard import cached_value, heavy_query_guard
from backend.collectors.freshness import SPECS, freshness_meta
from backend.database import get_db
from backend.models.energy import IngestArrival, PowerRevision, QualityDaily, SeriesDim, ZoneDim
from backend.power.hourly_store import REVISION_EXCLUDED_PREFIXES
from backend.power.quality import (
    QUALITY_SERIES,
    REVISION_MATURITY_S,  # noqa: F401 — re-export; canonical home is the engine
    ZONE_SERIES_KEY,
)
from backend.power.zones import POWER_ZONES
from backend.routes.api_v1 import _rate_limit  # same v1 per-IP budget — quality IS v1 traffic

router = APIRouter(prefix="/api/v1/quality", tags=["v1"])

# READ-time maturity threshold for /revisions: canonically defined (and
# documented) in backend/power/quality.py::REVISION_MATURITY_S — the radar's
# quality_major_restatement detector reads the same number, and the engine is
# the one home both can import without pulling in the router stack. Re-exported
# above so this module's name (used by tests/docs and the response payload)
# keeps working.

#: /revisions per-request row cap (MAX_SCAN_ROWS pattern from routes/api_v1):
#: fetch cap+1 and refuse with a reason rather than silently truncate — a
#: truncated ledger is a wrong ledger. 20k rows ≈ months of heavy restatement
#: for one series+zone; a UI never needs more per pull.
MAX_REVISION_ROWS = 20_000

#: /summary windows (days): trailing short/long completeness horizons.
SUMMARY_SHORT_DAYS = 30
SUMMARY_LONG_DAYS = 90
#: /summary is one all-zones × all-series matrix — computed once, served from
#: the keyed TTL cache (api_guard.cached_value) for 15 min. The nightly quality
#: job is the only writer, so even 15 min is generous.
SUMMARY_TTL_S = 900.0

#: /revisions staleness window (days) for the arrival-based `as_of` (module
#: docstring): every live series is fetched at least daily, so two days of NO
#: arrival rows means the fetch path is quiet — the same window the
#: quality_daily spec uses for the nightly engine, kept as one number on
#: purpose (both say "the honest-record machinery ran recently").
ARRIVAL_STALE_DAYS = 2

#: Stale window for the quality endpoints — the quality_daily freshness spec's
#: own max age, looked up by key so spec and API can never drift.
_QUALITY_MAX_AGE_DAYS = int(
    next(s for s in SPECS if s.key == "quality_daily").max_age.total_seconds() // 86400
)

#: The charter matrix: the completeness series plus the reserved zone-level key.
_VALID_QUALITY_SERIES: tuple[str, ...] = QUALITY_SERIES + (ZONE_SERIES_KEY,)

_DAY_S = 86400


# ─── shared helpers ───────────────────────────────────────────────────────────


def _iso(ts: int | None) -> str | None:
    """Epoch seconds UTC → ISO 8601 UTC string (the v1 wire format)."""
    return datetime.fromtimestamp(ts, UTC).isoformat() if ts is not None else None


def _require_zone(zone: str) -> None:
    if zone not in POWER_ZONES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown zone {zone!r}. Valid zones: {', '.join(POWER_ZONES)}.",
        )


def _require_quality_series(series: str) -> None:
    if series not in _VALID_QUALITY_SERIES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown quality series {series!r}. Valid: "
                f"{', '.join(_VALID_QUALITY_SERIES)} "
                f"({ZONE_SERIES_KEY!r} = zone-level flags such as gen_below_load_exports)."
            ),
        )


def _decode_flags(raw: str | None) -> list[dict]:
    """quality_daily.flags (JSON text) → list of flag dicts with the stored
    epoch `hours` converted to ISO strings, keeping one time format on the wire.

    A row whose JSON won't parse yields a visible `_decode_error` flag instead
    of 500ing the whole response — "panels never disappear silently" applies to
    rows too, and one corrupt row must not hide the other 89 days."""
    try:
        flags = json.loads(raw) if raw else []
    except json.JSONDecodeError:
        return [{"rule": "_decode_error", "hours": [], "detail": {}}]
    return [{**f, "hours": [_iso(t) for t in f.get("hours", [])]} for f in flags]


def _delta_pct(old: float, new: float) -> float | None:
    """Restatement size as % of the previously published value; None when the
    old value was exactly 0 (no honest percentage exists). Divides by ABS(old),
    so the sign always equals the direction of movement — a negative price
    restated further down reads as a negative delta, never a sign flip."""
    if old == 0:
        return None
    return round(100.0 * (new - old) / abs(old), 2)


def _pctl(values: list[int], q: float) -> int | None:
    """Linear-interpolated percentile of `values`, rounded to whole seconds.
    None when empty. (Local on purpose — quality.py's quantile helper is private
    to the engine, and this slice stays self-contained in the router.)"""
    if not values:
        return None
    s = sorted(values)
    pos = (len(s) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return int(round(s[lo] + (s[hi] - s[lo]) * (pos - lo)))


def _latest_frontier_lags(db: Session) -> dict[tuple[int, int], int]:
    """(series_id, zone_id) → observed_at − max_ts_new of the NEWEST arrival row
    that brought new hours (rows with n_new == 0 carry no frontier and are
    skipped). ONE greatest-per-group join over ingest_arrival instead of a
    point query per summary cell (O(cells) SELECTs at 37 zones × 6 series —
    pinned by the SELECT-budget test). Lag may be NEGATIVE: day-ahead auctions
    publish hours that lie in the future, so their frontier runs ahead of the
    wall clock — that is the honest number, not an error. Should two frontier
    batches for one pair share the same observed_at second, MIN(max_ts_new)
    wins — the conservative (larger) lag."""
    newest = (
        db.query(
            IngestArrival.series_id.label("sid"),
            IngestArrival.zone_id.label("zid"),
            func.max(IngestArrival.observed_at).label("obs"),
        )
        .filter(IngestArrival.max_ts_new.isnot(None))
        .group_by(IngestArrival.series_id, IngestArrival.zone_id)
        .subquery()
    )
    rows = (
        db.query(
            IngestArrival.series_id,
            IngestArrival.zone_id,
            IngestArrival.observed_at,
            func.min(IngestArrival.max_ts_new),
        )
        .join(
            newest,
            (IngestArrival.series_id == newest.c.sid)
            & (IngestArrival.zone_id == newest.c.zid)
            & (IngestArrival.observed_at == newest.c.obs),
        )
        .filter(IngestArrival.max_ts_new.isnot(None))
        .group_by(IngestArrival.series_id, IngestArrival.zone_id, IngestArrival.observed_at)
        .all()
    )
    return {(sid, zid): int(obs - mts) for sid, zid, obs, mts in rows}


# ─── /summary ─────────────────────────────────────────────────────────────────


def _summary_payload(db: Session) -> dict:
    """The full enabled-zones × charter-series quality matrix. A fixed handful
    of queries (one 90-day quality scan, one revision GROUP BY, one
    greatest-per-group arrival join — never O(cells); the SELECT-budget test
    pins it) → computed at most once per SUMMARY_TTL_S via cached_value;
    freshness stamps are added per REQUEST, outside the cache (a warm cache
    must not freeze age_days — marginal/overview convention)."""
    now = datetime.now(UTC)
    today = now.date()
    cut_long = (today - timedelta(days=SUMMARY_LONG_DAYS)).isoformat()
    cut_short = (today - timedelta(days=SUMMARY_SHORT_DAYS)).isoformat()

    rows = (
        db.query(
            QualityDaily.zone,
            QualityDaily.series_key,
            QualityDaily.date,
            QualityDaily.hours_present,
            QualityDaily.hours_expected,
            QualityDaily.flags,
        )
        .filter(QualityDaily.date >= cut_long)
        .all()
    )

    # (zone, series) → per-window accumulators, Python-side (one pass).
    cells: dict[tuple[str, str], dict] = {}
    for zone, skey, day, present, expected, flags in rows:
        if zone not in POWER_ZONES or skey not in _VALID_QUALITY_SERIES:
            continue  # disabled zone / config drift — not part of the charter matrix
        c = cells.setdefault(
            (zone, skey),
            {"r30": [], "r90": [], "flagged30": 0},
        )
        ratio = (present / expected) if expected > 0 else None  # _zone rows carry 0/0
        if ratio is not None:
            c["r90"].append(ratio)
        flagged = bool(flags and flags != "[]")
        if day >= cut_short:
            if ratio is not None:
                c["r30"].append(ratio)
            if flagged:
                c["flagged30"] += 1

    # Trailing-30d revision counts per (series, zone), one GROUP BY — a covering
    # scan of ix_power_revision_series_zone_observed (series, zone, observed_at):
    # grouped by the index's own prefix, windowed on its third column, no table
    # touch (measured ~924ms full-scan → 4-40ms covering at 2M rows).
    cut_epoch_30 = int(now.timestamp()) - SUMMARY_SHORT_DAYS * _DAY_S
    sid_key = dict(db.query(SeriesDim.id, SeriesDim.key).all())
    zid_key = dict(db.query(ZoneDim.id, ZoneDim.key).all())
    rev30: dict[tuple[str, str], int] = {}
    for sid, zid, n in (
        db.query(PowerRevision.series_id, PowerRevision.zone_id, func.count())
        .filter(PowerRevision.observed_at >= cut_epoch_30)
        .group_by(PowerRevision.series_id, PowerRevision.zone_id)
        .all()
    ):
        skey, zkey = sid_key.get(sid), zid_key.get(zid)
        if skey is not None and zkey is not None:
            rev30[(zkey, skey)] = int(n)

    key_sid = {v: k for k, v in sid_key.items()}
    key_zid = {v: k for k, v in zid_key.items()}
    frontier = _latest_frontier_lags(db)

    zones_out = []
    for zone in POWER_ZONES:  # registry order — deterministic
        series_out = []
        for skey in _VALID_QUALITY_SERIES:
            c = cells.get((zone, skey))
            if c is None:
                continue  # zone doesn't carry this series (or no _zone flags) — omit
            if skey == ZONE_SERIES_KEY:
                # Zone-level flag rows: no completeness, no series of their own
                # in the store — revision count / arrival lag don't apply.
                revisions, lag = None, None
            else:
                revisions = rev30.get((zone, skey), 0)
                lag = frontier.get((key_sid.get(skey), key_zid.get(zone)))
            series_out.append(
                {
                    "series_key": skey,
                    "completeness_30d": round(sum(c["r30"]) / len(c["r30"]), 4) if c["r30"] else None,
                    "completeness_90d": round(sum(c["r90"]) / len(c["r90"]), 4) if c["r90"] else None,
                    "flagged_days_30d": c["flagged30"],
                    "revisions_30d": revisions,
                    "arrival_lag_s": lag,
                }
            )
        if series_out:
            zones_out.append({"zone": zone, "series": series_out})

    # as_of = the newest quality DAY on record (indexed point lookup) — a data
    # fact, cacheable; age_days/stale are stamped per request from it.
    latest = db.query(func.max(QualityDaily.date)).scalar()
    return {
        "available": bool(zones_out),
        "zones": zones_out,
        "windows": {"short_days": SUMMARY_SHORT_DAYS, "long_days": SUMMARY_LONG_DAYS},
        "series_keys": list(_VALID_QUALITY_SERIES),
        "as_of": latest,
        "note": (
            "Completeness = mean(hours_present/hours_expected) over days WITH quality rows "
            "in the window; flags describe the published data (see /api/v1/quality/series); "
            "revisions_30d counts the source's own restatements beyond float noise; "
            "arrival_lag_s = last fetch's wall-clock minus the newest hour it brought "
            "(negative for day-ahead series — the auction publishes the future). "
            f"'{ZONE_SERIES_KEY}' rows are zone-level flags and appear only on flagged days."
        ),
    }


@router.get("/summary")
def quality_summary(
    db: Session = Depends(get_db),
    _rl: None = Depends(_rate_limit),
    _g: None = Depends(heavy_query_guard),
):
    """Data-quality matrix over enabled zones × charter series: trailing 30/90-day
    completeness, flagged days, restatement counts and latest arrival lag —
    "here is what the published record looks like", per cell. Descriptive.

    Zone-independent and the heaviest read on this router → one compute per
    15 min (cached_value) behind heavy_query_guard; freshness is stamped per
    request so a warm cache can't freeze age_days. The cached dict is never
    mutated — the response is rebuilt around it."""
    data = cached_value("quality_summary", lambda: _summary_payload(db), ttl=SUMMARY_TTL_S)
    return {
        **data,
        **freshness_meta(data.get("as_of"), datetime.now(UTC).date(), _QUALITY_MAX_AGE_DAYS),
    }


# ─── /series ──────────────────────────────────────────────────────────────────


@router.get("/series")
def quality_series(
    series: str = Query(..., description=f"Quality series key ({', '.join(_VALID_QUALITY_SERIES)})"),
    zone: str = Query(..., description="Bidding zone key, e.g. DE_LU"),
    days: int = Query(90, ge=1, le=365, description="Trailing window (UTC days)"),
    db: Session = Depends(get_db),
    _rl: None = Depends(_rate_limit),
):
    """Daily quality rows for ONE series+zone, newest first: hours present vs
    expected plus the decoded rule flags (each flag: rule, affected hours as
    ISO UTC, detail) — the drill-down behind a /summary cell. Also reports
    arrival-lag stats over the same window (median + p90 of frontier lag =
    batch observed_at − newest hour it brought; negative for day-ahead series,
    whose frontier runs ahead of the clock). Every flag DESCRIBES the source's
    published output — none of it judges the market.

    Cheap by construction (≤365 indexed quality rows + one indexed arrival
    range scan bounded by the log's 90-day retention) — rate-limited only, no
    heavy slot."""
    _require_quality_series(series)
    _require_zone(zone)
    now = datetime.now(UTC)
    today = now.date()
    cut_day = (today - timedelta(days=days)).isoformat()

    rows = (
        db.query(QualityDaily)
        .filter(
            QualityDaily.zone == zone,
            QualityDaily.series_key == series,
            QualityDaily.date >= cut_day,
        )
        .order_by(QualityDaily.date.desc())
        .all()
    )
    data = [
        {
            "date": r.date,
            "hours_present": r.hours_present,
            "hours_expected": r.hours_expected,
            "flags": _decode_flags(r.flags),
        }
        for r in rows
    ]

    # Arrival-lag stats over the window. "_zone" is a reserved pseudo-series
    # with no store series of its own → no arrival rows, honest zeros.
    sid = db.query(SeriesDim.id).filter(SeriesDim.key == series).scalar()
    zid = db.query(ZoneDim.id).filter(ZoneDim.key == zone).scalar()
    lags: list[int] = []
    if sid is not None and zid is not None:
        cut_epoch = int(now.timestamp()) - days * _DAY_S
        lags = [
            int(obs - mts)
            for obs, mts in db.query(IngestArrival.observed_at, IngestArrival.max_ts_new)
            .filter(
                IngestArrival.series_id == sid,
                IngestArrival.zone_id == zid,
                IngestArrival.observed_at >= cut_epoch,
                IngestArrival.max_ts_new.isnot(None),
            )
            .all()
        ]

    return {
        "available": bool(data),
        "series": series,
        "zone": zone,
        "days": days,
        "data": data,
        "arrival": {
            "n_batches": len(lags),
            "median_lag_s": _pctl(lags, 0.5),
            "p90_lag_s": _pctl(lags, 0.9),
        },
        **freshness_meta(rows[0].date if rows else None, today, _QUALITY_MAX_AGE_DAYS),
    }


# ─── /revisions ───────────────────────────────────────────────────────────────


@router.get("/revisions")
def quality_revisions(
    series: str = Query(..., description="Series key, e.g. load.actual (see /api/v1/series/catalog)"),
    zone: str = Query(..., description="Bidding zone key, e.g. DE_LU"),
    days: int = Query(30, ge=1, le=365, description="Trailing window over observed_at (UTC days)"),
    mature: bool = Query(
        True,
        description="true (default): only restatements observed >48h after the hour "
        "they restate (settled data changed); false: include the normal "
        "provisional fill-in window too",
    ),
    db: Session = Depends(get_db),
    _rl: None = Depends(_rate_limit),
    _g: None = Depends(heavy_query_guard),
):
    """The revision ledger for ONE series+zone: every time the source re-published
    a different value for an hour it had already published (beyond the float-noise
    epsilon — see backend/power/hourly_store.py), with old/new value, when the
    change was observed, and its size in %. Plus a per-hour roll-up of hours
    restated MORE THAN ONCE. Descriptive: the ledger reports the source's own
    restatements, it never says the data was "wrong".

    `mature=true` (default) keeps only revisions observed more than 48 h
    (REVISION_MATURITY_S) after the hour they restate — real changes to settled
    data, not the routine provisional fill-in of the first couple of days. The ledger is
    forward-only (accrues from first deploy), so `as_of` here is the newest
    ingest_arrival.observed_at for this series+zone — the last moment the source
    was polled and could have restated something — not a per-spec window.

    Heavy-guarded: the per-pair ledger scan is unbounded by retention (the
    ledger is never pruned — it is the product)."""
    _require_zone(zone)
    if series.startswith(REVISION_EXCLUDED_PREFIXES):
        return {
            "available": False,
            "series": series,
            "zone": zone,
            "reason": (
                f"{series!r} is a derived series and is not revision-ledgered: it restates "
                "whenever its inputs restate, so its ledger would double-count every "
                "upstream revision. Query the input series instead (e.g. load.actual, "
                "gen.B16, gen.B18/B19)."
            ),
            # inert triple — there is no ledger to be fresh about, and every
            # response on this router carries the three keys
            **freshness_meta(None, None, ARRIVAL_STALE_DAYS),
        }
    sid = db.query(SeriesDim.id).filter(SeriesDim.key == series).scalar()
    if sid is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown series {series!r} — see /api/v1/series/catalog for the queryable keys.",
        )
    zid = db.query(ZoneDim.id).filter(ZoneDim.key == zone).scalar()

    now = datetime.now(UTC)
    cut_epoch = int(now.timestamp()) - days * _DAY_S
    rows: list[PowerRevision] = []
    if zid is not None:
        q = (
            db.query(PowerRevision)
            .filter(
                PowerRevision.series_id == sid,
                PowerRevision.zone_id == zid,
                PowerRevision.observed_at >= cut_epoch,
            )
        )
        if mature:
            q = q.filter(PowerRevision.observed_at - PowerRevision.ts_utc > REVISION_MATURITY_S)
        rows = (
            q.order_by(PowerRevision.observed_at.desc(), PowerRevision.id.desc())
            .limit(MAX_REVISION_ROWS + 1)
            .all()
        )

    # Honest freshness for a forward-only ledger (module docstring): the newest
    # arrival for this pair — evidence the source was last POLLED then.
    last_polled = (
        db.query(func.max(IngestArrival.observed_at))
        .filter(IngestArrival.series_id == sid, IngestArrival.zone_id == zid)
        .scalar()
        if zid is not None
        else None
    )
    fresh = freshness_meta(_iso(last_polled), now.date(), ARRIVAL_STALE_DAYS)

    if len(rows) > MAX_REVISION_ROWS:
        return {
            "available": False,
            "series": series,
            "zone": zone,
            "reason": (
                f"Window matches more than {MAX_REVISION_ROWS:,} revision rows — narrow `days`. "
                "This is a per-request cap, not the ledger's extent."
            ),
            **fresh,
        }

    data = [
        {
            "ts_utc": _iso(r.ts_utc),
            "old_value": r.old_value,
            "new_value": r.new_value,
            "observed_at": _iso(r.observed_at),
            "delta_pct": _delta_pct(r.old_value, r.new_value),
        }
        for r in rows
    ]

    # Per-hour roll-up over the SAME filtered rows: hours restated more than
    # once. `rows` is observed_at-desc, so the first row seen per hour is that
    # hour's LATEST restatement — its delta is last_change_pct.
    per_hour: dict[int, list[PowerRevision]] = {}
    for r in rows:
        per_hour.setdefault(r.ts_utc, []).append(r)
    restated = [
        {
            "ts_utc": _iso(ts),
            "n_revisions": len(rs),
            "last_change_pct": _delta_pct(rs[0].old_value, rs[0].new_value),
        }
        for ts, rs in sorted(per_hour.items(), reverse=True)
        if len(rs) > 1
    ]

    out = {
        "available": bool(data),
        "series": series,
        "zone": zone,
        "days": days,
        "mature": mature,
        "maturity_threshold_s": REVISION_MATURITY_S,
        "count": len(data),
        "data": data,
        "restated_hours": restated,
        **fresh,
    }
    if not data:
        out["reason"] = (
            "No revisions on record for this series+zone in the window. The ledger accrues "
            "from first deploy (forward-only), and with mature=true the routine provisional "
            "fill-in is filtered out — a quiet ledger means the source has not restated "
            "settled data here."
        )
    return out
