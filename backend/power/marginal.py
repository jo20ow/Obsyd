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

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from backend.models.energy import SeriesDim
from backend.power.hourly_store import read_hourly
from backend.power.zones import POWER_ZONES

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

    Overrides, in precedence order (each earlier rule wins outright):
      (a) price <= PRICE_FLOOR_EPS        → must_run_renewables, regardless
      (b) no dispatchable band qualifies  → must_run_renewables
      (c) hydro_flex share > EVERY qualifying thermal band's share → hydro_flex
    Otherwise: the MOST EXPENSIVE qualifying dispatchable band.
    """
    def share(band: str) -> float:
        return 100.0 * bands.get(band, 0.0) / total

    qualifying = [
        b for b in DISPATCHABLE_ORDER
        if bands.get(b, 0.0) >= MIN_MW and share(b) >= MIN_SHARE_PCT
    ]
    hydro_share = share("hydro_flex")

    if price <= PRICE_FLOOR_EPS:
        tech = "must_run_renewables"
    elif not qualifying:
        tech = "must_run_renewables"
    elif hydro_share > max(share(b) for b in qualifying):
        tech = "hydro_flex"
    else:
        tech = qualifying[-1]  # most expensive rung still meaningfully on

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

    prices = dict(read_hourly(db, PRICE_SERIES, zone, start_ts))

    # MW per band and the ALL-codes total, per hour. B20/unknown feed the total
    # only (never `bands`) — present in the denominator, impossible to attribute.
    bands_by_hour: dict[int, dict[str, float]] = {}
    total_by_hour: dict[int, float] = {}
    for key in _gen_series_keys(db):
        band = _BAND_OF.get(key.removeprefix("gen."))
        for ts, mw in read_hourly(db, key, zone, start_ts):
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
        "method": (
            "Technology-level estimate from a FIXED conventional merit order "
            "(must-run renewables → nuclear → lignite → hard coal → gas → oil): "
            "per hour, the most expensive band that meaningfully dispatches "
            f"(≥{MIN_SHARE_PCT}% of generation and ≥{MIN_MW:.0f} MW) is taken to "
            "have set the price. The order is assumed, not computed — no fuel, CO2 "
            "or per-plant efficiency data exists here "
            "(docs/findings/2026-06-24-eua-coal-data-source.md)."
        ),
        "note": (
            "A descriptive attribution heuristic, not a model of the SDAC auction "
            "and not a forecast. A fixed order cannot see coal↔gas fuel switching; "
            "pumped storage and reservoir hydro bid opportunity cost and can set "
            "the price at any level (hours where flexible hydro out-runs every "
            "qualifying thermal band are attributed 'hydro_flex', with that "
            "caveat); imports can set the price with no domestic technology "
            "marginal at all. 'tension' hours sit outside the technology's coarse "
            "expected price band and are reported, never reclassified — "
            "consistent_pct is the canary that the static order is off."
        ),
    }
