"""Price-setting technology (estimated) — which band of the merit order the hour landed on.

The desk stores the full hourly generation mix and the hourly day-ahead price for
every zone, and until now never asked the question every power analyst asks of
those two series together: WHAT was setting the price at 18:00? A German evening
at €140 with gas running is a different market from a Nordic afternoon at €140
with the reservoirs choosing to sell — and the mix panel makes the reader do that
attribution by eye, hour after hour.

WHAT THESE NUMBERS ARE, AND ARE NOT
-----------------------------------
The conventional marginal-cost ORDER is ASSUMED here, not computed. A real merit
order is built from fuel prices, CO2 prices and per-plant efficiencies — and this
repo holds none of those inputs: no per-zone fuel prices, no EUA price, no coal
price (the documented blocker: docs/findings/2026-06-24-eua-coal-data-source.md).
So this module takes the textbook ordering (must-run renewables → nuclear →
lignite → hard coal → gas → oil) as a fixed assumption and, per hour, attributes
the price to the most expensive band that meaningfully dispatches. Everything
that follows from that is an estimate, and the honest list of what the fixed
order CANNOT see:

  * Coal↔gas fuel switching is invisible. When gas is cheap and carbon dear, gas
    plants undercut coal and the real order flips — a fixed ladder keeps saying
    "coal" while the market is clearing on gas, or vice versa.
  * Pumped storage and reservoir hydro (HYDRO_FLEX) bid OPPORTUNITY COST, not a
    fuel cost, and can set the price at any level — a Nordic reservoir is happy
    to be the marginal plant at €30 or at €300. They are therefore attributed
    separately and are never a rung of the cost ladder.
  * Imports can set the price with no domestic technology marginal at all: in a
    coupled hour the marginal plant may stand in a neighbouring zone, and the
    domestic mix says nothing about it.
  * The actual marginal UNIT is published by nobody. This is a technology-level
    attribution heuristic on the public record — NOT a model of the SDAC auction,
    and not a forecast.

Two grain caveats on the bands themselves: ENTSO-E A75 reports one code (B04)
for all gas-fired generation, so CCGT and OCGT — different plants at different
costs — are indistinguishable here; and B20 ("Other") plus any unknown code
counts toward total generation but is NEVER attributed as price-setting, because
attributing the price to a band nobody can name would be an invented claim.

The per-hour `consistency` field is a SANITY CHECK, not a cost model: each
technology carries a coarse expected day-ahead price band, and an attributed
hour whose price sits outside its band is flagged "tension" — and deliberately
NOT reclassified, because silently moving the attribution to whatever band the
price fits would turn a stated assumption into a hidden circular one. The
summary's `consistent_pct` is the canary: when it sags, the static order is off
for this zone and the reader should trust the panel less, which is exactly what
the number is there to let them do.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from backend.models.energy import SeriesDim
from backend.power.hourly_store import read_hourly
from backend.power.zones import POWER_ZONES

logger = logging.getLogger(__name__)

PRICE_SERIES = "price.dayahead"

#: The assumed merit order, cheapest band first. must_run_renewables is the zero
#: (or near-zero) marginal-cost floor; the DISPATCHABLE ladder above it is the
#: textbook European ordering. The B-code membership is fixed by what each fuel
#: is priced off: peat (B08) rides with lignite, coal-derived gas (B03) is priced
#: off coal, oil shale (B07) off oil. Gas (B04) is one band because A75 cannot
#: tell a CCGT from an OCGT (see module docstring).
MERIT_BANDS: list[tuple[str, list[str]]] = [
    ("must_run_renewables",
     ["B01", "B09", "B11", "B13", "B15", "B16", "B17", "B18", "B19"]),
    ("nuclear", ["B14"]),
    ("lignite", ["B02", "B08"]),
    ("hard_coal", ["B05", "B03"]),
    ("gas", ["B04"]),
    ("oil", ["B06", "B07"]),
]

#: Pumped storage (B10) and reservoir hydro (B12): dispatchable, but they bid
#: opportunity cost, not fuel cost — never a rung of the cost ladder above.
HYDRO_FLEX = ["B10", "B12"]

#: The cost ladder, cheapest → most expensive. must_run_renewables is not in it:
#: it is the floor the ladder stands on, reached by override, never by rank.
DISPATCHABLE_ORDER = [name for name, _codes in MERIT_BANDS[1:]]

_BAND_OF: dict[str, str] = {
    **{code: name for name, codes in MERIT_BANDS for code in codes},
    **{code: "hydro_flex" for code in HYDRO_FLEX},
}
# B20 and any unknown code are deliberately absent from _BAND_OF: they count
# toward total generation but are never attributed (module docstring).

#: A dispatchable band "meaningfully dispatches" only above BOTH thresholds —
#: the share keeps a rounding trickle in a huge zone from claiming the price,
#: the MW floor keeps a single peaker in a tiny zone from doing the same.
MIN_SHARE_PCT = 1.5
MIN_MW = 200.0

#: At or below this price the marginal cost logic is moot: something is being
#: paid to run (or bidding must-run), so the hour is attributed to the must-run
#: band regardless of what else is on.
PRICE_FLOOR_EPS = 0.0

#: Coarse expected day-ahead price band per technology (EUR/MWh), for the
#: `consistency` sanity check ONLY — these are NOT computed costs (no fuel or
#: CO2 price exists in this repo to compute one from), just wide plausibility
#: bands. (lo, hi); None = unbounded on that side. hydro_flex is always
#: consistent: opportunity-cost bidding has no expected band by definition.
CONSISTENCY_BANDS: dict[str, tuple[float | None, float | None]] = {
    "must_run_renewables": (None, 5.0),
    "nuclear": (None, 40.0),
    "lignite": (20.0, 90.0),
    "hard_coal": (30.0, 120.0),
    "gas": (40.0, 250.0),
    "oil": (90.0, None),
    "hydro_flex": (None, None),
}

TECH_LABELS = {
    "must_run_renewables": "Renewables / must-run",
    "nuclear": "Nuclear",
    "lignite": "Lignite",
    "hard_coal": "Hard coal",
    "gas": "Gas",
    "oil": "Oil",
    "hydro_flex": "Flexible hydro (opportunity cost)",
}

#: The honesty strings, shared verbatim by compute_marginal and
#: compute_marginal_overview — module constants so the two responses can never
#: drift apart.
METHOD = (
    "Technology-level estimate from a FIXED conventional merit order "
    "(must-run renewables → nuclear → lignite → hard coal → gas → oil): "
    "per hour, the most expensive band that meaningfully dispatches "
    f"(≥{MIN_SHARE_PCT}% of generation and ≥{MIN_MW:.0f} MW) is taken to "
    "have set the price. The order is assumed, not computed — no fuel, CO2 "
    "or per-plant efficiency data exists here "
    "(docs/findings/2026-06-24-eua-coal-data-source.md)."
)
NOTE = (
    "A descriptive attribution heuristic, not a model of the SDAC auction "
    "and not a forecast. A fixed order cannot see coal↔gas fuel switching; "
    "pumped storage and reservoir hydro bid opportunity cost and can set "
    "the price at any level (hours where flexible hydro meaningfully "
    "dispatches and out-runs every qualifying thermal band are attributed "
    "'hydro_flex', with that caveat); imports can set the price with no "
    "domestic technology "
    "marginal at all. 'tension' hours sit outside the technology's coarse "
    "expected price band and are reported, never reclassified — "
    "consistent_pct is the canary that the static order is off."
)


def _consistency(tech: str, price: float) -> str:
    """"ok" when the price sits in the attributed technology's coarse band.

    "tension" is reported, never acted on: the attribution stands (module
    docstring — reclassifying on tension would be circular).
    """
    lo, hi = CONSISTENCY_BANDS[tech]
    if lo is not None and price < lo:
        return "tension"
    if hi is not None and price > hi:
        return "tension"
    return "ok"


def attribute_hour(price: float, bands: dict[str, float], total: float) -> dict:
    """One hour's attribution. Pure.

    `bands` holds MW per band name (incl. "hydro_flex"); `total` is ALL
    generation including B20/unknown — shares are measured against the whole
    fleet, so an "Other"-heavy hour honestly dilutes every named band.
    Precondition: `total` > 0 — the caller guards it (an hour of zero reported
    generation is skipped, never attributed).

    Attribution, in precedence order (each earlier rule wins outright):
      (a) price <= PRICE_FLOOR_EPS → must_run_renewables, regardless
      (b) hydro_flex itself qualifies (same MIN_SHARE_PCT/MIN_MW thresholds as
          a thermal band) AND its share exceeds EVERY qualifying thermal band's
          — vacuously so when no thermal band qualifies at all → hydro_flex.
          The vacuous case is the Nordic one: a 95%-reservoir hour with no
          thermal fleet on is genuinely priced by the reservoirs, and calling
          it "must-run" would be wrong.
      (c) otherwise: the MOST EXPENSIVE qualifying thermal band
      (d) otherwise (nothing qualifies at all) → must_run_renewables
    """
    def share(band: str) -> float:
        return 100.0 * bands.get(band, 0.0) / total

    qualifying = [
        b for b in DISPATCHABLE_ORDER
        if bands.get(b, 0.0) >= MIN_MW and share(b) >= MIN_SHARE_PCT
    ]
    hydro_share = share("hydro_flex")
    hydro_qualifies = (
        bands.get("hydro_flex", 0.0) >= MIN_MW and hydro_share >= MIN_SHARE_PCT
    )

    if price <= PRICE_FLOOR_EPS:
        tech = "must_run_renewables"
    elif hydro_qualifies and all(hydro_share > share(b) for b in qualifying):
        tech = "hydro_flex"
    elif qualifying:
        tech = qualifying[-1]  # most expensive rung still meaningfully on
    else:
        tech = "must_run_renewables"

    return {
        "tech": tech,
        "tech_label": TECH_LABELS[tech],
        "share_pct": round(share(tech), 1),
        "mw": round(bands.get(tech, 0.0), 1),
        "consistency": _consistency(tech, price),
    }


def _gen_series_keys(db: Session) -> list[str]:
    """Every `gen.<Bxx>` series key in the catalog. LIKE on the tiny series_dim
    is fine — the range scans themselves go id-first through read_hourly, which
    is what keeps 28M-row power_hourly off a key join (see capture.py::_ids)."""
    return [
        key for (key,) in
        db.query(SeriesDim.key).filter(SeriesDim.key.like("gen.%")).all()
    ]


def compute_marginal(
    db: Session, zone: str, hours: int = 168, *, now: datetime | None = None
) -> dict:
    """Hour-by-hour estimated price-setting technology for one zone.

    Compute-on-read: nothing is persisted and no collector exists for this —
    it is arithmetic on the gen.* and price.dayahead series already ingested.
    Hours with a missing price or zero/missing generation are skipped: not
    attributed, absent from `hourly`, and not counted in `consistent_pct`.
    """
    if zone not in POWER_ZONES:
        return {"available": False, "zone": zone, "reason": f"Unknown zone {zone}."}

    now = now or datetime.now(timezone.utc)
    label = POWER_ZONES[zone]["label"]
    start_ts = int((now - timedelta(hours=hours)).timestamp())
    # A closed window, like the live desk's reads (backend/power/live.py): the
    # day-ahead auction publishes into tomorrow, and an open-ended price read
    # would pull those hours only for the gen∩price intersection to discard
    # them. End at the next top-of-hour so the in-progress hour still counts.
    # The row cap matches the window exactly — these series are hourly-canonical,
    # so more rows than hours is a store bug worth hearing about (read_hourly
    # raises on the cap instead of truncating).
    end_ts = (int(now.timestamp()) // 3600 + 1) * 3600
    max_rows = hours + 1

    prices = dict(
        read_hourly(db, PRICE_SERIES, zone, start_ts, end_ts, max_rows=max_rows)
    )

    # MW per band and the ALL-codes total, per hour. B20/unknown feed the total
    # only (never `bands`) — present in the denominator, impossible to attribute.
    bands_by_hour: dict[int, dict[str, float]] = {}
    total_by_hour: dict[int, float] = {}
    for key in _gen_series_keys(db):
        band = _BAND_OF.get(key.removeprefix("gen."))
        for ts, mw in read_hourly(db, key, zone, start_ts, end_ts, max_rows=max_rows):
            total_by_hour[ts] = total_by_hour.get(ts, 0.0) + mw
            if band is not None:
                per = bands_by_hour.setdefault(ts, {})
                per[band] = per.get(band, 0.0) + mw

    hourly: list[dict] = []
    ok_hours = 0
    tech_hours: dict[str, int] = {}
    daily_tech: dict[str, dict[str, int]] = {}
    for ts in sorted(set(prices) & set(total_by_hour)):
        total = total_by_hour[ts]
        if total <= 0:
            continue  # an hour of zero reported generation attributes nothing
        att = attribute_hour(prices[ts], bands_by_hour.get(ts, {}), total)
        when = datetime.fromtimestamp(ts, tz=timezone.utc)
        hourly.append({
            "ts_utc": when.isoformat(),
            "price": round(prices[ts], 2),
            **att,
        })
        if att["consistency"] == "ok":
            ok_hours += 1
        tech_hours[att["tech"]] = tech_hours.get(att["tech"], 0) + 1
        day = daily_tech.setdefault(when.strftime("%Y-%m-%d"), {})
        day[att["tech"]] = day.get(att["tech"], 0) + 1

    if not hourly:
        return {
            "available": False,
            "zone": zone,
            "zones": list(POWER_ZONES),
            "hours": hours,
            "reason": (
                f"No overlapping generation and day-ahead price hours for {label} "
                "in this window yet — check back shortly."
            ),
        }

    n = len(hourly)
    daily = [
        {
            "date": day,
            "shares": {
                tech: round(100.0 * count / sum(per.values()), 1)
                for tech, count in sorted(per.items())
            },
        }
        for day, per in sorted(daily_tech.items())
    ]
    return {
        "available": True,
        "zone": zone,
        "zones": list(POWER_ZONES),
        "hours": hours,
        "unit": "EUR/MWh",
        "hourly": hourly,
        "daily": daily,
        "summary": {
            "share_of_hours": {
                tech: round(100.0 * count / n, 1)
                for tech, count in sorted(tech_hours.items())
            },
            # The canary on the assumed order: the share of attributed hours whose
            # price sat inside the attributed technology's coarse band.
            "consistent_pct": round(100.0 * ok_hours / n, 1),
            "attributed_hours": n,
        },
        "as_of": hourly[-1]["ts_utc"],
        "method": METHOD,
        "note": NOTE,
    }


def compute_marginal_overview(
    db: Session, hours: int = 168, *, now: datetime | None = None
) -> dict:
    """Latest price-setting attribution for EVERY enabled zone — one map read.

    Compute-on-read like compute_marginal, which it calls per zone and reduces
    to each zone's newest attributed hour (`hourly[-1]`). A zone with no
    attributable hour in the window goes into `missing` — shown as no-data,
    never painted with an invented value — and a zone whose compute RAISES
    goes into `errors`, kept apart from `missing` so a failure can never pass
    itself off as an honest data gap. The 168 h default is compute_marginal's
    own, and deliberately wider than the 3-day stale threshold: a shorter
    window would age a zone into `missing` before `stale` could ever fire,
    making painted-but-stale unrepresentable.
    """
    zones: list[dict] = []
    missing: list[str] = []
    errors: list[str] = []
    for zone in POWER_ZONES:
        try:
            result = compute_marginal(db, zone, hours=hours, now=now)
        except Exception:
            logger.exception("marginal overview: compute failed for zone %s", zone)
            errors.append(zone)
            continue
        hourly = result.get("hourly") if result.get("available") else None
        if not hourly:
            missing.append(zone)
            continue
        latest = hourly[-1]
        zones.append({
            "zone": zone,
            "zone_label": POWER_ZONES[zone]["label"],
            "tech": latest["tech"],
            "tech_label": latest["tech_label"],
            "share_pct": latest["share_pct"],
            "mw": latest["mw"],
            "consistency": latest["consistency"],
            "price": latest["price"],
            "ts_utc": latest["ts_utc"],
        })
    return {
        "available": bool(zones),
        "hours": hours,
        "zones": zones,
        "missing": missing,
        "errors": errors,
        "method": METHOD,
        "note": NOTE,
    }
