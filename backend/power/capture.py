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

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from backend.models.energy import SeriesDim, ZoneDim
from backend.power.entsoe_grid import PSR_LABELS
from backend.power.zones import POWER_ZONES

PRICE_SERIES = "price.dayahead"

#: The technologies worth a capture rate. Solar and wind are the point (their value
#: factor is the cannibalisation story); nuclear and gas are the contrast — dispatchable
#: fleets capture ABOVE baseload because they run when it is dear. Hydro is BOTH, and
#: that is why it is here: reservoir (B12) is the most dispatchable plant in Europe and
#: should out-earn baseload, while run-of-river (B11) must run whatever the price does.
#: Leaving hydro out would also leave the Nordic and Alpine zones with a table showing
#: nothing but a peaking gas plant, when hydro IS their fleet.
#:
#: Pumped storage (B10) is deliberately absent: it is a consumer as much as a producer,
#: and a capture price on its generation leg alone would be a half-truth about an asset
#: whose entire economics is the round trip.
CAPTURE_FUELS = ["B16", "B18", "B19", "B11", "B12", "B14", "B04"]

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


def _ids(db: Session, zone: str, series_keys: list[str]) -> tuple[dict[int, str], int | None]:
    """({series_id: key}, zone_id).

    Resolving ids and filtering on THEM is not a micro-optimisation: power_hourly is
    WITHOUT ROWID on (series_id, zone_id, ts_utc), so ids hit the clustered key while a
    predicate on the joined series_dim.key cannot use it and scans 28M rows.
    """
    sids = {
        sid: key
        for sid, key in db.query(SeriesDim.id, SeriesDim.key)
        .filter(SeriesDim.key.in_(series_keys))
        .all()
    }
    return sids, db.query(ZoneDim.id).filter(ZoneDim.key == zone).scalar()


#: Both halves of the capture arithmetic, aggregated per (fuel, month) IN SQLite.
#:
#: The first version pulled every hour of every series into Python — ~200k rows per zone
#: over a 4-year window — and spent most of its time in strftime. It answered in 13ms on a
#: one-month dev database and in 7.8s on prod, which is the shape of that mistake: a query
#: that is only fast where the data is thin. The desk aggregates in SQL for the same reason
#: it does everywhere else on this table.
#:
#: capture_metrics() below remains the DEFINITION of the arithmetic, and a test pins this
#: SQL to it — one derivation, two evaluators, never allowed to drift.
_FUEL_SQL = """
WITH p AS (
    SELECT ts_utc, value AS price FROM power_hourly
     WHERE series_id = :pid AND zone_id = :zid AND ts_utc >= :start
), g AS (
    SELECT series_id, ts_utc, value AS gen FROM power_hourly
     WHERE series_id IN :gids AND zone_id = :zid AND ts_utc >= :start
)
SELECT g.series_id                                        AS sid,
       strftime('%Y-%m', g.ts_utc, 'unixepoch')           AS month,
       SUM(p.price * g.gen)                               AS pxg,
       SUM(g.gen)                                         AS total_gen,
       COUNT(*)                                           AS hours,
       COUNT(DISTINCT date(g.ts_utc, 'unixepoch'))        AS days,
       SUM(CASE WHEN p.price < 0 THEN g.gen ELSE 0 END)   AS neg_gen
  FROM g JOIN p ON p.ts_utc = g.ts_utc
 GROUP BY sid, month
"""

#: The baseload leg: the mean over ALL priced hours of the month, and how many there were.
#: A separate query on purpose — joining it to the fuels would silently restrict it to the
#: hours the fuel generated in, which is precisely the bug this metric exists to avoid.
_PRICE_SQL = """
SELECT strftime('%Y-%m', ts_utc, 'unixepoch') AS month, AVG(value) AS baseload, COUNT(*) AS hours
  FROM power_hourly
 WHERE series_id = :pid AND zone_id = :zid AND ts_utc >= :start
 GROUP BY month
"""


def _aggregate(db: Session, zone: str, start_ts: int) -> tuple[dict, dict]:
    """(price_months, fuel_months) — the whole capture table in two aggregate queries.

    price_months: {month: (baseload, hours)}
    fuel_months:  {psr: {month: {hours, days, generation_gwh, capture_price, negative_gen_pct}}}
    """
    keys = [PRICE_SERIES] + [f"gen.{f}" for f in CAPTURE_FUELS]
    sids, zid = _ids(db, zone, keys)
    pid = next((sid for sid, key in sids.items() if key == PRICE_SERIES), None)
    gids = {sid: key for sid, key in sids.items() if key != PRICE_SERIES}
    if pid is None or zid is None or not gids:
        return {}, {}

    params = {"pid": pid, "zid": zid, "start": start_ts}
    price_months = {
        m: (float(baseload), int(hours))
        for m, baseload, hours in db.execute(text(_PRICE_SQL), params).all()
    }

    fuels: dict[str, dict[str, dict]] = {}
    rows = db.execute(
        text(_FUEL_SQL).bindparams(bindparam("gids", expanding=True)),
        {**params, "gids": list(gids)},
    ).all()
    for sid, month, pxg, total_gen, hours, days, neg_gen in rows:
        if not total_gen or total_gen <= 0:
            continue  # a technology that produced nothing has no capture price
        psr = gids[sid].removeprefix("gen.")
        fuels.setdefault(psr, {})[month] = {
            "hours": int(hours),
            "days": int(days),
            "generation_gwh": round(total_gen / 1000.0, 1),  # hourly MW → MWh → GWh
            "capture_price": round(pxg / total_gen, 2),
            "negative_gen_pct": round(100.0 * (neg_gen or 0.0) / total_gen, 1),
        }
    return price_months, fuels


def _latest_price_hour(db: Session, zone: str, month: str, start_ts: int) -> int:
    """The newest priced hour inside `month` — what `as_of` must report."""
    sids, zid = _ids(db, zone, [PRICE_SERIES])
    pid = next(iter(sids))
    return db.execute(
        text(
            "SELECT MAX(ts_utc) FROM power_hourly "
            " WHERE series_id = :pid AND zone_id = :zid AND ts_utc >= :start"
            "   AND strftime('%Y-%m', ts_utc, 'unixepoch') = :month"
        ),
        {"pid": pid, "zid": zid, "start": start_ts, "month": month},
    ).scalar()


def compute_capture(
    db: Session, zone: str, months: int = 24, *, today: date | None = None
) -> dict:
    """Capture price and value factor per technology per month for one zone."""
    if zone not in POWER_ZONES:
        return {"available": False, "zone": zone, "reason": f"Unknown zone {zone}."}

    today = today or datetime.now(timezone.utc).date()
    start_ts = _window_start(today, months)
    label = POWER_ZONES[zone]["label"]
    price_months, fuel_months = _aggregate(db, zone, start_ts)

    # Only COMPLETE months. The running month would be a month-to-date figure wearing
    # a month's label — the reader would put a 12-day July next to a full June and read
    # a trend that is really a calendar artefact. It appears once it has finished.
    running = today.strftime("%Y-%m")
    baseload = {
        m: bl
        for m, (bl, hours) in price_months.items()
        if m < running and hours >= MIN_PRICE_HOURS
    }
    if not baseload:
        return {
            "available": False,
            "zone": zone,
            "reason": f"No complete month of hourly day-ahead prices for {label} yet.",
        }

    fuels = []
    for fuel in CAPTURE_FUELS:
        rows = []
        for month, m in sorted(fuel_months.get(fuel, {}).items()):
            bl = baseload.get(month)
            # Days, not hours: solar is absent every night and present every day.
            if bl is None or m["days"] < MIN_DAYS:
                continue
            rows.append({
                "month": month,
                **m,
                "baseload_price": round(bl, 2),
                # A ratio through zero is a number, not a meaning.
                "value_factor": round(m["capture_price"] / bl, 3) if bl > 0 else None,
            })
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

    # as_of is the newest hour the numbers were actually built from, per the
    # convention every panel shares — not the month label, which is only a caption.
    as_of = _day(_latest_price_hour(db, zone, latest_month, start_ts))
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
        "baseload_price": round(baseload[latest_month], 2),
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
