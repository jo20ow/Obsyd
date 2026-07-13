"""Capture rate — what a solar MWh actually earned.

The desk can say a zone had 180 negative-price hours. It cannot say what a solar
MWh was WORTH, and that is the number the European power market argues about most:
the capture price (the generation-weighted average price a technology achieved)
and the capture RATE, or value factor, which is that price divided by the plain
baseload price.

    "DE-LU solar captured €31/MWh in June — 62% of baseload. Wind onshore: 88%."

An asset owner, a PPA desk, anyone modelling a merit order asks this first. No
free European tool publishes it per bidding zone, and Obsyd has held both legs at
hourly resolution — price.dayahead × gen.<psr> — for months without ever
multiplying them together.

WHAT IT IS
----------
    capture_price(fuel, month) = Σ(price_h × gen_h) / Σ(gen_h)
    baseload_price(month)      = mean(price_h) over ALL hours of the month
    value_factor               = capture_price / baseload_price

Both are realised, backward-looking averages of published auction prices. There is
no model and no forecast: this is arithmetic on the record. The declining value
factor of solar IS the cannibalisation story, told in the market's own numbers.

THE DENOMINATOR IS THE WHOLE MONTH, AND THAT IS THE ENTIRE POINT
----------------------------------------------------------------
Solar's value factor is below 1.00 *because* it only produces in the hours it
produces in — and those hours, thanks to solar itself, are the cheap ones. Divide
by the mean price of solar's OWN hours and you have compared midday against midday:
the factor snaps back toward 1.00 and the cannibalisation vanishes from the number
that exists to show it. The baseload reference is the month's base product — every
hour, the same denominator for every technology.

The corollary is that an hour with no generation row is an hour with no output. For
solar at 03:00 that is simply true, and it costs the average nothing (0 × price = 0).
For a fuel whose FEED broke it would be a lie — so the sample guard counts DAYS, not
hours: a technology must appear on MIN_DAYS distinct days of the month. Solar is
absent every night and present every day; a broken feed is absent for whole days.
That is the difference the guard is built to see.

FURTHER CAVEATS THAT TRAVEL WITH THE NUMBER
-------------------------------------------
* A month whose price series is itself a fragment is reported as absent, not averaged
  into a headline (MIN_PRICE_HOURS).
* A month whose baseload is zero or negative gets no value factor: dividing through
  zero produces a number, not a meaning.
* These are day-ahead revenues only. Real assets also earn (or lose) in intraday,
  balancing and their PPA — the capture price is the market's mark, not a P&L.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from backend.models.energy import PowerHourly, SeriesDim, ZoneDim
from backend.power.entsoe_grid import PSR_LABELS
from backend.power.zones import POWER_ZONES

PRICE_SERIES = "price.dayahead"

#: The technologies worth a capture rate. Solar and wind are the point (their
#: value factor is the cannibalisation story); nuclear and gas are the contrast —
#: dispatchable fleets capture ABOVE baseload because they run when it is dear.
CAPTURE_FUELS = ["B16", "B18", "B19", "B14", "B04"]

#: Below this many priced hours a month is a fragment, not a month.
MIN_PRICE_HOURS = 24 * 20

#: A technology must show up on this many distinct days of the month. Counting days
#: rather than hours is what lets solar (absent every night, present every day) pass
#: while a broken feed (absent for whole days) does not.
MIN_DAYS = 20

#: A monthly figure is legitimately weeks behind — on the 1st of a month the newest
#: complete month just ended. Only a feed that failed to deliver a whole month is
#: stale, so the window is wider than any daily panel's and deliberately so.
STALE_AFTER_DAYS = 45


def _day(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def _window_start(today: date, months: int) -> int:
    """First instant of the calendar month `months` months before this one.

    Capture is a monthly figure, so the window has to be cut on month boundaries. A
    rolling `31 × months`-day window would start mid-month, and that first partial
    month is then always a fragment: ask for 1 month and you get nothing at all.
    """
    total = today.year * 12 + (today.month - 1) - months
    return int(
        datetime(total // 12, total % 12 + 1, 1, tzinfo=timezone.utc).timestamp()
    )


def capture_metrics(prices: dict[int, float], generation: dict[int, float]) -> dict | None:
    """Capture price and value factor for one fuel over one month. Pure.

    ``prices`` is every priced hour of the month; ``generation`` only the hours the
    technology produced in. An hour missing from ``generation`` is an hour of no
    output — it moves neither sum. The baseload denominator is the mean over ALL of
    ``prices``, which is what makes a value factor below 1.00 mean cannibalisation
    rather than nothing at all.
    """
    if not prices:
        return None
    hours = sorted(h for h in generation if h in prices)
    gen = [generation[h] for h in hours]
    total_gen = sum(gen)
    if total_gen <= 0:
        return None  # a technology that produced nothing has no capture price

    px = [prices[h] for h in hours]
    capture = sum(p * g for p, g in zip(px, gen)) / total_gen
    baseload = sum(prices.values()) / len(prices)
    return {
        "hours": len(hours),
        "days": len({_day(h) for h in hours}),
        "generation_gwh": round(total_gen / 1000.0, 1),  # hourly MW → MWh → GWh
        "capture_price": round(capture, 2),
        "baseload_price": round(baseload, 2),
        # A ratio through zero is a number, not a meaning.
        "value_factor": round(capture / baseload, 3) if baseload > 0 else None,
        # The share of a technology's OWN output that landed in negative-price hours —
        # the sharpest single line of the cannibalisation story.
        "negative_gen_pct": round(
            100.0 * sum(g for p, g in zip(px, gen) if p < 0) / total_gen, 1
        ),
    }


def _series_by_month(
    db: Session, zone: str, series_keys: list[str], start_ts: int
) -> dict[str, dict[str, dict[int, float]]]:
    """{series_key: {YYYY-MM: {ts: value}}} — one query for the price and every fuel.

    Filtering on the integer ids rather than on ``series_dim.key`` is not a
    micro-optimisation: ``power_hourly`` is WITHOUT ROWID on (series_id, zone_id,
    ts_utc), so ids hit the clustered key and a join on the text key scans 28M rows.
    """
    sids = {
        sid: key
        for sid, key in db.query(SeriesDim.id, SeriesDim.key)
        .filter(SeriesDim.key.in_(series_keys))
        .all()
    }
    zid = db.query(ZoneDim.id).filter(ZoneDim.key == zone).scalar()
    if not sids or zid is None:
        return {}
    rows = (
        db.query(PowerHourly.series_id, PowerHourly.ts_utc, PowerHourly.value)
        .filter(
            PowerHourly.series_id.in_(sids),
            PowerHourly.zone_id == zid,
            PowerHourly.ts_utc >= start_ts,
        )
        .all()
    )
    out: dict[str, dict[str, dict[int, float]]] = {}
    for sid, ts, value in rows:
        month = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m")
        out.setdefault(sids[sid], {}).setdefault(month, {})[int(ts)] = float(value)
    return out


def compute_capture(
    db: Session, zone: str, months: int = 24, *, today: date | None = None
) -> dict:
    """Capture price and value factor per technology per month for one zone."""
    if zone not in POWER_ZONES:
        return {"available": False, "zone": zone, "reason": f"Unknown zone {zone}."}

    today = today or datetime.now(timezone.utc).date()
    keys = [PRICE_SERIES] + [f"gen.{f}" for f in CAPTURE_FUELS]
    data = _series_by_month(db, zone, keys, _window_start(today, months))
    label = POWER_ZONES[zone]["label"]

    # Only COMPLETE months. The running month would be a month-to-date figure wearing
    # a month's label — the reader would put a 12-day July next to a full June and read
    # a trend that is really a calendar artefact. It appears once it has finished.
    running = today.strftime("%Y-%m")
    price_by_month = {
        m: p
        for m, p in data.get(PRICE_SERIES, {}).items()
        if m < running and len(p) >= MIN_PRICE_HOURS
    }
    if not price_by_month:
        return {
            "available": False,
            "zone": zone,
            "reason": f"No complete month of hourly day-ahead prices for {label} yet.",
        }

    fuels = []
    for fuel in CAPTURE_FUELS:
        gen_by_month = data.get(f"gen.{fuel}", {})
        rows = []
        for month in sorted(gen_by_month):
            prices = price_by_month.get(month)
            if not prices:
                continue
            m = capture_metrics(prices, gen_by_month[month])
            # Days, not hours: solar is absent every night and present every day.
            if m is None or m["days"] < MIN_DAYS:
                continue
            rows.append({"month": month, **m})
        if rows:
            fuels.append(
                {
                    "psr": fuel,
                    "label": PSR_LABELS.get(fuel, fuel),
                    "latest": rows[-1],
                    "data": rows,
                }
            )

    if not fuels:
        return {
            "available": False,
            "zone": zone,
            "reason": (
                f"No complete month of generation data for {label} yet — a capture price "
                "needs a technology's output and the price in the same hours."
            ),
        }

    # Worst value factor first: the cannibalised technology is the one being asked about.
    fuels.sort(key=lambda f: f["latest"]["value_factor"] or 0)
    latest_month = max(f["latest"]["month"] for f in fuels)
    prices = price_by_month[latest_month]

    # as_of is the newest hour the numbers were actually built from, per the
    # convention every panel shares — not the month label, which is only a caption.
    as_of = _day(max(prices))
    age_days = (today - date.fromisoformat(as_of)).days
    return {
        "available": True,
        "zone": zone,
        "zone_label": label,
        "unit": "EUR/MWh",
        "months": months,
        "min_days": MIN_DAYS,
        "latest_month": latest_month,
        "as_of": as_of,
        "age_days": age_days,
        "stale": age_days > STALE_AFTER_DAYS,
        "baseload_price": round(sum(prices.values()) / len(prices), 2),
        "fuels": fuels,
        "note": (
            "Capture price = the generation-weighted average day-ahead price a technology "
            "actually achieved; value factor = capture price ÷ the month's baseload price "
            "(the mean of ALL hours). Realised and backward-looking: arithmetic on published "
            "auction results, not a model and not a forecast. Below 1.00 the technology "
            "earned less than baseload — for solar and wind, that is cannibalisation. "
            "Day-ahead only: real assets also earn in intraday, balancing and their PPA."
        ),
    }
